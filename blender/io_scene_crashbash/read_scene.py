"""Read a collection back out of Blender, in the terms the writers work in.

The mirror of `build_scene`. What comes out is an `ImportRequest`, which is the
same thing the glTF front end produces, so from here on the two paths are one
piece of code and one set of rules.

What this refuses is as much the point as what it accepts. Every check below
stands for a way a model has reached the console looking wrong while every
static test passed: a face with no colour drew at full brightness, a UV past
its slot's edge sampled whatever shared the page, a painted material with no
slot silently came back flat. They are collected rather than raised one at a
time, so an artist sees the whole list at once.
"""

from __future__ import annotations

import re

import numpy as np

import bpy

from . import actions, build_scene, naming as N
from .build_scene import SCALE, read_image, to_model

# The shape key naming lives with the half that writes it.
SHAPE_KEY = build_scene.SHAPE_KEY_NAME


def _objects(collection: bpy.types.Collection) -> dict[int, bpy.types.Object]:
    """The collection's meshes, by the model mesh index each one stands for.

    Object-pool meshes count. They are what a level draws (§8.3) and the writer
    can rebuild one -- inside the span it already owns, because the pool is a
    packed run and blocks that leave it black-screen the disc. Whether a
    particular rebuild fits is the writer's to say, with a measurement.

    A placement object shares its mesh data with the object it copies, and only
    one of them should be read: the copies carry no `crashbash_mesh` of their
    own, so they are skipped here and picked up as placements instead.
    """
    found: dict[int, bpy.types.Object] = {}
    for obj in collection.all_objects:
        if obj.type != "MESH" or obj.get(N.PROP_PLACEMENT) is not None:
            continue
        if obj.get(N.PROP_PREVIEW):
            continue  # a baked particle, drawn to be watched and nothing else
        index = obj.get(N.PROP_MESH)
        if index is not None:
            found[int(index)] = obj
    return found


def _placements(collection, model, warnings: list[str]) -> dict[int, dict]:
    """Every placement record the collection moved, renamed or re-aimed.

    Only the ones that actually differ: a record is eleven bytes of a live list
    and rewriting one that did not change is work the file does not need. The
    list cannot be made longer (§8.5), so a new object here is not a new
    placement -- it is nothing, and saying so is better than dropping it.
    """
    edits: dict[int, dict] = {}
    by_index = {i.index: i for i in model.instances}
    for obj in collection.all_objects:
        index = obj.get(N.PROP_PLACEMENT)
        if index is None:
            continue
        instance = by_index.get(int(index))
        if instance is None:
            warnings.append(
                f"{obj.name}: placement {int(index)} is not in this model, "
                f"which has {len(model.instances)}; it was left out")
            continue
        rotation, translation = build_scene.placement_record(obj.matrix_basis)
        identifier = int(obj.get(N.PROP_PLACES, instance.id))
        # Against what the importer's own transform reads back as, not against
        # the file: a quantised rotation is not exactly orthonormal and Blender
        # keeps a transform as location, euler and scale, so recomposing it
        # never gives the file's nine values back. Comparing against the file
        # reported 24 of `warp_room1`'s 81 untouched records as moved, and
        # rewriting a record that did not move is a byte of a live list spent
        # for nothing.
        rest = list(obj.get(N.PROP_PLACE_REST) or []) or (
            list(instance.rotation) + list(instance.translation))
        now = list(rotation) + list(translation)
        still = len(rest) == len(now) and all(
            abs(a - b) <= 1e-5 for a, b in zip(rest, now))
        if still and identifier == instance.id:
            continue
        edits[int(index)] = {"id": identifier, "translation": translation,
                             "rotation": rotation}
    return edits


