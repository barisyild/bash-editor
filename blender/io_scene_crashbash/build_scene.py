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

import re

import numpy as np

import bpy

from crashbash.formats import placewrite

from . import actions, naming as N

# The shape keys a clip's poses are stored under. The exporter reads them back
# with the same pattern; it lives here because the shot preview matches them too.
SHAPE_KEY_NAME = re.compile(r"^(?P<label>.+)#(?P<key>\d+)$")

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


# sRGB, and it has to be: measured emission to file byte, Blender's Standard
# view transform is that curve to the unit. `(c + 0.055) / 1.055` raised to 2.4
# is its inverse above the toe, and the toe itself is worth one more pair of
# nodes -- without them black leaves at 3 of 255 instead of 0.
SRGB_OFFSET = 0.055
SRGB_SCALE = 1.0 / 1.055
SRGB_POWER = 2.4
SRGB_AT_ZERO = (SRGB_OFFSET * SRGB_SCALE) ** SRGB_POWER


def _to_linear(tree, source):
    """Take a display value back to scene-linear, so the view transform undoes it.

    `texel * colour / 128` is what the console puts on a TV -- an 8-bit display
    value -- and Blender treats what a shader emits as scene-linear and encodes
    it on the way to the screen. Handing the product over as it stands therefore
    brightens everything, and not slightly: measured on a flat quad, a texel of
    128 under a colour of 128 should draw 128 and drew **188**; 32 drew 99. That
    is the washed-out look, and it was in the viewport and the render alike.
    """
    offset = tree.nodes.new("ShaderNodeVectorMath")
    offset.operation = "ADD"
    offset.location = (100, -240)
    offset.inputs[1].default_value = (SRGB_OFFSET,) * 3
    tree.links.new(source.outputs["Vector"], offset.inputs[0])

    scale = tree.nodes.new("ShaderNodeVectorMath")
    scale.operation = "SCALE"
    scale.location = (240, -240)
    scale.inputs["Scale"].default_value = SRGB_SCALE
    tree.links.new(offset.outputs["Vector"], scale.inputs[0])

    curve = tree.nodes.new("ShaderNodeGamma")
    curve.location = (380, -240)
    curve.inputs["Gamma"].default_value = SRGB_POWER
    tree.links.new(scale.outputs["Vector"], curve.inputs["Color"])

    # The curve does not pass through the origin -- sRGB has a linear toe there
    # -- so its value at zero comes off again and the result is floored.
    lift = tree.nodes.new("ShaderNodeVectorMath")
    lift.operation = "SUBTRACT"
    lift.location = (520, -240)
    lift.inputs[1].default_value = (SRGB_AT_ZERO,) * 3
    tree.links.new(curve.outputs["Color"], lift.inputs[0])

    floor = tree.nodes.new("ShaderNodeVectorMath")
    floor.operation = "MAXIMUM"
    floor.location = (660, -240)
    floor.inputs[1].default_value = (0.0, 0.0, 0.0)
    tree.links.new(lift.outputs["Vector"], floor.inputs[0])
    return floor


# The GPU's semi-transparency, by the value of the colour index's top three
# bits (§6.3). Bit 15 turns blending on and 13-14 pick the mode, and the two
# never occur apart, so 0..3 are opaque and 4..7 are the four ABR modes. What
# each mode does to the background B and the fragment F, and how much of F this
# preview adds for it.
BLEND_ADD = {5: 1.0, 7: 0.25}          # B + F, B + F/4
BLEND_AVERAGE = {4: 0.5, 6: 0.5}       # B/2 + F/2, and B - F approximated by it


