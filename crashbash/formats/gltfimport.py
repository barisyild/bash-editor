"""Import a model back from glTF: geometry, textures and animation in one step.

The return half of `gltf.export_glb`, built for the round trip: export an
entry, reshape it in a modelling tool, and bring the result back. Everything is
matched by the names the exporter wrote -- meshes as `<name>_meshNN`, materials
as `tex_NNN_...` carrying the pack slot in their name, animations as the clip's
label -- so the file can be edited freely as long as those names survive.

What it produces is a pair of new entry payloads: the model with the imported
meshes rebuilt (geometry re-striped, clips rewritten) and, when any material's
image was repainted, the texture pack with those slots' pixels replaced. The
rules each writer enforces are documented in docs/IMPORTING.md; this module
only orchestrates them.

Scope, stated rather than discovered: a mesh absent from the file keeps its
game geometry and clips; a clip absent from the file, on a mesh that was
rebuilt, falls back to its rest pose rather than guessing; a palette shared
with textures outside the import is never rewritten -- repainted pixels are
matched to the existing colours instead.
"""

from __future__ import annotations

import re
import struct
from collections import Counter
from dataclasses import dataclass, field

import numpy as np

from ..binreader import GTE_SCALE_SMALL
from . import animwrite as AW
from . import mdlwrite as MW
from . import scenewrite as SW
from . import texwrite as TW
from .anim import WEIGHT_ONE, read_animations
from . import gltf
from .gltf import AXIS_FLIP, FRAMES_PER_SECOND
from .gltfread import Glb, parse_glb, read_glb
from .mdl import Model, read_model
from .tex import TexturePack, read_pack

MESH_NAME = re.compile(r"_mesh(\d+)$")
MATERIAL_SLOT = re.compile(r"^tex_(\d+)_")

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
    warnings: list[str] = field(default_factory=list)
    scene: SW.Patched | None = None
    model: bytes = b""
    pack: bytes | None = None


def _accessor_or_none(glb: Glb, primitive: dict, name: str):
    index = primitive.get("attributes", {}).get(name)
    return None if index is None else glb.accessor(index)


def _face_key(points, colours, uvs, texture):
    """One triangle, canonical under rotation but not under reversal.

    Rotating a triangle's corners leaves it the same triangle; reversing them
    turns it inside out (§11.3), so the two must not compare equal.
    """
    start = min(range(3), key=lambda k: points[k])
    order = [(start + k) % 3 for k in range(3)]
    return (tuple(points[k] for k in order), tuple(colours[k] for k in order),
            tuple(uvs[k] for k in order), texture)


def _payload_bag(positions, colours, uvs, textures) -> Counter:
    """A mesh's triangles as a multiset, order-independent.

    The exporter groups a mesh's triangles by texture, so a round trip brings
    them back grouped rather than in the strip order the file stores; only the
    set of triangles can be compared, not their order.
    """
    bag = Counter()
    rounded = np.clip(np.round(positions), -32768, 32767).astype(np.int64)
    for f in range(positions.shape[0]):
        bag[_face_key(
            [tuple(int(v) for v in rounded[f, k]) for k in range(3)],
            [tuple(int(v) for v in colours[f, k]) for k in range(3)],
            [tuple(int(v) for v in uvs[f, k]) for k in range(3)]
            if uvs is not None else [(0, 0)] * 3,
            int(textures[f]) if textures is not None else -1,
        )] += 1
    return bag


def _reference_bags(model_data: bytes, model, pack, slot_of, warnings):
    """What this importer reads back from the exporter's own output, per mesh.

    The comparison has to be like for like. The exporter folds a swatch texel
    into the vertex colour and writes swatch cell UVs, so an incoming mesh
    never matches the model's stored arrays even when nothing was edited --
    measured over `mainmenu/models`, positions agree on 6031 of 6031 triangles
    while the stored UVs agree on 2015. Exporting the shipped model here and
    reading it back through the same path gives the arrays an untouched mesh
    must equal.
    """
    try:
        blob = gltf.export_glb(model, pack, [], name="reference")
        reference = parse_glb(blob, "<reference>")
        # Its own material map, not the incoming file's: a material index means
        # whatever the file it came from says it means. Reusing the caller's map
        # read every mesh through the wrong slots the moment an edit changed
        # which materials the file carries, and eight meshes nobody had touched
        # were rebuilt for it.
        own_slots = _material_slots(reference, pack)
    except Exception:
        return {}
    bags = {}
    for mesh_json in reference.json.get("meshes", []):
        match = MESH_NAME.search(mesh_json.get("name", ""))
        if not match:
            continue
        try:
            positions, colours, uvs, textures, _ = _mesh_payload(
                reference, mesh_json, own_slots, None)
        except Exception:
            continue
        bags[int(match.group(1))] = _payload_bag(positions, colours, uvs, textures)
    return bags