def _shot(collection, warnings: list[str]) -> dict | None:
    """The shot as it goes back: carried whole, with the emitters as edited.

    Only the emitters have objects in the scene, so only they can have changed.
    Everything else -- the tracks, the camera keys, the sub-scene frames -- goes
    back exactly as the file stated it, which is what makes carrying a shot
    through an edit safe rather than a re-derivation of things Blender has no
    way to say.
    """
    import json  # noqa: PLC0415

    stored = collection.get(N.PROP_SCENE)
    if not stored:
        return None
    try:
        extras = json.loads(stored)
    except ValueError as exc:
        warnings.append(f"the stored shot could not be read back ({exc}), so "
                        f"it was left alone")
        return None

    by_node = {int(e["node"]): e for e in extras.get("emitters") or []}
    for obj in collection.all_objects:
        node = obj.get(N.PROP_EMITTER)
        if node is None:
            continue
        emitter = by_node.get(int(node))
        if emitter is None:
            warnings.append(f"{obj.name}: emitter node {int(node):#x} is not in "
                            f"this shot, so it was left out")
            continue
        for field_name in N.EMITTER_FIELDS:
            value = obj.get(field_name)
            if value is None:
                continue
            emitter[field_name] = (list(value) if hasattr(value, "__len__")
                                   and not isinstance(value, str) else value)
        emitter["position"] = list(to_model([tuple(obj.location)])[0] * SCALE)
    return extras


def _texture_entry(material, sizes, problems, where) -> tuple[int, tuple | None]:
    """What a face wearing this material reads, and the texture its UVs address."""
    if material is None:
        return -1, None
    slot = material.get(N.PROP_SLOT)
    if slot is not None:
        # A slot the pack does not hold is addressed against `UNKNOWN_SIZE`,
        # the same nominal square the importer used, so its texels survive.
        return int(slot), sizes.get(("slot", int(slot)), N.UNKNOWN_SIZE)
    palette = material.get(N.PROP_PALETTE)
    if palette is not None:
        # Negated, which is how `NewMesh` states a verbatim swatch entry: the
        # face names this palette and reads one texel of the pack's swatch
        # image through it (§6.2).
        return -(0x8000 | (int(palette) & 0x1FF)), sizes.get(("swatch",))
    if material.use_nodes and any(
        node.type == "TEX_IMAGE" and node.image is not None
        for node in material.node_tree.nodes
    ):
        problems.append(
            f"{where}: material '{material.name}' carries a picture but names "
            f"no pack slot, so its faces would be rebuilt flat. Give it a "
            f"'{N.PROP_SLOT}' custom property with the slot number, or a "
            f"'{N.PROP_PALETTE}' one if it is a swatch material"
        )
    return -1, None


def _sizes(pack) -> dict[tuple, tuple[int, int]]:
    """Texel dimensions per slot and for the pack's swatch image.

    The swatch texture is listed under its own slot as well as under `swatch`.
    It carries no palette of its own, but it is still a slot a face may name
    directly -- `chars/crate/coco` has 100 triangles whose strip flag says
    textured and whose entry is 18, which is that pack's swatch image. Leaving
    it out of the slot map dropped their UVs to (0, 0) and painted them all
    from one texel.
    """
    found: dict[tuple, tuple[int, int]] = {}
    if pack is None:
        return found
    for texture in pack.textures:
        found[("slot", texture.index)] = (texture.width, texture.height)
        if texture.is_swatch:
            found[("swatch",)] = (texture.width, texture.height)
    return found


def _corner_colours(mesh, triangles, where, problems) -> np.ndarray:
    """One RGB per corner, 0..255, from the colour attribute the importer wrote."""
    attribute = mesh.color_attributes.get(N.COLOUR_ATTRIBUTE)
    if attribute is None:
        attribute = mesh.color_attributes.active_color
    if attribute is None:
        problems.append(
            f"{where}: the mesh carries no colour attribute, so every face "
            f"would draw at full brightness. The importer writes one called "
            f"'{N.COLOUR_ATTRIBUTE}'; add it back, or re-import and edit that"
        )
        return np.full((len(triangles), 3, 3), 128, dtype=np.uint8)

    count = (len(mesh.loops) if attribute.domain == "CORNER"
             else len(mesh.vertices))
    flat = np.empty(count * 4, dtype=np.float32)
    attribute.data.foreach_get("color", flat)
    values = flat.reshape(count, 4)[:, :3]
    picks = np.array([list(t.loops) if attribute.domain == "CORNER"
                      else list(t.vertices) for t in triangles],
                     dtype=np.int64)[:, build_scene.CORNER_ORDER]
    return np.clip(np.round(values[picks] * 255.0), 0, 255).astype(np.uint8)


