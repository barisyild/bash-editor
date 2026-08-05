"""Turn a model entry into Blender datablocks, losing nothing on the way.

The whole model becomes one collection: an object per mesh, a material per
texture slot and per swatch palette, a shape key per stored pose and an action
per clip. What each piece carries is spelt out in `naming`; what makes this
different from exporting to an interchange format is that nothing is folded or
approximated. A swatch face keeps its palette *and* its cell, a colour keeps
its full 0..255 range, and the corner order arrives as the file states it.

The frame the game works in is Y-down and Z-forward. Blender is Z-up, and the
map between them -- `(x, y, z) -> (x, z, -y)` -- has determinant +1, so the
winding is carried across untouched. That matters: the console flips its
backface test per vertex flag (§11.3), so a mirrored transform here would cull
every triangle it should draw and nothing on a static render would say so.
"""

from __future__ import annotations

import numpy as np

import bpy

from . import actions, naming as N

# Model units to Blender units. The same scale the glTF exporter uses, so a
# measurement taken through one path means the same through the other.
SCALE = 1.0 / 256.0
# The blend the hardware performs: `texel * colour / 128`, so a stored 255 is a
# 2x multiplier. Used only for the preview shader; the data keeps 0..1 for
# 0..255 and the exporter reads it back unchanged.
COLOUR_GAIN = 255.0 / 128.0


def to_blender(points: np.ndarray) -> np.ndarray:
    """Raw model units -> Blender units, orientation preserved."""
    points = np.asarray(points, dtype=np.float64)
    return np.stack([points[:, 0], points[:, 2], -points[:, 1]], axis=1) * SCALE


def to_model(points: np.ndarray) -> np.ndarray:
    """Blender units -> raw model units."""
    points = np.asarray(points, dtype=np.float64) / SCALE
    return np.stack([points[:, 0], -points[:, 2], points[:, 1]], axis=1)


# The change of basis the two functions above perform, without the scale.
# A placement rotates and translates a model-space vertex -- `v' = R v + t` --
# so in Blender it is `(B R B^-1) b + B t`, and the scale cancels out of the
# rotation. Its determinant is +1, which is why no winding has to be undone.
BASIS = np.array([[1.0, 0.0, 0.0], [0.0, 0.0, 1.0], [0.0, -1.0, 0.0]])


def placement_matrix(rotation, translation):
    """A placement record as a Blender 4x4, ready to assign to an object.

    A `mathutils.Matrix`, and it has to be: assigning a nested Python list to
    `matrix_basis` is accepted and then quietly does nothing at all. Every
    placement was standing at the origin with an identity rotation, and a check
    that compared each object against its own read-back transform agreed with
    itself the whole way.
    """
    from mathutils import Matrix  # noqa: PLC0415

    rot = BASIS @ np.asarray(rotation, dtype=np.float64).reshape(3, 3) @ BASIS.T
    # `Instance.translation` is in the reader's scaled units, like a vertex.
    at = BASIS @ np.asarray(translation, dtype=np.float64)
    matrix = np.eye(4)
    matrix[:3, :3] = rot
    matrix[:3, 3] = at
    return Matrix([[float(v) for v in row] for row in matrix])


def placement_record(matrix) -> tuple[tuple[float, ...], tuple[float, float, float]]:
    """The mirror: a Blender 4x4 back to a record's rotation and translation."""
    world = np.array([[matrix[r][c] for c in range(4)] for r in range(4)])
    rot = BASIS.T @ world[:3, :3] @ BASIS
    at = BASIS.T @ world[:3, 3]
    return tuple(float(v) for v in rot.reshape(-1)), tuple(float(v) for v in at)


# --- images and materials -------------------------------------------------


def _image(name: str, rgba: np.ndarray) -> bpy.types.Image:
    """A packed image datablock holding exactly these bytes.

    Non-Color, deliberately. Blender returns `pixels` through the colour space
    a texture declares, so an sRGB image would come back transformed and have
    to be transformed again on the way out -- two lossy conversions around data
    that is already exactly what the console samples. The importer sets the
    scene's view transform to Standard so it still looks right.
    """
    image = bpy.data.images.new(name, rgba.shape[1], rgba.shape[0], alpha=True)
    image.colorspace_settings.name = "Non-Color"
    # Blender's first row is the bottom one.
    flat = (rgba[::-1].astype(np.float32) / 255.0).reshape(-1)
    image.pixels.foreach_set(flat)
    image.pack()
    return image


