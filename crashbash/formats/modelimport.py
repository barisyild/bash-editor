"""Rebuild a model's meshes and clips from an already-extracted description.

This is the half of an import that has nothing to do with the file it came out
of. `gltfimport` reads a `.glb` and this module does the surgery; a Blender
add-on reads `bpy` data and hands the same description to the same surgery. What
travels between them is `ImportRequest`: per-mesh corner arrays in the writer's
own terms, per-clip absolute poses, repainted images by slot.

Keeping it in one place is not tidiness. Every rule the import enforces was
learned from a disc that booted into a crash or drew garbage -- the layout
boundary and its ordering, the untouched-mesh rule that keeps the colour table
inside 13 bits, the swatch cell an untextured face still has to name, the frozen
clip that needs one keyframe rather than one per key. A second front end that
re-implemented any of them would re-learn them the same way.

The terms a payload is stated in:

* positions in raw model units, per corner, `(triangles, 3, 3)`;
* colours as the console draws them, `(triangles, 3, 3)` of 0..255;
* UVs in texels of the slot the face names, or the cell of the pack's swatch
  texture when it names none;
* `textures[face] >= 0` is a pack slot, `< -1` is a verbatim `0x8000 | palette`
  swatch entry stored negated (§6.2), and `-1` is "whatever this mesh's own
  list uses most", which is also the only case given a cell here.
"""

from __future__ import annotations

import struct
from collections import Counter
from dataclasses import dataclass, field

import numpy as np

from ..binreader import GTE_SCALE_SMALL
from . import animwrite as AW
from . import mdlwrite as MW
from . import placewrite as PW
from . import scenewrite as SW
from . import texwrite as TW
from .anim import WEIGHT_ONE, read_animations
from .mdl import TEXTURE_FLAG_SWATCH, TEXTURE_INDEX_MASK, Mesh, Model, read_model
from .tex import TexturePack, read_pack

# How far a keyframe vertex may sit from its rest match, in model units. The
# pool is built from the same corner data the targets index, so anything beyond
# rounding means the file does not belong to this mesh -- but the rounding is
# per axis and the test is a distance, so a vertex quantised half a unit on each
# of three axes is √3/2 = 0.87 away while still being the right vertex. 0.51
# rejected it: reshaping a mesh in Blender and reimporting failed with "a pool
# vertex sits 0.65 units from the nearest glTF vertex" on geometry that was
# perfectly matched. One unit is the smallest bound that admits pure
# quantisation and still rejects a mesh that is genuinely a different shape.
MATCH_TOLERANCE = 1.0


@dataclass
class MeshPayload:
    """One incoming mesh, in the terms `mdlwrite.NewMesh` is stated in."""

    positions: np.ndarray  # (T, 3, 3) float, raw model units, per corner
    colours: np.ndarray  # (T, 3, 3) uint8 RGB, per corner
    uvs: np.ndarray  # (T, 3, 2) uint8 texels, or the swatch cell
    textures: np.ndarray  # (T,) int64: slot, negated swatch entry, or -1
    # (T,) of the colour index's top three bits: bit 15 turns the GPU's
    # semi-transparency on and bits 13-14 pick the ABR mode (§6.3). `None`
    # leaves the writer to recover them from the mesh being replaced, by
    # matching corner positions, which is what a source with nowhere to state
    # them has to fall back on.
    blend: np.ndarray | None = None
    # (V, 3) per source vertex: the per-vertex normals at mesh +0x28 (§4.3), in
    # GTE 1/4096 fixed point. 300 of the archive's 5989 meshes carry them, and
    # while no reader for the field has been found in the executable, searched
    # and not found is not the same as absent -- so they travel.
    normals: np.ndarray | None = None
    # The source's own vertex array, in model units, in the order its poses are
    # stated in. Only animation needs it: a clip's keyframes are matched onto
    # the rebuilt pool through these.
    vertices: np.ndarray | None = None
    # (T, 3) index into `vertices` per corner, when the source has one. Nothing
    # in the import needs it -- the writer works in corner positions -- but a
    # front end building an editable mesh does, because it is the difference
    # between welding by position and welding by identity.
    corner_vertices: np.ndarray | None = None


@dataclass
class ClipPayload:
    """One clip the source drives, as absolute poses over the source's vertices."""

    mesh_index: int
    poses: list[np.ndarray]  # each (V, 3) in model units, `MeshPayload.vertices` order
    frames: list[AW.FrameSpec]  # keys index `poses`
    # The source says this reproduces the shipped clip exactly. Whether that
    # means anything is this module's to decide, and it depends on the mesh: a
    # clip whose mesh was rebuilt has to be rebuilt with it, because the pool it
    # indexes is a different pool. Only when the mesh was left alone can an
    # unchanged clip be copied through byte for byte -- and it must be, since a
    # rebuild lays down a fresh pose pool and that is most of what a model
    # weighs. Thirteen untouched clips rewritten took `chars/crate/coco`
    # 163,160 bytes away from the file it came out of.
    unchanged: bool = False


