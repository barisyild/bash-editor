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

from crashbash.formats import placewrite

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


def _at_rest(obj, instance) -> bool:
    """Is this object still standing exactly where its record stands it?

    Against what the importer's own transform reads back as, not against the
    file: a quantised rotation is not exactly orthonormal and Blender keeps a
    transform as location, euler and scale, so recomposing it never gives the
    file's nine values back. Comparing against the file reported 24 of
    `warp_room1`'s 81 untouched records as moved, and rewriting a record that
    did not move is a byte of a live list spent for nothing.
    """
    rotation, translation = build_scene.placement_record(obj.matrix_basis)
    rest = list(obj.get(N.PROP_PLACE_REST) or []) or (
        list(instance.rotation) + list(instance.translation))
    now = list(rotation) + list(translation)
    return len(rest) == len(now) and all(
        abs(a - b) <= 1e-5 for a, b in zip(rest, now))


def _claims(collection, model, warnings: list[str]):
    """Sort the placement objects into record-holders and new records.

    **Duplicating a placement is how a level gains an object**, and Blender
    copies custom properties with the object -- so the copy arrives claiming the
    same `crashbash_placement` as the one it came from. Read literally that is
    two objects for one record, and the second silently overwrote the first: the
    obvious edit in Blender moved nothing and lost the original's move with it.

    So a record is held by one object and every other claimant is a new record.
    The holder is the claimant still standing where the record stands it, which
    is the original whenever the copy is the one that got dragged; with none or
    several at rest it is the first the collection lists, and the rest are new.
    An object with no claim at all is new too -- that is a mesh dropped in by
    hand rather than duplicated.
    """
    by_index = {i.index: i for i in model.instances}
    claimed: dict[int, list] = {}
    fresh = []
    for obj in collection.all_objects:
        if obj.type != "MESH" or obj.get(N.PROP_PREVIEW):
            continue
        index = obj.get(N.PROP_PLACEMENT)
        if index is None:
            if obj.get(N.PROP_MESH) is None and obj.get(N.PROP_OBJECT) is None:
                fresh.append((obj, None))
            continue
        if int(index) not in by_index:
            warnings.append(
                f"{obj.name}: placement {int(index)} is not in this model, "
                f"which has {len(model.instances)}; it was left out")
            continue
        claimed.setdefault(int(index), []).append(obj)

    holders: dict[int, object] = {}
    for index, objects in claimed.items():
        if len(objects) == 1:
            holders[index] = objects[0]
            continue
        resting = [o for o in objects if _at_rest(o, by_index[index])]
        holder = resting[0] if len(resting) == 1 else objects[0]
        holders[index] = holder
        for obj in objects:
            if obj is not holder:
                fresh.append((obj, index))
    return holders, fresh


def _placements(holders, model, warnings: list[str]) -> dict[int, dict]:
    """Every record its holder moved or re-aimed, and only those.

    A record is eleven bytes of a live list and rewriting one that did not
    change is work the file does not need.
    """
    edits: dict[int, dict] = {}
    by_index = {i.index: i for i in model.instances}
    for index, obj in sorted(holders.items()):
        instance = by_index[index]
        rotation, translation = build_scene.placement_record(obj.matrix_basis)
        identifier = int(obj.get(N.PROP_PLACES, instance.id))
        if identifier == instance.id and _at_rest(obj, instance):
            continue
        edits[index] = {"id": identifier, "translation": translation,
                        "rotation": rotation}
    return edits