def read_image(image: bpy.types.Image) -> np.ndarray:
    """The mirror of `_image`: (H, W, 4) uint8, top row first."""
    width, height = image.size
    flat = np.empty(width * height * 4, dtype=np.float32)
    image.pixels.foreach_get(flat)
    rgba = flat.reshape(height, width, 4)[::-1]
    return np.clip(np.round(rgba * 255.0), 0, 255).astype(np.uint8)


def _shader(material: bpy.types.Material, image: bpy.types.Image | None) -> None:
    """Draw the face the way the console does: texel x colour x 2, no lighting.

    An emission shader rather than a lit one, because the vertex colour *is* the
    lighting -- the hardware has no other. Nearest-neighbour sampling, because
    the console has no other either, and a bilinear preview hides exactly the
    single-texel reads a swatch face is made of.
    """
    material.use_nodes = True
    tree = material.node_tree
    tree.nodes.clear()
    output = tree.nodes.new("ShaderNodeOutputMaterial")
    output.location = (600, 0)
    emission = tree.nodes.new("ShaderNodeEmission")
    emission.location = (400, -100)

    attribute = tree.nodes.new("ShaderNodeAttribute")
    attribute.attribute_type = "GEOMETRY"
    attribute.attribute_name = N.COLOUR_ATTRIBUTE
    attribute.location = (-400, -200)

    gain = tree.nodes.new("ShaderNodeVectorMath")
    gain.operation = "SCALE"
    gain.location = (0, -100)
    gain.inputs["Scale"].default_value = COLOUR_GAIN

    if image is None:
        tree.links.new(attribute.outputs["Color"], gain.inputs[0])
        tree.links.new(gain.outputs["Vector"], emission.inputs["Color"])
        tree.links.new(emission.outputs["Emission"], output.inputs["Surface"])
        return

    texture = tree.nodes.new("ShaderNodeTexImage")
    texture.image = image
    texture.interpolation = "Closest"
    texture.extension = "EXTEND"
    texture.location = (-400, 200)

    product = tree.nodes.new("ShaderNodeVectorMath")
    product.operation = "MULTIPLY"
    product.location = (-200, 0)
    tree.links.new(texture.outputs["Color"], product.inputs[0])
    tree.links.new(attribute.outputs["Color"], product.inputs[1])
    tree.links.new(product.outputs["Vector"], gain.inputs[0])
    tree.links.new(gain.outputs["Vector"], emission.inputs["Color"])

    # BGR555 0x0000 is the hardware's skip pixel, which `to_rgba` gives an alpha
    # of zero; a genuinely black texel carries the STP bit and stays opaque.
    transparent = tree.nodes.new("ShaderNodeBsdfTransparent")
    transparent.location = (400, 100)
    mix = tree.nodes.new("ShaderNodeMixShader")
    mix.location = (520, 0)
    tree.links.new(texture.outputs["Alpha"], mix.inputs["Fac"])
    tree.links.new(transparent.outputs["BSDF"], mix.inputs[1])
    tree.links.new(emission.outputs["Emission"], mix.inputs[2])
    tree.links.new(mix.outputs["Shader"], output.inputs["Surface"])
    if hasattr(material, "surface_render_method"):
        material.surface_render_method = "DITHERED"
    elif hasattr(material, "blend_method"):
        material.blend_method = "BLEND"