def _shader(material: bpy.types.Material, image: bpy.types.Image | None,
            blend: int = 0) -> None:
    """Draw the face the way the console does: texel x colour x 2, no lighting.

    An emission shader rather than a lit one, because the vertex colour *is* the
    lighting -- the hardware has no other. Nearest-neighbour sampling, because
    the console has no other either, and a bilinear preview hides exactly the
    single-texel reads a swatch face is made of.

    `blend` is the colour index's top three bits (§6.3). 42,969 of the archive's
    triangles ask for semi-transparency and every particle in `intro_eurocom`
    asks for `B + F` -- drawn opaque, a dark spark texture is a black square,
    which is what the sparks were. `B - F` has no equivalent in EEVEE and is
    approximated by `B/2 + F/2`; it is 854 triangles of the archive.
    """
    material.use_nodes = True
    # The console culls backfaces, so a surface seen from its wrong side is not
    # there -- and a shot's camera stands inside the room it films. Left off,
    # the backdrop shell drew over everything the shot was pointed at.
    material.use_backface_culling = True
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

    display = _to_linear(tree, gain)

    def blended(alpha) -> bool:
        """Wire the surface through the GPU's blend, and say whether it did."""
        share = BLEND_ADD.get(blend) or BLEND_AVERAGE.get(blend)
        if share is None:
            return False
        material.use_backface_culling = True
        if hasattr(material, "surface_render_method"):
            material.surface_render_method = "BLENDED"
        elif hasattr(material, "blend_method"):
            material.blend_method = "BLEND"
        transparent = tree.nodes.new("ShaderNodeBsdfTransparent")
        transparent.location = (400, 160)
        if blend in BLEND_ADD:
            # B + F: the background shows through everywhere and the fragment
            # is added on top, which is what an Add Shader over a Transparent
            # BSDF does. The skip pixel is honoured by scaling F by the alpha,
            # so a skipped texel adds nothing at all.
            weight = tree.nodes.new("ShaderNodeVectorMath")
            weight.operation = "SCALE"
            weight.location = (300, -240)
            weight.inputs["Scale"].default_value = share
            tree.links.new(display.outputs["Vector"], weight.inputs[0])
            if alpha is not None:
                fade = tree.nodes.new("ShaderNodeVectorMath")
                fade.operation = "SCALE"
                fade.location = (340, -320)
                tree.links.new(weight.outputs["Vector"], fade.inputs[0])
                tree.links.new(alpha, fade.inputs["Scale"])
                weight = fade
            tree.links.new(weight.outputs["Vector"], emission.inputs["Color"])
            add = tree.nodes.new("ShaderNodeAddShader")
            add.location = (520, 0)
            tree.links.new(transparent.outputs["BSDF"], add.inputs[0])
            tree.links.new(emission.outputs["Emission"], add.inputs[1])
            tree.links.new(add.outputs["Shader"], output.inputs["Surface"])
            return True
        # B/2 + F/2, and B - F approximated by it: half of each.
        tree.links.new(display.outputs["Vector"], emission.inputs["Color"])
        mix = tree.nodes.new("ShaderNodeMixShader")
        mix.location = (520, 0)
        mix.inputs["Fac"].default_value = share
        if alpha is not None:
            scale = tree.nodes.new("ShaderNodeMath")
            scale.operation = "MULTIPLY"
            scale.location = (340, 120)
            scale.inputs[1].default_value = share
            tree.links.new(alpha, scale.inputs[0])
            tree.links.new(scale.outputs["Value"], mix.inputs["Fac"])
        tree.links.new(transparent.outputs["BSDF"], mix.inputs[1])
        tree.links.new(emission.outputs["Emission"], mix.inputs[2])
        tree.links.new(mix.outputs["Shader"], output.inputs["Surface"])
        return True

    if image is None:
        tree.links.new(attribute.outputs["Color"], gain.inputs[0])
        if blended(None):
            return
        tree.links.new(display.outputs["Vector"], emission.inputs["Color"])
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
    if blended(texture.outputs["Alpha"]):
        return
    tree.links.new(display.outputs["Vector"], emission.inputs["Color"])

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

    def for_slot(self, slot: int, notes: list[str],
                 palette: int | None = None,
                 blend: int = 0) -> bpy.types.Material:
        texture = (self.pack.textures[slot]
                   if self.pack and 0 <= slot < len(self.pack.textures) else None)
        # A slot may *be* the pack's swatch image -- 23,413 textured faces
        # across 225 models name it -- and that image carries no palette of its
        # own, so decoding it the ordinary way gives the reader's "no palette"
        # magenta and a quarter of the game's models come in with pink patches.
        # The disassembly says the descriptor simply gets no CLUT (0x80028EE8
        # compares against 0x7FFF and skips the lookup), so what the console
        # puts there is not established. Showing it through the palette the
        # mesh names for its own swatch faces is a choice, and a far better one
        # than magenta.
        if texture is not None and texture.is_swatch and palette is not None:
            key = ("slot", slot, palette, blend)
            if key in self.by_key:
                return self.by_key[key]
            material = bpy.data.materials.new(
                f"{texture.name}_p{palette:03d}{_blend_suffix(blend)}")
            material[N.PROP_SLOT] = slot
            material[N.PROP_BLEND] = blend
            _shader(material, _image(
                f"{N.IMAGE_SLOT.format(stem=self.stem, slot=slot)}_p{palette:03d}",
                texture.to_rgba(self.pack.palettes, palette_override=palette)),
                blend)
            self.by_key[key] = material
            return material
        key = ("slot", slot, blend)
        if key in self.by_key:
            return self.by_key[key]
        if texture is None:
            # A model may name a slot its own pack does not hold -- the cutscene
            # models do, drawing from a pack the shot loads alongside them. The
            # picture cannot be shown, but the entry and its texels still have
            # to survive the trip, so the UVs are addressed against a nominal
            # 256x256: a stored texel is a byte, so that carries all of them.
            material = bpy.data.materials.new(
                f"tex_{slot:03d}_unresolved{_blend_suffix(blend)}")
            material[N.PROP_SLOT] = slot
            material[N.PROP_BLEND] = blend
            _shader(material, None, blend)
            notes.append(
                f"slot {slot} is not in this pack ({len(self.pack.textures) if self.pack else 0} "
                f"textures), so its faces have no picture to show; the slot and "
                f"its UVs are carried through unchanged")
            self.by_key[key] = material
            return material
        # The pack's own name for it, `_swatch` suffix and all: a face may name
        # the swatch image as an ordinary slot, and that is worth seeing in the
        # material list rather than discovering from a magenta preview.
        material = bpy.data.materials.new(texture.name + _blend_suffix(blend))
        material[N.PROP_SLOT] = slot
        material[N.PROP_BLEND] = blend
        _shader(material, _image(
            N.IMAGE_SLOT.format(stem=self.stem, slot=slot),
            texture.to_rgba(self.pack.palettes)), blend)
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

    def for_swatch(self, palette: int, notes: list[str],
                   blend: int = 0) -> bpy.types.Material:
        key = ("swatch", palette, blend)
        if key in self.by_key:
            return self.by_key[key]
        material = bpy.data.materials.new(
            N.MATERIAL_SWATCH.format(palette=palette) + _blend_suffix(blend))
        material[N.PROP_PALETTE] = palette
        material[N.PROP_BLEND] = blend
        image = None
        if self.swatch is not None:
            # Decoded through this palette, so the artist sees the colours the
            # face actually means rather than a grid of indices.
            image = _image(
                N.IMAGE_SWATCH.format(stem=self.stem, palette=palette),
                self.swatch.to_rgba(self.pack.palettes, palette_override=palette))
        _shader(material, image, blend)
        self.by_key[key] = material
        return material

    def plain(self, blend: int = 0) -> bpy.types.Material:
        key = ("plain", blend)
        if key not in self.by_key:
            material = bpy.data.materials.new(
                N.MATERIAL_PLAIN + _blend_suffix(blend))
            material[N.PROP_BLEND] = blend
            _shader(material, None, blend)
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