def _mesh_payload(glb: Glb, mesh: dict, slot_of: dict[int, int | None],
                  warnings: list[str] | None = None):
    """One glTF mesh -> per-corner arrays in the writer's own terms."""
    positions, colours, uvs, textures = [], [], [], []
    base_vertices = []
    for primitive in mesh["primitives"]:
        pos = glb.accessor(primitive["attributes"]["POSITION"]).astype(np.float64)
        indices = glb.accessor(primitive["indices"]).astype(np.int64) \
            if "indices" in primitive else np.arange(len(pos))
        corners = indices.reshape(-1, 3)
        base_vertices.append(pos)

        # The application attribute first: it is the one channel that carries
        # the game's full 0..2 multiplier through Blender untouched. COLOR_0
        # is the fallback -- correct from this exporter, but clamped at 1 by
        # Blender's importer, so a file that has been through Blender without
        # "Attributes" ticked on export comes back with 128..255 crushed.
        colour = _accessor_or_none(glb, primitive, "_CRASHBASH_COLOR")
        legacy_scale = False
        if colour is None:
            colour = _accessor_or_none(glb, primitive, "COLOR_0")
            legacy_scale = colour is not None
            if (colour is not None and warnings is not None
                    and not any("_CRASHBASH_COLOR" in w for w in warnings)):
                warnings.append(
                    "_CRASHBASH_COLOR is missing, so colours above 128 may "
                    "have been dimmed in transit; tick Data > Attributes "
                    "in Blender's glTF export to carry them through"
                )
        uv = _accessor_or_none(glb, primitive, "TEXCOORD_0")
        slot = slot_of.get(primitive.get("material", -1))

        # glTF -> model units: undo the exporter's axis flip and fixed point.
        model_pos = pos / AXIS_FLIP / GTE_SCALE_SMALL
        positions.append(model_pos[corners])

        if colour is not None:
            rgb = np.asarray(colour, dtype=np.float64)[:, :3]
            # Invert the exporter's scale, which follows the material: a
            # textured corner holds the multiplier colour / 127.5, an
            # untextured one holds the pixel colour / 255 -- the console
            # draws untextured polygons with the colour directly. Rounding,
            # not truncating: the float32 accessor sits a hair off the
            # ratio. Files from before the split (no _CRASHBASH_COLOR)
            # used the multiplier scale everywhere.
            scale = 127.5 if (slot is not None or legacy_scale) else 255.0
            corner_rgb = np.clip(np.round(rgb[corners] * scale), 0, 255)
        else:
            corner_rgb = np.full((len(corners), 3, 3), 128.0)
        colours.append(corner_rgb)

        if slot is not None and uv is not None:
            texel = np.asarray(uv, dtype=np.float64)[corners]
            width, height = slot_of["sizes"][slot]
            u = np.clip(np.round(texel[..., 0] * width - 0.5), 0, width - 1)
            v = np.clip(np.round(texel[..., 1] * height - 0.5), 0, height - 1)
            uvs.append(np.stack([u, v], axis=-1))
            textures.append(np.full(len(corners), slot, dtype=np.int64))
        else:
            uvs.append(np.zeros((len(corners), 3, 2)))
            textures.append(np.full(len(corners), -1, dtype=np.int64))

    return (
        np.concatenate(positions),
        np.concatenate(colours).astype(np.uint8),
        np.concatenate(uvs).astype(np.uint8),
        np.concatenate(textures),
        base_vertices,
    )


def _material_slots(glb: Glb, pack: TexturePack | None) -> dict:
    """Material index -> pack slot, from the names the exporter wrote."""
    slots: dict = {"sizes": {}}
    for index, material in enumerate(glb.json.get("materials", [])):
        match = MATERIAL_SLOT.match(material.get("name", ""))
        if not match:
            continue
        slot = int(match.group(1))
        if pack is None or not 0 <= slot < len(pack.textures):
            continue
        if pack.textures[slot].is_swatch:
            continue  # swatch colour was folded into COLOR_0 on the way out
        slots[index] = slot
        slots["sizes"][slot] = (pack.textures[slot].width,
                                pack.textures[slot].height)
    return slots