@dataclass
class ImportRequest:
    """Everything a front end extracted, before any of it is written."""

    meshes: dict[int, MeshPayload] = field(default_factory=dict)
    # Keyed by the shipped clip's label. A clip absent here keeps its own
    # animation when its mesh was left alone and falls back to the rest pose
    # when the mesh was rebuilt -- guessing at a pose is not on the table.
    clips: dict[str, ClipPayload] = field(default_factory=dict)
    images: dict[int, np.ndarray] = field(default_factory=dict)  # slot -> RGBA
    # Slots the source named at all, whether or not it repainted them. The
    # palette-sharing test is over these, not over the images.
    slots: set[int] = field(default_factory=set)
    scene: dict | None = None  # `scenewrite.patch_scene` extras
    # Placement records to rewrite, by index: `{"id", "translation",
    # "rotation"}`, any of them absent meaning "leave it". A level draws what
    # this list names and nothing else (§8.5), so rewriting a record is the one
    # edit that changes a level -- and the list cannot be made longer.
    placements: dict[int, dict] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    # Reasons to refuse. Collected rather than raised one at a time so an
    # artist sees every problem in the file at once.
    problems: list[str] = field(default_factory=list)
    # What to say when the request turned out to name no mesh at all; a front
    # end knows how its own names are spelt and this module does not.
    empty_error: str = "nothing in the source names a mesh of this model"
    empty_note: str = ("nothing named a mesh; the scene was written and the "
                       "geometry left as it was")


@dataclass
class Report:
    meshes_rebuilt: list[int] = field(default_factory=list)
    # Meshes the file brought back exactly as they left, whose blocks were
    # therefore not touched at all.
    meshes_unchanged: list[int] = field(default_factory=list)
    clips_rebuilt: list[str] = field(default_factory=list)
    clips_static: list[str] = field(default_factory=list)
    clips_copied: list[str] = field(default_factory=list)
    textures_written: list[int] = field(default_factory=list)
    textures_unchanged: list[int] = field(default_factory=list)
    palettes_shared: list[int] = field(default_factory=list)
    placements_written: list[int] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    scene: SW.Patched | None = None
    model: bytes = b""
    pack: bytes | None = None


# --- comparing one mesh against another ---------------------------------


def face_key(points, colours, uvs, texture):
    """One triangle, canonical under rotation but not under reversal.

    Rotating a triangle's corners leaves it the same triangle; reversing them
    turns it inside out (§11.3), so the two must not compare equal.
    """
    start = min(range(3), key=lambda k: points[k])
    order = [(start + k) % 3 for k in range(3)]
    return (tuple(points[k] for k in order), tuple(colours[k] for k in order),
            tuple(uvs[k] for k in order), texture)


def face_bag(positions, colours, uvs, textures) -> Counter:
    """A mesh's triangles as a multiset, order-independent.

    Triangles come back from a modelling tool grouped by material rather than in
    the strip order the file stores, so only the set of them can be compared.
    """
    bag: Counter = Counter()
    rounded = np.clip(np.round(positions), -32768, 32767).astype(np.int64)
    for f in range(positions.shape[0]):
        bag[face_key(
            [tuple(int(v) for v in rounded[f, k]) for k in range(3)],
            [tuple(int(v) for v in colours[f, k]) for k in range(3)],
            [tuple(int(v) for v in uvs[f, k]) for k in range(3)]
            if uvs is not None else [(0, 0)] * 3,
            int(textures[f]) if textures is not None else -1,
        )] += 1
    return bag


def payload_bag(payload: MeshPayload) -> Counter:
    return face_bag(payload.positions, payload.colours, payload.uvs,
                    payload.textures)


def weld_vertices(rest: np.ndarray, poses: list[np.ndarray] | None = None
                  ) -> tuple[np.ndarray, np.ndarray]:
    """Group coincident vertices, but only where nothing tells them apart.

    Returns one group id per input vertex and, per group, the input vertex that
    stands for it.

    Welding by position alone is not safe on an animated mesh. The strip pool
    repeats a position wherever a strip stitches to the next and those repeats
    are the same point of the surface -- but two entries that sit together at
    rest may still be driven apart by a clip, and merging them gives both the
    same pose. Measured over the archive: **49 of 357 animated meshes** hold
    such a pair, 128 in all, the worst being six in `cutscene/aku/data` mesh 2.
    One of them moved a corner of `cutscene/level_shot12` four units off on 280
    of that clip's 429 frames while every static check passed.

    So the signature is the rest position *and* every pose the source states.
    """
    rows = [np.asarray(rest, dtype=np.float64)]
    for pose in poses or []:
        pose = np.asarray(pose, dtype=np.float64)
        if pose.shape == rows[0].shape:
            rows.append(pose)
    signature = np.concatenate(rows, axis=1)
    _, first, groups = np.unique(signature, axis=0, return_index=True,
                                 return_inverse=True)
    return groups.reshape(-1), first