# Blender's polygon normal follows the right-hand rule over its loops, and the
# console's front face is the other way round. Measured over the shipped
# characters with the file's own corner order: `chars/crate/crash` mesh 0
# encloses -3,235,872 and only 86 of its 227 faces point away from the mesh's
# own centre; `crate/coco`, `warp/coco` and the cutscene casts all agree in
# sign. So a character handed to Blender as the file states it is inside out to
# Blender, and with backface culling on -- which is what the console does --
# you see through the front of a model to the inside of its back. That is what
# put Crash's face on screen while his back was turned.
#
# The corners are therefore reversed on the way in and reversed again on the
# way out, so the file's own convention is untouched and the round trip is
# exact. Which direction the *file* calls outward is a separate question, and
# §11.3 answers it the other way; this is only about matching two renderers.
CORNER_ORDER = (0, 2, 1)


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


def _blend_suffix(blend: int) -> str:
    """A readable tail for a material that asks for semi-transparency (§6.3)."""
    return {4: "_avg", 5: "_add", 6: "_sub", 7: "_add4"}.get(blend, "")


def _entry_material(entry: int, materials: Materials, notes: list[str],
                    swatch: int = 0, blend: int = 0) -> bpy.types.Material:
    if entry >= 0:
        return materials.for_slot(entry, notes,
                                  (swatch & 0x1FF) if swatch else None, blend)
    if entry < -1:
        return materials.for_swatch((-entry) & 0x1FF, notes, blend)
    return materials.plain(blend)


