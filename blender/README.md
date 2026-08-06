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
| a shape key per clip keyframe | that clip's stored pose, switched off |
| an action per clip | the clip's frame table, at 30 fps |
| an object in *placements* | one record of the level's list (§8.5) |
| an empty in *shot* | a particle emitter, every field of it (§9.11.7) |
| `crashbash_volumes` on the object | the `+0x2C` collision records, shown not edited |
| `crashbash_scene` on the collection | the whole shot, carried through untouched |

A **level** draws what its placement list names and nothing else — `warp_room1`
has 81 records and not one names any of its 42 numbered meshes — so the
*placements* collection is the room, and the numbered meshes beside it are the
pieces. Move a placement, export, and one record changes: measured on
`warp_room1`, **one byte**, the file the same size.

**Duplicate a placement and the copy becomes a new record.** Select one, press
Shift+D or *Add Placement* in the sidebar, drag it where you want it, export —
measured on `warp_room1`, 81 records to 84 with three of them added, the file
exactly the size it was, all 81 originals byte-identical and nothing removed.
**The disc runs**, with all three drawing where they were dragged and the room's
own 81 objects where they always were — and those three spend the level's
padding down to zero, so the route is good to the last byte a level has.

That the obvious gesture is the working one took a fix: Blender copies custom
properties with the object, so the copy arrives claiming the same
`crashbash_placement` as its original. Read literally that is two objects for
one record, and the second overwrote the first — duplicating and dragging moved
nothing, and lost the original's move with it. Now a record is held by one
object and every other claimant is a new record; the holder is the claimant
still standing where the record stands it, which is the original whenever the
copy is the one that got dragged.

An object built from scratch works too, if you give it a `crashbash_places`
saying which object it draws. How many a level can take is its own padding
divided by a record, and the import note says so: **53 across the game**, 3 for
`warp_room1`, 10 for Oxide's chase level, and none at all for an arena, whose
resident region ends with 6 or 18 bytes of alignment and no room. *Add
Placement* counts down and refuses the one past the end; so does the export,
rather than write a level that cannot load.

A record can also be **re-aimed** rather than added, which costs nothing but a
duplicate: a **spare** is a record whose object another record already places,
the panel marks them, and `warp_room1` has ten (five objects placed more than
once, four of them copies of 0x5041). Point record 69 at 0x501C and the room has
two of that blue arm — twenty-seven bytes, the file the same size.

Either way, remember that a record's translation is an **offset**: a pool mesh
carries its own place in the room, 0x501C being authored centred on
(-9.71, -5.6, 0.4), so a re-aimed record starts out translating the new object
in the old one's frame and needs moving.

An **object-pool mesh** can be rebuilt, but only inside the span it already
owns: the pool is one packed run and a mesh whose blocks leave it boots to a
black screen. The writer measures and refuses rather than build that disc.

That span is also how a model from **another pack** gets into a level, which has
been done and run: `arena/crate_snow`'s penguin stands in `warp_room1` today, in
the 6592 bytes the decorative arm 0x501C owned.

**Borrow Selected Mesh** does it. Import both models, select the mesh you are
borrowing, shift-select the model mesh it replaces, and press it:

```
penguin_mesh00 -> level_object501C: 116 faces replacing 444, 2.0x1.7x2.4
against 3.2x4.7x4.4; 6592 bytes owned and roughly 1722 wanted. cleared 34 shape
keys; baked the texture into the colour on 116 faces; put all 116 faces on
swatch_152 cell (13, 5) (the pinned table allows it); stood it on its own
origin, feet at z=0
```