def _new_placements(fresh, model, data, warnings: list[str]) -> list[dict]:
    """The objects that stand for no record yet, as records to append.

    The list grows into the padding the resident region ends with and
    `placewrite.spare_capacity` says by how much -- 3 for `warp_room1`, 10 for
    Oxide's chase level, none at all for an arena. Past that the export refuses
    rather than write a level that cannot load, so this reports what it dropped
    instead of dropping it quietly.

    Each new record copies an existing one whole, so the fields this project has
    not read arrive set to something the game already ran. A duplicate copies
    the record it was duplicated from; anything else copies the record that
    places the same object, and failing that record 0.
    """
    if not fresh or not model.instances:
        return []
    room = placewrite.spare_capacity(data, model)
    if not room:
        warnings.append(
            f"{len(fresh)} object(s) stand for no placement and this level has "
            f"room for none: its resident region ends without the padding a new "
            f"record grows into, so they were left out")
        return []

    added = []
    for obj, came_from in fresh:
        identifier = obj.get(N.PROP_PLACES)
        if identifier is None:
            warnings.append(
                f"{obj.name}: nothing says which object it places, so it was "
                f"left out; give it a '{N.PROP_PLACES}' or duplicate a placement")
            continue
        identifier = int(identifier)
        copies = came_from
        if copies is None:
            copies = next(
                (i.index for i in model.instances if i.id == identifier),
                model.instances[0].index)
        rotation, translation = build_scene.placement_record(obj.matrix_basis)
        added.append({"copies": copies, "id": identifier,
                      "translation": translation, "rotation": rotation})
        if len(added) == room:
            break
    if len(fresh) > len(added):
        warnings.append(
            f"{len(fresh)} object(s) stand for no placement and there is room "
            f"for {room}; {len(fresh) - len(added)} were left out")
    return added


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


def _shipped_outside(model, pack, sizes, index) -> dict[int, int]:
    """How many of this mesh's triangles already read outside their slot.

    The game does it: `cutscene/level_intro_crashplain` has faces reading texel
    31 of a 16x16 slot in the file the disc shipped. So an export that carries
    those through is reporting the model, not breaking it, and only a UV the
    edit put outside is a refusal.
    """
    from crashbash.formats import modelimport as MI

    out: dict[int, int] = {}
    mesh = next((m for m in list(model.meshes)
                 + [o.mesh for o in model.objects if o.mesh is not None]
                 if m.index == index), None)
    if mesh is None:
        return out
    payload = MI.payload_from_model(None, model, pack, index, {})
    if payload is None:
        return out
    for row in range(payload.positions.shape[0]):
        entry = int(payload.textures[row])
        size = sizes.get(("slot", entry) if entry >= 0 else ("swatch",))
        if size is None:
            continue
        width, height = size
        texel = payload.uvs[row]
        if (texel[:, 0] >= width).any() or (texel[:, 1] >= height).any():
            out[entry] = out.get(entry, 0) + 1
    return out