def build_mesh(name: str, payload, materials: Materials, notes: list[str],
               swatch: int = 0):
    """One `MeshPayload` as a Blender mesh, with its colours, UVs and materials.

    Returns the mesh and the vertex map: which of the payload's vertices each
    Blender vertex is, which is `None` only when the fallback below fires.
    """
    faces = payload.positions.shape[0]
    vertices, indices = _weld(payload)
    # Reversed, so Blender's front face is the console's. See `CORNER_ORDER`.
    indices = indices[:, CORNER_ORDER]
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
        indices = np.arange(faces * 3).reshape(-1, 3)[:, CORNER_ORDER]
        mapping = (np.asarray(payload.corner_vertices).reshape(-1)
                   if payload.corner_vertices is not None else None)
        mesh.from_pydata(to_blender(vertices).tolist(), [],
                         [tuple(int(v) for v in row) for row in indices])
        mesh.validate(verbose=False)
        notes.append(
            f"{name}: {dropped} face(s) share their three corners with another, "
            f"so the mesh is built one vertex per corner and has no shared edges")

    # Materials, and the face -> material map that carries the texture entry.
    order: dict[tuple, int] = {}
    material_index = np.zeros(faces, dtype=np.int32)
    blends = (payload.blend if payload.blend is not None
              else np.zeros(faces, dtype=np.uint8))
    for face in range(faces):
        # The blend is per face and belongs in the key: a slot drawn opaque and
        # the same slot drawn additively are two materials.
        key = (int(payload.textures[face]), int(blends[face]))
        if key not in order:
            order[key] = len(order)
            mesh.materials.append(
                _entry_material(key[0], materials, notes, swatch, key[1]))
        material_index[face] = order[key]
    mesh.polygons.foreach_set("material_index", material_index)

    # Colours per corner, 0..1 standing for the file's 0..255.
    colours = np.ones((faces, 3, 4), dtype=np.float32)
    # The attributes follow the corners, so they are reversed with them.
    colours[..., :3] = payload.colours[:, CORNER_ORDER].astype(np.float32) / 255.0
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
        texel = payload.uvs[face][list(CORNER_ORDER)].astype(np.float64)
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