def read_mesh(obj, pack, sizes, problems, warnings):
    """One Blender object as a `MeshPayload`."""
    from crashbash.formats import modelimport as MI

    where = obj.name
    mesh = obj.data
    if obj.modifiers:
        warnings.append(
            f"{where}: {len(obj.modifiers)} modifier(s) are not applied; what "
            f"is written is the mesh underneath them")
    if hasattr(mesh, "calc_loop_triangles"):
        mesh.calc_loop_triangles()
    triangles = list(mesh.loop_triangles)
    if not triangles:
        problems.append(f"{where}: the mesh has no faces")
        return None

    points = np.empty(len(mesh.vertices) * 3, dtype=np.float64)
    mesh.vertices.foreach_get("co", points)
    vertices = to_model(points.reshape(-1, 3))

    # Back to the file's corner order: the importer reversed it so Blender's
    # front face would be the console's, and that comes off again here.
    corners = np.array([list(t.vertices) for t in triangles],
                       dtype=np.int64)[:, build_scene.CORNER_ORDER]
    positions = vertices[corners]
    colours = _corner_colours(mesh, triangles, where, problems)

    uv_layer = mesh.uv_layers.get(N.UV_LAYER) or mesh.uv_layers.active
    uv_values = None
    if uv_layer is not None:
        flat = np.empty(len(mesh.loops) * 2, dtype=np.float32)
        uv_layer.data.foreach_get("uv", flat)
        uv_values = flat.reshape(-1, 2).astype(np.float64)

    textures = np.full(len(triangles), -1, dtype=np.int64)
    uvs = np.zeros((len(triangles), 3, 2), dtype=np.uint8)
    outside: dict[int, int] = {}
    for row, triangle in enumerate(triangles):
        polygon = mesh.polygons[triangle.polygon_index]
        material = (mesh.materials[polygon.material_index]
                    if polygon.material_index < len(mesh.materials) else None)
        entry, size = _texture_entry(material, sizes, problems, where)
        textures[row] = entry
        if size is None or uv_values is None:
            continue
        width, height = size
        texel = uv_values[[triangle.loops[k] for k in build_scene.CORNER_ORDER]]
        u = np.round(texel[:, 0] * width - 0.5)
        v = np.round((1.0 - texel[:, 1]) * height - 0.5)
        # A slot cannot be resized (§10.1), so a UV past its edge is not a near
        # miss to be clamped: on hardware the triangle samples whatever shares
        # its page, which is how one model's eyes came back blank.
        if ((u < 0) | (u >= width) | (v < 0) | (v >= height)).any():
            outside[entry] = outside.get(entry, 0) + 1
        uvs[row, :, 0] = np.clip(u, 0, width - 1)
        uvs[row, :, 1] = np.clip(v, 0, height - 1)
    for entry, count in sorted(outside.items()):
        width, height = sizes.get(("slot", entry), sizes.get(("swatch",), (0, 0)))
        what = (f"slot {entry}" if entry >= 0
                else f"the swatch image palette {(-entry) & 0x1FF} reads")
        problems.append(
            f"{where}: {what} is {width}x{height} and {count} triangle(s) have "
            f"a corner outside it (UVs run 0..{width - 1} and 0..{height - 1}); "
            f"a slot cannot be resized, so the UVs have to fit it")

    # `corner_vertices` is what lets the writer put a pose back exactly: each
    # pool entry it lays down names the corner it came from, and that names one
    # of these vertices. Without it the core falls back to matching by rest
    # position, which cannot tell apart two vertices a clip drives apart.
    return MI.MeshPayload(positions=positions, colours=colours, uvs=uvs,
                          textures=textures, vertices=vertices,
                          corner_vertices=corners)