def read_mesh(obj, pack, sizes, problems, warnings, shipped=None):
    """One Blender object as a `MeshPayload`.

    Geometry only, in the mesh's own frame. Every mesh in this format is placed
    by whatever draws it -- a node's keys in a cutscene, a placement record in a
    level -- and the importer stands them all at the origin, so an object's
    transform is never read here. A mesh being *added* is no exception: its
    transform becomes its new node's keys, through `build_scene.track_key`.
    """
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
    # The GPU's semi-transparency (§6.3), read off the material rather than
    # recovered by matching corner positions: the material is where the
    # importer put it, and two faces can share their sorted corners.
    blend = np.zeros(len(triangles), dtype=np.uint8)
    # §5.1's strip flag, read off the face attribute the importer wrote. It is
    # a separate fact from the texture entry's bit 15 and cannot be derived
    # from it (§6.2), so leaving it out did not fall back to something sensible
    # -- it moved 758 of `mainmenu/models`' 3498 textured triangles into
    # untextured strips, which draw flat shaded with no texture at all. A face
    # the artist made carries no value, and 0 is the right default for it:
    # every mesh in the archive that draws untextured says so through the
    # swatch bit as well, so the writer still has that to go on.
    untextured = None
    layer = mesh.attributes.get(N.STRIP_FLAG_ATTRIBUTE)
    if layer is not None and layer.domain == "FACE":
        stored = np.zeros(len(mesh.polygons), dtype=np.int32)
        layer.data.foreach_get("value", stored)
        untextured = np.array([bool(stored[t.polygon_index]) for t in triangles])
    outside: dict[int, int] = {}
    shipped = shipped or {}
    for row, triangle in enumerate(triangles):
        polygon = mesh.polygons[triangle.polygon_index]
        material = (mesh.materials[polygon.material_index]
                    if polygon.material_index < len(mesh.materials) else None)
        entry, size = _texture_entry(material, sizes, problems, where)
        textures[row] = entry
        if material is not None and material.get(N.PROP_BLEND) is not None:
            blend[row] = int(material[N.PROP_BLEND]) & 7
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
        # Clamped to the byte the field is, not to the slot: the game itself
        # reads past a slot -- `cutscene/level_intro_crashplain` samples texel
        # 31 of a 16x16 one -- and clipping to `width - 1` silently rewrote
        # those eight triangles on the way back out.
        uvs[row, :, 0] = np.clip(u, 0, 255)
        uvs[row, :, 1] = np.clip(v, 0, 255)
    for entry, count in sorted(outside.items()):
        width, height = sizes.get(("slot", entry), sizes.get(("swatch",), (0, 0)))
        what = (f"slot {entry}" if entry >= 0
                else f"the swatch image palette {(-entry) & 0x1FF} reads")
        # The game does this itself, so it cannot be a refusal on its own:
        # `cutscene/level_intro_crashplain` ships triangles reading texel 31 of
        # a 16x16 slot, four faces in each of two meshes. What the refusal is
        # for is a UV the *edit* put outside -- a mesh moved between packs and
        # rescaled by `dest / source` instead of `(dest-1)/(source-1)`, which
        # is how one model's eyes came back blank. So only the excess counts,
        # and matching what shipped is merely said.
        already = shipped.get(entry, 0)
        if count <= already:
            warnings.append(
                f"{where}: {count} triangle(s) read outside {what}, which is "
                f"{width}x{height} -- as they do in the file the disc shipped")
            continue
        problems.append(
            f"{where}: {what} is {width}x{height} and {count - already} more "
            f"triangle(s) than shipped have a corner outside it (UVs run "
            f"0..{width - 1} and 0..{height - 1}); a slot cannot be resized, "
            f"so the UVs have to fit it")

    # `corner_vertices` is what lets the writer put a pose back exactly: each
    # pool entry it lays down names the corner it came from, and that names one
    # of these vertices. Without it the core falls back to matching by rest
    # position, which cannot tell apart two vertices a clip drives apart.
    return MI.MeshPayload(positions=positions, colours=colours, uvs=uvs,
                          textures=textures, blend=blend, vertices=vertices,
                          corner_vertices=corners, untextured=untextured)


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