def _material_image(glb: Glb, material_index: int) -> np.ndarray | None:
    """The material's base-colour image as RGBA, decoded from the embedded PNG."""
    from io import BytesIO  # noqa: PLC0415
    from PIL import Image  # noqa: PLC0415

    material = glb.json["materials"][material_index]
    texture = material.get("pbrMetallicRoughness", {}).get("baseColorTexture")
    if texture is None:
        return None
    source = glb.json["textures"][texture["index"]]["source"]
    view = glb.json["bufferViews"][glb.json["images"][source]["bufferView"]]
    start = view.get("byteOffset", 0)
    blob = glb.binary[start : start + view["byteLength"]]
    return np.array(Image.open(BytesIO(blob)).convert("RGBA"))


def _write_slot(pack_data: bytes, pack: TexturePack, slot: int,
                image: np.ndarray, exclusive: set[int],
                report: Report) -> bytes:
    """Put a repainted image back into its slot, honouring palette sharing."""
    from PIL import Image  # noqa: PLC0415

    entry = pack.textures[slot]
    current = entry.to_rgba(pack.palettes)
    if image.shape[:2] != current.shape[:2]:
        image = np.array(
            Image.fromarray(image, "RGBA").resize(
                (current.shape[1], current.shape[0]), Image.LANCZOS
            )
        )
    if np.array_equal(image[..., :3], current[..., :3]):
        report.textures_unchanged.append(slot)
        return pack_data

    rgb = image[..., :3]
    colours = 1 << entry.bit_depth
    if entry.palette_index in exclusive:
        # The palette belongs to this import alone: requantise it outright.
        pil = Image.fromarray(rgb, "RGB").quantize(
            colors=colours, method=Image.MEDIANCUT, dither=Image.NONE
        )
        palette = np.zeros((colours, 3), dtype=np.uint8)
        raw = np.array(pil.getpalette() or [], dtype=np.uint8).reshape(-1, 3)
        palette[: min(len(raw), colours)] = raw[:colours]
        indices = np.array(pil, dtype=np.uint8)
        r, g, b = (palette.astype(np.uint16) >> 3).T
        values = (b << 10) | (g << 5) | r
        values = np.where(values == 0, 0x8000, values)  # keep true black opaque
        pack_data = TW.replace_palette(pack_data, entry.palette_index, values)
    else:
        # Shared palette: keep it, map every pixel to its nearest colour.
        report.palettes_shared.append(slot)
        palette = pack.palettes[entry.palette_index][:, :3].astype(np.int32)
        flat = rgb.reshape(-1, 3).astype(np.int32)
        nearest = np.argmin(
            ((flat[:, None, :] - palette[None, :, :]) ** 2).sum(axis=2), axis=1
        )
        indices = nearest.reshape(rgb.shape[:2]).astype(np.uint8)

    pack_data = TW.replace_pixels(
        pack_data, slot, TW._pack_indices(indices, entry.bit_depth)
    )
    report.textures_written.append(slot)
    return pack_data


def _timeline(glb: Glb, animation: dict, frame_total: int):
    """Resample the weights channel onto the game's 30 Hz tick grid."""
    sampler = animation["samplers"][animation["channels"][0]["sampler"]]
    times = np.asarray(glb.accessor(sampler["input"]), dtype=np.float64).reshape(-1)
    weights = np.asarray(glb.accessor(sampler["output"]), dtype=np.float64)
    targets = weights.size // times.size
    weights = weights.reshape(times.size, targets)

    span = float(times[-1] - times[0])
    count = max(int(round(span * FRAMES_PER_SECOND)) + 1, 1)
    ticks = times[0] + np.arange(count) / FRAMES_PER_SECOND
    resampled = np.stack(
        [np.interp(ticks, times, weights[:, t]) for t in range(targets)], axis=1
    )

    used = sorted(int(t) for t in np.flatnonzero(resampled.max(axis=0) > 1e-6))
    slot_of = {t: i for i, t in enumerate(used)}
    frames = []
    for row in resampled:
        order = np.argsort(row)[::-1]
        first = int(order[0])
        second = int(order[1]) if targets > 1 else None
        w_first = float(row[first])
        w_second = float(row[second]) if second is not None else 0.0
        if w_first <= 0 or first not in slot_of:
            frames.append(AW.FrameSpec(0, None, 0))
            continue
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


