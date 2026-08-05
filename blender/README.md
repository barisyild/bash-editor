# Crash Bash for Blender

A Blender add-on that opens `CRASHBSH.DAT`'s models directly — geometry,
textures, palettes and every animation clip — edits them as ordinary Blender
data, and writes a model entry back. There is no interchange format in the
middle.

It is not a second implementation of the format. Everything it knows comes from
[`crashbash/`](../crashbash), the same package the desktop editor drives, so a
rule learned on one side is in force on the other. The add-on is the Blender end
of that library and nothing more.

## Installing

```bash
.venv/bin/python blender/build_addon.py
```

That writes `out/io_scene_crashbash.zip` with the library vendored inside it.
Install it through **Edit → Preferences → Get Extensions → Install from Disk**.
Blender ships numpy, which is the only thing the library needs.

Working from a checkout instead, point **Preferences → Add-ons → Crash Bash
Model → crash-bash-editor** at the repository; the add-on finds `crashbash`
there. Running Blender from inside the checkout needs no setting at all.

## Using it

**File → Import → Crash Bash Model.** Pick `SCUS_945.70` (or `CRASHBSH.DAT`)
and choose a model from the list; a loose `.mdl` works too and takes its
sibling `.tex` if one is beside it. What arrives is a collection holding:

| in Blender | in the file |
| --- | --- |
| one object per mesh | `model.meshes[NN]`, or an object-pool mesh (§8.3) |
| `crashbash_colour`, a corner colour attribute | the per-corner colour, 0..1 standing for 0..255 |
| `crashbash_uv` | the texel the face samples, in its own texture's space |
| a `tex_NNN_...` material | that pack slot, its picture decoded |
| a `swatch_NNN` material | an untextured face's palette (§6.2); the UV is the cell |
| a shape key per clip keyframe | that clip's stored pose |
| an action per clip | the clip's frame table, at 30 fps |
| an object in *placements* | one record of the level's list (§8.5) |
| an empty in *shot* | a particle emitter, every field of it (§9.11.7) |
| `crashbash_volumes` on the object | the `+0x2C` collision records, shown not edited |
| `crashbash_scene` on the collection | the whole shot, carried through untouched |

A **level** draws what its placement list names and nothing else — `warp_room1`
has 81 records and not one names any of its 42 numbered meshes — so the
*placements* collection is the room, and the numbered meshes beside it are the
pieces. Move a placement, export, and one record changes: measured on
`warp_room1`, **one byte**, the file the same size. The list cannot be made
longer (§8.5), so a new object there is not a new placement.

An **object-pool mesh** can be rebuilt, but only inside the span it already
owns: the pool is one packed run and a mesh whose blocks leave it boots to a
black screen. The writer measures and refuses rather than build that disc.

A **particle emitter** has no representation in any interchange format, so it
gets one here: an empty carrying all sixteen fields — the window it runs in,
when it stops spawning, the budget, the rate, the lifetime, the mesh it sprays,
the speed range, the yaw and pitch cones, the acceleration, the damping, the
spin, and the fade and grow ramps. Change one and export: one field, one word,
the file the same size. Every field of every one of the game's 23 emitters was
changed and read back — 368 of 368 survived. You cannot *add* one; a node lives
in a fixed graph. **Bake Particle Preview** in the sidebar runs the game's own
simulation and keyframes every live particle so the spray can be watched; it is
a preview, the export ignores it, and it goes stale as soon as an emitter is
edited.

**Texture animation** is shown but not edited. A slot the pack flips through
stored frames, or slides under its own UVs, says so on its material
(`crashbash_flipbook`, `crashbash_scroll`) and in the import's notes — 86 of the
game's packs animate a texture. What you see and can repaint is the base frame;
nothing in this project writes a flipbook's frames or a scroller's rate yet, and
both are carried through untouched.

**File → Export → Crash Bash Model** writes the entry back, and a `.tex`
beside it when a texture was repainted. Take the result into the desktop editor
with *Replace selected file…* and build a disc. Import and export with nothing
changed and the bytes come back identical — meshes recognised as untouched,
clips copied, the shot's tracks, camera keys and emitters written back exactly
as the file stated them.

The identity of a mesh is a custom property, not its name — rename, duplicate
or reorder objects freely. Blender's `.001` suffix, which broke matching on the
glTF path more than once, cannot reach anything here.

## What it refuses

The add-on stops an export rather than writing a file that draws wrong. Each of
these was a silent loss first and a console screenshot second:

* a mesh with no colour attribute, which would draw at full brightness;
* a UV outside the slot it names — a slot cannot be resized (§10.1), so on
  hardware the triangle samples whatever shares its page;
* a material carrying a picture but naming no slot, whose faces would come back
  flat.

Warnings, which do not stop it, cover the rest: a mesh wound against the one it
replaces, unapplied modifiers, a scene not at 30 fps, object-pool meshes left
behind because their blocks may not leave the pool (§8.3).

## Checking it

```bash
Blender --background --factory-startup --python blender/roundtrip.py -- game/SCUS_945.70 90
```

Three checks per model, each against the **shipped entry** rather than against
anything this project wrote on the way:

* **Untouched.** Import, export, and demand the same bytes back. One byte out
  means something was re-derived that should have been carried.
* **A placement edit.** Move one record and confirm that exactly that record
  moved, the file kept its size, and no mesh was rebuilt.
* **A full rebuild.** Force every mesh through the writer and compare triangle
  by triangle. A triangle counts only when its positions, its three corner
  colours, its UVs, its texture entry *and its corner order* all survive — the
  corner order is the facing, and the console culls a triangle it gets backwards
  (§11.3). Clips are compared frame by frame over the triangles they draw.

`tools/native_roundtrip.py` runs the rebuild comparison over the whole archive
without Blender in the way, which is how the library half is checked on its own:
347,509 of 347,509 triangles and 132,330 of 132,330 swatch faces.