def _build_placements(collection, model, model_data: bytes, stem: str,
                      by_id: dict, notes: list[str]) -> None:
    """One object per placement record, standing where the record stands it.

    This is what a level *is*: it draws what its placement list names and
    nothing else (§8.5). `warp_room1` has 81 records and not one of them names
    any of the 42 meshes in `model.meshes`, so opening a level and seeing the
    numbered meshes at the origin is seeing the pieces, not the room.

    Each object shares the mesh data of the pool mesh its id resolves to, so
    moving one moves that copy alone. Editing the mesh edits every copy, which
    is what the file does too.

    **A record's translation is an offset, not a position.** A pool mesh carries
    its own place in the room: `warp_room1`'s 0x501C is authored centred on
    (-9.71, -5.6, 0.4) and the record that places it translates it by nothing.
    Dragging a placement in Blender is unaffected -- the object is drawn where
    it belongs and the delta comes out right -- but re-aiming a spare at another
    object leaves the old record's translation applied in the *new* mesh's
    frame, which puts it a long way from where that record used to draw.
    """
    if not model.instances:
        return
    places = bpy.data.collections.new(N.COLLECTION_PLACES.format(stem=stem))
    collection.children.link(places)
    missing = 0
    # Which records are duplicates, so the panel can point at them. Saying a
    # spare exists is not the same as saying which one it is: `warp_room1` has
    # 81 records and five spares, and picking wrong deletes something from the
    # room instead of adding to it.
    spare = set(placewrite.spare_records(model))
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
        if instance.index in spare:
            obj[N.PROP_SPARE] = True
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
    room = placewrite.spare_capacity(model_data, model)
    notes.append(
        f"{len(model.instances)} placements. {len(spare)} are spare -- a record "
        f"whose object another record already places -- and re-aiming one puts "
        f"something new in the level at the cost of a duplicate. "
        + (f"The list can also take {room} more record(s): put an object in this "
           f"collection with a '{N.PROP_PLACES}' saying what it places, and the "
           f"export appends it."
           if room else
           "The list cannot be made longer in this level -- its resident region "
           "ends without the padding a new record grows into."))
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


def _point(scaled) -> tuple[float, float, float]:
    """A shot's point -- eye, target, a key's position -- into Blender units."""
    return tuple(float(v) for v in
                 to_blender(np.asarray([scaled], dtype=np.float64) / SCALE)[0])


def _scale(scale) -> tuple[float, float, float]:
    """A track key's scale into Blender's axes.

    It is a diagonal in the model's frame, so the basis change permutes it the
    same way it permutes a point: `B diag(x, y, z) B^-1 = diag(x, z, y)`. The
    shot squashes and stretches -- `intro_eurocom` asks for 32 distinct scales
    and (0.75, 2.0, 1.0) among them -- so assigning it unswapped stretches a
    letter into the screen instead of up, which is what made the logo sit
    oddly as it landed. A check that compared the file's scale against the
    object's agreed with itself the whole time, because both were unswapped.
    """
    x, y, z = (float(v) for v in scale)
    return (x, z, y)


def _orientation(quaternion):
    """A key's rotation into Blender's frame, basis change and all."""
    from mathutils import Matrix  # noqa: PLC0415

    from crashbash.scene import rotation_matrix

    turn = BASIS @ rotation_matrix(quaternion) @ BASIS.T
    return Matrix([[float(v) for v in row] for row in turn]).to_quaternion()


def _shot_object(name, data, collection, marker: str):
    obj = bpy.data.objects.new(name, data)
    obj[N.PROP_PREVIEW] = True
    obj[marker] = True
    obj.rotation_mode = "QUATERNION"
    collection.objects.link(obj)
    return obj


def _key_window(obj, first: int, last: int, start: int, end: int) -> None:
    """Hide the object outside the window its node is open in.

    Keyed rather than faked by scaling to zero, which is what an interchange
    format has to do: a node is drawn while its window is open and not before
    or after, and `level_shot8` has one that opens at tick 63 in a shot running
    295..372 -- so it never opens at all.
    """
    for frame, hidden in ((first, start > first), (start, False),
                          (end + 1, True), (last, True)):
        if not first <= frame <= last:
            continue
        obj.hide_viewport = obj.hide_render = hidden
        obj.keyframe_insert("hide_viewport", frame=frame + 1)
        obj.keyframe_insert("hide_render", frame=frame + 1)
    for curve in (obj.animation_data.action and actions.curves(
            obj.animation_data.action) or []):
        if "hide" in curve.data_path:
            for point in curve.keyframe_points:
                point.interpolation = "CONSTANT"