def payload_from_model(model_data: bytes, model: Model, pack: TexturePack | None,
                       index: int, clips: list | None = None) -> MeshPayload | None:
    """A shipped mesh restated as an incoming one, exactly.

    This is what makes a native front end able to say "nothing changed" without
    a round trip through a file format: it builds the Blender mesh from this,
    reads it back into this, and compares. glTF cannot use it -- that exporter
    folds the swatch texel into the vertex colour and writes cell UVs, so its
    own output never equals the model's stored arrays (positions agree on 6031
    of 6031 triangles of `mainmenu/models` while the stored UVs agree on 2015)
    and it has to compare against a round trip instead.

    Give it the model's `clips` and the vertices it reports are the surface's,
    welded the way `weld_vertices` welds them; without them the pool's repeats
    are welded on position alone, which is right for a mesh nothing animates.
    """
    mesh = MW.mesh_index(model).get(index)
    if mesh is None or not mesh.face_count:
        return None
    points = np.asarray(mesh.positions, dtype=np.float64) / GTE_SCALE_SMALL
    triangles = mesh.indexed_triangles()
    faces = len(triangles)
    positions = np.zeros((faces, 3, 3), dtype=np.float64)
    colours = np.full((faces, 3, 3), 128, dtype=np.uint8)
    uvs = np.zeros((faces, 3, 2), dtype=np.uint8)
    textures = np.full(faces, -1, dtype=np.int64)
    corner_vertices = np.zeros((faces, 3), dtype=np.int64)
    blend = np.zeros(faces, dtype=np.uint8)
    for row, (a, b, c, face) in enumerate(triangles):
        # Outward order, not strip order. Consecutive triangles in a strip wind
        # opposite ways and bit 0 of the third corner's vertex flag says which
        # way this one does (§11.3). The writer seeds every strip unflipped and
        # the game flips the sign of its NCLIP backface test per that bit, so a
        # triangle handed over in strip order is handed over inside out and is
        # *culled* rather than drawn -- and nothing on a static render shows it.
        # 62 of `chars/crate/coco`'s 511 triangles arrive that way.
        flipped = bool(mesh.vertex_flags[c] & 1)
        order = (a, c, b) if flipped else (a, b, c)
        # Corner k takes colour k and UV k -- the game writes vertex i, i+1, i+2
        # and UV 0, 1, 2 in step -- so reversing the corners reverses these too.
        corners = (0, 2, 1) if flipped else (0, 1, 2)
        positions[row] = points[list(order)]
        corner_vertices[row] = order
        if face < len(mesh.face_colour_index):
            blend[row] = int(mesh.face_colour_index[face]) >> 13
        triple = model.face_colours(mesh, face)
        if triple is not None:
            colours[row] = np.asarray(triple, dtype=np.uint8)[list(corners)]
        entry = int(mesh.face_texture[face]) if face < len(mesh.face_texture) else 0
        corner_uvs = model.face_uvs(mesh, face)
        if corner_uvs is not None:
            uvs[row] = np.asarray(corner_uvs, dtype=np.uint8)[list(corners)]
        if model.face_is_untextured(mesh, face):
            # Untextured is not untexted: the face reads one texel of the pack's
            # swatch image through the palette this entry names (§6.2). Carried
            # verbatim, negated, which is how `NewMesh` states it.
            #
            # Masked to what the entry actually says. The reader expands a
            # run-length list and leaves the run field in bits 9..14, so the
            # same swatch palette arrives as 0x8012, 0x8212 or 0xB612 depending
            # on how many triangles shared its run. `_pack_runs` masks them off
            # again, so the file is the same either way -- but the payload is
            # what "this mesh came back unchanged" is decided on, and three
            # spellings of one entry never compare equal.
            textures[row] = (-(entry & (TEXTURE_FLAG_SWATCH | TEXTURE_INDEX_MASK))
                             if entry & TEXTURE_FLAG_SWATCH else -1)
        else:
            textures[row] = entry & TEXTURE_INDEX_MASK
    poses = []
    for clip in clips or []:
        if clip.mesh_index != index:
            continue
        for key in clip.keyframes():
            poses.append(clip.pool()[clip._slots(key)])
    groups, first = weld_vertices(points, poses)
    normals = None
    if mesh.normals and len(mesh.normals) == len(mesh.positions):
        # Stated per pool entry and taken per welded vertex, through the same
        # representative the poses use. Two entries that weld together agree
        # about their rest position; nothing says they agree about a normal, so
        # this takes the first and says nothing more.
        normals = (np.asarray(mesh.normals, dtype=np.float64)
                   / GTE_SCALE_SMALL)[first]
    return MeshPayload(positions=positions, colours=colours, uvs=uvs,
                       textures=textures, blend=blend, normals=normals,
                       vertices=points[first],
                       corner_vertices=groups[corner_vertices])