class Materials:
    """One material per thing a face can read, made once per model."""

    def __init__(self, stem: str, pack) -> None:
        self.stem = stem
        self.pack = pack
        self.by_key: dict[tuple, bpy.types.Material] = {}
        self.swatch = next((t for t in pack.textures if t.is_swatch), None) \
            if pack else None

    def size_of(self, material: bpy.types.Material) -> tuple[int, int] | None:
        """The texture a material's UVs address, in texels."""
        slot = material.get(N.PROP_SLOT)
        if slot is not None:
            if self.pack and 0 <= slot < len(self.pack.textures):
                texture = self.pack.textures[slot]
                return texture.width, texture.height
            return N.UNKNOWN_SIZE
        if material.get(N.PROP_PALETTE) is not None and self.swatch is not None:
            return self.swatch.width, self.swatch.height
        return None

    def for_slot(self, slot: int, notes: list[str]) -> bpy.types.Material:
        key = ("slot", slot)
        if key in self.by_key:
            return self.by_key[key]
        texture = (self.pack.textures[slot]
                   if self.pack and 0 <= slot < len(self.pack.textures) else None)
        if texture is None:
            # A model may name a slot its own pack does not hold -- the cutscene
            # models do, drawing from a pack the shot loads alongside them. The
            # picture cannot be shown, but the entry and its texels still have
            # to survive the trip, so the UVs are addressed against a nominal
            # 256x256: a stored texel is a byte, so that carries all of them.
            material = bpy.data.materials.new(f"tex_{slot:03d}_unresolved")
            material[N.PROP_SLOT] = slot
            _shader(material, None)
            notes.append(
                f"slot {slot} is not in this pack ({len(self.pack.textures) if self.pack else 0} "
                f"textures), so its faces have no picture to show; the slot and "
                f"its UVs are carried through unchanged")
            self.by_key[key] = material
            return material
        # The pack's own name for it, `_swatch` suffix and all: a face may name
        # the swatch image as an ordinary slot, and that is worth seeing in the
        # material list rather than discovering from a magenta preview.
        material = bpy.data.materials.new(texture.name)
        material[N.PROP_SLOT] = slot
        _shader(material, _image(
            N.IMAGE_SLOT.format(stem=self.stem, slot=slot),
            texture.to_rgba(self.pack.palettes)))
        # A slot the pack animates does not hold one picture. Shown so an
        # artist repainting it knows what they are repainting -- the base
        # frame -- and not written back: nothing in this project writes a
        # flipbook's frames or a scroller's rate yet.
        book = self.pack.animated().get(slot)
        if book is not None:
            material[N.PROP_FLIPBOOK] = [len(book.frames), round(book.fps, 3)]
            notes.append(
                f"slot {slot} is a flipbook of {len(book.frames)} frames at "
                f"{book.fps:.1f} fps; the base frame is shown and the frames "
                f"are carried through untouched")
        scroll = next((s for s in self.pack.scrollers if s.texture == slot), None)
        if scroll is not None:
            material[N.PROP_SCROLL] = round(scroll.texels_per_second, 3)
            notes.append(
                f"slot {slot} scrolls at {scroll.texels_per_second:.1f} texels "
                f"a second; it is shown still and carried through untouched")
        self.by_key[key] = material
        return material

    def for_swatch(self, palette: int, notes: list[str]) -> bpy.types.Material:
        key = ("swatch", palette)
        if key in self.by_key:
            return self.by_key[key]
        material = bpy.data.materials.new(
            N.MATERIAL_SWATCH.format(palette=palette))
        material[N.PROP_PALETTE] = palette
        image = None
        if self.swatch is not None:
            # Decoded through this palette, so the artist sees the colours the
            # face actually means rather than a grid of indices.
            image = _image(
                N.IMAGE_SWATCH.format(stem=self.stem, palette=palette),
                self.swatch.to_rgba(self.pack.palettes, palette_override=palette))
        _shader(material, image)
        self.by_key[key] = material
        return material

    def plain(self) -> bpy.types.Material:
        key = ("plain",)
        if key not in self.by_key:
            material = bpy.data.materials.new(N.MATERIAL_PLAIN)
            _shader(material, None)
            self.by_key[key] = material
        return self.by_key[key]


# --- geometry -------------------------------------------------------------


def pool_poses(mesh, clips) -> list[np.ndarray]:
    """Every stored pose of every clip that drives this mesh, in pool order."""
    poses = []
    for clip in clips:
        if clip.mesh_index != mesh.index:
            continue
        for key in clip.keyframes():
            poses.append(np.asarray(clip.pool()[clip._slots(key)],
                                    dtype=np.float64))
    return poses


