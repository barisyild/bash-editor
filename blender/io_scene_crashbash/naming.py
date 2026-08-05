"""The contract between the two halves of the add-on, in one place.

Everything the importer writes into a `.blend` and the exporter reads back out
is named here. Two rules decide the shapes below, and both were paid for on the
glTF path:

* **Identity lives in a custom property, never in a name.** Blender suffixes any
  name that collides with one already in the file, which is exactly what happens
  when an artist duplicates the mesh they are replacing -- and `_mesh07` became
  `_mesh07.001`, matched nothing, and the import refused a model that was ready.
  A property survives renaming, duplication and the outliner.
* **Nothing is folded.** A swatch face names its palette and its cell, a colour
  keeps its full 0..255 range, and the winding arrives as authored. Every one of
  those was lost in translation to glTF and had to be guessed back.
"""

from __future__ import annotations

# --- object and collection properties ------------------------------------

# On the collection: where the model came from, so an export can put it back.
PROP_ENTRY = "crashbash_entry"      # archive entry name, e.g. models/chars/crate/coco.mdl
PROP_SOURCE = "crashbash_source"    # path to the game EXE or a loose .mdl
PROP_PACK = "crashbash_pack"        # entry name or path of the sibling .tex

# On an object: which mesh of that model it is. Exactly one of these is set.
PROP_MESH = "crashbash_mesh"        # index into `model.meshes`
PROP_OBJECT = "crashbash_object"    # 0x5000-namespace object id, for pool meshes

# On an object, for information: what the file says and the add-on does not edit.
PROP_VOLUMES = "crashbash_volumes"  # the +0x2C collision records (§8.4)
PROP_CLIPS = "crashbash_clips"      # {clip label: action name}
PROP_FRAMES = "crashbash_frames"    # {clip label: frame count the file states}

# On a material: what the face it paints reads.
PROP_SLOT = "crashbash_slot"        # pack texture slot, for a textured face
PROP_PALETTE = "crashbash_palette"  # palette index, for a swatch face (§6.2)

# --- mesh layers ---------------------------------------------------------

# Corner colours, 0..1 standing for the file's 0..255. Float rather than byte:
# the hardware draws `texel * colour / 128`, so 255 is a 2x multiplier and a
# byte-colour attribute would be read back through Blender's own conversion.
COLOUR_ATTRIBUTE = "crashbash_colour"
UV_LAYER = "crashbash_uv"

# --- names ---------------------------------------------------------------

MATERIAL_SLOT = "tex_{slot:03d}_{width}x{height}_{depth}bpp"
MATERIAL_SWATCH = "swatch_{palette:03d}"
MATERIAL_PLAIN = "swatch_default"
IMAGE_SLOT = "{stem}_tex_{slot:03d}"
IMAGE_SWATCH = "{stem}_swatch_{palette:03d}"
OBJECT_MESH = "{stem}_mesh{index:02d}"
OBJECT_POOL = "{stem}_object{id:04X}"
# A shape key per stored pose. The label is the clip's, and the number is its
# position in that clip's key list -- which is what the frame table indexes.
SHAPE_KEY = "{label}#{key:02d}"
ACTION = "{stem}_{label}"

# The game ticks at 30 Hz and Blender defaults to 24. A scene left at 24
# resamples every clip on the way in and again on the way out, so the importer
# sets it and the exporter checks it.
FRAMES_PER_SECOND = 30

# What a UV is addressed against when the slot it names is not in this model's
# pack -- the cutscene models draw from a pack the shot loads alongside them.
# A stored texel is one byte, so 256 carries every value a face can hold and
# the round trip stays exact even though nothing can be previewed.
UNKNOWN_SIZE = (256, 256)
