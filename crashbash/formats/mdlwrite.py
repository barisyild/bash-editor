"""Move a mesh from one model into another, in place of one of its own.

Nothing is relocated. Every pointer in an MDL is relative to the field holding
it, so the incoming mesh's blocks can simply be appended to the end of the file
and the six pointers in the target mesh's header aimed at them.

The two tables a triangle indexes -- colours at `model+0x20`, UVs at `model+0x24`
-- are model-wide, and the incoming triangles need entries in both. They are
appended too, as a verbatim copy of the original table followed by the new
entries, and the header pointer moved to the copy. Every other mesh keeps
working because its indices address the same entries they always did.

What this does not do is add a texture. The incoming mesh's texture and palette
indices are remapped onto slots in the destination's own pack, which the caller
chooses -- ordinarily the slots the replaced mesh was the only user of. Pack
layout therefore never changes, which matters because how a pack reaches VRAM is
still unknown (docs/FORMAT.md §10.1, `+0x14`).
"""

from __future__ import annotations

import struct
from collections import Counter
from dataclasses import dataclass, field, replace

import numpy as np

from . import modelwrite as MOW
from .mdl import (
    COLOUR_INDEX_MASK,
    MESH_HEADER_SIZE,
    OBJECT_STRIDE,
    PTR_COLOUR_TABLE,
    PTR_UV_TABLE,
    STRIP_FLAG_UNTEXTURED,
    TEXTURE_FLAG_SWATCH,
    TEXTURE_INDEX_MASK,
    Mesh,
    Model,
    read_model,
)

# The file header's own end-of-geometry field, and the boundary it draws. Across
# the 373 models with geometry, no mesh's data ever lies past it and every
# animation blob starts at or after it -- the model proper is what the game
# loads up to this point, and the blobs come in separately. Anything appended
# past it is therefore not in memory when the mesh headers are read, so new
# geometry has to go inside it and the field has to move with it.
PTR_MODEL_END = 0x08
# The shared position pool (§7.3) runs from here to the boundary, so moving the
# boundary without moving the pool turns a degenerate pool into a huge bogus one.
PTR_MODEL_POOL = 0x28
# Two invariants every shipped model keeps, and appending at the end breaks both:
# `T(0x08) <= T(0x44)` in 400/400 and `T(0x08) <= i32@0x50` in 400/400. So new
# geometry is *inserted* in front of the clip table rather than added after it,
# and these two move with it.
PTR_CLIP_TABLE = 0x44
RESIDENT_SIZE = 0x50  # base-relative, not self-relative
SECTOR = 0x800

MESH_HEADER_START = 0x58
COLOUR_ENTRY_SIZE = 4
UV_ENTRY_SIZE = 2

# Header fields, from the mesh header's own base.
FIELD_FACE_COUNT = 0x08
FIELD_FORMAT = 0x0A
FIELD_UNK13 = 0x0C
FIELD_BOUNDS = 0x10
FIELD_STRIPS = 0x14
FIELD_UV_INDEX = 0x18
FIELD_TEXTURE = 0x1C
FIELD_COLOUR_INDEX = 0x20
FIELD_END = 0x24
FIELD_NORMALS = 0x28
FIELD_ATTACHMENT = 0x2C

# A colour index is 13 bits, so a model cannot name more than this many colours.
MAX_COLOURS = COLOUR_INDEX_MASK + 1

# Bit 3 of a strip's flag byte. docs/FORMAT.md recorded it as having no reader
# and no known meaning; it is the winding of the strip's first triangle, and
# equals bit 0 of that triangle's vertex flag in 42,267 of 42,267 strips. This
# writer seeds every strip outward, so the bit is always clear here -- it is
# named for the meshes that carry it, which a reader still has to honour.
STRIP_FLAG_FIRST_FLIPPED = 0x08


@dataclass
class Transplant:
    """The mesh to move, and how its texture references map into the target."""

    data: bytes  # the source file, whole
    model: Model
    mesh: Mesh
    texture_map: dict[int, int] = field(default_factory=dict)
    palette_map: dict[int, int] = field(default_factory=dict)
    # Per source texture, the UV shift needed when its pixels were written into
    # a corner of a larger slot in the destination pack.
    uv_shift: dict[int, tuple[int, int]] = field(default_factory=dict)
    # Per source texture, the factor its UVs need when the destination slot is
    # a different size. A UV is a texel coordinate, so moving a mesh onto a
    # pack whose matching texture is half as wide leaves every corner sampling
    # twice as far out -- off the texture and into whatever shares its page.
    # A slot cannot be resized to avoid this. Its size is what picks the VRAM
    # bucket the loader allocates from (§10.4), so the geometry adapts instead
    # -- or the picture is *added* to the pack at its own size, which
    # `texwrite.append_texture` does and which costs no existing slot at all.
    #
    # The factor is `(dest - 1) / (source - 1)`, not `dest / source`: a 32x32
    # texture's UVs run 0..31, and halving those gives 0..16, which is one
    # column past the end of a 16x16 slot. 35 of the high-poly Coco's 79
    # textured faces read from outside their own slot that way and none do this
    # way; the four that showed are her eyes, which span their texture corner to
    # corner and so have no margin to absorb the error -- on hardware they came
    # back blank.
    uv_scale: dict[int, tuple[float, float]] = field(default_factory=dict)
    # Per source face, the destination palette and swatch cell that give the
    # colour it meant. A swatch face is painted by one texel of the pack's
    # palette-less swatch texture (§6.2), so it names a palette and points its
    # UVs at a cell -- and neither the palette numbering nor the cell layout
    # survives a move between packs. Mapping the palette alone left 279 of the
    # high-poly Coco's 358 faces reading whatever happened to sit at the
    # source's cell: black hair. Matched by the colour each face means, 231 of
    # the 279 land exactly and the worst is 8 of 255 out.
    swatch_face: dict[int, tuple[int, tuple[int, int]]] = field(default_factory=dict)
    # The attachment block (mesh+0x2C) the installed mesh should carry, raw:
    # [u16 flags][u16 count][16 bytes x count]. Gameplay reads it live through
    # the 0x2000 id namespace, and for a character it is the collision volume
    # -- the crate game's crates stopped colliding when it was zeroed. Supply
    # the replaced mesh's own block when the stand-in matches its height.
    attachment: bytes | None = None


def _pool_span(target: Mesh) -> tuple[int, int]:
    """Where an object-pool mesh's blocks begin, and how many bytes it owns."""
    low = min(target.ptr_bounds, target.ptr_strips, target.ptr_uv_index,
              target.ptr_texture, target.ptr_colour_index)
    return low, target.ptr_end - low


def _write_in_place(out: bytearray, dest_data: bytes, target: Mesh, blocks: dict,
                    colour_index: bytes, uv_index: bytes, index: int):
    """Lay an object-pool mesh's blocks back inside the span it already owns.

    The pool is one packed run: over the corpus the next object mesh's header
    sits exactly four bytes past the previous mesh's `ptr_end` in **1802 of
    1898** consecutive pairs. Rebuilding one of them the way a numbered mesh is
    rebuilt -- blocks appended past the end of the file and the header repointed
    at them -- leaves that run with a hole and a `ptr_end` pointing off the end
    of everything. `warp_room1` built that way boots to a black screen, while
    the same graft applied to a numbered mesh boots and draws.

    So the blocks go back where they were. `ptr_end` keeps its shipped value
    even when the rebuild is smaller, because it is what the next header is
    measured from; the leftover stays as slack inside this mesh's own span.
    """
    low, span = _pool_span(target)
    order = [blocks["strips"], blocks["geometry"], uv_index,
             blocks["texture"], colour_index]
    needed = sum(len(b) + (-len(b) % 4) for b in order)
    if needed > span:
        raise ValueError(
            f"mesh {index} is an object-pool mesh and the pool is a packed run "
            f"(the next header is four bytes past the previous mesh's end in "
            f"1802 of 1898 pairs), so its blocks cannot be moved elsewhere -- a "
            f"disc built that way boots to a black screen. The rebuild needs "
            f"{needed} bytes and the mesh owns {span}."
        )

    cursor = low
    offsets = []
    for block in order:
        out[cursor:cursor + len(block)] = block
        offsets.append(cursor)
        cursor += len(block) + (-len(block) % 4)
    strips_at, geometry_at, uv_at, texture_at, colour_at = offsets
    return (blocks["faces"], geometry_at, strips_at, uv_at, texture_at,
            colour_at, target.ptr_end, target.ptr_attachment)


def mesh_index(model: Model) -> dict[int, Mesh]:
    """Every mesh the model holds, by index, the object pool included.

    `model.meshes` is only the plain meshes -- the ones whose headers run from
    0x58. An object-pool mesh is reached through an object record instead, its
    header sits past the boundary, and the reader numbers it from where the
    plain ones stop. In a level those are the meshes that are drawn: nothing
    names `warp_room1`'s 42 plain meshes, while all 81 of its placements name
    pool meshes.
    """
    out = {mesh.index: mesh for mesh in model.meshes}
    for obj in model.objects:
        if obj.mesh is not None:
            out.setdefault(obj.mesh.index, obj.mesh)
    return out


def _table_bounds(data: bytes, model: Model) -> tuple[int, int, int]:
    """Where the colour table starts, where the UV table starts, and its length.

    The length is the **span** `T(0x24)..T(0x28)`, not the reader's entry count.
    Those two agree in only 168 of 373 models: the reader stops at the last entry
    any triangle names, and the table often runs further. Taking the count instead
    made the writer copy a short prefix and then lay the new mesh's UVs over the
    rest, so every *other* mesh's `face_uv_index` pointed into rewritten data --
    205 models would lose between 2 and 4748 bytes of live UVs that way, the warp
    rooms and hubs worst of all. It is what put the wrong texture on a rebuilt
    warp room, and rebuilding one mesh of `warp_room1` crashed the game.
    """
    colour_start = PTR_COLOUR_TABLE + struct.unpack_from("<i", data, PTR_COLOUR_TABLE)[0]
    uv_start = PTR_UV_TABLE + struct.unpack_from("<i", data, PTR_UV_TABLE)[0]
    uv_end = PTR_MODEL_POOL + struct.unpack_from("<i", data, PTR_MODEL_POOL)[0]
    return colour_start, uv_start, max(0, uv_end - uv_start)


def _mesh_blocks(mesh: Mesh) -> tuple[int, int, int]:
    """The strip list's range and the end of the vertex pool.

    The strip list is stored ahead of the bounds block, which is the one place a
    mesh's blocks are not in pointer order.
    """
    if not mesh.ptr_strips < mesh.ptr_bounds < mesh.ptr_uv_index:
        raise ValueError(
            f"mesh {mesh.index} is laid out unusually "
            f"(strips {mesh.ptr_strips:#x}, bounds {mesh.ptr_bounds:#x}, "
            f"uv {mesh.ptr_uv_index:#x}); refusing to guess"
        )
    return mesh.ptr_strips, mesh.ptr_bounds, mesh.ptr_uv_index