def build_request(collection, model, clips, pack, materials_pack=None,
                  model_data: bytes | None = None):
    """Everything the collection has to say about the model it came from.

    `model_data` is the entry's own bytes. Only the new-placement path needs
    them -- how much room the list has is a fact about the file's padding, not
    about anything the reader keeps -- and without them that path is skipped
    rather than guessed at.
    """
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

    holders, fresh = _claims(collection, model, request.warnings)
    request.placements = _placements(holders, model, request.warnings)
    if model_data is not None:
        request.new_placements = _new_placements(
            fresh, model, model_data, request.warnings)
    elif fresh:
        request.warnings.append(
            f"{len(fresh)} object(s) stand for no placement and the entry's own "
            f"bytes were not passed, so how much room the list has could not be "
            f"read; they were left out")
    request.scene = _shot(collection, request.warnings)
    # A mesh the artist put on stage: the shot gets a node of its own for it,
    # which is what makes a borrowed model *added* to a cutscene rather than
    # swapped in for something already drawn.
    request.new_props = sorted(
        int(obj[N.PROP_MESH]) for obj in collection.all_objects
        if obj.get(N.PROP_ON_STAGE) and obj.get(N.PROP_MESH) is not None)
    # And one that names no mesh of this model at all is a mesh being *added*.
    # The core takes it from there -- it appends the node first and the slot
    # after, because the new blocks go at the end and a node appended behind
    # them would be cut off from the region carrying the shot.
    fresh_meshes = [obj for obj in collection.all_objects
                    if obj.type == "MESH" and obj.get(N.PROP_ON_STAGE)
                    and obj.get(N.PROP_MESH) is None
                    and obj.get(N.PROP_PLACEMENT) is None]

    sizes = _sizes(pack)
    found = _objects(collection)
    # A slot being *appended* is not in the pack yet, so `_sizes` has nothing
    # for it and its UVs would be addressed against `UNKNOWN_SIZE`. The picture
    # the material carries is its size, and getting this wrong is not a small
    # error: the penguin's faces were snapped onto a pinned table's triples in
    # 16x16 texels and read back through 256x256, so 115 of its 116 came out
    # holding a triple the table does not have.
    for obj in list(found.values()) + fresh_meshes:
        for material in obj.data.materials:
            if material is None or not material.get(N.PROP_NEW_SLOT):
                continue
            image = next((node.image for node in material.node_tree.nodes
                          if node.type == "TEX_IMAGE" and node.image is not None),
                         None) if material.use_nodes else None
            if image is not None and tuple(image.size) != (0, 0):
                sizes[("slot", int(material[N.PROP_SLOT]))] = tuple(image.size)
    known = {mesh.index for mesh in model.meshes}
    known |= {o.mesh.index for o in model.objects if o.mesh is not None}
    for index, obj in sorted(found.items()):
        if index not in known:
            request.warnings.append(
                f"{obj.name}: mesh {index} is not one this model holds, so it "
                f"was left out")
            continue
        payload = read_mesh(obj, pack, sizes, request.problems, request.warnings,
                            _shipped_outside(model, pack, sizes, index))
        if payload is not None:
            request.meshes[index] = payload

    # `_shipped_outside` answers for a mesh the model already holds, and these
    # replace nothing, so there is no shipped face to compare against.
    #
    # The transform goes to the *node*, not into the vertices. A prop is placed
    # by its keys and by nothing else, so geometry moved in Blender would have
    # the node's own placement applied on top of it -- which is how Cortex
    # arrived at `intro_logo`'s ceiling, tilted, wearing the template prop's
    # quaternion and its 0.24 scale.
    for obj in fresh_meshes:
        payload = read_mesh(obj, pack, sizes, request.problems, request.warnings)
        if payload is not None:
            request.new_meshes.append(payload)
            request.new_mesh_placements.append(
                build_scene.track_key(obj.matrix_world))

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
    for material in {m for obj in list(found.values()) + fresh_meshes
                     for m in obj.data.materials if m is not None}:
        slot = material.get(N.PROP_SLOT)
        if slot is None:
            continue
        image = next((node.image for node in material.node_tree.nodes
                      if node.type == "TEX_IMAGE" and node.image is not None),
                     None) if material.use_nodes else None
        # A slot the pack does not have yet: the export appends it rather than
        # repainting anything, so it never joins `slots` -- that set is what
        # decides whether a palette may be requantised, and an appended texture
        # brings its own.
        if material.get(N.PROP_NEW_SLOT):
            if image is None or tuple(image.size) == (0, 0):
                request.warnings.append(
                    f"{material.name} asks for a new slot {int(slot)} and "
                    f"carries no picture; it was left out")
                continue
            request.new_textures[int(slot)] = read_image(image)
            continue
        request.slots.add(int(slot))
        # Not the swatch image: it has no palette of its own, so what the
        # importer showed is one of the many pictures it can be -- decoded
        # through the palette one mesh happens to name -- and handing that back
        # as "the texture" would repaint it for every face that names another.
        if pack is not None and 0 <= int(slot) < len(pack.textures) \
                and pack.textures[int(slot)].is_swatch:
            continue
        if image is not None and tuple(image.size) != (0, 0):
            request.images[int(slot)] = read_image(image)

    return request