def _weld(payload) -> tuple[np.ndarray, np.ndarray]:
    """The payload's own vertices, or position welding when it has none.

    A payload out of `payload_from_model` already carries the surface's
    vertices -- the pool's repeats folded together, and the pairs a clip drives
    apart kept apart. Anything else falls back to position, which is what makes
    the surface's shared edges; without any weld at all the mesh is a triangle
    soup with nothing to select a loop along.
    """
    if payload.corner_vertices is not None and payload.vertices is not None:
        return (np.round(np.asarray(payload.vertices)).astype(np.int64),
                np.asarray(payload.corner_vertices, dtype=np.int64))
    corners = np.round(payload.positions.reshape(-1, 3)).astype(np.int64)
    unique, inverse = np.unique(corners, axis=0, return_inverse=True)
    return unique, inverse.reshape(-1, 3)


def _entry_material(entry: int, materials: Materials,
                    notes: list[str]) -> bpy.types.Material:
    if entry >= 0:
        return materials.for_slot(entry, notes)
    if entry < -1:
        return materials.for_swatch((-entry) & 0x1FF, notes)
    return materials.plain()


def build_mesh(name: str, payload, materials: Materials, notes: list[str]):
    """One `MeshPayload` as a Blender mesh, with its colours, UVs and materials.

    Returns the mesh and the vertex map: which of the payload's vertices each
    Blender vertex is, which is `None` only when the fallback below fires.
    """
    faces = payload.positions.shape[0]
    vertices, indices = _weld(payload)
    mapping = np.arange(len(vertices), dtype=np.int64)
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(to_blender(vertices).tolist(), [],
                     [tuple(int(v) for v in row) for row in indices])
    mesh.validate(verbose=False)
    if len(mesh.polygons) != faces:
        # Two triangles over the same three welded corners are one face to
        # Blender and two to the file. Rebuilding a corner per vertex keeps them
        # apart; the surface loses its shared edges, which is worth saying.
        dropped = faces - len(mesh.polygons)
        bpy.data.meshes.remove(mesh)
        mesh = bpy.data.meshes.new(name)
        vertices = np.round(payload.positions.reshape(-1, 3)).astype(np.int64)
        indices = np.arange(faces * 3).reshape(-1, 3)
        mapping = (np.asarray(payload.corner_vertices).reshape(-1)
                   if payload.corner_vertices is not None else None)
        mesh.from_pydata(to_blender(vertices).tolist(), [],
                         [tuple(int(v) for v in row) for row in indices])
        mesh.validate(verbose=False)
        notes.append(
            f"{name}: {dropped} face(s) share their three corners with another, "
            f"so the mesh is built one vertex per corner and has no shared edges")

    # Materials, and the face -> material map that carries the texture entry.
    order: dict[int, int] = {}
    material_index = np.zeros(faces, dtype=np.int32)
    for face in range(faces):
        entry = int(payload.textures[face])
        if entry not in order:
            order[entry] = len(order)
            mesh.materials.append(_entry_material(entry, materials, notes))
        material_index[face] = order[entry]
    mesh.polygons.foreach_set("material_index", material_index)

    # Colours per corner, 0..1 standing for the file's 0..255.
    colours = np.ones((faces, 3, 4), dtype=np.float32)
    colours[..., :3] = payload.colours.astype(np.float32) / 255.0
    attribute = mesh.color_attributes.new(
        name=N.COLOUR_ATTRIBUTE, type="FLOAT_COLOR", domain="CORNER")
    attribute.data.foreach_set("color", colours.reshape(-1))

    # UVs in the texel space of whatever the face reads -- a real texture, or
    # the swatch image whose single cell paints an untextured face (§6.2).
    uv_layer = mesh.uv_layers.new(name=N.UV_LAYER)
    uvs = np.zeros((faces, 3, 2), dtype=np.float32)
    for face in range(faces):
        size = materials.size_of(mesh.materials[material_index[face]])
        if size is None:
            continue
        width, height = size
        texel = payload.uvs[face].astype(np.float64)
        uvs[face, :, 0] = (texel[:, 0] + 0.5) / width
        uvs[face, :, 1] = 1.0 - (texel[:, 1] + 0.5) / height
    uv_layer.data.foreach_set("uv", uvs.reshape(-1))
    return mesh, mapping