Those four clauses are four rules that each cost a broken export to find. The
borrowed mesh brings its **clips as shape keys** — 34 of them, a dozen switched
on — and Blender adds them all together, which drew a fan of shards across the
whole room while the exported file was already right; a shard storm here is the
preview lying, not the export. Its **art cannot travel**, because the slots it
names mean other pictures in this pack, so each face's texel is folded into its
vertex colour and every face made a swatch face on the destination's own palette
— with the cell's own value divided back out, or `texel * colour / 128` applies
it twice. And a §8.6 carrier's **UV table is pinned**, so a face needs a triple
the table already holds: `warp_room1` accepts exactly one cell, and putting the
faces anywhere else has the export refuse all 116 of them. Last, it **stands the
mesh on its own origin**, because a record's translation is an offset from where
the mesh is authored — a borrowed model left in its source's frame lands wherever
that frame put it, which for the penguin was outside the room, and reads on
screen as the object simply not being there.

The sizes and the byte count are there to be read before you commit to it. What
the pool mesh owns is the **whole budget** (§8.3) and this writer stripes looser
than the authoring tool, so the figure is a rough reading, not a promise; the
export measures for real and refuses rather than break the pool.

**It brings its own pictures, and takes nobody's slot.** *Bring its own
textures* adds them to the destination pack instead of overwriting anything —
`crate_jungle/arena` went from 62 textures to 68 with all 61 of its own and all
62 of its palettes byte-identical, and each added picture matching the
penguin's to the byte, transparency included. That is possible because §10.4
closed VRAM placement: it was never in the file, and the loader allocates a rect
from the free list a texture's size class names.

The one thing that moves is the **swatch's slot number**. Bit 15 of a texture
entry finds the swatch by it being the pack's *last* texture (§6.2), so a new
record goes in before it and its number rises. A model whose faces name that
number directly would then read the newcomer, and 21 of the 393 models with a
growable table do; the export refuses those rather than repaint them by
accident.

In the seven §8.6 carriers — all five warp rooms and both demo hubs — this is
not available, because the UV table cannot grow and a textured face has nowhere
to put its texels: 2 of the penguin's 29 would have found their triple in
`warp_room1`'s. There the art is baked into the vertex colours instead, and the
report says which wall it hit.

Then place it and export. Done from the buttons alone, `warp_room1` with the
penguin came out **byte for byte identical** to the hand-made version of the
same edit — the one that runs.

## Watching a cutscene

**Bake Shot Preview** in the sidebar plays the shot (§9.11): every actor and
prop keyed along its own track, each clip playing on the shot's clock rather
than its own, the node windows opening and closing, the camera set as the
scene's with the field of view its node names — and the particles with them.
Scrub the timeline and the cutscene plays; `cutscene/level_shot12` comes out as
6 actors, 1 prop and a camera over 198 ticks.

It is a preview and nothing else: everything it makes is marked, the export
ignores it, and the shot itself goes back to the file exactly as it was read.
The meshes a node owns are hidden where they stand at the origin, because that
is not where the shot draws them.

Materials are built with **backface culling on**, which is what the console
does, and the corners are **reversed on the way in** so that Blender's front
face is the console's. Measured on the shipped characters with the file's own
corner order, `chars/crate/crash` mesh 0 encloses −3,235,872 with 86 of its 227
faces pointing away from the mesh's own centre — so a model handed over as the
file states it is inside out to Blender's right-hand rule, and with culling on
you see through the front of it to the inside of its back. That is what put
Crash's face on screen while his back was turned and left Aku Aku with no face.
The reversal comes off again on export, so the file is untouched.

They also take their result back to scene-linear before emitting it. What the
console computes — `texel × colour / 128` — is a display value bound for a TV,
and Blender treats what a shader emits as linear and encodes it on the way to
the screen, so handing the product over as it stands brightens everything.
Measured on a flat quad against the console's own formula: a texel of 128 under
a colour of 128 should draw 128 and drew **188**, 32 drew 99. With the transfer
in place the worst error over the range is **3 of 255**, in the deepest
shadows. Keep the view transform on **Standard**, which the importer sets — the
shader is the exact inverse of that one.