def bake_shot(collection, model_data: bytes, model, clips, stem: str) -> dict:
    """The whole shot as Blender animation: actors, props, camera, particles.

    A cutscene is not its meshes standing at the origin. It is nodes carrying
    those meshes along tracks while a camera films them (§9.11), and until this
    existed the add-on could open a shot and never play it. Everything it makes
    is a preview keyed on the shot's own 30 Hz clock: the export ignores it, and
    the shot itself is carried back to the file exactly as it was read.
    """
    from crashbash.scene import read_scene

    shot = read_scene(model_data, model, clips)
    if shot is None:
        return {}
    old = bpy.data.collections.get(N.COLLECTION_PREVIEW.format(stem=stem))
    if old is not None:
        for obj in list(old.all_objects):
            bpy.data.objects.remove(obj, do_unlink=True)
        bpy.data.collections.remove(old)
    preview = bpy.data.collections.new(N.COLLECTION_PREVIEW.format(stem=stem))
    collection.children.link(preview)

    sources = {obj.get(N.PROP_MESH): obj for obj in collection.all_objects
               if obj.type == "MESH" and obj.get(N.PROP_MESH) is not None
               and not obj.get(N.PROP_PREVIEW)}
    first, last = shot.start, shot.end
    made = {"props": 0, "actors": 0, "cameras": 0, "particles": 0,
            "ticks": max(last - first + 1, 0)}

    for number, prop in enumerate(shot.props):
        source = sources.get(prop.mesh_index)
        if source is None:
            continue
        obj = _shot_object(f"{stem}_prop{number:02d}", source.data, preview,
                           N.PROP_SHOT_PROP)
        for tick in range(first, last + 1):
            position, rotation, scale = prop.track.at(tick)
            obj.location = _point(position)
            obj.rotation_quaternion = _orientation(rotation)
            obj.scale = _scale(scale)
            for path in ("location", "rotation_quaternion", "scale"):
                obj.keyframe_insert(path, frame=tick + 1)
        _key_window(obj, first, last, prop.track.start, prop.track.end)
        made["props"] += 1

    for number, actor in enumerate(shot.actors):
        source = sources.get(actor.mesh_index)
        if source is None:
            continue
        # Its own copy of the mesh: an actor plays a clip on the *shot's* clock
        # and the source object plays it on the clip's own, and one set of shape
        # keys cannot be driven two ways.
        data = source.data.copy()
        obj = _shot_object(f"{stem}_actor{number:02d}", data, preview,
                           N.PROP_SHOT_ACTOR)
        clip = clips[actor.clip_index] if 0 <= actor.clip_index < len(clips) else None
        blocks = {}
        if clip is not None and data.shape_keys is not None:
            # Every clip of the mesh is a shape key on it, and Blender's are
            # *relative*: a key left at 1 adds its whole delta to whatever else
            # is set. The copy arrives with the source object's values frozen
            # at whatever frame it was on, so an actor playing one clip was
            # also carrying every other clip at full strength -- 21 of
            # `level_shot12` actor 0's 28 keys, which pulled Crash's arms into
            # spikes. The ones this clip does not use are removed outright.
            for block in list(data.shape_keys.key_blocks):
                if block == data.shape_keys.reference_key:
                    continue
                match = SHAPE_KEY_NAME.match(block.name)
                if match and match.group("label") == clip.label:
                    blocks[int(match.group("key"))] = block
                else:
                    obj.shape_key_remove(block)
            if blocks:
                actions.assign(data.shape_keys,
                               actions.make(f"{stem}_shot_actor{number:02d}"))
        at_key = {k: i for i, k in enumerate(clip.keyframes())} if clip else {}
        for tick in range(first, last + 1):
            position, rotation, scale = actor.track.at(tick)
            obj.location = _point(position)
            obj.rotation_quaternion = _orientation(rotation)
            obj.scale = _scale(scale)
            for path in ("location", "rotation_quaternion", "scale"):
                obj.keyframe_insert(path, frame=tick + 1)
            if not blocks or clip is None:
                continue
            frame = clip.frames[actor.frame(tick, clip.frame_count)]
            blend = frame.weight / 4096.0 if frame.key_b else 0.0
            wanted = {at_key.get(frame.key_a): 1.0 - blend}
            if frame.key_b:
                wanted[at_key.get(frame.key_b)] = wanted.get(
                    at_key.get(frame.key_b), 0.0) + blend
            for number_key, block in blocks.items():
                block.value = wanted.get(number_key, 0.0)
                block.keyframe_insert("value", frame=tick + 1)
        _key_window(obj, first, last, actor.track.start, actor.track.end)
        made["actors"] += 1

    if shot.cameras:
        from mathutils import Vector  # noqa: PLC0415

        lens = bpy.data.cameras.new(f"{stem}_camera")
        lens.sensor_fit = "VERTICAL"
        obj = _shot_object(f"{stem}_camera", lens, preview, N.PROP_SHOT_CAMERA)
        obj.rotation_mode = "QUATERNION"
        for tick in range(first, last + 1):
            camera = shot.camera_at(tick)
            if camera is None:
                continue
            eye, target = camera.at(tick)
            obj.location = _point(eye)
            obj.rotation_quaternion = (
                Vector(_point(target)) - Vector(_point(eye))
            ).to_track_quat("-Z", "Y")
            lens.angle_y = np.radians(camera.field_of_view)
            obj.keyframe_insert("location", frame=tick + 1)
            obj.keyframe_insert("rotation_quaternion", frame=tick + 1)
            lens.keyframe_insert("lens", frame=tick + 1)
        made["cameras"] = len(shot.cameras)
        bpy.context.scene.camera = obj

    made["particles"] = _bake_particles(preview, shot, sources, stem)

    # A mesh a node owns is drawn where the node carries it and nowhere else,
    # so the source object standing at the origin is not part of the shot --
    # and with the camera's eye a quarter of a unit from that origin, the pile
    # of them is the only thing it can see.
    owned = shot.mesh_indices
    for index, obj in sources.items():
        if index in owned:
            obj.hide_set(True)
            obj.hide_render = True
    made["hidden"] = sum(1 for i in sources if i in owned)

    scene = bpy.context.scene
    scene.frame_start, scene.frame_end = first + 1, last + 1
    scene.frame_set(first + 1)
    return made