# --- animation ------------------------------------------------------------


def clip_fingerprint(poses, frames) -> str:
    """A digest of a clip's poses and its frame table, in the writer's terms.

    Both halves of the add-on compute it the same way, so an untouched clip is
    recognised as untouched and copied through byte for byte rather than
    rebuilt. That is not tidiness: a rebuild lays down a fresh pose pool, which
    is most of what a model weighs.
    """
    import hashlib  # noqa: PLC0415
    import struct  # noqa: PLC0415

    digest = hashlib.sha1()
    for pose in poses:
        digest.update(np.clip(np.round(np.asarray(pose, dtype=np.float64)),
                              -32768, 32767).astype("<i2").tobytes())
    for first, second, weight in frames:
        # Canonical, because a blend has two spellings and the archive uses
        # both: `chars/crate/coco`'s BREATHE frame 11 names key 1 then key 0 at
        # 409, while `cutscene/level_shot12` runs ascending throughout. Reading
        # the pair back off Blender's shape key values recovers the blend and
        # not the order it was written in, so the digest has to ignore it or
        # every such clip looks edited and is rebuilt for nothing.
        first, second, weight = int(first), second, int(weight)
        if second is not None and int(second) < first:
            first, second = int(second), first
            weight = 4096 - weight
        digest.update(struct.pack("<iii", first,
                                  -1 if second is None else int(second), weight))
    return digest.hexdigest()


def build_clips(obj, mesh, rows: np.ndarray | None, clips, stem: str,
                notes: list[str]) -> None:
    """Poses as shape keys, the frame table as an action over their values.

    `rows` is the pool entry each Blender vertex stands for. It comes from the
    library's own weld rather than being rediscovered by position here -- the
    whole point of that weld is that position is not enough to tell two pool
    entries apart when a clip drives them somewhere different.
    """
    mine = [c for c in clips if c.mesh_index == obj.get(N.PROP_MESH)]
    if not mine or rows is None:
        return

    obj.shape_key_add(name="Basis", from_mix=False)
    labels: dict[str, str] = {}
    counts: dict[str, int] = {}
    rest: dict[str, str] = {}
    for clip in mine:
        keys = clip.keyframes()
        blocks = []
        gathered = []
        for number, key in enumerate(keys):
            pose = clip.pool()[clip._slots(key)].astype(np.float64)
            block = obj.shape_key_add(
                name=N.SHAPE_KEY.format(label=clip.label, key=number),
                from_mix=False)
            gathered.append(pose[rows])
            block.data.foreach_set("co", to_blender(pose[rows]).reshape(-1))
            blocks.append(block)
        at_key = {k: i for i, k in enumerate(keys)}
        rest[clip.label] = clip_fingerprint(gathered, [
            (at_key[f.key_a],
             at_key[f.key_b] if f.key_b else None, f.weight)
            for f in clip.frames])

        action = actions.make(N.ACTION.format(stem=stem, label=clip.label))
        frames = len(clip.frames)
        # One value per pose per frame, exactly as the file states it: the
        # weight is how far from `key_a` towards `key_b` the frame sits, and a
        # weight of zero is a frame that names one key and reads no other.
        at = {k: i for i, k in enumerate(keys)}
        for number, block in enumerate(blocks):
            values = np.zeros(frames, dtype=np.float64)
            for index, frame in enumerate(clip.frames):
                first = at.get(frame.key_a)
                second = at.get(frame.key_b) if frame.key_b else None
                blend = frame.weight / 4096.0 if second is not None else 0.0
                if first == number:
                    values[index] += 1.0 - blend
                if second == number:
                    values[index] += blend
            if not values.any():
                continue
            curve = actions.new_curve(
                action, f'key_blocks["{block.name}"].value')
            curve.keyframe_points.add(frames)
            points = np.empty(frames * 2, dtype=np.float64)
            points[0::2] = np.arange(frames) + 1  # Blender counts from 1
            points[1::2] = values
            curve.keyframe_points.foreach_set("co", points)
            for point in curve.keyframe_points:
                point.interpolation = "LINEAR"
            curve.update()
        labels[clip.label] = action.name
        counts[clip.label] = frames

    first = next(iter(labels.values()), None)
    if first:
        actions.assign(mesh.shape_keys, bpy.data.actions[first])
    obj[N.PROP_CLIPS] = labels
    obj[N.PROP_FRAMES] = counts
    obj[N.PROP_CLIP_REST] = rest


