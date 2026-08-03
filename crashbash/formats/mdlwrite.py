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

from .mdl import (
    COLOUR_INDEX_MASK,
    MESH_HEADER_SIZE,
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
# equals bit 0 of that triangle's vertex flag in 42,267 of 42,267 strips.
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
    # The attachment block (mesh+0x2C) the installed mesh should carry, raw:
    # [u16 flags][u16 count][16 bytes x count]. Gameplay reads it live through
    # the 0x2000 id namespace, and for a character it is the collision volume
    # -- the crate game's crates stopped colliding when it was zeroed. Supply
    # the replaced mesh's own block when the stand-in matches its height.
    attachment: bytes | None = None


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


def build_strips(positions: np.ndarray, keys: np.ndarray | None = None
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
    """
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

    used = np.zeros(faces, dtype=bool)
    strips: list[list[tuple[int, tuple[int, int, int]]]] = []
    for seed in range(faces):
        if used[seed]:
            continue
        used[seed] = True
        strip = [(seed, (0, 1, 2))]
        # The last two vertices the strip has emitted, and how far along it is.
        tail = (int(welded[seed][1]), int(welded[seed][2]))
        step = 1
        while True:
            # Triangle k is presented as it stands when k is even and reversed
            # when it is odd, so the edge the next face must carry flips with it.
            wanted = tail if step % 2 == 0 else (tail[1], tail[0])
            key = (int(keys[seed]), wanted[0], wanted[1])
            following = next((f for f in directed.get(key, ()) if not used[f]), None)
            if following is None:
                break
            # Found by the directed edge, but ordered by the strip's own: the
            # strip always presents (s[k], s[k+1], s[k+2]), which on an odd step
            # is the edge the other way round from the one that found the face.
            used[following] = True
            order = corner_order(following, tail[0], tail[1])
            strip.append((following, order))
            third = int(welded[following][order[2]])
            tail = (tail[1], third)
            step += 1
        strips.append(strip)
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

    # A negative texture index marks a triangle drawn untextured; the rest name
    # a slot in the pack. Strips need only group by that binary distinction --
    # the untextured flag is per strip, but the texture itself is not: the run
    # list advances per triangle and runs cross strip boundaries freely (§6.2),
    # so one strip may sample several textures. Grouping by slot fragments the
    # strips for nothing.
    keys = (np.asarray(mesh.textures, dtype=np.int64) >= 0) if textured else None
    runs = build_strips(mesh.positions, keys)
    strips = bytearray()
    for run in runs:
        plain = (not textured) or int(mesh.textures[run[0][0]]) < 0
        # Bit 3 states the winding of the strip's first triangle and must agree
        # with the vertex flags below -- 42,267 of the game's 42,267 strips do.
        # Seeds are written in the source's own order, which the game marks as
        # winding 1: rebuilding one of its meshes reproduces its pool exactly,
        # 473 of 473 slots, and the flag it gives that first triangle is 1.
        flags = STRIP_FLAG_FIRST_FLIPPED | (STRIP_FLAG_UNTEXTURED if plain else 0)
        strips += struct.pack("<H", (len(run) << 8) | flags)
    strips += struct.pack("<H", 0xFF00)

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
    winding: list[int] = []
    for run in runs:
        winding += [0 if k < 2 else (k - 1) % 2 for k in range(len(run) + 2)]
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
            [int(mesh.textures[face]) & TEXTURE_INDEX_MASK for face, _ in plan]
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

    return {
        "strips": bytes(strips),
        "geometry": bounds + vertices.tobytes(),
        "texture": texture,
        "colours": bytes(colours),
        "uvs": uvs,
        "faces": len(plan),
        "textured": textured,
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
        if entry & TEXTURE_FLAG_SWATCH:
            new_entry = TEXTURE_FLAG_SWATCH | source.palette_map.get(index, index)
        else:
            new_entry = source.texture_map.get(index, index)
            shift = source.uv_shift.get(index, (0, 0))
        # One entry per triangle, run length zero: the run only ever compresses.
        texture += struct.pack("<H", new_entry & 0xFFFF)

        at = src_uv + mesh.face_uv_index[face] * UV_ENTRY_SIZE
        for corner in range(3):
            u = source.data[at + corner * 2] + shift[0]
            v = source.data[at + corner * 2 + 1] + shift[1]
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
    if mesh.textures is None and not mesh.swatch:
        mesh = replace(mesh, swatch=_swatch_entry(dest_data, target))
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
            found = len(colours) // COLOUR_ENTRY_SIZE
            colours += triple
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
                    "shared table, and growing it is what crashes these rooms; "
                    "use untextured geometry or existing UVs"
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
                   colour_index_new, end_new)
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
                   attachment: int = 0) -> None:
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
    struct.pack_into("<i", out, header + FIELD_NORMALS, 0)
    # The attachment block at +0x2C is read live by gameplay through the 0x2000
    # id namespace, and for a character it is the collision volume -- the crate
    # game's crates stopped colliding the first time this was zeroed. When the
    # caller supplies a block it is pointed at here; otherwise zero, the state
    # of 5,213 of the game's own 5,990 meshes, since a stale pointer would name
    # records for a mesh that no longer exists.
    at = header + FIELD_ATTACHMENT
    struct.pack_into("<i", out, at, attachment - at if attachment else 0)