def _bake_particles(preview, shot, sources, stem: str) -> int:
    """Instance every live particle, tick by tick, so the spray can be watched.

    A preview and nothing else: it is keyframed from the simulation the game
    runs (§9.11.7) and goes stale the moment the emitter is edited, so the
    exporter skips anything it makes. What it is worth is that a spray is
    otherwise invisible until the disc is built -- there is no other way to see
    whether an emitter aims where it was meant to.
    """
    made = 0
    for index, emitter in enumerate(shot.emitters):
        source = sources.get(emitter.mesh_index)
        data = source.data if source is not None else None
        slots = [bpy.data.objects.new(f"{stem}_spark{index:02d}_{n:03d}", data)
                 for n in range(emitter.budget)]
        for obj in slots:
            obj[N.PROP_PREVIEW] = True
            preview.objects.link(obj)
        made += len(slots)
        for tick in range(emitter.start, emitter.end + 1):
            live = emitter.particles(tick)
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
    return made


# --- the whole model ------------------------------------------------------


def build_model(entry: str, model_data: bytes, pack_data: bytes | None,
                source: str, pack_entry: str = "") -> tuple[bpy.types.Collection,
                                                            list[str]]:
    """Everything in one model entry, as a collection ready to be edited."""
    from crashbash.formats import mdlwrite as MW
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
        mesh, mapping = build_mesh(name, payload, materials, notes,
                                   MW._swatch_entry(model_data, mesh_record))
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
        mesh, mapping = build_mesh(name, payload, materials, notes,
                                   MW._swatch_entry(model_data, record.mesh))
        obj = bpy.data.objects.new(name, mesh)
        obj[N.PROP_OBJECT] = record.id
        obj[N.PROP_MESH] = record.mesh.index
        collection.objects.link(obj)
        by_id[record.id] = obj
        build_clips(obj, mesh, None if mapping is None else first[mapping],
                    clips, stem, notes)

    _build_placements(collection, model, model_data, stem, by_id, notes)
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