# --- the level's set ------------------------------------------------------


def _build_placements(collection, model, stem: str, by_id: dict,
                      notes: list[str]) -> None:
    """One object per placement record, standing where the record stands it.

    This is what a level *is*: it draws what its placement list names and
    nothing else (§8.5). `warp_room1` has 81 records and not one of them names
    any of the 42 meshes in `model.meshes`, so opening a level and seeing the
    numbered meshes at the origin is seeing the pieces, not the room.

    Each object shares the mesh data of the pool mesh its id resolves to, so
    moving one moves that copy alone. Editing the mesh edits every copy, which
    is what the file does too.
    """
    if not model.instances:
        return
    places = bpy.data.collections.new(N.COLLECTION_PLACES.format(stem=stem))
    collection.children.link(places)
    missing = 0
    for instance in model.instances:
        name = N.OBJECT_PLACE.format(stem=stem, index=instance.index,
                                     id=instance.id)
        source = by_id.get(instance.id)
        data = source.data if source is not None else (
            instance.mesh and bpy.data.meshes.get(
                N.OBJECT_MESH.format(stem=stem, index=instance.mesh.index)))
        if data is None:
            missing += 1
        obj = bpy.data.objects.new(name, data)
        obj[N.PROP_PLACEMENT] = instance.index
        obj[N.PROP_PLACES] = instance.id
        # `matrix_basis`, not `matrix_world`: the world matrix is evaluated by
        # the dependency graph and reads back stale until it runs, so an object
        # moved and exported in one pass came out where it started -- and 24 of
        # `warp_room1`'s 81 untouched records read as if they had moved. The
        # basis is the object's own transform and is always current.
        obj.matrix_basis = placement_matrix(instance.rotation,
                                            instance.translation)
        # What that transform reads back as, which is not what went in: a
        # transform is stored as location, euler and scale, and the file's
        # rotation is quantised to 1/4096 and so not exactly orthonormal.
        # This is the reference "unmoved" is decided against.
        rotation, translation = placement_record(obj.matrix_basis)
        obj[N.PROP_PLACE_REST] = list(rotation) + list(translation)
        obj.hide_render = not instance.is_drawn
        places.objects.link(obj)
    notes.append(f"{len(model.instances)} placements; the list cannot be made "
                 f"longer, and a record whose object another record already "
                 f"places is the only spare a level has")
    if missing:
        notes.append(f"{missing} placement(s) name something this file does not "
                     f"hold -- a clip, or an object in a model loaded alongside "
                     f"-- so they are empties you can still move")


# --- the shot, and its particles -----------------------------------------


