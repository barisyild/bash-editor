"""Import a model back from glTF: geometry, textures and animation in one step.

The return half of `gltf.export_glb`, built for the round trip: export an
entry, reshape it in a modelling tool, and bring the result back. Everything is
matched by the names the exporter wrote -- meshes as `<name>_meshNN`, materials
as `tex_NNN_...` carrying the pack slot in their name, animations as the clip's
label -- so the file can be edited freely as long as those names survive.

This module is the glTF *front end* only: it reads the file and states what it
found in the terms `modelimport` works in, and that module does the surgery. The
split is what lets a second front end -- the Blender add-on, which never writes
a `.glb` at all -- enforce exactly the same rules rather than re-learning them.

What comes back is a pair of new entry payloads: the model with the imported
meshes rebuilt (geometry re-striped, clips rewritten) and, when any material's
image was repainted, the texture pack with those slots' pixels replaced. The
rules each writer enforces are documented in docs/IMPORTING.md.

Scope, stated rather than discovered: a mesh absent from the file keeps its
game geometry and clips; a clip absent from the file, on a mesh that was
rebuilt, falls back to its rest pose rather than guessing; a palette shared
with textures outside the import is never rewritten -- repainted pixels are
matched to the existing colours instead.
"""

from __future__ import annotations

import re

import numpy as np

from ..binreader import GTE_SCALE_SMALL
from . import modelimport as MI
from .anim import read_animations
from . import gltf
from .gltf import AXIS_FLIP, FRAMES_PER_SECOND
from .gltfread import Glb, parse_glb, read_glb
from .mdl import Model, read_model
from .modelimport import Report
from .tex import TexturePack, read_pack

# The trailing `.001` is Blender's: it suffixes any name that collides with one
# already in the file, which happens the moment an artist rebuilds a mesh beside
# the one it replaces. Anchored without it, not one mesh in the file matched and
# the import refused a model that was otherwise ready.
DUPLICATE = r"(?:\.\d{3})?$"
MESH_NAME = re.compile(r"_mesh(\d+)" + DUPLICATE)
# The exporter names an object-pool mesh after the id the game reaches it by,
# because it has no slot in the numbered array. It still has a mesh index, and
# in a level it is the only kind of mesh that is drawn.
OBJECT_NAME = re.compile(r"_object([0-9A-Fa-f]{4})" + DUPLICATE)
MATERIAL_SLOT = re.compile(r"^tex_(\d+)_")


def _mesh_index_for(name: str, model: Model) -> int | None:
    """The model mesh a glTF mesh's name stands for, plain or object-pool."""
    match = MESH_NAME.search(name)
    if match:
        index = int(match.group(1))
        return index if 0 <= index < len(model.meshes) else None
    match = OBJECT_NAME.search(name)
    if not match:
        return None
    wanted = int(match.group(1), 16)
    for obj in model.objects:
        if obj.id == wanted and obj.mesh is not None:
            return obj.mesh.index
    return None

def _accessor_or_none(glb: Glb, primitive: dict, name: str):
    index = primitive.get("attributes", {}).get(name)
    return None if index is None else glb.accessor(index)


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
        index = _mesh_index_for(mesh_json.get("name", ""), model)
        if index is None:
            continue
        try:
            positions, colours, uvs, textures, _ = _mesh_payload(
                reference, mesh_json, own_slots, None)
        except Exception:
            continue
        bags[index] = MI.face_bag(positions, colours, uvs, textures)
    return bags