def build_strips(positions: np.ndarray, keys: np.ndarray | None = None,
                 identity: np.ndarray | None = None,
                 prefer: np.ndarray | None = None
                 ) -> list[list[tuple[int, tuple[int, int, int]]]]:
    """Chain triangles into strips along shared edges.

    One strip per triangle is legal by the format's rules, but no mesh in the
    game has more than 348 strips and this is not the place to find out what the
    loader does with more. Welding by position recovers the adjacency an
    exported triangle soup does not carry.

    Only positions are shared along a strip: UVs and colours are stored per
    triangle, so a seam in either costs nothing here. Each triangle is returned
    with the order its corners take inside the strip, since its UV and colour
    triples correspond to that order positionally.

    `keys` groups triangles that may share a strip -- the texture each samples,
    since one strip is drawn with one texture.

    `prefer` is the texture entry each triangle names, used only to break a
    tie. It must not be a *key*: one strip may sample several textures -- 12 %
    of the archive's 47,139 strips do -- so grouping by slot fragments them and
    takes this writer from 4.769 triangles a strip down to 3.486. But the run
    list that records the slot is run-length coded (§6.2), so a strip that
    wanders between textures costs bytes there instead: on `warp_room1` the
    rebuilt blocks came to 988 more than shipped, of which the pool explains
    only 336. Preferring a follower that keeps the same entry costs no strip
    length and shortens the run list.

    `identity` says which corners are the *same vertex*, when the caller knows.
    Two corners at one position need not be: 49 of the archive's 357 animated
    meshes hold a pair that sits together at rest and is driven apart by a clip.
    Chaining through such a pair makes the two triangles share one pool entry,
    and the second then animates with the first -- four units off, on 280 of one
    clip's 429 frames, with every static check passing. Position welding stays
    the fallback for a caller that has no vertices of its own.
    """
    if identity is not None:
        _, welded = np.unique(np.asarray(identity).reshape(-1),
                              return_inverse=True)
    else:
        quantised = np.round(positions.reshape(-1, 3)).astype(np.int64)
        _, welded = np.unique(quantised, axis=0, return_inverse=True)
    welded = welded.reshape(-1, 3)
    faces = welded.shape[0]
    if keys is None:
        keys = np.zeros(faces, dtype=np.int64)

    # Directed edges, not undirected ones. A strip presents its triangles
    # alternately reversed and the renderer flips them back, so the triangle
    # that may follow is the one sharing the edge the *other* way round. Match
    # on the undirected edge instead and half of them come out inside out.
    directed: dict[tuple[int, int, int], list[int]] = {}
    for face in range(faces):
        a, b, c = (int(v) for v in welded[face])
        for u, v in ((a, b), (b, c), (c, a)):
            directed.setdefault((int(keys[face]), u, v), []).append(face)

    def corner_order(face: int, first: int, second: int) -> tuple[int, int, int]:
        """The face's corners in the order the strip presents them."""
        ids = [int(v) for v in welded[face]]
        for a in range(3):
            b = (a + 1) % 3
            if ids[a] == first and ids[b] == second:
                return (a, b, (a + 2) % 3)
        for a in range(3):
            for b in range(3):
                if a != b and ids[a] == first and ids[b] == second:
                    return (a, b, next(i for i in range(3) if i not in (a, b)))
        raise ValueError(f"face {face} does not carry the edge it was chosen for")

    # How many faces each one can still chain to. Strips are grown from the
    # least-connected face and continued into the least-connected follower,
    # which is the standard tristrip heuristic and the one thing this writer was
    # missing: taking the lowest-numbered follower instead strands the corners
    # of a surface, and a stranded face is a strip of one costing three pool
    # entries for one triangle.
    neighbours: list[set[int]] = [set() for _ in range(faces)]
    for holders in directed.values():
        for face in holders:
            a, b, c = (int(v) for v in welded[face])
            for u, v in ((b, a), (c, b), (a, c)):
                for other in directed.get((int(keys[face]), u, v), ()):
                    if other != face:
                        neighbours[face].add(other)

    def free_degree(face: int, used: np.ndarray, taken: set[int]) -> int:
        return sum(1 for n in neighbours[face] if not used[n] and n not in taken)

    def walk(seed: int, rotation: tuple[int, int, int], used: np.ndarray
             ) -> list[tuple[int, tuple[int, int, int]]]:
        """The strip that grows from this seed presented this way round."""
        taken = {seed}
        strip = [(seed, rotation)]
        ids = [int(welded[seed][k]) for k in rotation]
        # The last two vertices the strip has emitted, and how far along it is.
        tail = (ids[1], ids[2])
        step = 1
        while True:
            # Triangle k is presented as it stands when k is even and reversed
            # when it is odd, so the edge the next face must carry flips with it.
            wanted = tail if step % 2 == 0 else (tail[1], tail[0])
            key = (int(keys[seed]), wanted[0], wanted[1])
            candidates = [f for f in directed.get(key, ())
                          if not used[f] and f not in taken]
            here = None if prefer is None else int(prefer[strip[-1][0]])
            following = (min(candidates,
                             key=lambda f: (free_degree(f, used, taken),
                                            0 if here is not None
                                            and int(prefer[f]) == here else 1,
                                            f))
                         if candidates else None)
            if following is None:
                return strip
            # Found by the directed edge, but ordered by the strip's own: the
            # strip always presents (s[k], s[k+1], s[k+2]), which on an odd step
            # is the edge the other way round from the one that found the face.
            order = corner_order(following, tail[0], tail[1])
            taken.add(following)
            strip.append((following, order))
            third = int(welded[following][order[2]])
            tail = (tail[1], third)
            step += 1

    # Which corner a seed leads with is the caller's to choose, and it decides
    # the whole strip: the chain continues along the edge the second and third
    # corners make, so a seed rotated the wrong way finds nothing and the strip
    # is one triangle long. Rotating a triangle's corners is a cyclic
    # permutation, so it cannot change the winding -- only which edge is tried
    # next -- and all three are therefore free to try.
    #
    # This is not a small effect on geometry that did not arrive already
    # chained. A Blender export of a 648-triangle model left 328 of its faces as
    # a strip of one, 495 strips against the 348 no shipped mesh exceeds, and
    # since a strip of n triangles costs n + 2 pool vertices that also inflated
    # every animation pose: 1638 pool vertices against the 970 the same
    # geometry needs at 161 strips.
    rotations = ((0, 1, 2), (1, 2, 0), (2, 0, 1))
    used = np.zeros(faces, dtype=bool)
    strips: list[list[tuple[int, tuple[int, int, int]]]] = []
    # Seeded least-connected first, for the same reason the follower is chosen
    # that way: a face with one neighbour left has to be taken now or it becomes
    # a strip of one, while a face in the middle of a sheet will still have a
    # chain to join later. Face-index order instead left this writer at 4.56
    # triangles a strip against the game's own 5.57.
    #
    # The degree has to be counted *now*, not once at the start: what makes a
    # face urgent is how many neighbours it has left, and every strip laid down
    # strands a few more. Sorting once by the initial degree got 4.94; asking
    # again each round gets further.
    remaining = np.array([len(n) for n in neighbours], dtype=np.int64)
    for _ in range(faces):
        free = np.flatnonzero(~used)
        if not len(free):
            break
        seed = int(free[np.argmin(remaining[free])])
        best = max((walk(seed, rotation, used) for rotation in rotations), key=len)
        for face, _ in best:
            used[face] = True
            for other in neighbours[face]:
                remaining[other] -= 1
        strips.append(best)
    return strips


@dataclass
class NewMesh:
    """A mesh built from nothing, rather than moved from another model.

    A triangle with no texture is drawn as a plain gouraud triangle, which is
    what the strip's untextured flag says. With one, `textures` names a slot in
    the destination's pack and `uvs` addresses it in texels, exactly as the
    game's own triangles do.
    """

    positions: np.ndarray  # (T, 3, 3) int16 model units, per corner
    colours: np.ndarray  # (T, 3, 3) uint8 RGB, per corner
    textures: np.ndarray | None = None  # (T,) pack texture index
    uvs: np.ndarray | None = None  # (T, 3, 2) uint8 texel coordinates
    # What an untextured mesh still has to name. See `_swatch_entry`.
    swatch: int = 0
    # (T, 3) naming the source vertex behind each corner, when the caller has
    # one. It decides where a strip may chain: see `build_strips`.
    corner_vertices: np.ndarray | None = None
    # (T,) of the colour index's top three bits per triangle: bit 15 turns the
    # GPU's semi-transparency on and bits 13-14 are the ABR mode (§6.3). 42,969
    # of the archive's 363,251 triangles carry them, and a rebuild that drops
    # them draws every one of those opaque. `None` means "recover them from the
    # mesh being replaced", which is what a front end with nowhere to put them
    # -- glTF has no such concept -- has to fall back on.
    blend: np.ndarray | None = None
    # (V, 3) per *source* vertex, indexed through `corner_vertices`: the
    # per-vertex normals at mesh +0x28 (§4.3), in GTE 1/4096 fixed point. 300
    # of the archive's 5989 meshes carry them.
    normals: np.ndarray | None = None
    # (T,) the owning strip's untextured flag (§5.1). Separate from `textures`
    # because the two are separate facts: bit 15 of a texture entry says the
    # face reads the swatch (§6.2, `0x80017FB8`), and the strip flag says which
    # primitive the face is drawn as -- and 33,097 of the archive's faces carry
    # the swatch bit inside a strip flagged textured. `None` derives it from
    # the entry, which is right for a mesh that has no strips of its own yet.
    untextured: np.ndarray | None = None


def _attachment_bytes(data: bytes, mesh: Mesh) -> bytes:
    """The mesh's own `+0x2C` block, whole, or empty when it has none.

    `[u16 flags][u16 count][16 bytes x count]`, and it lives inside the mesh's
    own span, so a rebuild that lays new blocks down drops it. §8.4: for a
    character this is the collision volume gameplay reads live, and zeroing it
    let the crate game's crates be walked through. A whole-model rebuild of
    `mainmenu/models` zeroed it on all nine of the meshes that had one.
    """
    at = mesh.ptr_attachment
    if not at or at + 4 > len(data):
        return b""
    count = struct.unpack_from("<H", data, at + 2)[0]
    end = at + 4 + 16 * count
    return bytes(data[at:end]) if end <= len(data) else b""


def _run_entry(mesh: "NewMesh", value: int) -> int:
    """One texture-run entry: a slot, a recovered swatch, or the mesh default."""
    if value >= 0:
        return value & TEXTURE_INDEX_MASK
    if value < -1:
        return -value          # a verbatim entry `_restore_swatches` recovered
    return mesh.swatch


def _restore_swatches(target: Mesh, mesh: "NewMesh") -> "NewMesh":
    """Give each untextured incoming face the swatch entry its triangle had.

    A swatch face carries `0x8000 | palette`, and a mesh may name several
    palettes -- `mainmenu/models`'s menu heads use 113 through 117 at once,
    which is how one mesh paints itself in several colour schemes from a single
    16x16 image (§6.2). The exporter folds the swatch texel into the vertex
    colour and the face comes back untextured, so without this every one of them
    collapses onto the mesh's first palette and the heads change colour.

    Faces are matched by their corner positions, which is exact wherever the
    triangle still exists; anything reshaped or new keeps the mesh-wide default.

    Only a face that arrived with no entry of its own is filled in. A front end
    that carries the entry per face -- the Blender add-on does, since it never
    folds the texel away -- has already said which palette the face means, and
    a positional guess must not overrule it: two faces can share their sorted
    corner positions, and the first one seen won the lookup for both. That cost
    ten meshes their palettes across the corpus, all of them 186-face menu heads
    of the kind that paint themselves in five colour schemes at once.
    """
    if mesh.textures is None or not len(target.face_texture):
        return mesh
    if not target.positions:
        return mesh
    scale = 1.0 / 0.00390625  # 1 / GTE_SCALE_SMALL, back to raw int16
    original = np.round(np.asarray(target.positions, dtype=np.float64) * scale)
    by_key: dict[tuple, int] = {}
    for face, triangle in enumerate(target.triangles()):
        if face >= len(target.face_texture) or max(triangle) >= len(original):
            continue
        key = tuple(sorted(tuple(int(v) for v in original[i]) for i in triangle))
        by_key.setdefault(key, int(target.face_texture[face]))

    textures = np.array(mesh.textures, dtype=np.int64, copy=True)
    for face in range(len(textures)):
        if textures[face] != -1:
            continue
        key = tuple(sorted(tuple(int(v) for v in corner)
                           for corner in mesh.positions[face]))
        was = by_key.get(key)
        if was is not None and was & TEXTURE_FLAG_SWATCH:
            textures[face] = -(was & 0xFFFF)   # negative marks "verbatim entry"
    return replace(mesh, textures=textures)


