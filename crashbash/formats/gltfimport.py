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
from dataclasses import dataclass, field

import numpy as np

from ..binreader import GTE_SCALE_SMALL
from . import animwrite as AW
from . import mdlwrite as MW
from . import scenewrite as SW
from . import texwrite as TW
from .anim import WEIGHT_ONE, read_animations
from .gltf import AXIS_FLIP, FRAMES_PER_SECOND
from .gltfread import Glb, read_glb
from .mdl import Model, read_model
from .tex import TexturePack, read_pack

MESH_NAME = re.compile(r"_mesh(\d+)$")
MATERIAL_SLOT = re.compile(r"^tex_(\d+)_")

# How far a keyframe vertex may sit from its rest match, in model units. The
# pool is built from the same corner data the targets index, so anything beyond
# rounding means the file does not belong to this mesh.
MATCH_TOLERANCE = 0.51


@dataclass
class Report:
    meshes_rebuilt: list[int] = field(default_factory=list)
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


def _orient_consistently(positions, colours, uvs, textures):
    """Give every triangle the same orientation as its neighbours.

    The exporter emits corners in strip-presentation order, which alternates
    winding triangle by triangle; the strip builder needs the authored,
    consistent orientation or it finds no directed edges to chain and falls
    back to one strip per triangle -- a shape the game never ships. Flipping a
    triangle reverses its corners and its colour and UV rows with them, since
    those are positional.

    Orientation spreads by flood fill over shared edges; each connected
    component is then signed so its faces point outward (the corpus
    convention), measured as the sum of cross products against the component's
    own centre. Flat components -- sprites -- have no outward and are left as
    they arrive.
    """
    quantised = np.round(positions.reshape(-1, 3)).astype(np.int64)
    _, welded = np.unique(quantised, axis=0, return_inverse=True)
    welded = welded.reshape(-1, 3)
    faces = welded.shape[0]

    by_edge: dict[tuple[int, int], list[int]] = {}
    for face in range(faces):
        a, b, c = (int(v) for v in welded[face])
        for u, v in ((a, b), (b, c), (c, a)):
            by_edge.setdefault((min(u, v), max(u, v)), []).append(face)

    flip = np.zeros(faces, dtype=bool)
    seen = np.zeros(faces, dtype=bool)
    component = np.full(faces, -1, dtype=np.int64)
    for seed in range(faces):
        if seen[seed]:
            continue
        stack = [seed]
        seen[seed] = True
        component[seed] = seed
        while stack:
            face = stack.pop()
            ids = [int(v) for v in welded[face]]
            if flip[face]:
                ids = ids[::-1]
            edges = {(ids[0], ids[1]), (ids[1], ids[2]), (ids[2], ids[0])}
            for u, v in edges:
                for other in by_edge.get((min(u, v), max(u, v)), ()):
                    if seen[other]:
                        continue
                    other_ids = [int(x) for x in welded[other]]
                    other_edges = {(other_ids[0], other_ids[1]),
                                   (other_ids[1], other_ids[2]),
                                   (other_ids[2], other_ids[0])}
                    # Consistent neighbours traverse a shared edge in opposite
                    # directions; seeing it the same way round means a flip.
                    flip[other] = (u, v) in other_edges
                    seen[other] = True
                    component[other] = seed
                    stack.append(other)

    for face in np.flatnonzero(flip):
        positions[face] = positions[face, ::-1]
        colours[face] = colours[face, ::-1]
        uvs[face] = uvs[face, ::-1]

    for seed in np.unique(component):
        member = component == seed
        tri = positions[member]
        centre = tri.reshape(-1, 3).mean(axis=0)
        normals = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
        outward = float((normals * (tri.mean(axis=1) - centre)).sum())
        if abs(outward) < 1e-6:
            continue
        if outward < 0:
            positions[member] = positions[member][:, ::-1]
            colours[member] = colours[member][:, ::-1]
            uvs[member] = uvs[member][:, ::-1]
    return positions, colours, uvs, textures


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
) -> Report:
    """Rebuild `model_data`'s meshes and clips from the glTF file at `path`.

    `other_pack_users` maps texture slot -> the set of *other* meshes sampling
    it, and palette slot uses are derived from the pack itself; a palette named
    by any texture outside the imported slots is treated as shared.
    """
    report = Report()
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
    for index, mesh in sorted(incoming.items()):
        positions, colours, uvs, textures, bases = _mesh_payload(
            glb, mesh, slot_of, report.warnings
        )
        payloads[index] = (mesh, bases)
        positions, colours, uvs, textures = _orient_consistently(
            positions, colours, uvs, textures
        )
        new_mesh = MW.NewMesh(
            positions=np.clip(np.round(positions), -32768, 32767).astype(np.int16),
            colours=colours,
            textures=textures if (textures >= 0).any() else None,
            uvs=uvs if (textures >= 0).any() else None,
        )
        trimmed = MW.install_mesh(trimmed, index, new_mesh)
        report.meshes_rebuilt.append(index)
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
        if target_mesh not in report.meshes_rebuilt:
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

        animation = animations_by_name.get(clip.label)
        drives_this = animation is not None and node_mesh.get(
            animation["channels"][0]["target"].get("node"), target_mesh
        ) == target_mesh
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