def _mesh_payload(glb: Glb, mesh: dict, slot_of: dict[int, int | None],
                  warnings: list[str] | None = None,
                  problems: list[str] | None = None):
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
            said = list(warnings or []) + list(problems or [])
            if (colour is not None and (warnings is not None or problems is not None)
                    and not any("_CRASHBASH_COLOR" in w for w in said)):
                # Blender writes COLOR_0 flat white unless the material's node
                # tree actually reads the colour attribute -- it says so in its
                # own log and exports a placeholder. Left alone that imports a
                # model which draws at luminance 248 against the shipped Crash's
                # 64: white, not dim. Worth separating from the case the second
                # message describes, since the two need different fixes.
                flat = np.asarray(colour, dtype=np.float64)[:, :3]
                if flat.size and float(flat.min()) >= 0.999:
                    (problems if problems is not None else warnings).append(
                        "the file carries no vertex colour: COLOR_0 is flat "
                        "white and _CRASHBASH_COLOR is absent, so every face "
                        "would draw at full brightness. Blender only exports a "
                        "colour attribute when the material reads it -- wire a "
                        "Color Attribute node into the shader, or tick "
                        "Data > Attributes so _CRASHBASH_COLOR travels"
                    )
                else:
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
            raw_u = np.round(texel[..., 0] * width - 0.5)
            raw_v = np.round(texel[..., 1] * height - 0.5)
            # A slot cannot be resized (§10.1), so a UV past its edge is not a
            # near miss to be clamped: on hardware the triangle samples whatever
            # shares its page, which is how the other Coco's eyes came back
            # blank. Clamping hid it; the caller is told instead.
            outside = int(((raw_u < 0) | (raw_u >= width)
                           | (raw_v < 0) | (raw_v >= height)).any(axis=1).sum())
            if outside and problems is not None:
                problems.append(
                    f"slot {slot} is {width}x{height} and {outside} of the "
                    f"{len(corners)} triangles aimed at it have a corner "
                    f"outside it (UVs run 0..{width - 1} and 0..{height - 1}); "
                    f"a slot cannot be resized, so the UVs have to fit it"
                )
            u = np.clip(raw_u, 0, width - 1)
            v = np.clip(raw_v, 0, height - 1)
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
    return MI.frames_from_weights(resampled, frame_total)


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
    request = MI.ImportRequest(
        # The shot rides in `extras`. `import_payload` writes it before anything
        # resizes the file, which is what §2.1 requires.
        scene=(glb := read_glb(path)).json.get("extras", {}).get("crashbash"),
        empty_error=(
            "no mesh in the file is named like the exporter names them "
            "(<model>_meshNN), and there was no scene to write either; "
            "nothing to import"),
        empty_note=(
            "no mesh is named like the exporter names them (<model>_meshNN); "
            "the scene was written and the geometry left as it was"),
    )
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

    # A material with a picture but no slot in its name is a face the artist
    # painted and the importer would quietly rebuild flat. The name is how a
    # slot is chosen -- there is nowhere else to put the picture -- so say which
    # materials need renaming rather than dropping their texture on the floor.
    painted = [
        material.get("name", f"material {index}")
        for index, material in enumerate(glb.json.get("materials", []))
        if material.get("pbrMetallicRoughness", {}).get("baseColorTexture") is not None
        and not MATERIAL_SLOT.match(material.get("name", ""))
    ]
    if painted:
        raise ValueError(
            f"{len(painted)} material(s) carry a texture but do not name a "
            f"pack slot, so their faces would be rebuilt flat: "
            f"{', '.join(painted[:6])}"
            + (" ..." if len(painted) > 6 else "")
            + ". Name a material `tex_NNN_WxH_4bpp` for the slot it should "
              "replace -- the slot decides the size, and its picture is taken "
              "from the material."
        )

    # --- which glTF mesh replaces which model mesh ---------------------
    incoming: dict[int, dict] = {}
    for mesh in glb.json.get("meshes", []):
        index = _mesh_index_for(mesh.get("name", ""), model)
        if index is not None:
            incoming[index] = mesh
    # `rebuild_all` puts every mesh through the writer even when the file did
    # not change it. Nothing in the app wants that -- it costs colour entries
    # for nothing -- but the verification tools do: with untouched meshes left
    # alone, a round trip of the shipped corpus rebuilds nothing and compares
    # nothing, which is a check that passes by doing no work.
    reference = {} if rebuild_all else _reference_bags(
        model_data, model, pack, slot_of, request.warnings)
    bases: dict[int, np.ndarray] = {}
    for index, mesh in sorted(incoming.items()):
        found = len(request.problems)
        positions, colours, uvs, textures, base = _mesh_payload(
            glb, mesh, slot_of, request.warnings, request.problems
        )
        # Name the mesh each problem belongs to, since a file holds many.
        for i in range(found, len(request.problems)):
            request.problems[i] = f"mesh {index}: {request.problems[i]}"
        bases[index] = np.concatenate(base)
        request.meshes[index] = MI.MeshPayload(
            positions=positions, colours=colours, uvs=uvs, textures=textures,
            vertices=bases[index] / AXIS_FLIP / GTE_SCALE_SMALL,
        )

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

    for clip in clips:
        target_mesh = clip.mesh_index
        animation = animations_by_name.get(clip.label)
        # An animation drives a clip only when the node it targets is the clip's
        # own mesh; a file may carry several and they are told apart by that.
        if animation is None or node_mesh.get(
            animation["channels"][0]["target"].get("node"), target_mesh
        ) != target_mesh or target_mesh not in bases:
            continue
        deltas = []
        for primitive in incoming[target_mesh]["primitives"]:
            deltas.append([glb.accessor(t["POSITION"])
                           for t in primitive.get("targets", [])])
        if not deltas or not deltas[0]:
            continue
        used, frames = _timeline(glb, animation, clip.frame_count)
        if not used:
            continue
        # Absolute keyframe poses in glTF vertex order. Putting them into pool
        # order is `import_payload`'s job, and it does it by matching rest
        # positions -- exact by construction, since the pool was welded from
        # these very corners.
        base = bases[target_mesh]
        request.clips[clip.label] = MI.ClipPayload(
            mesh_index=target_mesh,
            poses=[(base + np.concatenate([d[t] for d in deltas]).astype(np.float64))
                   / AXIS_FLIP / GTE_SCALE_SMALL for t in used],
            frames=frames,
        )

    # --- textures ------------------------------------------------------
    # Every slot the file names, whether or not its picture was repainted: the
    # palette-sharing test is over the slots this import covers, and a slot left
    # alone still stakes its claim on the palette behind it.
    request.slots = {slot for key, slot in slot_of.items() if key != "sizes"}
    for material_index in sorted(k for k in slot_of if k != "sizes"):
        image = _material_image(glb, material_index)
        if image is not None:
            request.images[slot_of[material_index]] = image

    return MI.import_payload(
        model_data, pack_data, request, pin_tables=pin_tables,
        animation_only=animation_only, reference=reference,
        rebuild_all=rebuild_all,
    )