def _build_shot(collection, model, model_data: bytes, clips, stem: str,
                notes: list[str]) -> None:
    """The shot (§9.11) as data on the collection, its emitters as objects.

    The whole shot is stored as the file itself holds it, so everything an
    artist does not touch here is written back exactly as it was: the tracks,
    the camera keys, the sub-scene frames. What gets an object of its own is
    the **particle emitter**, because it has no representation anywhere else --
    glTF has no particles at all, so an emitter that went out through one came
    back only because the exporter wrote its fields into `extras` by hand.

    An emitter's fields are the simulation: how many particles the spray has,
    how many leave per tick, how long one lives, the speed and the cone it
    leaves in, the acceleration and damping that bend it, the spin, and the two
    ramps that fade and grow it (§9.11.7). All of them are editable here and all
    of them write back.
    """
    import json  # noqa: PLC0415

    from crashbash.formats.scenewrite import scene_extras
    from crashbash.scene import read_scene

    try:
        shot = read_scene(model_data, model, clips)
    except Exception as exc:  # noqa: BLE001
        notes.append(f"the shot could not be read, so it is left alone: {exc}")
        return
    if shot is None:
        return
    extras = scene_extras(shot, model)
    collection[N.PROP_SCENE] = json.dumps(extras)
    if not shot.emitters:
        notes.append(f"shot: {len(shot.actors)} actors, {len(shot.props)} props, "
                     f"{len(shot.cameras)} cameras, carried through unchanged")
        return

    shot_collection = bpy.data.collections.new(
        N.COLLECTION_SHOT.format(stem=stem))
    collection.children.link(shot_collection)
    for index, emitter in enumerate(extras["emitters"]):
        name = N.OBJECT_EMITTER.format(stem=stem, index=index)
        obj = bpy.data.objects.new(name, None)
        obj.empty_display_type = "SPHERE"
        obj.empty_display_size = 0.2
        obj[N.PROP_EMITTER] = emitter["node"]
        for field_name in N.EMITTER_FIELDS:
            value = emitter.get(field_name)
            obj[field_name] = list(value) if isinstance(value, list) else value
        obj.location = to_blender(
            np.asarray([emitter["position"]], dtype=np.float64) / SCALE)[0]
        shot_collection.objects.link(obj)
    notes.append(
        f"shot: {len(shot.actors)} actors, {len(shot.props)} props, "
        f"{len(shot.cameras)} cameras carried through unchanged, and "
        f"{len(shot.emitters)} particle emitter(s) you can edit")


def bake_particles(collection, model_data: bytes, model, clips, stem: str
                   ) -> tuple[int, int]:
    """Instance every live particle, tick by tick, so the spray can be watched.

    A preview and nothing else: it is keyframed from the simulation the game
    runs (§9.11.7) and goes stale the moment the emitter is edited, so the
    exporter skips anything it makes. What it is worth is that a spray is
    otherwise invisible until the disc is built -- there is no other way to see
    whether an emitter aims where it was meant to.
    """
    from crashbash.scene import read_scene

    shot = read_scene(model_data, model, clips)
    if shot is None or not shot.emitters:
        return 0, 0
    old = bpy.data.collections.get(N.COLLECTION_PREVIEW.format(stem=stem))
    if old is not None:
        for obj in list(old.objects):
            bpy.data.objects.remove(obj, do_unlink=True)
        bpy.data.collections.remove(old)
    preview = bpy.data.collections.new(N.COLLECTION_PREVIEW.format(stem=stem))
    collection.children.link(preview)

    meshes = {obj.get(N.PROP_MESH): obj.data for obj in collection.all_objects
              if obj.type == "MESH" and obj.get(N.PROP_MESH) is not None}
    made = frames = 0
    for index, emitter in enumerate(shot.emitters):
        data = meshes.get(emitter.mesh_index)
        slots = [bpy.data.objects.new(f"{stem}_spark{index:02d}_{n:03d}", data)
                 for n in range(emitter.budget)]
        for obj in slots:
            obj[N.PROP_PREVIEW] = True
            preview.objects.link(obj)
        made += len(slots)
        for tick in range(emitter.start, emitter.end + 1):
            live = emitter.particles(tick)
            frames += 1
            for slot, obj in enumerate(slots):
                shown = live[slot] if slot < len(live) else None
                if shown is None:
                    obj.scale = (0.0, 0.0, 0.0)
                else:
                    obj.location = to_blender(
                        np.asarray([shown.position], dtype=np.float64) / SCALE)[0]
                    # The spin is about the console's own up axis, which is
                    # Blender's Z after the change of basis.
                    obj.rotation_euler = (0.0, 0.0, shown.spin)
                    obj.scale = (shown.scale,) * 3
                obj.keyframe_insert("location", frame=tick + 1)
                obj.keyframe_insert("rotation_euler", frame=tick + 1)
                obj.keyframe_insert("scale", frame=tick + 1)
    return made, frames


# --- the whole model ------------------------------------------------------