def read_clip(obj, clip, warnings):
    """One shipped clip, re-read from the shape keys and the action driving them."""
    from crashbash.formats import modelimport as MI

    keyset = obj.data.shape_keys
    labels = obj.get(N.PROP_CLIPS) or {}
    if keyset is None or clip.label not in labels:
        return None
    blocks = []
    for block in keyset.key_blocks:
        match = SHAPE_KEY.match(block.name)
        if match and match.group("label") == clip.label:
            blocks.append((int(match.group("key")), block))
    blocks.sort()
    if not blocks:
        return None
    action = bpy.data.actions.get(labels[clip.label])
    if action is None:
        warnings.append(
            f"{obj.name}: clip '{clip.label}' has poses but its action "
            f"'{labels[clip.label]}' is gone, so the clip was left as it is")
        return None

    frames = clip.frame_count
    curves = {}
    for curve in actions.curves(action):
        found = re.search(r'key_blocks\["(.+)"\]\.value', curve.data_path)
        if found:
            curves[found.group(1)] = curve
    weights = np.zeros((frames, len(blocks)), dtype=np.float64)
    for column, (_, block) in enumerate(blocks):
        curve = curves.get(block.name)
        if curve is None:
            continue
        # Blender counts frames from 1; the game counts from 0.
        weights[:, column] = [curve.evaluate(f + 1) for f in range(frames)]
    used, table = MI.frames_from_weights(weights, frames)
    if not used:
        return None

    everything = []
    for _, block in blocks:
        flat = np.empty(len(block.data) * 3, dtype=np.float64)
        block.data.foreach_get("co", flat)
        everything.append(to_model(flat.reshape(-1, 3)))
    # Whether an unchanged clip may be copied through depends on the mesh, and
    # that is the core's to decide: a clip whose mesh was rebuilt indexes a
    # different pool and has to be rebuilt with it. All that is said here is
    # whether the clip still reproduces the one that arrived.
    rest = (obj.get(N.PROP_CLIP_REST) or {}).get(clip.label)
    now = build_scene.clip_fingerprint(everything, [
        (used[f.key_a], used[f.key_b] if f.key_b is not None else None,
         f.weight) for f in table])
    return MI.ClipPayload(mesh_index=clip.mesh_index,
                          poses=[everything[column] for column in used],
                          frames=table, unchanged=bool(rest) and now == rest)


def build_request(collection, model, clips, pack, materials_pack=None):
    """Everything the collection has to say about the model it came from."""
    from crashbash.formats import modelimport as MI

    request = MI.ImportRequest(
        empty_error=(
            f"no object in '{collection.name}' carries a '{N.PROP_MESH}' "
            f"property and no placement was moved, so there is nothing to "
            f"write; import the entry through this add-on and edit what it "
            f"makes"),
        empty_note=(f"no object in '{collection.name}' names a mesh; the "
                    f"placements were written and the geometry left as it was"),
    )
    if bpy.context.scene.render.fps != N.FRAMES_PER_SECOND:
        request.warnings.append(
            f"the scene runs at {bpy.context.scene.render.fps} fps and the "
            f"game ticks at {N.FRAMES_PER_SECOND}; every clip was read on the "
            f"scene's grid and will be resampled")

    request.placements = _placements(collection, model, request.warnings)
    request.scene = _shot(collection, request.warnings)

    sizes = _sizes(pack)
    found = _objects(collection)
    known = {mesh.index for mesh in model.meshes}
    known |= {o.mesh.index for o in model.objects if o.mesh is not None}
    for index, obj in sorted(found.items()):
        if index not in known:
            request.warnings.append(
                f"{obj.name}: mesh {index} is not one this model holds, so it "
                f"was left out")
            continue
        payload = read_mesh(obj, pack, sizes, request.problems, request.warnings)
        if payload is not None:
            request.meshes[index] = payload

    for clip in clips:
        obj = found.get(clip.mesh_index)
        if obj is None:
            continue
        payload = read_clip(obj, clip, request.warnings)
        if payload is not None:
            request.clips[clip.label] = payload

    # Every slot the collection names, and the picture behind each one. The
    # writer compares against what the pack already holds and reports the slots
    # it left alone, so handing over an untouched image costs nothing.
    for material in {m for obj in found.values() for m in obj.data.materials
                     if m is not None}:
        slot = material.get(N.PROP_SLOT)
        if slot is None:
            continue
        request.slots.add(int(slot))
        # Not the swatch image: it has no palette of its own, so what the
        # importer showed is one of the many pictures it can be -- decoded
        # through the palette one mesh happens to name -- and handing that back
        # as "the texture" would repaint it for every face that names another.
        if pack is not None and 0 <= int(slot) < len(pack.textures) \
                and pack.textures[int(slot)].is_swatch:
            continue
        image = next((node.image for node in material.node_tree.nodes
                      if node.type == "TEX_IMAGE" and node.image is not None),
                     None) if material.use_nodes else None
        if image is not None and tuple(image.size) != (0, 0):
            request.images[int(slot)] = read_image(image)

    return request