def reference_bags(model_data: bytes, model: Model, pack: TexturePack | None,
                   wanted, clips: list | None = None) -> dict[int, Counter]:
    """What an untouched mesh must equal, per mesh index."""
    bags: dict[int, Counter] = {}
    for index in wanted:
        payload = payload_from_model(model_data, model, pack, index, clips)
        if payload is not None:
            bags[index] = payload_bag(payload)
    return bags


# --- what an untextured face still has to name ---------------------------


def neutral_swatch_cell(model_data: bytes, target: Mesh, pack: TexturePack
                        ) -> tuple[int, int] | None:
    """The cell of the pack's swatch texture that leaves a colour most alone.

    An untextured face in this format is a swatch face (§6.2): it names a
    palette and reads a single texel, and the hardware draws `texel * colour /
    128`. A front end that folds that texel into the vertex colour writes no
    cell, so a rebuilt face has to be given one -- and it was given (0,0), which
    through `chars/crate/coco`'s palette 18 is the palette's entry 0, pure
    black. Every swatch face of a rebuilt mesh therefore drew black, while
    `tools/roundtrip.py`, which compares positions, reported no loss at all:
    a shipped mesh using seven cells came back with all 163 faces on (0,0).

    Choosing the cell nearest the hardware's neutral lets the folded colour
    through instead. It is not always exact -- palette 18's best is
    (123,123,123), 4 % dark -- but it is the difference between a model that
    draws and one that does not.
    """
    swatch = next((t for t in pack.textures if t.is_swatch), None)
    if swatch is None:
        return None
    entry = MW._swatch_entry(model_data, target)
    palette_index = entry & TEXTURE_INDEX_MASK
    if not 0 <= palette_index < len(pack.palettes):
        return None
    palette = pack.palettes[palette_index]
    cells = swatch.indices()
    best, best_cost = None, None
    for y in range(swatch.height):
        for x in range(swatch.width):
            cell = int(cells[y, x])
            if cell >= palette.shape[0]:
                continue
            cost = int(np.abs(palette[cell][:3].astype(np.int32) - 128).sum())
            if best_cost is None or cost < best_cost:
                best, best_cost = (x, y), cost
    return best


# --- facing ---------------------------------------------------------------


def signed_volume(corners: np.ndarray) -> float:
    """Six times the volume a closed surface encloses, sign and all."""
    p, q, r = corners[:, 0], corners[:, 1], corners[:, 2]
    return float(np.einsum("ij,ij->i", p, np.cross(q, r)).sum()) / 6.0


def warn_if_inside_out(index: int, positions: np.ndarray, target: Mesh | None,
                       report: Report) -> None:
    """Say so when an incoming mesh is wound against the one it replaces.

    The winding is taken as it arrives and must be: a room shell is seen from
    inside and its faces correctly point inward, and a flood fill imposed here
    inverted all 875 triangles of `mainmenu/models` mesh 6 and lost the menu's
    backdrop. But a *character* handed over inside out is drawn inside out --
    the game flips the NCLIP test per vertex flag (§11.3) -- and nothing else
    reports it: the geometry is right, the strips are right, the round trip
    measures worst corner 0.0000, and on screen the body reads as parts facing
    the wrong way.

    Comparing the closed volume against the mesh being replaced catches it
    without deciding anything. Modelling a character in Blender and exporting it
    straight out gives the opposite sign to every shipped character, so this is
    the first thing to check when an import draws inside out.
    """
    if target is None or not target.face_count or not len(target.positions):
        return
    shipped = np.asarray(target.positions, dtype=np.float64)
    was = []
    for a, b, c, _ in target.indexed_triangles():
        order = (a, b, c) if not (target.vertex_flags[c] & 1) else (a, c, b)
        was.append(shipped[list(order)])
    if not was:
        return
    before = signed_volume(np.array(was))
    after = signed_volume(np.asarray(positions, dtype=np.float64) * GTE_SCALE_SMALL)
    if before == 0.0 or after == 0.0 or np.sign(before) == np.sign(after):
        return
    report.warnings.append(
        f"mesh {index} is wound against the mesh it replaces: it encloses "
        f"{after:+.3f} where the shipped one encloses {before:+.3f}. The "
        f"winding is taken as authored, so if this is a solid body it will "
        f"draw inside out -- flip the normals in the modelling tool and export "
        f"again. A surface meant to be seen from inside is the other case."
    )


# --- textures -------------------------------------------------------------


def _resize_rgba(image: np.ndarray, height: int, width: int,
                 notes: list[str]) -> np.ndarray:
    """An RGBA image at the slot's size. A slot cannot be resized (§10.1)."""
    try:
        from PIL import Image  # noqa: PLC0415
    except ImportError:
        notes.append(
            f"an image was resampled to {width}x{height} by nearest neighbour "
            f"because PIL is not installed; paint at the slot's own size for a "
            f"clean result"
        )
        rows = np.arange(height) * image.shape[0] // height
        cols = np.arange(width) * image.shape[1] // width
        return image[rows[:, None], cols[None, :]]
    return np.array(
        Image.fromarray(image, "RGBA").resize((width, height), Image.LANCZOS)
    )


