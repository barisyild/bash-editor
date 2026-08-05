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
| one object per mesh | `model.meshes[NN]`, or an object-pool mesh (read only) |
| `crashbash_colour`, a corner colour attribute | the per-corner colour, 0..1 standing for 0..255 |
| `crashbash_uv` | the texel the face samples, in its own texture's space |
| a `tex_NNN_...` material | that pack slot, its picture decoded |
| a `swatch_NNN` material | an untextured face's palette (§6.2); the UV is the cell |
| a shape key per clip keyframe | that clip's stored pose |
| an action per clip | the clip's frame table, at 30 fps |
| `crashbash_volumes` on the object | the `+0x2C` collision records, shown not edited |

**File → Export → Crash Bash Model** writes the entry back, and a `.tex`
beside it when a texture was repainted. Take the result into the desktop editor
with *Replace selected file…* and build a disc.

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
Blender --background --factory-startup --python blender/roundtrip.py -- game/SCUS_945.70 60
```

Imports each model, reads it straight back out and compares against the
**shipped entry** — not against anything this project wrote on the way. A
triangle counts only when its positions, its three corner colours, its UVs, its
texture entry *and its corner order* all survive; the corner order is the
facing, and the console culls a triangle it gets backwards (§11.3). Clips are
compared frame by frame over the triangles they draw.

`tools/native_roundtrip.py` runs the same comparison over the whole archive
without Blender in the way, which is how the library half is checked on its own.
