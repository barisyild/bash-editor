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
    for clip in mine:
        keys = clip.keyframes()
        blocks = []
        for number, key in enumerate(keys):
            pose = clip.pool()[clip._slots(key)].astype(np.float64)
            block = obj.shape_key_add(
                name=N.SHAPE_KEY.format(label=clip.label, key=number),
                from_mix=False)
            moved = to_blender(pose[rows])
            block.data.foreach_set("co", moved.reshape(-1))
            blocks.append(block)

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

    # Object-pool meshes are what a level actually draws (§8.3), so they go in
    # to be looked at -- but nothing here can install one back, and a mesh that
    # cannot go home should not look like one that can.
    for record in model.objects:
        if record.mesh is None:
            continue
        payload = MI.payload_from_model(model_data, model, pack,
                                        record.mesh.index)
        if payload is None:
            continue
        name = N.OBJECT_POOL.format(stem=stem, id=record.id)
        mesh, _ = build_mesh(name, payload, materials, notes)
        obj = bpy.data.objects.new(name, mesh)
        obj[N.PROP_OBJECT] = record.id
        collection.objects.link(obj)

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