def _quantise(rgb: np.ndarray, colours: int) -> tuple[np.ndarray, np.ndarray] | None:
    """A fresh palette and its indices, or None when nothing can build one."""
    try:
        from PIL import Image  # noqa: PLC0415
    except ImportError:
        return None
    pil = Image.fromarray(rgb, "RGB").quantize(
        colors=colours, method=Image.MEDIANCUT, dither=Image.NONE
    )
    palette = np.zeros((colours, 3), dtype=np.uint8)
    raw = np.array(pil.getpalette() or [], dtype=np.uint8).reshape(-1, 3)
    palette[: min(len(raw), colours)] = raw[:colours]
    return palette, np.array(pil, dtype=np.uint8)


def _nearest_in_palette(rgb: np.ndarray, palette: np.ndarray) -> np.ndarray:
    flat = rgb.reshape(-1, 3).astype(np.int32)
    nearest = np.argmin(
        ((flat[:, None, :] - palette[None, :, :]) ** 2).sum(axis=2), axis=1
    )
    return nearest.reshape(rgb.shape[:2]).astype(np.uint8)


def write_slot(pack_data: bytes, pack: TexturePack, slot: int,
               image: np.ndarray, exclusive: set[int], report: Report) -> bytes:
    """Put a repainted image back into its slot, honouring palette sharing."""
    entry = pack.textures[slot]
    current = entry.to_rgba(pack.palettes)
    if image.shape[:2] != current.shape[:2]:
        image = _resize_rgba(image, current.shape[0], current.shape[1],
                             report.warnings)
    if np.array_equal(image[..., :3], current[..., :3]):
        report.textures_unchanged.append(slot)
        return pack_data

    rgb = np.ascontiguousarray(image[..., :3])
    colours = 1 << entry.bit_depth
    fresh = _quantise(rgb, colours) if entry.palette_index in exclusive else None
    if fresh is not None:
        # The palette belongs to this import alone: requantise it outright.
        palette, indices = fresh
        r, g, b = (palette.astype(np.uint16) >> 3).T
        values = (b << 10) | (g << 5) | r
        values = np.where(values == 0, 0x8000, values)  # keep true black opaque
        pack_data = TW.replace_palette(pack_data, entry.palette_index, values)
    else:
        # Shared palette, or no quantiser: keep it and map every pixel to its
        # nearest colour. A palette named by a texture outside this import is
        # never rewritten -- it paints faces nobody asked to change.
        report.palettes_shared.append(slot)
        indices = _nearest_in_palette(
            rgb, pack.palettes[entry.palette_index][:, :3].astype(np.int32))

    pack_data = TW.replace_pixels(
        pack_data, slot, TW._pack_indices(indices, entry.bit_depth)
    )
    report.textures_written.append(slot)
    return pack_data


# --- the timeline ---------------------------------------------------------


def frames_from_weights(weights: np.ndarray, frame_total: int = 0
                        ) -> tuple[list[int], list[AW.FrameSpec]]:
    """A frame table from one weight per pose per frame.

    `weights` is `(frames, poses)`, already on the game's own 30 Hz tick grid.
    Whatever the source called blending -- glTF morph target weights, a Blender
    shape key's animated value -- comes down to this: the game holds at most two
    poses per frame and one weight between them, so the two strongest win and
    the rest are dropped. The poses that no frame reaches are dropped with them,
    which is what makes the returned index list worth having.

    The returned `weight` is 0 exactly when there is no second key, because the
    archive holds it that way in 13,652 records each way and the game reads
    `key_b` only through the blend decoder a non-zero weight selects.
    """
    weights = np.asarray(weights, dtype=np.float64)
    if weights.ndim != 2:
        raise ValueError("weights must be (frames, poses)")
    targets = weights.shape[1]
    used = sorted(int(t) for t in np.flatnonzero(weights.max(axis=0) > 1e-6))
    slot_of = {t: i for i, t in enumerate(used)}
    frames: list[AW.FrameSpec] = []
    for row in weights:
        order = np.argsort(row)[::-1]
        first = int(order[0])
        second = int(order[1]) if targets > 1 else None
        w_first = float(row[first])
        w_second = float(row[second]) if second is not None else 0.0
        if w_first <= 0 or first not in slot_of:
            frames.append(AW.FrameSpec(0, None, 0))
            continue
        # The stronger key first. There is no ordering to match here: the
        # archive states a blend both ways round -- `chars/crate/coco`'s BREATHE
        # frame 11 is `key 1 -> key 0` at 409 while `cutscene/level_shot12` runs
        # ascending throughout -- and `a + (b-a)*w` and `b + (a-b)*(1-w)` are
        # the same blend read from opposite ends. A caller that needs the file's
        # own ordering back has to keep the clip rather than rebuild it.
        blend = w_second / (w_first + w_second) if w_second > 1e-4 else 0.0
        step = int(round(blend * WEIGHT_ONE))
        if step <= 0 or second not in slot_of:
            frames.append(AW.FrameSpec(slot_of[first], None, 0))
        elif step >= WEIGHT_ONE:
            frames.append(AW.FrameSpec(slot_of[second], None, 0))
        else:
            frames.append(AW.FrameSpec(slot_of[first], slot_of[second], step))
    if frame_total and len(frames) != frame_total:
        # The caller's expected frame count wins when the clip drives a scene
        # that asks for it; stretch or trim by resampling frame indices.
        picks = np.linspace(0, len(frames) - 1, frame_total).round().astype(int)
        frames = [frames[i] for i in picks]
    return used, frames