def build_model(entry: str, model_data: bytes, pack_data: bytes | None,
                source: str, pack_entry: str = "") -> tuple[bpy.types.Collection,
                                                            list[str]]:
    """Everything in one model entry, as a collection ready to be edited."""
    from crashbash.formats import modelimport as MI
    from crashbash.formats.anim import read_animations
    from crashbash.formats.mdl import read_model
    from crashbash.formats.tex import read_pack

    notes: list[str] = []
    model = read_model(model_data)
    pack = read_pack(pack_data) if pack_data else None
    clips = read_animations(model_data, model)
    stem = entry.rsplit("/", 1)[-1].rsplit(".", 1)[0]

    collection = bpy.data.collections.new(stem)
    collection[N.PROP_ENTRY] = entry
    collection[N.PROP_SOURCE] = source
    collection[N.PROP_PACK] = pack_entry
    bpy.context.scene.collection.children.link(collection)

    materials = Materials(stem, pack)
    for mesh_record in model.meshes:
        payload = MI.payload_from_model(model_data, model, pack,
                                        mesh_record.index, clips)
        if payload is None:
            continue
        name = N.OBJECT_MESH.format(stem=stem, index=mesh_record.index)
        # The same weld the payload used, for the pool entry behind each of its
        # vertices -- the shape keys are stated in pool order and have to be
        # gathered through it.
        pool = np.round(np.asarray(mesh_record.positions, dtype=np.float64)
                        / SCALE)
        _, first = MI.weld_vertices(pool, pool_poses(mesh_record, clips))
        mesh, mapping = build_mesh(name, payload, materials, notes)
        rows = None if mapping is None else first[mapping]
        obj = bpy.data.objects.new(name, mesh)
        obj[N.PROP_MESH] = mesh_record.index
        if mesh_record.volumes:
            # Shown, not edited. §8.4: for a character this is the collision
            # body gameplay reads live, and the writer carries the shipped
            # block through a rebuild untouched -- zeroing it let the crate
            # game's crates be walked through.
            obj[N.PROP_VOLUMES] = [
                {"offset": list(v.offset), "half_width": v.half_width,
                 "height": v.height, "depth": v.depth, "flags": v.flags}
                for v in mesh_record.volumes
            ]
        collection.objects.link(obj)
        build_clips(obj, mesh, rows, clips, stem, notes)

    # Object-pool meshes are what a level actually draws (§8.3): in
    # `warp_room1` not one of the 81 placements names any of the 42 numbered
    # meshes. They can be rebuilt, but only inside the span each already owns --
    # the pool is one packed run and a mesh whose blocks leave it boots to a
    # black screen -- so the writer refuses with a measurement when a rebuild
    # does not fit rather than building that disc.
    by_id: dict[int, bpy.types.Object] = {}
    for record in model.objects:
        if record.mesh is None:
            continue
        payload = MI.payload_from_model(model_data, model, pack,
                                        record.mesh.index, clips)
        if payload is None:
            continue
        name = N.OBJECT_POOL.format(stem=stem, id=record.id)
        pool = np.round(np.asarray(record.mesh.positions, dtype=np.float64)
                        / SCALE)
        _, first = MI.weld_vertices(pool, pool_poses(record.mesh, clips))
        mesh, mapping = build_mesh(name, payload, materials, notes)
        obj = bpy.data.objects.new(name, mesh)
        obj[N.PROP_OBJECT] = record.id
        obj[N.PROP_MESH] = record.mesh.index
        collection.objects.link(obj)
        by_id[record.id] = obj
        build_clips(obj, mesh, None if mapping is None else first[mapping],
                    clips, stem, notes)

    _build_placements(collection, model, stem, by_id, notes)
    _build_shot(collection, model, model_data, clips, stem, notes)

    scene = bpy.context.scene
    if scene.render.fps != N.FRAMES_PER_SECOND:
        scene.render.fps = N.FRAMES_PER_SECOND
        scene.render.fps_base = 1.0
        notes.append("scene frame rate set to 30, which is the game's tick")
    # The images are Non-Color on purpose; Standard is what shows them as the
    # console does, without a film curve over the top.
    if scene.view_settings.view_transform != "Standard":
        scene.view_settings.view_transform = "Standard"
    for warning in model.warnings + (pack.warnings if pack else []):
        notes.append(warning)
    return collection, notes
