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

# On a placement object: which record of the level's list it stands for (§8.5).
# A level draws what that list names and nothing else, so moving one of these
# and exporting is what changes a level. The list cannot be made longer.
PROP_PLACEMENT = "crashbash_placement"  # index into `model.instances`
PROP_PLACES = "crashbash_places"        # the id the record names
# What the object's transform read back as the moment it was built: nine
# rotation values then three of translation. The file's rotation is quantised
# to 1/4096 and is therefore not exactly orthonormal, and Blender stores a
# transform as location/euler/scale -- so recomposing it does not give the same
# matrix back. Comparing against the file called 24 of `warp_room1`'s 81
# untouched records moved. Comparing against this calls none of them moved.
PROP_PLACE_REST = "crashbash_placement_rest"
# Set on a placement whose object another record already places. The list
# cannot grow (§8.5), so these are the only room a level has for something new,
# and spending one costs a duplicate of whatever it currently draws. Without
# this an artist has 81 records in front of them and no way to tell which one
# is free -- `warp_room1` has ten, spread over five objects it places more than
# once, and 0x5041 alone accounts for four of them.
PROP_SPARE = "crashbash_spare"

# On the collection: the shot as the file holds it (§9.11), JSON. Everything an
# artist does not edit here is carried through untouched, which is how a scene
# survives a trip it was never fully represented in.
PROP_SCENE = "crashbash_scene"
# On an emitter object: the node it patches, and every field the simulation
# runs on (§9.11.7). They are editable and they write back.
PROP_EMITTER = "crashbash_emitter"          # the node's byte offset
EMITTER_FIELDS = (
    # when it runs, and when it stops spawning
    "start", "end", "last_tick",
    # how much it sprays, and what of
    "budget", "per_tick", "lifetime", "mesh",
    # where each particle goes
    "speed", "yaw", "pitch", "accel", "damp", "spin",
    # and how it comes and goes
    "fade", "grow",
)
# On anything the add-on drew only to be looked at. The exporter skips it, and
# it goes stale the moment the emitter it came from is edited.
PROP_PREVIEW = "crashbash_preview"
# What each preview object stands for, so the panel can name it.
PROP_SHOT_PROP = "crashbash_shot_prop"
PROP_SHOT_ACTOR = "crashbash_shot_actor"
PROP_SHOT_CAMERA = "crashbash_shot_camera"

OBJECT_EMITTER = "{stem}_emitter{index:02d}"
COLLECTION_SHOT = "{stem} shot"
COLLECTION_PREVIEW = "{stem} shot preview"

# On an object, for information: what the file says and the add-on does not edit.
PROP_VOLUMES = "crashbash_volumes"  # the +0x2C collision records (§8.4)
PROP_CLIPS = "crashbash_clips"      # {clip label: action name}
PROP_FRAMES = "crashbash_frames"    # {clip label: frame count the file states}
# {clip label: a digest of its poses and its frame table as they arrived}. A
# clip that still matches its digest is copied through byte for byte instead of
# rebuilt: rebuilding one costs a fresh pose pool, and the pose pool is what a
# model weighs -- `chars/crate/coco` came back 163,160 bytes different with not
# a single mesh rebuilt, because thirteen unedited clips were rewritten.
PROP_CLIP_REST = "crashbash_clips_rest"

# On a material: what the face it paints reads.
PROP_SLOT = "crashbash_slot"        # pack texture slot, for a textured face
PROP_PALETTE = "crashbash_palette"  # palette index, for a swatch face (§6.2)
# Texture animation, shown and not edited: a slot the pack flips through stored
# frames, or slides under its own UVs. Nothing in this project writes either
# back yet, so they are carried through as the pack states them.
PROP_FLIPBOOK = "crashbash_flipbook"  # [frame count, frames per second]
PROP_SCROLL = "crashbash_scroll"      # texels a second

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
OBJECT_PLACE = "{stem}_place{index:03d}_{id:04X}"
COLLECTION_MESHES = "{stem} meshes"
COLLECTION_PLACES = "{stem} placements"
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

# On a material: the GPU's semi-transparency for the faces wearing it (§6.3),
# as the colour index's top three bits. Kept so the exporter reads the blend
# back from the material rather than guessing it from corner positions.
PROP_BLEND = "crashbash_blend"