# --- the import itself ----------------------------------------------------


def import_payload(model_data: bytes, pack_data: bytes | None,
                   request: ImportRequest, *,
                   pin_tables: bool | None = None,
                   animation_only: bool = False,
                   reference: dict[int, Counter] | None = None,
                   rebuild_all: bool = False) -> Report:
    """Rebuild `model_data`'s meshes and clips from `request`.

    `reference` is what an untouched mesh must equal, per mesh index; a front
    end whose own output does not equal the model's stored arrays passes its
    own, and everything else lets `payload_from_model` decide.

    `animation_only` rebuilds the clips and leaves every mesh exactly as it is.
    A clip is only rebuilt when its mesh is in the import, so re-timing an
    animation otherwise means reinstalling geometry that did not change -- and
    `install_mesh` appends a fresh copy of the colour and UV tables on every
    call, so nine untouched meshes cost nine copies. Measured on
    `mainmenu/models`: 276 KB became 1.4 MB and the game hung on the loading
    screen; with this flag the same twelve clips come back in 400 KB and it
    boots. Use it whenever the edit is to the timeline rather than the mesh.
    """
    report = Report()
    report.warnings.extend(request.warnings)
    # The seven §8.6 carriers announce themselves: their chunk-descriptor count
    # at 0x38 is non-zero in exactly those files and no others (7/400). Their
    # shared tables and their §8.6 block are pinned on hardware, so the graft
    # layout is not optional there -- engage it whenever the caller did not
    # decide explicitly, and say so in the report.
    if pin_tables is None:
        pin_tables = struct.unpack_from("<i", model_data, 0x38)[0] > 0
        if pin_tables:
            report.warnings.append(
                "§8.6 carrier detected: pinned-table graft layout engaged; "
                "colours map to existing entries and the shared tables stay "
                "in place"
            )

    # The shot goes back first. Every offset it records was taken against the
    # file as exported, so it has to be written before `install_mesh` moves the
    # layout boundary (§2.1) -- and because the patch resizes nothing, the
    # rebuild below runs on it exactly as it would have run on the original.
    if request.scene:
        model_data, patched = SW.patch_scene(model_data, request.scene)
        report.scene = patched
        report.warnings.extend(patched.skipped)

    model = read_model(model_data)
    clips = read_animations(model_data, model)
    pack = read_pack(pack_data) if pack_data is not None else None

    # The placement list goes back next, and for the same reason the shot does:
    # every record is addressed by its offset in the file as it stands, and
    # `install_meshes` moves the layout boundary underneath them (§2.1). The
    # rewrite resizes nothing, so what follows runs on it exactly as it would
    # have run on the original bytes.
    if request.placements:
        by_index = {i.index: i for i in model.instances}
        for index, edit in sorted(request.placements.items()):
            instance = by_index.get(index)
            if instance is None:
                report.warnings.append(
                    f"placement {index} is not in this model, which has "
                    f"{len(model.instances)}; it was left out")
                continue
            model_data = PW.write_placement(
                model_data, instance,
                identifier=edit.get("id"),
                translation=edit.get("translation"),
                rotation=edit.get("rotation"))
            report.placements_written.append(index)
        if report.placements_written:
            model = read_model(model_data)
            clips = read_animations(model_data, model)

    if not request.meshes:
        # A scene patch is already done and valid at this point, and five
        # arenas reach here every time: they have no numbered meshes at all,
        # only object-pool ones (§8.3), which a front end cannot name and the
        # writers cannot install into. Raising would throw away a finished edit
        # to their 56 placement records, so the scene-only result is returned
        # instead -- and only a source that changed nothing is an error.
        wrote = bool(report.placements_written) or (
            report.scene is not None and report.scene.total)
        if not wrote:
            raise ValueError(request.empty_error)
        report.warnings.append(request.empty_note)
        report.model = model_data
        return report

    # --- geometry ------------------------------------------------------
    trimmed = MW.strip_animation(model_data, clips)
    staged: dict[int, MW.NewMesh] = {}
    # `rebuild_all` puts every mesh through the writer even when the source did
    # not change it. Nothing in the app wants that -- it costs colour entries
    # for nothing -- but the verification tools do: with untouched meshes left
    # alone, a round trip of the shipped corpus rebuilds nothing and compares
    # nothing, which is a check that passes by doing no work.
    if rebuild_all:
        reference = {}
    elif reference is None:
        reference = reference_bags(model_data, model, pack, request.meshes, clips)
    by_index = MW.mesh_index(model)
    for index, payload in sorted(request.meshes.items()):
        if index in reference and payload_bag(payload) == reference[index]:
            # Came back exactly as it went out, so its blocks are left alone:
            # re-striping an untouched mesh reorders every triangle's corners,
            # which costs colour table entries it did not need to spend. All 22
            # meshes of `mainmenu/models` rebuilt wanted 8402 entries against
            # the 8192 a 13-bit index can address, for one edited mesh. It also
            # keeps the clips of the meshes nobody edited byte-identical, since
            # a clip whose mesh was not rebuilt is copied rather than rebuilt.
            report.meshes_unchanged.append(index)
            continue
        report.meshes_rebuilt.append(index)
        if animation_only:
            # Nothing to install: the clips below still rebuild, because they
            # match their poses against the mesh that is already there.
            continue
        target = by_index.get(index)
        # The winding arrives authored -- a front end emits outward corner
        # order (§11.3) -- so it is taken as it comes. Reorienting it here
        # instead cost facing: rebuilding `mainmenu/models` against the shipped
        # facing scores 6031/6031 triangles with the soup left alone and
        # 5912/6031 with a flood fill imposed on it.
        warn_if_inside_out(index, payload.positions, target, report)
        uvs = payload.uvs
        # Only a face with no entry of its own: one carrying a verbatim swatch
        # entry already names the cell it reads, and overwriting that would
        # collapse a mesh's several palettes onto one texel.
        plain = payload.textures == -1
        if plain.any() and pack is not None and target is not None:
            cell = neutral_swatch_cell(model_data, target, pack)
            if cell is not None:
                uvs = np.array(uvs, copy=True)
                uvs[plain] = np.array(cell, dtype=uvs.dtype)
        # The arrays go through whether or not any face names a real slot. A
        # mesh with none still indexes UVs: an untextured face reads one texel
        # of the pack's swatch texture through the palette it names (§6.2), and
        # all 1032 of the archive's fully-untextured meshes carry a UV block for
        # it -- 887 of them exactly one entry per triangle. Handing `None` over
        # for those, as this did, wrote `uv_index = 0` for every face and one
        # palette for the whole mesh: measured over the corpus, 604 of the 862
        # fully-untextured meshes came back painting something else, while every
        # position matched to the unit. Mixed meshes were already right (803 of
        # 813), which is why nothing until now had cause to notice.
        staged[index] = MW.NewMesh(
            positions=np.clip(np.round(payload.positions), -32768, 32767)
            .astype(np.int16),
            colours=payload.colours,
            textures=payload.textures,
            uvs=uvs,
            corner_vertices=payload.corner_vertices,
            blend=payload.blend,
            normals=payload.normals,
        )
    # Refuse before anything is written. Each of these is something the source
    # asks for and the file cannot give, and every one of them used to be
    # absorbed quietly -- a UV clamped to the slot's edge, a painted material
    # falling through to flat, a model with no colour at all. None shows up in
    # the file afterwards; they show up on the console, which is a poor place
    # to find them.
    if request.problems:
        raise ValueError(
            "the source asks for things this model cannot give, so nothing was "
            "staged:\n  - " + "\n  - ".join(request.problems)
        )
    # One pass for all of them: a per-mesh call would append the shared tables
    # and the vector pool once each time, and those copies are unreachable
    # afterwards -- 70% of the file on a nine-mesh import (§ mdlwrite).
    plans: dict[int, np.ndarray] = {}
    if staged:
        trimmed = MW.install_meshes(trimmed, staged, pin_tables=pin_tables,
                                    notes=report.warnings, plans=plans)
    grown = trimmed
    rebuilt_model = read_model(grown)

    # --- animation -----------------------------------------------------
    specs = []
    for clip in clips:
        keys = clip.keyframes()
        at = {k: i for i, k in enumerate(keys)}
        original_frames = [
            AW.FrameSpec(at[f.key_a], at[f.key_b] if f.key_b else None,
                         f.weight, clip.aux_block(f.index))
            for f in clip.frames
        ]
        # Every keyframe of a static clip holds the same pose, so there is
        # nothing between them to interpolate -- and a frame that asks for the
        # blend anyway is the one that misbehaves. A model imported with its
        # clips frozen drew correctly standing still and stretched into threads
        # while walking, which is exactly the difference between the copy
        # decoder a weight of 0 selects and the GTE INTPL blend the rest take.
        # Every check this project can make said the file was sound: 451 frames
        # all decoding to the static pose to the unit, keyframe flags agreeing
        # with the mesh 968/968, the pool and the stride consistent. Why the
        # blend of a pose with itself does not come back as that pose is not
        # established here and is not guessed at; what is established is that
        # frames which never ask for it draw correctly.
        # One keyframe, and every frame sitting on it. Identical poses stored
        # once per key are the same bytes over and over, and the pose pool is
        # what a model weighs: freezing `chars/crate/coco` a key at a time gave
        # 328,112 bytes against the shipped 215,678, where one key gives 99,084.
        # That is not only waste. The model drew correctly in the crate game and
        # trailed threads across the warp room, which loads far more at once --
        # the hub is where the budget is tight, and the smaller file ran there.
        resting_frames = [
            AW.FrameSpec(0, None, 0, clip.aux_block(f.index))
            for f in clip.frames
        ]
        target_mesh = clip.mesh_index
        incoming = request.clips.get(clip.label)
        if incoming is not None and incoming.mesh_index != target_mesh:
            incoming = None
        # A mesh whose geometry came back untouched can still have been
        # re-animated -- that is the whole of an animation-only edit -- so the
        # clip is copied only when the source has nothing new to say about it.
        if target_mesh not in report.meshes_rebuilt and (
                incoming is None or incoming.unchanged):
            specs.append(AW.ClipSpec(
                poses=[clip.pool()[clip._slots(k)].astype(np.int16) for k in keys],
                frames=original_frames, name_hash=clip.name_hash,
                mesh_header=clip.mesh_header_offset,
                vertex_flags=(clip._entries(keys[0]) & 3).astype(np.uint16)))
            report.clips_copied.append(clip.label)
            continue

        built = rebuilt_model.meshes[target_mesh]
        rest = np.asarray(built.positions, dtype=np.float64) / GTE_SCALE_SMALL
        flags = np.asarray(built.vertex_flags, dtype=np.uint16) & 3
        header = MW.MESH_HEADER_START + MW.MESH_HEADER_SIZE * target_mesh
        rest_i16 = np.clip(np.round(rest), -32768, 32767).astype(np.int16)

        source = request.meshes.get(target_mesh)
        if (incoming is None or not incoming.poses or source is None
                or source.vertices is None):
            specs.append(AW.ClipSpec(
                poses=[rest_i16], frames=resting_frames,
                name_hash=clip.name_hash, mesh_header=header, vertex_flags=flags))
            report.clips_static.append(clip.label)
            continue

        # The poses arrive in the source's vertex order; the pool is in strip
        # order. When the writer laid this pool out in this same call it said
        # which corner of which triangle each entry came from, and the source
        # said which of its vertices that corner is -- so the map is exact and
        # nothing is inferred. Position matching cannot do that: two vertices
        # that sit together at rest and are driven apart by a clip are one
        # point to a nearest-neighbour search, and merging them moved 38
        # animated triangles of `cutscene/level_shot12` with every static check
        # passing.
        plan = plans.get(target_mesh)
        if plan is not None and source.corner_vertices is not None \
                and len(plan) == len(rest):
            nearest = source.corner_vertices[plan[:, 0], plan[:, 1]]
        else:
            distance = np.linalg.norm(
                rest[:, None, :] - source.vertices[None, :, :], axis=2)
            nearest = distance.argmin(axis=1)
            worst = float(distance[np.arange(len(rest)), nearest].max())
            if worst > MATCH_TOLERANCE:
                raise ValueError(
                    f"clip {clip.label}: a pool vertex sits {worst:.2f} units "
                    f"from the nearest source vertex; the source no longer "
                    f"matches the mesh")
        poses = [
            np.clip(np.round(np.asarray(pose, dtype=np.float64)[nearest]),
                    -32768, 32767).astype(np.int16)
            for pose in incoming.poses
        ]
        specs.append(AW.ClipSpec(poses=poses, frames=incoming.frames,
                                 name_hash=clip.name_hash, mesh_header=header,
                                 vertex_flags=flags))
        report.clips_rebuilt.append(clip.label)

    if not staged and len(report.clips_copied) == len(clips):
        # Nothing was installed and no clip changed, so the animation region is
        # left exactly where it is. Stripping and rewriting it is not free even
        # when every blob goes back with the same contents: `chars/crate/coco`
        # comes back four bytes short and `mainmenu/models` grows 276,712 bytes
        # to 343,660, a quarter again, for an edit that changed nothing. And
        # the hub is where the memory budget bites, so a file that grows for
        # nothing is a file that may not load.
        report.model = model_data
    else:
        report.model = AW.write_clips(grown, specs, reclaim=False)

    # --- textures ------------------------------------------------------
    if pack is not None and pack_data is not None and request.images:
        outside = {
            t.palette_index
            for t in pack.textures
            if not t.is_swatch and t.index not in request.slots
        }
        exclusive = {
            pack.textures[s].palette_index
            for s in request.slots
            if 0 <= s < len(pack.textures)
            and pack.textures[s].palette_index not in outside
        }
        patched = pack_data
        for slot, image in sorted(request.images.items()):
            if not 0 <= slot < len(pack.textures):
                continue
            patched = write_slot(patched, pack, slot, image, exclusive, report)
        report.pack = patched if patched != pack_data else None

    return report