def _restore_blend(target: Mesh, mesh: "NewMesh") -> "NewMesh":
    """Give each incoming face the semi-transparency the triangle it replaces had.

    Bits 13-15 of the colour index are the GPU's blend mode (§6.3) and no
    interchange format has anywhere to put them, so a front end that cannot
    state them per face gets them back by matching corner positions -- exact
    wherever the triangle still exists, opaque for anything new or reshaped.

    Without this every rebuilt translucent surface came back solid: 1949 of the
    5827 meshes the corpus rebuilds carry a blend mode, and all 1949 lost it.
    """
    if mesh.blend is not None or not len(target.face_colour_index):
        return mesh
    if not target.positions:
        return mesh
    scale = 1.0 / 0.00390625  # 1 / GTE_SCALE_SMALL, back to raw int16
    original = np.round(np.asarray(target.positions, dtype=np.float64) * scale)
    by_key: dict[tuple, int] = {}
    for face, triangle in enumerate(target.triangles()):
        if face >= len(target.face_colour_index) or max(triangle) >= len(original):
            continue
        key = tuple(sorted(tuple(int(v) for v in original[i]) for i in triangle))
        by_key.setdefault(key, int(target.face_colour_index[face]) >> 13)

    blend = np.zeros(mesh.positions.shape[0], dtype=np.uint8)
    for face in range(blend.shape[0]):
        key = tuple(sorted(tuple(int(v) for v in corner)
                           for corner in mesh.positions[face]))
        blend[face] = by_key.get(key, 0)
    return replace(mesh, blend=blend)


def _swatch_entry(data: bytes, mesh: Mesh) -> int:
    """The `0x8000 | palette` a mesh's own texture list names, or 0.

    An untextured mesh is not one whose texture entries are zero. Of the 897
    shipped meshes whose every strip flag says untextured, **not one** writes a
    zero list: 227 name a swatch palette throughout and the other 670 mix swatch
    entries with texture ones. Zero means *texture slot 0*, not "no texture", so
    a writer that fills the list with zeros points every triangle at a real slot
    with no palette behind it -- which is how a rebuilt cutscene came to draw the
    previous screen's VRAM as its background.

    The run structure cannot be carried across a rebuild, because re-striping
    reorders the triangles it counts. The palette can: this takes the entry the
    mesh's own list uses most and gives every triangle that one. For
    `warp_room1`'s mesh 1 that is `0x8000 | 152`, which is what 662 of its 663
    triangles already carried.
    """
    if not mesh.ptr_texture or mesh.ptr_colour_index <= mesh.ptr_texture:
        return 0
    count = (mesh.ptr_colour_index - mesh.ptr_texture) // 2
    seen: Counter = Counter()
    for i in range(count):
        entry = struct.unpack_from("<H", data, mesh.ptr_texture + 2 * i)[0]
        if entry & TEXTURE_FLAG_SWATCH:
            seen[entry & (TEXTURE_FLAG_SWATCH | TEXTURE_INDEX_MASK)] += 1
    return seen.most_common(1)[0][0] if seen else 0


def _nearest_triple(colours: bytes, triple: bytes) -> int:
    """The index of the existing colour triple closest to `triple`.

    Pinned-table installs may not append, so a colour with no exact match takes
    the nearest neighbour by summed channel distance over the three corners.
    Distance-mapped colour is an approximation and callers should say so.
    """
    want = list(triple)
    best, best_at = None, 0
    for at in range(0, len(colours) - 2 * COLOUR_ENTRY_SIZE, COLOUR_ENTRY_SIZE):
        have = colours[at : at + 3 * COLOUR_ENTRY_SIZE]
        score = sum(abs(want[i] - have[i]) for i in (0, 1, 2, 4, 5, 6, 8, 9, 10))
        if best is None or score < best:
            best, best_at = score, at // COLOUR_ENTRY_SIZE
            if score == 0:
                break
    return best_at


def _pack_runs(values: list[int]) -> bytes:
    """One entry per triangle, back into `(run << 9) | value` runs.

    The mirror of the reader's expander: each entry covers `run + 1` triangles
    and the run field is six bits, so a run tops out at 64. Writing one entry per
    triangle is legal -- a run of 0 covers one -- but it is not what the game
    ships, and it inflates the block by up to 55x: `warp_room1`'s mesh 1 states
    662 triangles in 12 entries, 24 bytes, against 1324 uncompressed.
    """
    out = bytearray()
    index = 0
    while index < len(values):
        value = values[index]
        run = 1
        while (index + run < len(values) and values[index + run] == value
               and run < 64):
            run += 1
        out += struct.pack("<H", ((run - 1) << 9) | (value & 0x81FF))
        index += run
    return bytes(out)


def build_blocks(mesh: NewMesh) -> dict:
    """The blocks and table extensions a mesh needs, as raw bytes."""
    faces = mesh.positions.shape[0]
    if mesh.colours.shape != (faces, 3, 3):
        raise ValueError("one colour per corner is required")
    textured = mesh.textures is not None
    if textured and (mesh.uvs is None or mesh.uvs.shape != (faces, 3, 2)):
        raise ValueError("a textured mesh needs one UV pair per corner")

    # A strip is uniform in its untextured flag and in nothing else. The run
    # list advances per triangle and runs cross strip boundaries freely (§6.2),
    # so one strip may sample several textures -- measured over 47,139 shipped
    # strips, **12 % carry more than one texture entry** and some carry five.
    # Grouping by slot fragments the strips for nothing: it takes this writer
    # from 4.769 triangles a strip to 3.486.
    if mesh.untextured is not None:
        plain_face = np.asarray(mesh.untextured, dtype=bool)
    elif textured:
        plain_face = np.asarray(mesh.textures, dtype=np.int64) < 0
    else:
        plain_face = np.ones(faces, dtype=bool)
    runs = build_strips(mesh.positions, plain_face, mesh.corner_vertices,
                        None if mesh.textures is None
                        else np.asarray(mesh.textures, dtype=np.int64))
    # The order the strips are written in is free -- the pool, the colours and
    # the UVs all follow it -- and the texture run list does not restart at a
    # strip boundary (§6.2), so strips that sample the same slot merge their
    # runs when they are adjacent. On `warp_room1` the rebuilt run lists came
    # to 2188 bytes against the 1528 shipped, the largest single item in a
    # rebuild that was 988 bytes over; sorting costs nothing anywhere else.
    if mesh.textures is not None and runs:
        entry = np.asarray(mesh.textures, dtype=np.int64)
        runs.sort(key=lambda run: (bool(plain_face[run[0][0]]),
                                   int(entry[run[0][0]])))
    strips = bytearray()
    for run in runs:
        plain = bool(plain_face[run[0][0]])
        # Bit 3 states the winding of the strip's first triangle and must agree
        # with the vertex flags below -- 42,267 of the game's 42,267 strips do.
        # A seed is emitted in the incoming order, which is the outward one, so
        # its first triangle is not flipped and bit 3 stays clear. Setting it
        # unconditionally -- as this did -- inverted the backface test on every
        # seed whose parity that guess got wrong.
        flags = STRIP_FLAG_UNTEXTURED if plain else 0
        strips += struct.pack("<H", (len(run) << 8) | flags)
    # 0xFFFF, and it has to be: every one of the archive's 7960 meshes ends its
    # strip list with that word and not one ends it with 0xFF00, which this
    # wrote. A high byte is a triangle count, so 0xFF00 is not a terminator at
    # all -- it reads as a strip of 255 triangles with the flags clear, and the
    # walk carries on into whatever follows the block. The decoders size their
    # output by that walk, so the vertex count comes out far larger than the
    # keyframes hold and the tail of the side buffer at 0x80056AC8 keeps
    # whatever it held last -- vertices in world space, from the previous draw.
    # Standing still that stale tail equals the live values and nothing shows;
    # walking it lags, and the model trails threads across the level that grow
    # with the distance moved. Diagonal movement, being faster, made it worse.
    # `transplant_mesh` copies a shipped block verbatim and so never carried it,
    # which is why the transplanted models were clean and the built ones were not.
    strips += struct.pack("<H", 0xFFFF)

    # Lay the pool out strip by strip: a run of n triangles occupies n + 2
    # vertices, the first triangle contributing all three and each after it one.
    order: list[tuple[int, int]] = []
    plan: list[tuple[int, tuple[int, int, int]]] = []
    for run in runs:
        first, corners = run[0]
        order += [(first, k) for k in corners]
        plan.append((first, corners))
        for face, corners in run[1:]:
            order.append((face, corners[2]))
            plan.append((face, corners))
    pick = np.array(order)
    points = mesh.positions[pick[:, 0], pick[:, 1]].astype(np.int16)
    vertices = np.zeros((points.shape[0], 4), dtype="<i2")
    vertices[:, :3] = points
    # Bit 0 of the vertex record's fourth field is the winding of the triangle
    # that ends at it, and it alternates along the strip. Where it starts is not
    # free: bit 3 of the strip's own flag byte states the first triangle's
    # winding, and the two agree in 42,267 of 42,267 strips. The seeds here are
    # written in their own order, so the first triangle is not flipped, the
    # parity starts at zero and the strip flag leaves bit 3 clear. Start it at
    # one instead -- as this did -- and the mesh contradicts its own flag byte.
    #
    # This is not cosmetic. The game flips the sign of the NCLIP backface test
    # per this bit (§11.3), so an inverted parity culls the triangle instead of
    # drawing it. Rebuilding `mainmenu/models` against the shipped facing:
    # 6031/6031 triangles correct with the parity starting at zero, 2944/6031
    # with it starting at one -- and the backdrop, mesh 6, was inverted in all
    # 875 of its triangles and vanished from the menu.
    winding: list[int] = []
    for run in runs:
        winding += [0 if k < 2 else k % 2 for k in range(len(run) + 2)]
    vertices[:, 3] = np.array(winding[: points.shape[0]], dtype="<i2")
    # int64 throughout: squaring an int16 span overflows well before a model does.
    low = points.min(axis=0).astype(np.int64)
    high = points.max(axis=0).astype(np.int64)
    centre = (low + high) // 2
    radius = int(np.ceil(np.sqrt(float(((high - low) ** 2).sum())) / 2))
    bounds = struct.pack(
        "<10h",
        low[0], high[1], low[2], high[0], low[1], high[2],
        centre[0], centre[1], centre[2], min(radius, 32767),
    )

    # The per-triangle arrays follow strip order, and each triangle's corners
    # follow the order the strip gives them: the game writes vertex i, i+1, i+2
    # and UV 0, 1, 2 in step, so the correspondence is positional.
    colours = bytearray()
    for face, corners in plan:
        for k in corners:
            triple = mesh.colours[face, k].astype(np.uint8)
            colours += bytes((int(triple[0]), int(triple[1]), int(triple[2]), 0))

    if textured:
        texture = _pack_runs(
            # A face with no slot is not slot 511: it is a swatch face, and a
            # mesh that mixes real textures with swatch ones is the common case
            # -- 670 of the archive's 897 untextured-flagged meshes mix them.
            # Writing `-1 & 0x1FF` sent those triangles at a slot that does not
            # exist and they vanished from the menu, background included.
            # `_restore_swatches` marks a recovered entry by storing it negated,
            # so -1 alone still means "no match, use the mesh default".
            [_run_entry(mesh, int(mesh.textures[face])) for face, _ in plan]
        )
        uvs = bytes(
            np.stack([mesh.uvs[face, list(corners)] for face, corners in plan])
            .reshape(-1, 2)
            .astype(np.uint8)
            .tobytes()
        )
    else:
        # Not zeros: zero is texture slot 0. See `_swatch_entry`.
        texture = _pack_runs([mesh.swatch] * len(plan))
        uvs = b""

    # The normal array follows the positions in the same block, `V` more
    # 8-byte records (§4.3), and `mesh+0x28` points at the first of them. The
    # pool is laid out corner by corner, so each entry takes the normal of the
    # source vertex that corner came from -- the same map the poses use.
    normals = b""
    if mesh.normals is not None and mesh.corner_vertices is not None:
        source = np.asarray(mesh.corner_vertices)[pick[:, 0], pick[:, 1]]
        records = np.zeros((points.shape[0], 4), dtype="<i2")
        records[:, :3] = np.clip(
            np.round(np.asarray(mesh.normals, dtype=np.float64)[source]),
            -32768, 32767).astype("<i2")
        normals = records.tobytes()

    return {
        "strips": bytes(strips),
        "geometry": bounds + vertices.tobytes() + normals,
        # Where the normal array starts inside the geometry block, or 0.
        "normals": len(bounds) + vertices.nbytes if normals else 0,
        "texture": texture,
        "colours": bytes(colours),
        "uvs": uvs,
        "faces": len(plan),
        "textured": textured,
        # The blend mode per triangle, in the order the strips lay them down,
        # so a caller composing the colour index can OR it into the top bits.
        "blend": ([int(mesh.blend[face]) for face, _ in plan]
                  if mesh.blend is not None else [0] * len(plan)),
        # Which corner of which incoming triangle each pool entry was written
        # from, in pool order. A caller that knows where its corners came from
        # can follow this back to its own vertices exactly, which is the only
        # way to place an animation pose: matching by rest position instead
        # cannot tell apart two vertices that sit together and move apart, and
        # collapsing such a pair moved 38 animated triangles of one cutscene
        # clip while every static check passed.
        "order": pick,
    }