def import_glb(
    path,
    model_data: bytes,
    pack_data: bytes | None,
    other_pack_users: dict[int, set[int]] | None = None,
    pin_tables: bool | None = None,
    animation_only: bool = False,
    rebuild_all: bool = False,
) -> Report:
    """Rebuild `model_data`'s meshes and clips from the glTF file at `path`.

    `other_pack_users` maps texture slot -> the set of *other* meshes sampling
    it, and palette slot uses are derived from the pack itself; a palette named
    by any texture outside the imported slots is treated as shared.

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
    glb = read_glb(path)

    # The shot rides in `extras`, and it goes back first. Every offset there was
    # recorded against the file as exported, so it has to be written before
    # `install_mesh` moves the layout boundary (§2.1) -- and because the patch
    # resizes nothing, the rebuild below runs on it exactly as it would have run
    # on the original bytes.
    extras = (glb.json.get("extras") or {}).get("crashbash")
    if extras:
        model_data, patched = SW.patch_scene(model_data, extras)
        report.scene = patched
        report.warnings.extend(patched.skipped)

    model = read_model(model_data)
    clips = read_animations(model_data, model)
    pack = read_pack(pack_data) if pack_data is not None else None
    slot_of = _material_slots(glb, pack)

    # Without a pack no material can resolve to a slot, so every mesh would be
    # rebuilt untextured -- silently, and the result looks like a texture bug in
    # the game rather than a missing argument here. It shipped one broken disc:
    # four models came back flat-shaded because the pack was left out of the
    # call. If the file names slots, the pack is not optional.
    named = sum(1 for material in glb.json.get("materials", [])
                if MATERIAL_SLOT.match(material.get("name", "")))
    if named and not any(isinstance(k, int) for k in slot_of):
        raise ValueError(
            f"{named} of the file's materials name a texture slot, but none "
            f"resolved"
            + (" because no texture pack was given; pass the model's sibling "
               ".tex so the slots can be matched" if pack is None else
               " against the pack that was given, so the two do not belong "
               "together")
            + ". Importing anyway would rebuild every mesh untextured."
        )

    # --- which glTF mesh replaces which model mesh ---------------------
    incoming: dict[int, dict] = {}
    for mesh in glb.json.get("meshes", []):
        match = MESH_NAME.search(mesh.get("name", ""))
        if match and 0 <= int(match.group(1)) < len(model.meshes):
            incoming[int(match.group(1))] = mesh
    if not incoming:
        # A scene patch is already done and valid at this point, and five
        # arenas reach here every time: they have no numbered meshes at all,
        # only object-pool ones (§8.3), which the export does not name
        # `_meshNN` and the writers cannot install into. Raising would throw
        # away a finished edit to their 56 placement records, so the scene-only
        # result is returned instead -- and only a file that changed nothing is
        # an error.
        if report.scene is None or not report.scene.total:
            raise ValueError(
                "no mesh in the file is named like the exporter names them "
                "(<model>_meshNN), and there was no scene to write either; "
                "nothing to import"
            )
        report.warnings.append(
            "no mesh is named like the exporter names them (<model>_meshNN); "
            "the scene was written and the geometry left as it was")
        report.model = model_data
        return report

    # --- geometry ------------------------------------------------------
    trimmed = MW.strip_animation(model_data, clips)
    payloads = {}
    staged = {}
    original = read_model(model_data)
    # `rebuild_all` puts every mesh through the writer even when the file did
    # not change it. Nothing in the app wants that -- it costs colour entries
    # for nothing -- but the verification tools do: with untouched meshes left
    # alone, a round trip of the shipped corpus rebuilds nothing and compares
    # nothing, which is a check that passes by doing no work.
    reference = {} if rebuild_all else _reference_bags(
        model_data, original, pack, slot_of, report.warnings)
    for index, mesh in sorted(incoming.items()):
        positions, colours, uvs, textures, bases = _mesh_payload(
            glb, mesh, slot_of, report.warnings
        )
        payloads[index] = (mesh, bases)
        if (index in reference
                and _payload_bag(positions, colours, uvs, textures)
                == reference[index]):
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
        # The winding arrives authored -- the exporter emits outward corner
        # order (§11.3) -- so it is taken as it comes. Reorienting it here
        # instead cost facing: rebuilding `mainmenu/models` against the shipped
        # facing scores 6031/6031 triangles with the soup left alone and
        # 5912/6031 with a flood fill imposed on it, and the strip builder does
        # not care either way (1876 strips against 1879, longest mesh 224 both
        # ways, against the 348 no shipped mesh exceeds).
        staged[index] = MW.NewMesh(
            positions=np.clip(np.round(positions), -32768, 32767).astype(np.int16),
            colours=colours,
            textures=textures if (textures >= 0).any() else None,
            uvs=uvs if (textures >= 0).any() else None,
        )
    # One pass for all of them: a per-mesh call would append the shared tables
    # and the vector pool once each time, and those copies are unreachable
    # afterwards -- 70% of the file on a nine-mesh import (§ mdlwrite).
    if staged:
        trimmed = MW.install_meshes(trimmed, staged, pin_tables=pin_tables,
                                    notes=report.warnings)
    grown = trimmed
    rebuilt_model = read_model(grown)

    # --- animation -----------------------------------------------------
    animations_by_name = {
        a.get("name", f"anim_{i}"): a
        for i, a in enumerate(glb.json.get("animations", []))
    }
    node_mesh = {}
    for node in glb.json.get("nodes", []):
        if "mesh" in node:
            match = MESH_NAME.search(node.get("name", ""))
            if match:
                node_mesh[glb.json["nodes"].index(node)] = int(match.group(1))

    specs = []
    for clip in clips:
        keys = clip.keyframes()
        at = {k: i for i, k in enumerate(keys)}
        original_frames = [
            AW.FrameSpec(at[f.key_a], at[f.key_b] if f.key_b else None,
                         f.weight, clip.aux_block(f.index))
            for f in clip.frames
        ]
        target_mesh = clip.mesh_index
        animation = animations_by_name.get(clip.label)
        drives_this = animation is not None and node_mesh.get(
            animation["channels"][0]["target"].get("node"), target_mesh
        ) == target_mesh
        # A mesh whose geometry came back untouched can still have been
        # re-animated -- that is the whole of an animation-only edit -- so the
        # clip is copied only when the file has nothing to say about it either.
        if target_mesh not in report.meshes_rebuilt and not drives_this:
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

        if animation is None or not drives_this:
            rest_i16 = np.clip(np.round(rest), -32768, 32767).astype(np.int16)
            specs.append(AW.ClipSpec(
                poses=[rest_i16.copy() for _ in keys], frames=original_frames,
                name_hash=clip.name_hash, mesh_header=header, vertex_flags=flags))
            report.clips_static.append(clip.label)
            continue

        mesh_json, bases = payloads[target_mesh]
        base = np.concatenate(bases)
        deltas = []
        for primitive in mesh_json["primitives"]:
            deltas.append([glb.accessor(t["POSITION"])
                           for t in primitive.get("targets", [])])
        target_count = len(deltas[0]) if deltas and deltas[0] else 0
        used, frames = _timeline(glb, animation, clip.frame_count)
        if not used or target_count == 0:
            rest_i16 = np.clip(np.round(rest), -32768, 32767).astype(np.int16)
            specs.append(AW.ClipSpec(
                poses=[rest_i16.copy() for _ in keys], frames=original_frames,
                name_hash=clip.name_hash, mesh_header=header, vertex_flags=flags))
            report.clips_static.append(clip.label)
            continue

        # Absolute keyframe poses in glTF vertex order, then in pool order by
        # matching rest positions -- exact by construction, since the pool was
        # welded from these very corners.
        base_model = base / AXIS_FLIP / GTE_SCALE_SMALL
        distance = np.linalg.norm(
            rest[:, None, :] - base_model[None, :, :], axis=2
        )
        nearest = distance.argmin(axis=1)
        worst = float(distance[np.arange(len(rest)), nearest].max())
        if worst > MATCH_TOLERANCE:
            raise ValueError(
                f"clip {clip.label}: a pool vertex sits {worst:.2f} units from "
                "the nearest glTF vertex; the file no longer matches the mesh"
            )
        poses = []
        for t in used:
            delta = np.concatenate([d[t] for d in deltas]).astype(np.float64)
            absolute = (base + delta) / AXIS_FLIP / GTE_SCALE_SMALL
            poses.append(np.clip(np.round(absolute[nearest]), -32768, 32767)
                         .astype(np.int16))
        specs.append(AW.ClipSpec(poses=poses, frames=frames,
                                 name_hash=clip.name_hash, mesh_header=header,
                                 vertex_flags=flags))
        report.clips_rebuilt.append(clip.label)

    report.model = AW.write_clips(grown, specs, reclaim=False)

    # --- textures ------------------------------------------------------
    if pack is not None and pack_data is not None:
        imported_slots = {v for k, v in slot_of.items() if k != "sizes"}
        outside = {
            t.palette_index
            for t in pack.textures
            if not t.is_swatch and t.index not in imported_slots
        }
        exclusive = {
            pack.textures[s].palette_index
            for s in imported_slots
            if pack.textures[s].palette_index not in outside
        }
        patched = pack_data
        for material_index, slot in slot_of.items():
            if material_index == "sizes":
                continue
            image = _material_image(glb, material_index)
            if image is None:
                continue
            patched = _write_slot(patched, pack, slot, image, exclusive, report)
        report.pack = patched if patched != pack_data else None

    return report