A **particle emitter** has no representation in any interchange format, so it
gets one here: an empty carrying all sixteen fields — the window it runs in,
when it stops spawning, the budget, the rate, the lifetime, the mesh it sprays,
the speed range, the yaw and pitch cones, the acceleration, the damping, the
spin, and the fade and grow ramps. Change one and export: one field, one word,
the file the same size. Every field of every one of the game's 23 emitters was
changed and read back — 368 of 368 survived. **One of those edits has been run on
hardware**: the intro's first emitter taken to a lifetime of 72, a speed range of
(400, 1200) and a window reaching tick 170, exported here, built into a disc by
patching the original image, and confirmed on screen. Read back out of that disc
with the same parser, 1 of its 992 entries differs and it is the same 44,764 bytes
as the one it replaced. You cannot *add* an emitter; a node lives
in a fixed graph. **Bake Particle Preview** in the sidebar runs the game's own
simulation and keyframes every live particle so the spray can be watched; it is
a preview, the export ignores it, and it goes stale as soon as an emitter is
edited.

A slot that *is* the pack's swatch image gets a material per palette —
`tex_045_16x16_4bpp_swatch_p045` — because that image carries none of its own
and 23,413 faces across 225 models name it as an ordinary textured slot. Decoded
the plain way it comes out as the reader's "no palette" magenta, which put pink
patches on a quarter of the game. The palette shown is the one the mesh names for
its own swatch faces; what the console puts in that CLUT is not established
(`0x80028EE8` compares against `0x7FFF` and skips the lookup), so this is a
stated choice rather than a reading of the hardware.

**Semi-transparency is drawn, not ignored.** Bits 13–15 of a face's colour index
are the GPU's blend (§6.3), and 42,969 of the archive's triangles ask for one —
including every particle the intro sprays, all of them `B + F`. Drawn opaque, a
dark spark texture is a black square, which is what the sparks were. A material
carries the mode (`crashbash_blend`, and an `_add` / `_avg` tail on its name) and
the shader wires `B + F`, `B + F/4` and `B/2 + F/2` through Blender's own
blending. `B − F` has no equivalent in EEVEE and is approximated by `B/2 + F/2`;
it is 854 triangles of the archive.

Magenta that is still there afterwards is the file's own: 120 palettes across 46
packs hold `0xFC1F`, and every one of them also holds the `0x0000` the hardware
skips — so it is a colour, and it draws.

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

## Budgets

The sidebar shows what the model can run out of and how much of it is spent,
because every one of these was a disc that did not work:

```
colour entries     4516 / 8192     [###########.........]
uv entries         2770                pinned: this model is a §8.6 carrier
placements           81 / 84       [###################.]
mesh region      167936 / 167936   [####################]
texture slots       170                a pack can be appended to
strips (mesh)         7 / 348      [....................]
mesh span (mesh)    644 / 644      [####################]
```

A limit of *no limit* is an answer, not a gap: a pack can be appended to, so its
slot count has no ceiling, while a colour index has thirteen bits and does. The
two rows that read full on almost every model are telling the truth — an
object-pool mesh owns exactly the bytes it uses, and the mesh region ends where
`i32@0x50` says, so the room there is the clip directory's own bytes and a model
with no clips has none. Those are the two walls a level edit actually hits.

The export says the same thing after the fact: a budget that has gone over is
reported with the number, and one that crossed 90 % while the edit was growing
it is reported too, so it is read before the next edit rather than after.

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

None of that can see the two failures only a console shows, so one rebuilt mesh
has been put on one. `intro_eurocom`'s mesh 6 — the M of the logo — was scaled
×1.9 in Blender, exported here, and built into a disc: **1 mesh rebuilt, 27
untouched**, the colour table chained 1162 → 1208 of its 8192, and the entry came
back 180 bytes *smaller* than the shipped one. It draws. So a strip list this
project built rather than copied walks correctly, and a corner order handed over
as §11.3 states it survives the backface test — a strip list that overruns draws
stale vertices only once the model moves, and a triangle handed over backwards is
culled and renders as nothing.