def strip_animation(data: bytes, clips) -> bytes:
    """Cut the animation blobs off, leaving room to grow the geometry.

    New geometry has to end up inside the span `model+0x08` describes, and the
    blobs sit immediately beyond it, so they have to be lifted out of the way
    and written again afterwards.
    """
    starts = [c.start for c in clips if c.start]
    return data[: min(starts)] if starts else data


def transplant_mesh(dest_data: bytes, dest_index: int, source: Transplant) -> bytes:
    """Replace mesh `dest_index` of `dest_data` with `source`'s mesh.

    The new blocks go on the end and the end-of-geometry field moves with them,
    so `dest_data` must not still carry its animation blobs -- they would end up
    inside the geometry span. Run `strip_animation` first and write the clips
    back afterwards.

    This path relocates the shared tables, which is fatal in the seven §8.6
    carriers -- repointing `0x20` crashes the room and repointing `0x24` alone
    scrambles every textured surface, four probes deep (§2.1). It refuses those
    files rather than producing a disc that fails on hardware; `install_mesh`
    with `pin_tables=True` is the way in.
    """
    _refuse_carrier(dest_data, "transplant_mesh")
    dest = read_model(dest_data)
    if not 0 <= dest_index < len(dest.meshes):
        raise ValueError(f"the model has no mesh {dest_index}")
    target = dest.meshes[dest_index]
    mesh = source.mesh
    faces = len(mesh.indexed_triangles())
    if faces != mesh.face_count_header:
        raise ValueError(
            f"source mesh {mesh.index} yields {faces} triangles but its header "
            f"says {mesh.face_count_header}"
        )

    dest_colour, dest_uv, dest_uv_len = _table_bounds(dest_data, dest)
    src_colour, src_uv, _ = _table_bounds(source.data, source.model)

    colours = bytearray(dest_data[dest_colour:dest_uv])
    uvs = bytearray(dest_data[dest_uv : dest_uv + dest_uv_len])
    colour_base = len(colours) // COLOUR_ENTRY_SIZE
    uv_base = len(uvs) // UV_ENTRY_SIZE
    if colour_base + faces * 3 > MAX_COLOURS:
        raise ValueError(
            f"{colour_base + faces * 3} colours would exceed the {MAX_COLOURS} a "
            "13-bit colour index can address"
        )

    uv_index = bytearray()
    texture = bytearray()
    colour_index = bytearray()

    for face in range(faces):
        old_colour = mesh.face_colour_index[face]
        at = src_colour + (old_colour & COLOUR_INDEX_MASK) * COLOUR_ENTRY_SIZE
        colours += source.data[at : at + COLOUR_ENTRY_SIZE * 3]
        new_colour = (colour_base + face * 3) | (old_colour & ~COLOUR_INDEX_MASK)
        colour_index += struct.pack("<H", new_colour & 0xFFFF)

        entry = mesh.face_texture[face]
        index = entry & TEXTURE_INDEX_MASK
        shift = (0, 0)
        scale = (1.0, 1.0)
        cell = None
        if entry & TEXTURE_FLAG_SWATCH:
            chosen = source.swatch_face.get(face)
            palette = chosen[0] if chosen else source.palette_map.get(index, index)
            cell = chosen[1] if chosen else None
            new_entry = TEXTURE_FLAG_SWATCH | palette
        else:
            new_entry = source.texture_map.get(index, index)
            shift = source.uv_shift.get(index, (0, 0))
            scale = source.uv_scale.get(index, (1.0, 1.0))
        # One entry per triangle, run length zero: the run only ever compresses.
        texture += struct.pack("<H", new_entry & 0xFFFF)

        at = src_uv + mesh.face_uv_index[face] * UV_ENTRY_SIZE
        for corner in range(3):
            if cell is not None:
                # A swatch face reads one texel, so all three corners name it.
                u, v = cell
            else:
                u = int(round(source.data[at + corner * 2] * scale[0])) + shift[0]
                v = int(round(source.data[at + corner * 2 + 1] * scale[1])) + shift[1]
            uvs += bytes((min(max(u, 0), 255), min(max(v, 0), 255)))
        uv_index += struct.pack("<H", uv_base + face * 3)

    strips_at, bounds_at, blocks_end = _mesh_blocks(mesh)
    strips = source.data[strips_at:bounds_at]
    geometry = source.data[bounds_at:blocks_end]

    out, tail, cut = _split_at_clip_table(dest_data)
    if len(out) % 4:
        out += b"\x00" * (4 - len(out) % 4)

    def append(block: bytes) -> int:
        at = len(out)
        out.extend(block)
        if len(out) % 4:
            out.extend(b"\x00" * (4 - len(out) % 4))
        return at

    # The colour table's length is the gap to the UV table, so the two have to
    # stay adjacent and in that order.
    new_colour_at = len(out)
    out.extend(colours)
    new_uv_at = len(out)
    out.extend(uvs)
    if len(out) % 4:
        out.extend(b"\x00" * (4 - len(out) % 4))

    strips_new = append(strips)
    geometry_new = append(geometry)
    uv_index_new = append(uv_index)
    texture_new = append(texture)
    colour_index_new = append(colour_index)
    attachment_new = append(source.attachment) if source.attachment else 0
    end_new = len(out)
    boundary = _carry_vector_pool(out, dest_data)
    boundary = _rejoin_tail(out, tail, cut, boundary)

    struct.pack_into("<i", out, PTR_COLOUR_TABLE, new_colour_at - PTR_COLOUR_TABLE)
    struct.pack_into("<i", out, PTR_UV_TABLE, new_uv_at - PTR_UV_TABLE)
    # Everything just appended has to fall inside the model's own span or it is
    # never read in. What this swallows on the way -- the clip descriptor table
    # and the other small tables that sit above the old boundary -- only makes
    # the span longer; every pointer into them still resolves where it did.
    struct.pack_into("<i", out, PTR_MODEL_END, boundary - PTR_MODEL_END)

    header = MESH_HEADER_START + MESH_HEADER_SIZE * dest_index
    if header != target.header_offset:
        raise ValueError(
            f"mesh {dest_index}'s header is at {target.header_offset:#x}, not the "
            f"{header:#x} the layout implies"
        )
    # No normal array: a transplant copies the source mesh's geometry block
    # verbatim, and where its normals sit inside that block is the source
    # model's business, not this one's.
    _finish_header(out, header, faces, mesh.format, mesh.unk13, mesh.unk14,
                   geometry_new, strips_new, uv_index_new, texture_new,
                   colour_index_new, end_new, attachment_new)
    return bytes(out)


