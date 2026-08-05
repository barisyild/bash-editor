# Bash Editor

An editor for Crash Bash's `CRASHBSH.DAT`, cross-platform (macOS / Windows / Linux)
in pure Python — PySide6 + OpenGL, no platform-specific code.

[![Discord](https://img.shields.io/badge/Discord-join%20the%20server-5865F2?logo=discord&logoColor=white)](https://discord.gg/3KYcUfHsPA)

**Where it stands.** Reading is done and verified: every model, texture, sound bank
and animation clip in the game parses, renders and exports. Writing works: entries
can be replaced and the disc rebuilt or patched in place, meshes can be transplanted
between models or built from scratch, animation clips rewritten, and texture pixels
and palettes replaced inside their slots. The proof is a whole foreign character —
Spyro, from a COLLADA file — re-striped, re-textured into orphaned pack slots, given
newly authored clips, and running in the game's menu. [Towards editing](#towards-editing)
lists what still cannot be written.

The container format and the build/MD5 table come from
[CTR-tools](https://github.com/CTR-tools/CTR-tools) (dcxdemo). The 3D format was
re-derived here against the game's own executable; several readings that made earlier
exports come out scrambled are corrected, and the whole format is written up in
[docs/FORMAT.md](docs/FORMAT.md).

**This project is vibecoded.** All of it — the readers and writers, the format
specification, the disc tooling, the GUI — was written by Claude (Claude Code) in
conversation, with a human directing, testing every build in the game, and reporting
what actually happened on screen. The method wasn't guesswork, though: format claims
were measured against all 992 files or the executable's own code before being written
down, and the in-game tests caught what no offline check could — which is how the
collision volumes and the spin-mesh swap were found.

![Bash Editor](docs/screenshot.png)

## Running

The quickest route is a build from the [releases
page](https://github.com/barisyild/bash-editor/releases): a standalone app per
platform, Python and Qt bundled in, nothing to install. Each release is built by
CI from the tagged source — Windows x64, macOS arm64 and x64, Linux x64.

Each archive unpacks into a folder holding the application and an empty `game`
beside it. **Put your Crash Bash files in that folder** — the extracted disc as
it comes, EXE and `CRASHBSH.DAT` together — and the editor opens it on launch.
That folder is the whole configuration: a packaged build reads its game from
there and nowhere else, so a copy of the editor always edits the game it was
unpacked next to. If the folder is missing the application makes it and says so.

Two things those builds cannot do for themselves. They are unsigned, so **macOS
quarantines a downloaded app**; clear it once with

```bash
xattr -dr com.apple.quarantine "Bash Editor.app"
```

and on **Linux** Qt still wants the system's own graphics libraries, which are
driver-side and cannot be bundled — `sudo apt install libgl1 libxkbcommon-x11-0`
covers Debian and Ubuntu.

### From the source

There is a launcher per platform. Each one creates the virtual environment the
first time, reinstalls when `requirements.txt` changes, and starts the editor —
nothing to set up by hand, and a game EXE can be passed straight to it to open
that game on launch.

| | |
| --- | --- |
| Windows | double-click **`run.bat`** |
| macOS | double-click **`run.command`**, or `./run.sh` from a terminal |
| Linux | `./run.sh` |

Python 3.10 or newer has to be on `PATH`; the launcher says so plainly, with the
install command for your platform, if it is not. On a bare Linux install Qt also
wants its system libraries — `libgl1` and `libxkbcommon-x11-0` cover it on Debian
and Ubuntu. For a menu entry rather than a terminal, a `.desktop` file pointing
at `run.sh` is all it takes:

```ini
[Desktop Entry]
Type=Application
Name=Bash Editor
Exec=/path/to/crash-bash-editor/run.sh
Terminal=false
```

By hand, if you would rather:

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python app/main.py
```

Then **File → Open game EXE…** and pick your `SCUS_945.70` (or drop the EXE or its
folder onto the window). `CRASHBSH.DAT` must sit next to the EXE or in a `CRASHBSH/`
subfolder — the file table lives inside the EXE, so both are needed. The game files
are not part of this repository; point the editor at your own copy.

The last-opened EXE is reloaded automatically on the next launch.

## What is readable

Measured against the NTSC-U release (992 entries):

| | Result |
| --- | --- |
| Archive | 992/992 entries, with the real file names |
| Models | 5990/5990 meshes reconstruct exactly the triangle count the file states |
| Level sets | 1971 object meshes over 73 arenas, warp rooms and hubs, all matching their stated triangle count |
| Level layout | 2689 placement records read, every one resolving to the object or clip it names |
| Colours | gouraud, three per triangle, semi-transparency flags decoded |
| Textures | 15160 textures from 400 packs, 4- and 8-bit, with transparency |
| Texture animation | 136 flipbooks over 1137 frames, and 108 scrolling textures |
| Animation | 1037 clips over 49167 frames, in 225 of 400 models |
| Sound | 160/160 VAB banks, 2581 samples decoded to PCM, 57 sequences |

Other builds (PAL, JPN, the prototypes and demos) load too, but only NTSC-U has known
file names — everything else shows as `00000.mdl` and friends.

## Using it

The tree filters by kind and opens on **Models**, since each model sits next to its
texture pack in the archive and only `.mdl` entries open in the viewport.

| Input | Action |
| --- | --- |
| Left-drag | Orbit |
| Right/middle-drag, or Shift+drag | Pan |
| Wheel, trackpad scroll, or pinch | Zoom |
| `+` / `-` | Zoom in / out |
| `0` | Fit the model, keeping the angle |
| `F` | Reset view |

Toggles for solid / wireframe / points / volumes / textures / vertex colours sit
under the viewport, and individual meshes can be hidden from the **Model** panel.

**Volumes** draws the gameplay volume a mesh carries. For a playable character
that is its collision body, and you can see the two it has: Crash stands in a
128-unit half-width, and the spin mesh he switches to widens it to 307. It is
drawn as a box because the record carries two horizontal extents and they
differ in 25 of the 349 records that set the second — a crate's volume is
exactly its own 256-unit cube. Only 812 of the archive's meshes carry one at
all, nearly all of them characters and props, since a level's floors and walls
have none. Nothing found so far reads the block, so what you see is the record's
own shape rather than a proven test volume.

An arena or a warp room keeps almost none of itself in the numbered mesh array the
file counts: the floor, the walls, the lamp posts and the level boards are *objects*,
a second array the game reaches by id. They are listed alongside the meshes under the
id that names them — `object 5001`.

Where each one stands is a separate list again, and a level leans on it heavily: Pogo
Painter's play grid is 72 placements over a handful of tile objects, and Oxide's chase
track is 178 placements over 26. An object is drawn once per placement, each with its
own position and rotation, so a viewer that skips the list piles the whole set on the
origin and leaves a hole where the floor should be. The level's sky is a mesh wrapped
around all of it, so the view opens under the dome rather than outside it looking at
its back.

**Vertex colours** is worth knowing about. The file stores three colours per triangle
and they multiply into the texture, so switching them off is the only way to see a
texture as it sits in the pack — and it separates the surfaces that are genuinely
textured from the ones that are flat-coloured, which on a character is most of the
body. Those colours also carry the model's shading, so with them off the viewport
lights the geometry itself instead.

### Texture animation

A texture pack can carry its own animation, and 86 of the game's 400 do. Two kinds: a
**flipbook** swaps a texture's pixels for one of a run of stored frames — the sparking
explosion, the electric arc, the plasma that flows across a cutscene backdrop — and a
**scroller** slides a texture under its own UVs so a surface appears to move while the
model stays put.

The texture list marks them, `▶ 12` for a twelve-frame flipbook and `⇄` for a scroller,
and selecting a flipbook gives you a play button and a frame slider. In the model
viewport they play by themselves on a 30 Hz clock, independent of the model's own
animation as they are in game; **Animate textures** under the viewport turns that off.

What identifies the frames beyond argument: each is exactly as long as the texture's own
pixel data, in all 136 flipbooks — and frame 0 is byte-identical to that data. The texture
as stored *is* the first frame of its own animation.

### Placements

A level draws what its placement list names (§8.5), so the **Placements** tab is
what changes a room. Each record names an object and stands it somewhere;
selecting one fills in its id and translation, and *Apply to entry* rewrites that
record and stages the file for the next build. Nothing moves and the file keeps
its size — a record is 160 bytes rewritten where it lies.

The list cannot be made longer. Its array is boxed in by four more arrays that
begin where it ends, the resident region holds no run of zeros big enough to
relocate 81 records into, and nothing past the shipped resident size is there at
run time — the same array copied beyond the old end of file, with `0x50` grown to
cover it, drew nothing at all. So a room's only spare capacity is a record whose
object is placed elsewhere too; those are marked **spare**, and `warp_room1` has
ten. Spending one of them put a second green panel in that room while the first
kept its own door.

### Meshes the game never draws

A level draws what its placement list names, not everything the file holds. `warp_room1`
has 81 placements and not one of them names any of its 42 numbered meshes — the room you
walk through is object-pool meshes, and geometry written into a numbered one is correct on
disc and never appears on screen. **Hide meshes no placement reaches**, under the mesh
list, is on by default so the viewport shows what the game shows; untick it to look at the
rest. The mesh list marks them either way. A model with no placement list at all — the
menu, which draws its meshes from code — is left alone.

### Animation

A model's clips are listed under the mesh list, with a play button and a frame slider.
Clip names are stored only as a hash, so the panel shows a name when one guess fits,
both when two do (`HIT / HOP`), and the raw hash when none does.

### Sound

Selecting an `.sfx` entry lists the bank's samples; pressing play decodes the SPU-ADPCM
and plays it. Whole banks export as WAV. The music sequences are PS1 `SEQp` data and
would need a sequencer driving the bank to play, which this editor does not have —
export those for a tool that does.

## glTF export

**File → Export model as glTF…** (`Ctrl+G`) writes one `.glb` holding the geometry,
the textures and every animation clip. It is the route out to a modelling tool, and
the mapping is exact rather than approximate:

| In the file | In glTF |
| --- | --- |
| keyframe | morph target (POSITION deltas) |
| frame record: keyframe A, keyframe B, 12-bit weight | one sample of a `weights` channel, two entries set |
| `A + (B − A) · w` | what a morph target already means |
| per-triangle gouraud colour | `COLOR_0` |
| per-triangle texture | one primitive per texture, one material each |

Rebuilding every pose as a weighted sum of keyframe poses — what a glTF renderer
does with that weights channel — reproduces the decoder's own output to within
0.0039 model units, and that figure is exactly 1/256: one step of the fixed point
the positions are stored in. The gap is the game's rounding, not a mismatch.

All 373 models with geometry export, carrying 1037 clips, and an independent glTF
library reads them all back.

**A cutscene exports as the shot, not just the cast.** Every actor and prop moves
along its own track, an actor's clip plays on the shot's clock rather than its own,
and the camera is a real glTF camera carrying the field of view the file names — so
`level_intro` opens in Blender already framed the way the game frames it, and
playing the timeline plays the scene. A level exports the same way where it has a
scene of its own. Everything is sampled once per tick over the shot's window: a
node's frame mapping is a step function the game recomputes every tick and a camera
cut is a hard change between two nodes, and neither survives being handed to an
interpolating exporter as sparse keys.

Two things have no place in glTF itself. It has no visibility track, so a node off
stage is scaled to zero — the usual convention, and every importer understands it.
And it has no particle system at all, so an emitter cannot be a spray in the scene
graph.

Neither is lost, though. The shot also rides in the file's `extras`, which is where
the glTF spec puts application data and which every loader carries through
untouched: every track and camera key, every emitter with all sixteen fields its
simulation runs on, every placement record — each with the byte offset it came
from. So the file is lossless even where the scene graph is not, and 130 shots
export with **zero** disagreement against what the reader sees in the DAT.

Those offsets are the thing that makes a trip back thinkable. All 7938 of them land
on exactly the bytes they name, once the sub-scene shift each track records is
undone — a sub-scene runs on its own clock and in its parent's frame, so 433 of the
corpus's prop keys are not the file's own values and `extras` says so per track.

In Blender the morph targets arrive as shape keys with the clips driving them, so a
character can be retargeted with the tools that already exist there.

The trip back exists too: **File → Import model from glTF…** (`Ctrl+Shift+G`) rebuilds
the entry's meshes, clips and repainted textures from a `.glb` and stages the result
like any other replacement — previewable in the viewport, written by the ordinary
disc build. Everything is matched by the names the export wrote, so edit freely but
keep the names; a mesh you delete keeps its game geometry, a clip you delete freezes
at rest. One rule matters in Blender: **set the scene to 30 fps before importing the
export** — Blender resamples animation onto its scene's frame grid, and at its
default 24 fps the clips come back audibly off-beat and measurably off-pose. At
30 fps the full round trip — export, Blender save, import — reproduces every pose
exactly, verified against Blender 5.2.

**Warp rooms and hubs import too, on a path of their own.** Those seven files pin two
things the rest do not: their shared colour and UV tables cannot be repointed — moving
the colour pointer crashes the room, moving the UV pointer alone scrambles every
textured surface — and their §8.6 block cannot change file offset, because each door's
object record indexes a table of *file* offsets that the game streams the block from.
Fourteen hardware probes fix those limits; why the tables are pinned is still unknown
and `docs/FORMAT.md` §14 carries the search. The importer does not need to be told any
of this: a carrier announces itself in its own header, and the pinned **graft layout**
engages automatically — the file stays byte-identical through its old end except the
rebuilt mesh's header and two fields, new geometry lands after the §8.6 block, and new
colours map to the nearest entry already in the palette. Adding a prop to a warp room
is a normal Blender edit; it has been done and run on hardware, previews intact.

**The import reads the shot back too.** Rebuilding the object graph at
`T(0x1C)..T(0x4C)` is still out of reach — the writers have never touched it and the
record kinds past the nodes are unread (`docs/FORMAT.md` §8.3), so adding a node or
changing how many keys one has means writing a region nobody has decoded. But
*changing what an existing record holds* is a different thing entirely: no count
moves, no size changes, no offset shifts, and the undecoded bytes are never even
read. That is what the offsets in `extras` are for, and it is what the importer now
does — it patches each field where it already sits, before any mesh rebuild moves a
boundary. Moving a camera, sliding a prop or repositioning a level object is the
reachable half; adding or deleting one is not.

The test is the one this project trusts: read the shot, write it straight back, and
compare against the game's own bytes. Across the 205 models that carry a placement
list or a shot the result is **byte-identical in 205 of 205** — 2689 placement
records, 5819 track keys and 173 camera keys rewritten, every one reproducing what
shipped, **nothing skipped**. And an edit lands where it should: nudging one
placement 100 units in x changes exactly **one byte**, the x word going 35 → 25635,
a delta of 25600 = 100 × 256 in the file's own 8.8 fixed point.

A sub-scene is the case that needed care. Its keys are read on the parent's clock
*and* in the parent's frame, so both have to be run backwards: `extras` carries the
clock shift and the parent's own placement, and the writer inverts each in turn.
Carrying the placement is what closed it — without it the frame cannot be undone
from the file alone, and those 117 keys could only be skipped.

`tools/roundtrip.py` runs the whole thing over the corpus — export each entry,
import the file just written, and compare what the two draw:

| Group | Files | Triangles | Same count | Worst corner | Scenes | Byte-identical | Scene only |
| --- | --- | --- | --- | --- | --- | --- | --- |
| level | 134 | 196,700 | 196,700 | **0.0000** | 113 | 113 | 5 |
| cutscene | 64 | 119,220 | 119,220 | **0.0000** | 64 | 64 | 0 |
| character | 104 | 45,300 | 45,300 | **0.0000** | 0 | 0 | 0 |
| models | 98 | 93,373 | 93,373 | **0.0000** | 28 | 28 | 0 |

Nothing fails. Every corner of all 454,593 triangles comes back where it went out,
and sorting the corners before comparing is what makes that number mean anything —
a rebuild re-strips the mesh, so the triangles return in a different order, and
comparing the two lists in sequence measures the re-ordering instead of the loss
(it reported errors of 5 to 20 units on models that had lost nothing).

The last column is the honest asterisk. Five arenas — `arena/test/objects`,
`medieval_ring/arena`, `tank_jungle/arena`, `tank_jungle/crystalarena` and
`crate_jungle/arena` — have **no numbered meshes at all**: their geometry lives
entirely in the object pool (`docs/FORMAT.md` §8.3), which the export does not name
`_meshNN` and the writers cannot install into. Their **scene still imports** — all
56 placement records between them — and the import says in its report that it left
the geometry alone. They are counted apart rather than as a rebuild that never ran.

Colours need one word, because the console uses them at two scales: on a textured
triangle the colour is a *multiplier* — the blend is `texel * colour / 128`, above
128 it brightens, and half the game's colours are above 128 — while an untextured
triangle draws its colour *directly*, no texel and no doubling. The export writes
each corner at its own scale, and glTF display stops at 1, so the true values ride
twice: in `COLOR_0`,
which faithful multipliers (the three.js family) show at full brightness and strict
viewers clamp — display only — and in a `_CRASHBASH_COLOR` attribute that Blender
passes through untouched. For the trip back, **tick Data → Attributes in Blender's
glTF export**; the importer then recovers every colour byte exactly, and warns when
the attribute is missing rather than dimming silently. Blender's own viewport still
draws with the clamped channel and relights in linear space, so hot-lit models (the
cutscene casts) look paler there than in game — run
[tools/blender_colours.py](tools/blender_colours.py) once after importing (Text
Editor → Run Script) and Blender shows the game's own colours: it rebinds the
materials to the unclamped channel, applies the gamma the game multiplies in, and
sets the view transform to Standard.
Re-striping on import also reorders the vertex pool, which is why the importer always
rewrites the clips with the mesh — the two cannot be imported separately, and are not.

## The Blender add-on

Everything above goes through glTF, and most of the care it needs is care about
glTF: a colour attribute Blender only exports when a material reads it, a slot
number smuggled through a material name, the `.001` suffix, the swatch texel
folded into the vertex colour because glTF has nowhere to put a palette.
[`blender/`](blender) skips the translation — it opens a `.mdl` entry directly,
as Blender data, and writes one back.

```bash
.venv/bin/python blender/build_addon.py     # -> out/io_scene_crashbash.zip
```

Install it through **Edit → Preferences → Get Extensions → Install from Disk**;
the library travels inside the zip and numpy, which Blender ships, is the only
thing it needs. Then **File → Import → Crash Bash Model**, pick `SCUS_945.70`
and choose a model. Meshes arrive as objects, texture slots and swatch palettes
as materials, clip keyframes as shape keys, clip timings as actions at 30 fps,
and the collision volumes as a property you can read. **File → Export → Crash
Bash Model** writes the entry back, ready for *Replace selected file…* here.

A **level** comes in as its placement list (§8.5) — the objects the room is
actually made of, standing where the file stands them — so moving one and
exporting changes that record and nothing else: one byte on `warp_room1`, the
file the same size. A **cutscene** comes in with its particle emitters
(§9.11.7), every field of them editable, and *Bake Particle Preview* runs the
game's own simulation so the spray can be watched before the disc is built.

It is not a second implementation: the add-on and this editor drive the same
`crashbash` package, so a rule learned on one side is in force on the other.
[blender/README.md](blender/README.md) says what maps to what and what an export
refuses. An import that changes nothing comes back **byte for byte identical** —
a character, a level and a cutscene all measured at 0 bytes different — and
forcing 90 shipped models through the writer reproduces 71,311 of 71,311
triangles, position, colour, UV, texture entry and corner order alike.

## Command line

```bash
.venv/bin/python -m crashbash.cli info    game/SCUS_945.70
.venv/bin/python -m crashbash.cli list    game/SCUS_945.70 -f chars/
.venv/bin/python -m crashbash.cli extract game/SCUS_945.70 -o out
.venv/bin/python -m crashbash.cli obj     game/SCUS_945.70 -o out -f chars/
.venv/bin/python -m crashbash.cli glb     game/SCUS_945.70 -o out -f chars/
.venv/bin/python -m crashbash.cli audio   game/SCUS_945.70 -o out   # VB/VH/SEQ
.venv/bin/python -m crashbash.cli wav     game/SCUS_945.70 -o out   # decoded WAV
.venv/bin/python -m crashbash.cli png     game/SCUS_945.70 -o out   # needs pillow
```

## Editing and building a disc

**File → Replace selected file…** (`Ctrl+R`) stages a file from disk in place of the
selected entry. The panels immediately show the staged content rather than what is on
the disc, so a swap can be checked before anything is written, and the tree marks the
entry with a bullet. **Revert selected file** puts it back.

Replacements are held in memory until a build, because a build rewrites both index
tables in the executable and has to see every change at once.

**File → Build disc…** (`Ctrl+B`) writes a playable `.bin`/`.cue`, or from the command
line:

```bash
.venv/bin/python -m crashbash.cli build game/SCUS_945.70 -o out/disc -i out/crashbash.bin
```

Either first writes a complete disc tree: `CRASHBSH.DAT` repacked, `SCUS_945.70` patched
to match, and everything else copied through. It then re-reads its own output with the
same parser the editor uses and checks every entry against what was meant for it — the
replacement where there was one, the original everywhere else — so a build that quietly
corrupts the table cannot pass. Only then does it write the image.

The DAT has no directory of its own — the game finds an entry through a table of 992
`(sector, size)` pairs compiled into the executable, and loads entries a *group* at a
time through a second table of 130 records. Writing one entry means rewriting both
tables, and they have to agree exactly. Entries keep their index and groups keep their
membership, so anything referring to either by number still works.

The output is packed tighter than the disc's own layout, on purpose. The original
reserves a spare sector for 12 entries and leaves padding inside 8 groups, which makes
a group's span disagree with the byte count the loader reads with; packing tight makes
the two identical and saves 24 KB.

### The disc image

A folder is not a disc: no PS1 emulator boots one, they all want an image. So the
image is written here rather than handed to an external mastering tool —
`crashbash/iso.py` is a self-contained CD-XA writer, Mode 2 Form 1 sectors with real
EDC and both Reed-Solomon parity passes. Those are not taken on trust: run against the
sectors of a pressed Crash Bash disc they reproduce its own EDC, P parity, Q parity and
addresses bit for bit.

There are two ways to get an image, and the difference matters:

**Patching your own disc image** — the default the editor offers, and the better one.
It copies your `.bin` and rewrites only the sectors of the two files that changed,
keeping each sector's existing subheader and address. Everything else on the disc is
untouched, byte for byte. A 73 MB archive swap takes about two seconds.

```bash
.venv/bin/python -m crashbash.cli build game/SCUS_945.70 -o out/disc \
    -i out/crashbash.bin --original "Crash Bash.bin"
```

**Mastering the extracted folder** — needs nothing but the folder, and loses two
things it cannot recover, both reported as warnings rather than passed over. The
licence area is Sony's and is not in this repository, so the image runs in emulators
but not on a console. And `SPYRO3/SPEECH.STR` is a Mode 2 **Form 2** stream: on the
disc it is 2324 bytes per sector across some thirty interleaved XA channels, but an
extracted copy keeps only the first 2048 bytes of each and no channel numbers at all,
so the demo's speech cannot be rebuilt from it.

This is also why `BASHY.` is written as ordinary data. Its length divides exactly by
2352, which reads like a raw sector stream — the disc says otherwise, and so does the
file: 31 MB of zeroes with no sync pattern anywhere. It is padding that pushes the real
data to the outside of the disc. The check is for the sync pattern, not the size.

## Towards editing

[docs/IMPORTING.md](docs/IMPORTING.md) is the record of the whole import pipeline —
custom geometry, custom textures, custom animation — as proven by putting Spyro into
the menu, with the failure that taught each rule. What is still missing, and why:
[docs/FORMAT.md](docs/FORMAT.md) §14 lists every open question.

**Geometry.** Writable in principle today — the strip list, the vertex pool, the
per-triangle UV/texture/colour arrays and the shared tables are all confirmed, so a
mesh can be rebuilt from scratch. The catch is that a strip list is a fixed partition
of the vertex pool: changing a triangle count means re-striping the mesh, not patching
a field.

**Textures.** Reading is solid, writing is not. How a pack is placed in VRAM is still
unknown: pack header `0x14` and texture record `+0x04..+0x07`, `+0x0E`, `+0x10` are
unidentified, so a repacked pack could decode correctly here and still land wrong on
the console. Replacing the pixels of an existing texture is safe; adding one or changing
its size is not. Flipbook frames are as safe as the texture itself, being the same
size by construction.

**Animation.** The clip format is fully decoded, including the shared position pool and
the blend weights, so clips can be rewritten. What the per-frame auxiliary block holds
is still unknown, so an editor must copy it through untouched.

**Sound.** VB/VH/SEQ are standard PS1 formats, so a bank can be rebuilt with existing
tools. Only the trimmed VAG offset table is non-standard, and it is documented.

## Format

[docs/FORMAT.md](docs/FORMAT.md) is the reference: every field with an offset, a type
and a confidence marker, the corpus measurement behind each claim, and the disassembly
that settles it. The short version of what matters most:

- Every pointer, in the file header and the mesh headers alike, is relative to its own
  position: `ptr = base + field_offset + value`.
- A mesh's strip list gives each strip's triangle count in the **high** byte of its
  `u16`, with flags in the low byte and a high byte of `0xFF` ending the list. Each
  strip spans `count + 2` vertices from the pool in order. Reading those two bytes the
  other way round is what scrambles other tools' exports.
- Colours are gouraud: the per-triangle colour index names three consecutive table
  entries, one per corner, and those colours already carry the shading — light them
  again and the dark side crushes to black.
- The three UVs correspond to the strip's three vertices **positionally**. Rotating a
  triangle's corners to normalise its facing, without rotating the UVs alongside, lands
  the texture on it mirrored.
- A texture entry's bit 15 does not mean "untextured": the triangle samples the pack's
  palette-less swatch texture, and the entry's low 9 bits name a **palette** instead of
  a texture. That is how one mesh paints itself in several colour schemes from a single
  16×16 image.

## Layout

```
crashbash/            format library, no GUI dependency
  binreader.py        little-endian reader, PS1 fixed-point scales
  archive.py          EXE version table, DAT entry table, extraction
  formats/mdl.py      models: bounds, vertices, strips, colours, UVs, textures
  formats/anim.py     animation clips: keyframes, blending, posed vertices
  formats/tex.py      texture packs: BGR555 palettes, 4/8-bit images
  formats/sfx.py      sound banks: VAB header, SPU-ADPCM decoder, WAV output
  formats/mdlwrite.py stripping, mesh install and transplant, table sharing
  formats/animwrite.py clip blobs: frame records, keyframes, shared pool
  formats/texwrite.py pixels and palettes replaced inside their slots
  formats/gltf.py     glTF 2.0 export: geometry, textures, morph animation, shots
  formats/gltfread.py poses back out of a .glb, matched by rest position
  formats/gltfimport.py the import path the GUI drives: mesh, clips, textures
  retarget.py         animation between characters of different proportions
  build.py            repack the DAT, patch the EXE tables, write a disc tree
  iso.py              CD-XA writer: master a folder, or patch an existing image
  cli.py              headless commands
app/                  PySide6 GUI
  glview.py           OpenGL 3.3 core viewport, orbit camera, textured, animated
  atlas.py            packs a .tex pack into one atlas for the viewport
  panels.py           file tree, mesh list, animation, texture/audio/hex panels
  window.py           main window and export actions
  main.py             entry point
tools/psxdis.py       MIPS disassembly helper for checking claims against the EXE
tools/blender_colours.py
                      run once in Blender: game-accurate colours in the viewport
docs/FORMAT.md        the format specification, field by field
docs/IMPORTING.md     the import pipeline, each rule with the failure behind it
run.sh / run.command / run.bat
                      launchers: build the venv if needed, then start the editor
packaging/bash-editor.spec
                      PyInstaller recipe for the standalone builds
.github/workflows/release.yml
                      builds all four and attaches them to a published release
```

A standalone build locally, if you want one without waiting for CI:

```bash
.venv/bin/pip install pyinstaller
.venv/bin/pyinstaller --noconfirm packaging/bash-editor.spec
```

## Talking to people

There is a [Discord server](https://discord.gg/3KYcUfHsPA) for this and the
neighbouring reverse-engineering work — the place to bring a model that will not
import, a disc that will not boot, or a field in `docs/FORMAT.md` still marked
unknown that you have worked out.

## Credits

`bash_dat`, the build/MD5 table and `bash_filelist.txt` are from CTR-tools by dcxdemo
and contributors. The specification records where this reading departs from theirs, and
why.