def install_mesh(dest_data: bytes, dest_index: int, mesh: NewMesh,
                 pin_tables: bool = False) -> bytes:
    """Put a mesh built from scratch in place of `dest_index`.

    Same rules as `transplant_mesh`: run `strip_animation` first, write the clips
    back afterwards. The colour table is extended with the mesh's own colours;
    the UV table is not touched, since untextured triangles never read it.

    `pin_tables` is for the seven §8.6 carriers, where relocating the shared
    tables is fatal on hardware: repointing `0x20` crashed the room and
    repointing `0x24`/`0x28` scrambled every textured surface, across eleven
    probes (§2.1). In this mode the colour and UV tables and the three header
    fields naming them are left byte-for-byte where they are. Each incoming
    colour maps to the **nearest existing triple** instead of appending; a
    textured triangle must find its exact UV triple already in the table or the
    install refuses. The new geometry goes into space opened by pushing the
    §8.6 block outward, which the block-shift probe showed the game accepts.
    """
    dest = read_model(dest_data)
    if not 0 <= dest_index < len(dest.meshes):
        raise ValueError(f"the model has no mesh {dest_index}")
    target = dest.meshes[dest_index]
    if not mesh.swatch:
        mesh = replace(mesh, swatch=_swatch_entry(dest_data, target))
    mesh = _restore_swatches(target, mesh)
    blocks = build_blocks(mesh)
    faces = blocks["faces"]

    dest_colour, dest_uv, dest_uv_len = _table_bounds(dest_data, dest)
    colours = bytearray(dest_data[dest_colour:dest_uv])
    uvs = bytearray(dest_data[dest_uv : dest_uv + dest_uv_len]) + blocks["uvs"]
    uv_base = dest_uv_len // UV_ENTRY_SIZE

    # A colour index names three consecutive table entries, at any alignment,
    # and the game leans on that to share them -- 5,216 entries carry 5,216
    # triangles in the menu model. Reusing any existing consecutive triple,
    # including ones the previous install of this import appended, is what
    # keeps a many-mesh import inside the 13-bit index. On a round trip the
    # original entries are all found again, so the table barely grows.
    triples: dict[bytes, int] = {}
    for at in range(0, len(colours) - 2 * COLOUR_ENTRY_SIZE, COLOUR_ENTRY_SIZE):
        triples.setdefault(bytes(colours[at : at + 3 * COLOUR_ENTRY_SIZE]),
                           at // COLOUR_ENTRY_SIZE)
    indices: list[int] = []
    new_colours = blocks["colours"]
    for f in range(faces):
        triple = bytes(new_colours[f * 12 : f * 12 + 12])
        found = triples.get(triple)
        if found is None and pin_tables:
            found = _nearest_triple(colours, triple)
        elif found is None:
            found = _append_triple(colours, triple)
            start = max(0, (found - 2) * COLOUR_ENTRY_SIZE)
            for at in range(start, len(colours) - 2 * COLOUR_ENTRY_SIZE,
                            COLOUR_ENTRY_SIZE):
                triples.setdefault(
                    bytes(colours[at : at + 3 * COLOUR_ENTRY_SIZE]),
                    at // COLOUR_ENTRY_SIZE,
                )
        indices.append(found)
    if indices and max(indices) + 3 > MAX_COLOURS:
        raise ValueError(
            f"{max(indices) + 3} colours would exceed the {MAX_COLOURS} a "
            "13-bit colour index can address"
        )
    # Pinned rebuilds preserve the original triangles' own per-face records
    # whenever re-striping reproduced the original strip list as a prefix --
    # which it does for unchanged geometry (the self-transplant reproduces the
    # strip list byte for byte), and cannot once an edit moves vertices, since
    # the joint re-strip then orders everything anew. Fidelity only: it keeps
    # original colour indices (ABR bits included) and texture runs exact where
    # the geometry is exact. One trap it does NOT guard: warp_room1's shipped
    # run list ends in a dead 0x0000 entry covering a triangle that does not
    # exist -- an entry past the last face is terminator debris, not a slot-0
    # sampler, and reading meaning into it cost a wrong theory once.
    prefix = 0
    if pin_tables:
        original_strips = dest_data[target.ptr_strips:target.ptr_bounds]
        if blocks["strips"][: len(original_strips)] == original_strips:
            prefix = min(len(target.face_texture), faces)
            for f in range(prefix):
                # The full u16: bits 13-14 are the ABR blend mode and ride along.
                indices[f] = target.face_colour_index[f]

    colour_index = struct.pack(f"<{faces}H", *[i & 0xFFFF for i in indices])
    if pin_tables and blocks["textured"]:
        table = dest_data[dest_uv : dest_uv + dest_uv_len]
        uv_slots = []
        for f in range(faces):
            need = blocks["uvs"][f * 6 : f * 6 + 6]
            at = table.find(need)
            while at >= 0 and at % 2:
                at = table.find(need, at + 1)
            if at < 0:
                raise ValueError(
                    "pinned tables: a triangle's UV triple is not in the "
                    "shared table, and growing it is what crashes these rooms. "
                    "A swatch face counts -- its cell is a UV entry like any "
                    "other (§6.2) -- so this is not only about textured faces; "
                    "reuse triples the table already holds"
                )
            uv_slots.append(at // UV_ENTRY_SIZE)
        uv_index = struct.pack(f"<{faces}H", *[s & 0xFFFF for s in uv_slots])
    else:
        uv_index = struct.pack(
            f"<{faces}H",
            *[((uv_base + f * 3) if blocks["textured"] else 0) & 0xFFFF
              for f in range(faces)],
        )

    if prefix:
        from .mdl import decode_texture_runs  # noqa: PLC0415
        per_face = decode_texture_runs(blocks["texture"], 0, faces)
        per_face[:prefix] = target.face_texture[:prefix]
        blocks = dict(blocks, texture=_pack_runs([v & 0xFFFF for v in per_face]))
        uv_list = list(struct.unpack(f"<{faces}H", uv_index))
        uv_list[:prefix] = [v & 0xFFFF for v in target.face_uv_index[:prefix]]
        uv_index = struct.pack(f"<{faces}H", *uv_list)

    if pin_tables:
        # The graft layout, proven on hardware by the safeadd2 probe: the file
        # stays byte-identical through its old EOF except this mesh's header
        # and 0x08/0x50. The §8.6 block must not move -- its consumer finds it
        # by position, and both probes that shifted it lost the map previews --
        # so the new blocks go after it, covered by a grown, sector-aligned
        # resident size. 0x44 is not touched.
        out = bytearray(dest_data)
    else:
        out, tail, cut = _split_at_clip_table(dest_data)
    if len(out) % 4:
        out += b"\x00" * (4 - len(out) % 4)

    def append(block: bytes) -> int:
        at = len(out)
        out.extend(block)
        if len(out) % 4:
            out.extend(b"\x00" * (4 - len(out) % 4))
        return at

    if not pin_tables:
        new_colour_at = len(out)
        out.extend(colours)
        new_uv_at = len(out)
        out.extend(uvs)
        if len(out) % 4:
            out.extend(b"\x00" * (4 - len(out) % 4))

    strips_new = append(blocks["strips"])
    geometry_new = append(blocks["geometry"])
    uv_index_new = append(uv_index)
    texture_new = append(blocks["texture"])
    colour_index_new = append(colour_index)
    end_new = len(out)
    if pin_tables:
        out.extend(b"\x00" * (-len(out) % SECTOR))
        boundary = len(out)
        struct.pack_into("<i", out, RESIDENT_SIZE, boundary)
    else:
        boundary = _carry_vector_pool(out, dest_data)
        boundary = _rejoin_tail(out, tail, cut, boundary)
        struct.pack_into("<i", out, PTR_COLOUR_TABLE, new_colour_at - PTR_COLOUR_TABLE)
        struct.pack_into("<i", out, PTR_UV_TABLE, new_uv_at - PTR_UV_TABLE)
    struct.pack_into("<i", out, PTR_MODEL_END, boundary - PTR_MODEL_END)

    header = MESH_HEADER_START + MESH_HEADER_SIZE * dest_index
    if header != target.header_offset:
        raise ValueError(f"mesh {dest_index}'s header is not where the layout implies")
    _finish_header(out, header, faces, target.format, target.unk13, target.unk14,
                   geometry_new, strips_new, uv_index_new, texture_new,
                   colour_index_new, end_new, 0,
                   geometry_new + blocks["normals"] if blocks["normals"] else 0)
    return bytes(out)


def _split_at_clip_table(data: bytes) -> tuple[bytearray, bytes, int]:
    """Everything before the clip table, everything from it on, and where it was.

    Appending geometry at the end of the file put it past `T(0x44)` and past the
    resident end at `i32@0x50` -- both of which every one of the 400 shipped
    models keeps the geometry inside, 400/400 each. For a warp room that is
    worse than untidy: `T(0x44)` is where §8.6's block starts and runs to EOF, so
    appending drops the new geometry into the middle of it. Splitting here lets
    the caller put its geometry in front of that tail instead.
    """
    cut = PTR_CLIP_TABLE + struct.unpack_from("<i", data, PTR_CLIP_TABLE)[0]
    if not 0 < cut <= len(data):
        cut = len(data)
    return bytearray(data[:cut]), bytes(data[cut:]), cut


def _rejoin_tail(out: bytearray, tail: bytes, cut: int, boundary: int) -> int:
    """Put the tail back after the inserted geometry and move what named it.

    Only two fields name anything past the insertion point: `0x44`, which is
    self-relative and now has further to reach, and `0x50`, which is a plain
    length from the file's base. Every pointer *inside* the tail is self-relative
    within it and survives the shift untouched, and `write_clips` recomputes each
    descriptor's mesh pointer afterwards from the header's own offset.

    **The insertion is padded to a whole sector when `0x50` was sector-aligned.**
    Eight shipped models have `i32@0x50` a multiple of 0x800, and seven of them
    are the hub and warp rooms, where it is also exactly where §8.6's block
    begins. §1.1's byte-range reader can only start on a 0x800 boundary, so
    shifting that field by an arbitrary amount leaves it naming a place the
    reader cannot start from. Rebuilding one mesh of `warp_room1` -- changing no
    geometry at all -- crashed the game until this padding was added.
    """
    resident = struct.unpack_from("<i", out, RESIDENT_SIZE)[0]
    if resident % SECTOR == 0 and resident >= cut:
        out.extend(b"\x00" * (-(boundary - cut) % SECTOR))
        boundary = len(out)
    inserted = boundary - cut
    out.extend(tail)
    struct.pack_into("<i", out, PTR_CLIP_TABLE, boundary - PTR_CLIP_TABLE)
    if resident >= cut:
        struct.pack_into("<i", out, RESIDENT_SIZE, resident + inserted)
    return boundary


def _refuse_carrier(data: bytes, what: str) -> None:
    """Stop a table-relocating writer from touching an §8.6 carrier.

    The carriers announce themselves: `i32@0x38`, the chunk-descriptor count, is
    non-zero in exactly the seven of them and zero in the other 393. Their
    shared tables are pinned on hardware, so a writer that moves the tables can
    only produce a disc that crashes or draws garbage -- better to refuse here
    than to find out on the television.
    """
    if len(data) >= 0x3C and struct.unpack_from("<i", data, 0x38)[0] > 0:
        raise ValueError(
            f"{what} relocates the shared colour and UV tables, which is fatal "
            "in a §8.6 carrier (warp room or hub): repointing 0x20 crashes the "
            "room and repointing 0x24 scrambles every textured surface. Use "
            "install_mesh(pin_tables=True) instead."
        )


def install_meshes(dest_data: bytes, meshes: dict[int, "NewMesh"],
                   pin_tables: bool = False,
                   notes: list[str] | None = None,
                   plans: dict[int, "np.ndarray"] | None = None,
                   rebuild_tables: bool = False,
                   relayout_carrier: bool = False) -> bytes:
    """Install several meshes in one pass, sharing one copy of the tables.

    `install_mesh` appends a colour table, a UV table and the vector pool on
    every call, and the copies from earlier calls are then unreachable. Nine
    meshes through `mainmenu/models` left **983,128 of 1,396,026 bytes
    unreachable -- 70 % of the file** -- dominated by the 58,672-byte vector
    pool copied nine times. The game hung on the loading screen. This appends
    the shared tables once, the pool once, and each mesh's blocks once, so a
    multi-mesh replacement costs what its own geometry costs.

    Colours are deduplicated across *all* the meshes at once, which is what
    keeps a many-mesh import inside the 13-bit index. `notes` collects the
    remarks a caller should pass on -- how many faces the 13-bit ceiling forced
    onto a neighbouring colour, when it forced any. `plans`, when given, is
    filled with each mesh's pool layout: one `(triangle, corner)` pair per pool
    entry, which is how a caller puts an animation pose back in pool order
    without guessing at it from rest positions.
    """
    if not meshes:
        return dest_data
    dest = read_model(dest_data)
    targets = mesh_index(dest)
    for index in meshes:
        if index not in targets:
            raise ValueError(f"the model has no mesh {index}")

    prepared = {}
    plain = {mesh.index for mesh in dest.meshes}
    for index, mesh in sorted(meshes.items()):
        target = targets[index]
        if not mesh.swatch:
            mesh = replace(mesh, swatch=_swatch_entry(dest_data, target))
        mesh = _restore_swatches(target, mesh)
        mesh = _restore_blend(target, mesh)
        blocks = build_blocks(mesh)
        if plans is not None:
            plans[index] = blocks["order"]
        prepared[index] = (target, blocks)

    dest_colour, dest_uv, dest_uv_len = _table_bounds(dest_data, dest)
    if rebuild_tables:
        # Both tables built from nothing but the meshes being installed, so the
        # file carries no entry that is not read by one of them.
        #
        # **This renumbers, and renumbering is the one thing measured to be
        # unsafe.** An entry's index is its whole identity, and nothing here can
        # see who else holds one: over the archive 378 of 378 models have no
        # interior gap in either table, so an outside consumer's index lands
        # inside a fully covered range and looks exactly like a mesh's own. The
        # menu came back from hardware drawing flat bands of the wrong colour
        # when its table was rebuilt this way. Every mesh of the model has to be
        # staged, or an unstaged one keeps indices into a table that no longer
        # means what it meant.
        if pin_tables:
            raise ValueError(
                "the shared tables cannot be rebuilt and pinned at once: a "
                "§8.6 carrier holds them still because its door previews read "
                "them from outside the model, and renumbering is exactly what "
                "that cannot survive")
        absent = sorted(set(mesh_index(dest)) - set(meshes))
        if absent:
            raise ValueError(
                f"rebuilding the shared tables renumbers every entry, so every "
                f"mesh has to be installed alongside -- {len(absent)} are not: "
                f"{absent[:8]}{' ...' if len(absent) > 8 else ''}")
        colours, uvs = bytearray(), bytearray()
    else:
        colours = bytearray(dest_data[dest_colour:dest_uv])
        uvs = bytearray(dest_data[dest_uv : dest_uv + dest_uv_len])

    triples: dict[bytes, int] = {}
    for at in range(0, len(colours) - 2 * COLOUR_ENTRY_SIZE, COLOUR_ENTRY_SIZE):
        triples.setdefault(bytes(colours[at : at + 3 * COLOUR_ENTRY_SIZE]),
                           at // COLOUR_ENTRY_SIZE)
    # The UV table is shared and overlapping in exactly the same way, and the
    # shipped files lean on it just as hard: `mainmenu/models` carries 6035
    # triangles in 2350 pairs. Appending a fresh triple per textured face
    # instead took that to 17,242 -- seven times the original for the same
    # geometry -- which is bytes on the disc for nothing.
    uv_runs: dict[bytes, int] = {}
    for at in range(0, len(uvs) - 2 * UV_ENTRY_SIZE, UV_ENTRY_SIZE):
        uv_runs.setdefault(bytes(uvs[at : at + 3 * UV_ENTRY_SIZE]),
                           at // UV_ENTRY_SIZE)

    # The UV table gets the same batch packing the colour table gets, and for
    # the same reason: an index names three *consecutive* entries, so chaining
    # a triple onto one that already ends with its first two costs one entry
    # instead of three. Appending face by face and only then backfilling took
    # `warp_room1`'s rebuilt table 674 bytes past what shipped, where the
    # colour table -- which has always been packed this way -- lands within 120.
    if not pin_tables:
        batch: list[bytes] = []
        for index, (target, blocks) in prepared.items():
            if not blocks["textured"]:
                continue
            source = blocks["uvs"]
            batch += [bytes(source[f * 6:f * 6 + 6])
                      for f in range(blocks["faces"])]
        uv_runs.update(_pack_appends(batch, uvs, uv_runs, UV_ENTRY_SIZE))

    per_mesh = {}
    wanted: dict[int, list[bytes]] = {}
    for index, (target, blocks) in prepared.items():
        faces = blocks["faces"]
        uv_base = len(uvs) // UV_ENTRY_SIZE
        if pin_tables:
            # A pinned table cannot grow, so a textured face has to find its own
            # UV triple already in it. Writing `uv_base + f*3` instead aimed
            # every face just past the end of the table -- `warp_room1`'s mesh
            # 75 came back with all twenty of its indices at 2770..2827 against
            # a 2770-entry table, and the slab drew with whatever followed it
            # for UVs. On screen that reads as a texture that went missing.
            uv_index = []
            missing = 0
            source = blocks["uvs"]
            for f in range(faces):
                if not blocks["textured"]:
                    uv_index.append(0)
                    continue
                at = uv_runs.get(bytes(source[f * 6 : f * 6 + 6]))
                if at is None:
                    missing += 1
                    at = 0
                uv_index.append(at & 0xFFFF)
            if missing:
                raise ValueError(
                    f"mesh {index} keeps a pinned UV table, so each triangle "
                    f"needs its exact UV triple already in it -- a textured "
                    f"face's texels and a swatch face's cell alike (§6.2) -- "
                    f"and {missing} of {faces} are not there. Re-striping "
                    f"orders a triangle's corners anew, which is usually why. "
                    f"The mesh cannot be rebuilt in this model."
                )
        else:
            uv_index = []
            source = blocks["uvs"]
            for f in range(faces):
                if not blocks["textured"]:
                    uv_index.append(0)
                    continue
                run = bytes(source[f * 6 : f * 6 + 6])
                at = uv_runs.get(run)
                if at is None:
                    at = _append_run(uvs, run, UV_ENTRY_SIZE)
                    start = max(0, (at - 2) * UV_ENTRY_SIZE)
                    for k in range(start, len(uvs) - 2 * UV_ENTRY_SIZE,
                                   UV_ENTRY_SIZE):
                        uv_runs.setdefault(
                            bytes(uvs[k : k + 3 * UV_ENTRY_SIZE]),
                            k // UV_ENTRY_SIZE)
                uv_index.append(at & 0xFFFF)

        wanted[index] = [bytes(blocks["colours"][f * 12 : f * 12 + 12])
                         for f in range(faces)]
        per_mesh[index] = (None, struct.pack(f"<{faces}H", *uv_index))

    # Every mesh's colours at once, so the chain can share across meshes as well
    # as inside one. Doing it per face in face order left `mainmenu/models`
    # wanting 8370 entries against the 8192 a 13-bit index can address.
    # §8.6's door previews are meshes of this model reading these same tables,
    # so their triples join the same packing pass. Left to chain on afterwards
    # they cost `warp_room1` 4252 bytes more than it has below its block.
    previews = ([] if pin_tables else
                preview_runs(dest_data, dest_data[dest_colour:dest_uv],
                             dest_data[dest_uv:dest_uv + dest_uv_len]))
    if not pin_tables:
        packed = _pack_appends([t for row in wanted.values() for t in row]
                               + [triple for triple, _ in previews],
                               colours, triples, COLOUR_ENTRY_SIZE)
        triples.update(packed)
        for _, run in previews:
            if run not in uv_runs:
                at = _append_run(uvs, run, UV_ENTRY_SIZE)
                uv_runs[run] = at
                start = max(0, (at - 2) * UV_ENTRY_SIZE)
                for k in range(start, len(uvs) - 2 * UV_ENTRY_SIZE, UV_ENTRY_SIZE):
                    uv_runs.setdefault(bytes(uvs[k:k + 3 * UV_ENTRY_SIZE]),
                                       k // UV_ENTRY_SIZE)

    for index, row in wanted.items():
        indices = []
        for triple in row:
            found = triples.get(triple)
            if found is None:
                found = (_nearest_triple(colours, triple) if pin_tables
                         else _append_run(colours, triple, COLOUR_ENTRY_SIZE))
                if not pin_tables:
                    triples[triple] = found
            indices.append(found)
        if indices and max(indices) + 3 > MAX_COLOURS:
            raise ValueError(
                f"{max(indices) + 3} colours would exceed the {MAX_COLOURS} a "
                "13-bit colour index can address")
        # The top three bits ride along: bit 15 turns the GPU's
        # semi-transparency on and bits 13-14 pick the ABR mode (§6.3), and
        # they are per triangle, not per colour.
        blend = prepared[index][1]["blend"]
        per_mesh[index] = (
            struct.pack(f"<{len(indices)}H",
                        *[(value | (blend[f] << 13)) & 0xFFFF
                          for f, value in enumerate(indices)]),
            per_mesh[index][1])

    if notes is not None and not pin_tables:
        grew = len(colours) // COLOUR_ENTRY_SIZE
        notes.append(
            f"colour table {(dest_uv - dest_colour) // COLOUR_ENTRY_SIZE} -> "
            f"{grew} entries of {MAX_COLOURS}; "
            + ("built from the staged meshes alone, so **every entry is "
               "renumbered** and anything outside this model holding an index "
               "now names a different colour"
               if rebuild_tables else
               "the shipped entries are unchanged and the new ones chained "
               "onto the end"))

    if not pin_tables:
        # §8.6's door previews are meshes of this model and read these same
        # tables, so they are renumbered onto them before anything is written.
        preview = _remap_previews(
            dest_data, colours, uvs, triples, uv_runs,
            dest_data[dest_colour:dest_uv],
            dest_data[dest_uv:dest_uv + dest_uv_len])
        if preview is not None and notes is not None:
            notes.append(
                f"{len(preview_meshes(dest_data))} §8.6 door-preview meshes "
                f"renumbered onto the rebuilt tables; they index them like any "
                f"other mesh and are read when a door is opened")
        relaid = _install_relaid(dest_data, dest, prepared, per_mesh,
                                 bytes(colours), bytes(uvs), notes,
                                 relayout_carrier, preview)
        if relaid is not None:
            return relaid

    layout = None if pin_tables else _geometry_region(dest_data, dest)
    if layout is not None:
        return _rewrite_region(dest_data, dest, layout, prepared, per_mesh,
                               colours, uvs)

    if pin_tables:
        out = bytearray(dest_data)
    else:
        out, tail, cut = _split_at_clip_table(dest_data)
    if len(out) % 4:
        out += b"\x00" * (4 - len(out) % 4)

    def append(block: bytes) -> int:
        at = len(out)
        out.extend(block)
        if len(out) % 4:
            out.extend(b"\x00" * (4 - len(out) % 4))
        return at

    if not pin_tables:
        new_colour_at = len(out)
        out.extend(colours)
        new_uv_at = len(out)
        out.extend(uvs)
        if len(out) % 4:
            out.extend(b"\x00" * (4 - len(out) % 4))

    placed = {}
    for index, (target, blocks) in prepared.items():
        colour_index, uv_index = per_mesh[index]
        if index not in plain:
            # An object-pool mesh is written back where it stands: the pool is
            # one packed run and its blocks may not leave it. `ptr_end` keeps
            # its old value however small the rebuild turns out, so the next
            # mesh's header stays four bytes past it and the run is undisturbed.
            placed[index] = _write_in_place(out, dest_data, target, blocks,
                                            colour_index, uv_index, index)
            continue
        strips_new = append(blocks["strips"])
        geometry_new = append(blocks["geometry"])
        uv_index_new = append(uv_index)
        texture_new = append(blocks["texture"])
        colour_index_new = append(colour_index)
        end_new = len(out)
        keep = _attachment_bytes(dest_data, target)
        placed[index] = (blocks["faces"], geometry_new, strips_new,
                         uv_index_new, texture_new, colour_index_new, end_new,
                         append(keep) if keep else 0)

    if pin_tables:
        out.extend(b"\x00" * (-len(out) % SECTOR))
        boundary = len(out)
        struct.pack_into("<i", out, RESIDENT_SIZE, boundary)
    else:
        boundary = _carry_vector_pool(out, dest_data)
        boundary = _rejoin_tail(out, tail, cut, boundary)
        struct.pack_into("<i", out, PTR_COLOUR_TABLE, new_colour_at - PTR_COLOUR_TABLE)
        struct.pack_into("<i", out, PTR_UV_TABLE, new_uv_at - PTR_UV_TABLE)
    struct.pack_into("<i", out, PTR_MODEL_END, boundary - PTR_MODEL_END)

    for index, (target, blocks) in prepared.items():
        faces, geo, strips, uvi, tex, ci, end, attach = placed[index]
        # The mesh's own header offset, not the position the plain-mesh layout
        # implies: an object-pool mesh's header sits past the boundary with the
        # rest of the pool (`warp_room1`'s first is at 0x111F8), and in a level
        # those are the only meshes the game draws -- nothing names the 42 in
        # `model.meshes`, so geometry written into them is never asked for.
        _finish_header(out, target.header_offset, faces, target.format,
                       target.unk13, target.unk14, geo, strips, uvi, tex, ci,
                       end, attach,
                       geo + blocks["normals"] if blocks["normals"] else 0)
    return bytes(out)


def preview_meshes(data: bytes) -> list[int]:
    """Header offsets of the §8.6 door-preview sub-blocks, if this is a carrier.

    They are meshes of this model -- the signature §8.6 records is a mesh
    header field for field: 0x34 bytes, `i32@+0x00 == 0`, `i32@+0x04 == 0`,
    `u16@+0x0A == 4`, `i32@+0x14 == 32` (the strip list immediately after the
    header), four ascending block offsets. They sit on 2048-byte boundaries
    inside the block, and they index the model's own colour and UV tables to
    the last triple: `warp_room1`'s five are 1156 triangles reaching colour
    4513 of 4516 and UV 2767 of 2770.
    """
    if not struct.unpack_from("<i", data, 0x38)[0]:
        return []
    at = _resolve(data, PTR_CLIP_TABLE)
    found = []
    while at + MESH_HEADER_SIZE <= len(data):
        zero_a, zero_b = struct.unpack_from("<2i", data, at)
        fmt = struct.unpack_from("<H", data, at + FIELD_FORMAT)[0]
        strips = struct.unpack_from("<i", data, at + FIELD_STRIPS)[0]
        if zero_a == 0 and zero_b == 0 and fmt == 4 and strips == 32:
            found.append(at)
        at += SECTOR
    return found


def preview_runs(data: bytes, old_colours: bytes,
                 old_uvs: bytes) -> list[tuple[bytes, bytes]]:
    """The colour triple and UV run every §8.6 preview triangle names.

    Gathered before the table is packed so these deduplicate against the
    model's own faces rather than chaining onto the end of them: appended
    afterwards, `warp_room1` wanted 4252 bytes more than it has below the
    block, and the door images are pictures of the very rooms whose colours
    are already there.
    """
    out: list[tuple[bytes, bytes]] = []
    for head in preview_meshes(data):
        faces = struct.unpack_from("<h", data, head + FIELD_FACE_COUNT)[0]
        uv_at = head + FIELD_UV_INDEX + struct.unpack_from(
            "<i", data, head + FIELD_UV_INDEX)[0]
        colour_at = head + FIELD_COLOUR_INDEX + struct.unpack_from(
            "<i", data, head + FIELD_COLOUR_INDEX)[0]
        if not 0 < faces < 4096:
            return []
        for face in range(faces):
            index = (struct.unpack_from("<H", data, colour_at + 2 * face)[0]
                     & COLOUR_INDEX_MASK)
            triple = old_colours[index * COLOUR_ENTRY_SIZE:
                                 (index + 3) * COLOUR_ENTRY_SIZE]
            index = struct.unpack_from("<H", data, uv_at + 2 * face)[0]
            run = old_uvs[index * UV_ENTRY_SIZE:(index + 3) * UV_ENTRY_SIZE]
            if len(triple) < 3 * COLOUR_ENTRY_SIZE or len(run) < 3 * UV_ENTRY_SIZE:
                return []
            out.append((triple, run))
    return out


def _remap_previews(dest_data: bytes, colours: bytearray, uvs: bytearray,
                    triples: dict, uv_runs: dict,
                    old_colours: bytes, old_uvs: bytes) -> tuple[int, bytes] | None:
    """Renumber the §8.6 preview meshes onto the table being written.

    They read the shared tables like any other mesh, so a table that renumbers
    under them repaints every triangle they draw -- and because they are
    streamed in when a door is opened, that is exactly when it shows: the room
    is perfect until the preview, and then every textured surface in the level
    is garbage. Two discs said so, and a third ruled memory out.
    """
    heads = preview_meshes(dest_data)
    if not heads:
        return None
    block = _resolve(dest_data, PTR_CLIP_TABLE)
    out = bytearray(dest_data[block:])
    for head in heads:
        faces = struct.unpack_from("<h", dest_data, head + FIELD_FACE_COUNT)[0]
        uv_at = head + FIELD_UV_INDEX + struct.unpack_from(
            "<i", dest_data, head + FIELD_UV_INDEX)[0]
        colour_at = head + FIELD_COLOUR_INDEX + struct.unpack_from(
            "<i", dest_data, head + FIELD_COLOUR_INDEX)[0]
        if not 0 < faces < 4096:
            return None
        for face in range(faces):
            at = colour_at + 2 * face
            value = struct.unpack_from("<H", dest_data, at)[0]
            index, blend = value & COLOUR_INDEX_MASK, value & ~COLOUR_INDEX_MASK
            triple = old_colours[index * COLOUR_ENTRY_SIZE:
                                 (index + 3) * COLOUR_ENTRY_SIZE]
            if len(triple) < 3 * COLOUR_ENTRY_SIZE:
                return None
            found = triples.get(triple)
            if found is None:
                found = _append_run(colours, triple, COLOUR_ENTRY_SIZE)
                triples[triple] = found
            if found + 3 > MAX_COLOURS:
                return None
            struct.pack_into("<H", out, at - block, (found | blend) & 0xFFFF)

            at = uv_at + 2 * face
            index = struct.unpack_from("<H", dest_data, at)[0]
            run = old_uvs[index * UV_ENTRY_SIZE:(index + 3) * UV_ENTRY_SIZE]
            if len(run) < 3 * UV_ENTRY_SIZE:
                return None
            found = uv_runs.get(run)
            if found is None:
                found = _append_run(uvs, run, UV_ENTRY_SIZE)
                uv_runs[run] = found
            struct.pack_into("<H", out, at - block, found & 0xFFFF)
    return block, bytes(out)


def _aim_object(out: bytearray, dest: Model, dest_data: bytes, target: Mesh,
                header: int, carried) -> bool:
    """Point the object record that names this pool mesh at its new header.

    `relayout` moves every record already, but it can only resolve a *region's
    start* inside a region it was handed new bytes for -- so all five of
    `balls_crash/crystalarena`'s meshes that share one region collapsed onto
    the first, and four of them came back drawing the wrong texture slot.
    Here the exact header is known, so it is written directly.
    """
    table = MOW.table_start_of_objects(dest_data)
    at = carried(table)
    if at is None:
        return False
    for index, obj in enumerate(dest.objects):
        if obj.reference == 0 and obj.offset == target.header_offset:
            struct.pack_into("<i", out, at + OBJECT_STRIDE * index + 4, header)
            return True
    return True   # nothing names it: a pool mesh no object record reaches


def _install_relaid(dest_data: bytes, dest: Model, prepared: dict,
                    per_mesh: dict, colours: bytes, uvs: bytes,
                    notes: list[str] | None,
                    relayout_carrier: bool = False,
                    preview: tuple[int, bytes] | None = None) -> bytes | None:
    """Install by laying the model out again, rather than by appending to it.

    Everything above this function decides *what* each mesh and each table
    should contain. This decides where it all goes, and it decides it for the
    whole file: `modelwrite.relayout` re-emits every region in file order and
    recomputes every pointer from where the region lands, so a table that grew
    is simply longer where it already stood and the regions after it move on.

    That is what ends the stranding. Appending a fresh table and repointing the
    header leaves the shipped one in the file, reachable by nothing:
    `boss_oxide/arena` grew 233,202 -> 265,966 bytes for one 116-triangle mesh
    and 30,528 of those bytes were the tables left behind. It is also what ends
    the pinning, because pinning existed only to avoid paying that.

    `out/crashbash-oxide-tall-pool.bin` runs, so this has been to a console: an
    arena laid out again here, carrying a pool mesh whose rebuild wants more
    bytes than the mesh owns -- the case the fit constraint used to refuse.

    Returns `None` when the layout writer cannot own this file -- a §8.6 carrier
    whose §8.6 block must keep its offset, or a mesh whose region merged with a
    neighbour's and so has no identity to replace -- and the caller falls back
    to the path that was there before.
    """
    if (struct.unpack_from("<i", dest_data, 0x38)[0]
            and preview is None and not relayout_carrier):
        # A §8.6 carrier. Its door-preview block *can* move now -- `relayout`
        # lands it on the sector grid and moves §8.1's descriptor rows with it,
        # 14 of 14 measured -- but what that costs on hardware is unproven, and
        # §2.1 says repointing `T(0x24)` alone scrambles every textured surface
        # in these seven rooms. So it is the caller's call, not a default.
        return None

    dest_colour, dest_uv, _ = MOW.table_bounds(dest_data)
    replace_map: dict[int, bytes] = {dest_colour: colours, dest_uv: uvs}
    if preview is not None:
        replace_map[preview[0]] = preview[1]
    pooled = {id(o.mesh) for o in dest.objects if o.mesh is not None}
    regions = MOW.plan(dest_data, dest)
    inner: dict[int, tuple[int, list[int], int, int]] = {}

    def region_of(offset: int) -> int | None:
        for start, end in regions:
            if start <= offset < end:
                return start
        return None

    # Grouped by the region each mesh lives in, because two pool meshes can
    # share one: 96 of the archive's 1971 have a span that overlaps a
    # neighbour's, and `plan` merges those. Writing them one at a time claimed
    # the same region twice and had to refuse; laid out together they simply
    # follow each other inside it, which is what the shipped file does anyway.
    def anchor(mesh: Mesh) -> int:
        low = min(mesh.ptr_bounds, mesh.ptr_strips, mesh.ptr_uv_index,
                  mesh.ptr_texture, mesh.ptr_colour_index)
        return min(low, mesh.header_offset) if id(mesh) in pooled else low

    # Every mesh that lives in a region, staged or not. A region is rewritten
    # whole, so a mesh sharing one with a rebuild has to be rebuilt alongside
    # or its blocks are gone: `balls_crash/crystalarena` packs five pool meshes
    # onto *one* set of blocks, five headers over the same strips, bounds, UVs
    # and colours with only `ptr_texture` four bytes apart each time.
    tenants: dict[int, list[int]] = {}
    for mesh in list(dest.meshes) + [o.mesh for o in dest.objects
                                     if o.mesh is not None]:
        region = region_of(anchor(mesh))
        if region is not None:
            tenants.setdefault(region, []).append(mesh.index)

    groups: dict[int, list[int]] = {}
    for index, (target, _) in prepared.items():
        region = region_of(anchor(target))
        if region is None:
            return None
        groups.setdefault(region, []).append(index)
    for region, members in groups.items():
        if set(tenants.get(region, ())) - set(members):
            return None

    for region, members in groups.items():
        blob = bytearray()
        members.sort(key=lambda i: prepared[i][0].header_offset)
        for index in members:
            target, blocks = prepared[index]
            low = min(target.ptr_bounds, target.ptr_strips, target.ptr_uv_index,
                      target.ptr_texture, target.ptr_colour_index)
            start = anchor(target)
            colour_index, uv_index = per_mesh[index]
            # A pool mesh's own header sits in front of its blocks and is part
            # of the region, so it is carried across and patched afterwards.
            # The block order is the one the shipped meshes use and the one
            # `_write_in_place` already relies on.
            head = len(blob)
            if id(target) in pooled:
                # Its own header only. Copying `start:low` instead swept up
                # every header sharing the region -- five of them, in
                # `balls_crash/crystalarena` -- and duplicated them all.
                blob.extend(dest_data[target.header_offset:
                                      target.header_offset + MESH_HEADER_SIZE])
            offsets = []
            for block in (blocks["strips"], blocks["geometry"], uv_index,
                          blocks["texture"], colour_index):
                if len(blob) % 4:
                    blob.extend(b"\x00" * (4 - len(blob) % 4))
                offsets.append(len(blob))
                blob.extend(block)
            end = len(blob)
            # §8.4's attachment block, when it lies inside the span this
            # rebuild is overwriting: it is the mesh's collision volume and
            # nothing in a payload carries it, so it is copied along here
            # rather than lost. It goes after `ptr_end`, where the shipped
            # meshes keep it.
            attach = 0
            if target.ptr_attachment and region_of(target.ptr_attachment) == region:
                keep = _attachment_bytes(dest_data, target)
                if not keep:
                    return None
                if len(blob) % 4:
                    blob.extend(b"\x00" * (4 - len(blob) % 4))
                attach = len(blob)
                blob.extend(keep)
            inner[index] = (region, offsets, end, attach, head)
            if len(blob) % 4:
                blob.extend(b"\x00" * (4 - len(blob) % 4))
        if region in replace_map:
            return None
        replace_map[region] = bytes(blob)

    landed: dict[int, int] = {}
    out = bytearray(MOW.relayout(dest_data, dest, replace_map, landed,
                                 move_block=preview is not None))
    if any(start not in landed for start, _, _, _, _ in inner.values()):
        return None

    def carried(offset: int) -> int | None:
        """Where a byte this rebuild does not own ended up.

        `landed` answers for a region's *start*, and an attachment block's
        start is often not one -- it sits in the padding between two pool
        meshes, which `plan` carries as a region beginning at the previous
        mesh's end. Reading `landed` directly and defaulting to zero wrote a
        null `+0x2C` for exactly the meshes being rebuilt: §8.4 is the
        collision volume, and on hardware the objects that lost it spun on the
        spot in an arena that was otherwise correct. A block inside a replaced
        region has already been copied into that mesh's own blob.
        """
        start = region_of(offset)
        if start is None or start in replace_map:
            return None
        return landed[start] + (offset - start)

    for index, (target, blocks) in prepared.items():
        start, offsets, length, attach, head = inner[index]
        at = landed[start]
        strips, geometry, uv_index, texture, colour_index = (
            at + off for off in offsets)
        header = (at + head if id(target) in pooled
                  else landed[MESH_HEADER_START]
                  + (target.header_offset - MESH_HEADER_START))
        attachment = 0
        if target.ptr_attachment:
            attachment = (at + attach) if attach else (carried(target.ptr_attachment) or 0)
            if not attachment:
                return None  # the block is gone; do not write a null +0x2C
        _finish_header(out, header, blocks["faces"], target.format,
                       target.unk13, target.unk14, geometry, strips, uv_index,
                       texture, colour_index, at + length, attachment,
                       geometry + blocks["normals"] if blocks["normals"] else 0)
        if id(target) in pooled and not _aim_object(out, dest, dest_data,
                                                   target, header, carried):
            return None

    if notes is not None:
        notes.append(f"model relaid from its own regions: {len(dest_data)} -> "
                     f"{len(out)} bytes, nothing stranded")
    return bytes(out)


# Header fields whose targets sit past the geometry boundary and so move with
# it. `0x50` is a plain length from the base; the rest are self-relative.
_SHIFTS_WITH_BOUNDARY = (0x1C, 0x2C, 0x3C, PTR_CLIP_TABLE, 0x4C)


def _resolve(data: bytes, field: int) -> int:
    """`T(field)` — the self-relative resolve every MDL header pointer uses."""
    return field + struct.unpack_from("<i", data, field)[0]


def _append_triple(colours: bytearray, triple: bytes) -> int:
    """Put a face's three colours at the end, overlapping what is already there.

    A colour index names three *consecutive* entries, so consecutive faces can
    share two of them, and the shipped files lean on it all the way: 5,216
    entries carry 5,216 triangles in `mainmenu/models`. Appending three entries
    per unmatched face instead of overlapping is what pushed a whole-model
    rebuild of that file to 8,396 entries and over the 8,192 a 13-bit index can
    address -- the import failed outright. Re-striping is why the faces are
    unmatched at all: the same triangle comes back with its corners rotated, so
    its triple is no longer the one the table holds.
    """
    return _append_run(colours, triple, COLOUR_ENTRY_SIZE)


def _append_run(table: bytearray, run: bytes, entry: int) -> int:
    """Append three entries at the end, sharing whatever the tail already has.

    Both shared tables are addressed the same way -- an index names three
    consecutive entries -- so both dedupe the same way, on the colour table's
    4-byte entry and the UV table's 2-byte pair alike.
    """
    for shared in (2, 1):
        if len(table) >= shared * entry and \
                run[: shared * entry] == bytes(table[-shared * entry:]):
            at = len(table) // entry - shared
            table += run[shared * entry:]
            return at
    at = len(table) // entry
    table += run
    return at


def _pack_appends(triples: list[bytes], table: bytearray,
                  known: dict[bytes, int], entry: int) -> dict[bytes, int]:
    """Append distinct triples in an order that shares as many entries as it can.

    An index names three consecutive entries, so two triples that overlap by two
    can be stored in four entries instead of six. The shipped files lean on that
    hard -- `mainmenu/models` carries 6035 triangles in 5216 colour entries --
    and appending in face order takes whatever overlap the faces happen to
    offer. Chaining greedily on the shared pair instead earns the rest.

    The shipped entries are never reordered or dropped. They cannot be: the
    meshes reach all 5216 of that model's, which means an index held anywhere
    else lands inside the same range, and rewriting the table under it drew the
    menu in flat bands of the wrong colour. Covered by the meshes is not the
    same as reached only by the meshes.
    """
    placed: dict[bytes, int] = {}
    # Face order first: neighbouring triangles share colours, so it already
    # overlaps well, and reordering wholesale threw that away -- a greedy chain
    # seeded arbitrarily wanted 9743 entries where face order wanted 8370.
    pending = [t for t in dict.fromkeys(triples) if t not in known]
    if not pending:
        return placed
    by_head: dict[bytes, list[bytes]] = {}
    for triple in pending:
        by_head.setdefault(triple[: 2 * entry], []).append(triple)

    def settled(triple: bytes) -> bool:
        return triple in placed or triple in known

    for triple in pending:
        if settled(triple):
            continue
        placed[triple] = _append_run(table, triple, entry)
        # Then follow the chain: anything starting with the two entries the
        # table now ends on costs one more entry instead of three.
        while True:
            tail = bytes(table[-2 * entry:])
            nxt = next((c for c in by_head.get(tail, ()) if not settled(c)), None)
            if nxt is None:
                break
            placed[nxt] = _append_run(table, nxt, entry)
    return placed


def _geometry_region(data: bytes, model) -> tuple[int, int, int, int] | None:
    """`(start, colour, uv, end)` when the region is laid out as expected.

    Appending rebuilt blocks and abandoning the originals leaves the old blocks,
    tables and vector pool unreachable -- 121,308 bytes of a 528,604-byte
    `mainmenu/models` import, 23 % of the file. They can be reclaimed only if
    nothing else lives in the span, and in `models.mdl` nothing does: the 22
    mesh blocks run contiguously from 0x4D0 with 4-byte gaps, the colour table
    starts at the byte after the last one, then the UV table, then the pool
    ending exactly at `T(0x08)`, and the object table at `T(0x1C)` sits *past*
    the boundary. This returns the span only when that shape holds, so a model
    laid out differently falls back to appending rather than being rearranged
    on an assumption.
    """
    if not model.meshes or model.objects:
        # An object mesh's blocks live in the same span and are not in
        # `model.meshes`, so rewriting the region would drop them: 41 models
        # came back short -- `polar_manic/arena` with 242 of its 897 triangles
        # -- before this guard. Those models take the appending path.
        return None
    colour, uv, pool, end = (_resolve(data,PTR_COLOUR_TABLE),
                             _resolve(data,PTR_UV_TABLE),
                             _resolve(data,0x28), _resolve(data,PTR_MODEL_END))
    spans = []
    for mesh in model.meshes:
        low = min(mesh.ptr_bounds, mesh.ptr_strips, mesh.ptr_uv_index,
                  mesh.ptr_texture, mesh.ptr_colour_index)
        if not 0 < low < mesh.ptr_end:
            return None
        spans.append((low, mesh.ptr_end))
    start = spans[0][0]
    for (lo, hi), (nlo, _) in zip(spans, spans[1:]):
        if not lo < hi <= nlo <= hi + 4:
            return None          # not contiguous in index order
    if not (spans[-1][1] <= colour < uv <= pool <= end):
        return None
    if colour - spans[-1][1] > 4:
        return None              # something between the blocks and the tables
    for field in _SHIFTS_WITH_BOUNDARY:
        if _resolve(data,field) < end:
            return None          # a header field points inside the span
    return start, colour, uv, end


def _rewrite_region(dest_data, dest, layout, prepared, per_mesh, colours, uvs):
    """Lay the whole geometry region out again with no unreachable bytes."""
    start, old_colour, old_uv, old_end = layout
    out = bytearray(dest_data[:start])

    def append(block: bytes) -> int:
        at = len(out)
        out.extend(block)
        if len(out) % 4:
            out.extend(b"\x00" * (4 - len(out) % 4))
        return at

    placed = {}
    shifted = {}
    attached = {}
    for index, mesh in enumerate(dest.meshes):
        if index in prepared:
            target, blocks = prepared[index]
            colour_index, uv_index = per_mesh[index]
            strips = append(blocks["strips"])
            geometry = append(blocks["geometry"])
            uvi = append(uv_index)
            tex = append(blocks["texture"])
            ci = append(colour_index)
            end = len(out)
            keep = _attachment_bytes(dest_data, mesh)
            attach = append(keep) if keep else 0
            placed[index] = (blocks["faces"], geometry, strips, uvi, tex, ci,
                             end, attach)
        else:
            low = min(mesh.ptr_bounds, mesh.ptr_strips, mesh.ptr_uv_index,
                      mesh.ptr_texture, mesh.ptr_colour_index)
            # Copied verbatim, so only the six pointers move. Rebuilding the
            # header instead loses whatever it holds that a reader does not
            # reconstruct -- it cost nine triangles the first time.
            shifted[index] = append(dest_data[low:mesh.ptr_end]) - low
            # The attachment block sits *after* `ptr_end`, outside the span
            # just copied, so it needs carrying separately -- and it was not.
            # `chars/crate/coco` rebuilt for its mesh 0 came back with mesh 1's
            # `+0x2C` still holding its shipped offset, which after the rewrite
            # addresses something else entirely: the spin body's collision
            # volume read as zero records. §8.4 is read live by gameplay, and
            # zeroing it is what let the crate game's character walk through
            # the crates.
            keep = _attachment_bytes(dest_data, mesh)
            attached[index] = append(keep) if keep else 0

    new_colour = len(out)
    out.extend(colours)
    new_uv = len(out)
    out.extend(uvs)
    if len(out) % 4:
        out.extend(b"\x00" * (4 - len(out) % 4))
    new_pool = len(out)
    out.extend(dest_data[_resolve(dest_data, 0x28):old_end])
    boundary = len(out)

    delta = boundary - old_end
    if struct.unpack_from("<i", dest_data, RESIDENT_SIZE)[0] % SECTOR == 0:
        out.extend(b"\x00" * (-delta % SECTOR))
        boundary = len(out)
        delta = boundary - old_end
    out.extend(dest_data[old_end:])

    struct.pack_into("<i", out, PTR_COLOUR_TABLE, new_colour - PTR_COLOUR_TABLE)
    struct.pack_into("<i", out, PTR_UV_TABLE, new_uv - PTR_UV_TABLE)
    struct.pack_into("<i", out, 0x28, new_pool - 0x28)
    struct.pack_into("<i", out, PTR_MODEL_END, boundary - PTR_MODEL_END)
    for field in _SHIFTS_WITH_BOUNDARY:
        struct.pack_into("<i", out, field,
                         struct.unpack_from("<i", out, field)[0] + delta)
    struct.pack_into("<i", out, RESIDENT_SIZE,
                     struct.unpack_from("<i", out, RESIDENT_SIZE)[0] + delta)

    for index, mesh in enumerate(dest.meshes):
        header = MESH_HEADER_START + MESH_HEADER_SIZE * index
        if index in placed:
            faces, geo, strips, uvi, tex, ci, end, attach = placed[index]
            target, blocks = prepared[index]
            _finish_header(out, header, faces, target.format, target.unk13,
                           target.unk14, geo, strips, uvi, tex, ci, end, attach,
                           geo + blocks["normals"] if blocks["normals"] else 0)
            continue
        shift = shifted[index]
        for field, was in ((FIELD_BOUNDS, mesh.ptr_bounds),
                           (FIELD_STRIPS, mesh.ptr_strips),
                           (FIELD_UV_INDEX, mesh.ptr_uv_index),
                           (FIELD_TEXTURE, mesh.ptr_texture),
                           (FIELD_COLOUR_INDEX, mesh.ptr_colour_index),
                           (FIELD_END, mesh.ptr_end)):
            at = header + field
            struct.pack_into("<i", out, at, was + shift - at)
        at = header + FIELD_ATTACHMENT
        struct.pack_into("<i", out, at,
                         attached[index] - at if attached[index] else 0)
    return bytes(out)


def _carry_vector_pool(out: bytearray, source: bytes) -> int:
    """Re-lay the shared position pool so it still ends at the new boundary.

    §7.3 defines the pool as `T(0x28)` up to `T(0x08)`, which means its length is
    the gap between the two. Appending geometry moves `T(0x08)` outward, so a
    writer that leaves `0x28` alone stretches the pool over everything it just
    appended: a degenerate pool in `intro_eurocom` became 201,216 bytes of it,
    and `mainmenu/models`'s real 58,676-byte pool became 806,436.

    Nothing indexes the pool by position in the file -- a blob either carries its
    own or reads `model+0x28` (0x80019C08), and §7.3's expander indexes from that
    base -- so copying the bytes to the end and repointing `0x28` at them is
    safe, and it is what keeps the gap equal to the real length.

    Returns the offset the boundary should now name.
    """
    start = PTR_MODEL_POOL + struct.unpack_from("<i", source, PTR_MODEL_POOL)[0]
    end = PTR_MODEL_END + struct.unpack_from("<i", source, PTR_MODEL_END)[0]
    if not 0 <= start <= end <= len(source):
        return len(out)
    if len(out) % 4:
        out.extend(b"\x00" * (4 - len(out) % 4))
    at = len(out)
    out.extend(source[start:end])
    struct.pack_into("<i", out, PTR_MODEL_POOL, at - PTR_MODEL_POOL)
    return len(out)


def _finish_header(out, header, faces, fmt, unk13, unk14, geometry, strips,
                   uv_index, texture, colour_index, end,
                   attachment: int = 0, normals: int = 0) -> None:
    struct.pack_into("<h", out, header + FIELD_FACE_COUNT, faces)
    struct.pack_into("<h", out, header + FIELD_FORMAT, fmt)
    struct.pack_into("<2h", out, header + FIELD_UNK13, unk13, unk14)
    for field_offset, destination in (
        (FIELD_BOUNDS, geometry),
        (FIELD_STRIPS, strips),
        (FIELD_UV_INDEX, uv_index),
        (FIELD_TEXTURE, texture),
        (FIELD_COLOUR_INDEX, colour_index),
        (FIELD_END, end),
    ):
        at = header + field_offset
        struct.pack_into("<i", out, at, destination - at)
    # +0x28 is the per-vertex normal array, `V` records of 8 bytes laid down
    # straight after the positions (§4.3). 300 of the archive's 5989 meshes
    # carry one; a rebuild that leaves the pointer at zero simply declares
    # none, which is what 5689 shipped meshes do, so it is consistent -- but no
    # EXE site dereferences the field and searched-and-not-found is not absent,
    # so the array is carried when the caller states it.
    at = header + FIELD_NORMALS
    struct.pack_into("<i", out, at, normals - at if normals else 0)
    # The attachment block at +0x2C is read live by gameplay through the 0x2000
    # id namespace, and for a character it is the collision volume -- the crate
    # game's crates stopped colliding the first time this was zeroed. When the
    # caller supplies a block it is pointed at here; otherwise zero, the state
    # of 5,213 of the game's own 5,990 meshes, since a stale pointer would name
    # records for a mesh that no longer exists.
    at = header + FIELD_ATTACHMENT
    struct.pack_into("<i", out, at, attachment - at if attachment else 0)
