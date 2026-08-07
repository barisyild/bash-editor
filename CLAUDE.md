# Bash Editor — working notes

An editor for Crash Bash's `CRASHBSH.DAT`: pure Python, PySide6 + OpenGL, no
platform-specific code. `README.md` is the user's view of it. This file is what
an agent should know before changing anything.

## House rules

- **English only.** Comments, docstrings, identifiers, log and UI strings,
  commit messages and documentation stay English even when the conversation is
  in another language.
- **Edit by hand with the editing tools.** No scripted string replacement over
  sources (`sed -i`, `perl -pi`, `str.replace` in a throwaway script).
- **`game/` is never committed.** It is copyrighted disc data (148 MB) the user
  supplies at run time; it is gitignored, and so are `out/` and `.venv/`.

## Setup and commands

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python app/main.py            # or ./run.sh — creates the venv itself
.venv/bin/python -m crashbash.cli info game/SCUS_945.70
```

`run.sh` (macOS/Linux), `run.command` (macOS double-click) and `run.bat`
(Windows) each create the virtual environment on first use, reinstall when
`requirements.txt` changes, and pass their arguments to the editor.

There is no test suite, and adding a synthetic one would prove little: the
material is a real 992-entry archive. Verification means measuring against that
corpus or against the game's own executable — see *Verification* below.

## Where knowledge lives

- **[docs/FORMAT.md](docs/FORMAT.md)** — the specification. Every field carries
  an offset, a type, a confidence marker, the corpus measurement behind it and
  the disassembly that settles it. Anything learned about the format belongs
  here, with its evidence.
- **[docs/IMPORTING.md](docs/IMPORTING.md)** — the import pipeline end to end,
  each rule stated with the failure that taught it.
- **[blender/README.md](blender/README.md)** — the Blender add-on, what it maps
  to what, and what it refuses.
- **[README.md](README.md)** — the user-facing account.

`crashbash/` is the format library and imports no GUI code; `app/` is the
PySide6 front end and `blender/io_scene_crashbash/` is the Blender one. Readers
are `formats/{mdl,anim,tex,sfx}.py`, writers are
`formats/{mdlwrite,animwrite,texwrite}.py`, glTF is `formats/gltf.py` out and
`formats/{gltfread,gltfimport}.py` back in. `build.py` repacks the DAT and
patches the EXE tables; `iso.py` masters or patches the disc image;
`retarget.py` moves animation between differently proportioned characters.

**`formats/modelimport.py` is where an import happens, and both front ends go
through it.** It holds everything that has nothing to do with the file the edit
arrived in — the layout ordering, the untouched-mesh rule, the swatch cell, the
frozen clip, the placement list, the shot, the refusals — behind one
`ImportRequest`. `gltfimport` fills one by reading a `.glb`; the add-on fills
one by reading `bpy` data. A rule added to one front end instead of to the core
is a rule the other will not have, and every trap in this file was learned once
already. The shot's serialisation lives in `scenewrite.scene_extras` for the
same reason: it is the shape both front ends speak, not a glTF detail.

## Invariants a writer must honour

Each of these was learned from a disc that booted into a crash or drew garbage.
They are not style preferences.

- **`model+0x08` is a layout boundary** (§2.1). Every mesh block and table sits
  inside the span it names; every animation blob sits after it. So the order is
  always: `strip_animation` (lift the blobs off) → `install_mesh` /
  `transplant_mesh` (which moves the boundary) → `write_clips` (put them back).
- **New geometry is inserted before `T(0x44)`, never appended after the file**
  (§2.1). Every shipped model keeps `T(0x08) <= T(0x44)` and
  `T(0x08) <= i32@0x50`, 400/400 each, and `0x44` and `0x50` have to move by the
  inserted length. Appending instead breaks both: a warp room has no clips to
  strip, so the only thing between `T(0x44)` and EOF is §8.6's block, and the
  new geometry lands inside it. `warp_room1` built that way would not load.
- **Nothing past the shipped resident size is there at run time, and growing
  `0x50` does not change that.** `warp_room1`'s placement array was copied byte
  for byte past its old EOF with `0x50` grown to cover it and `+0x20` repointed
  — four bytes changed below the old EOF — and **the whole room stopped
  drawing**. The same pointer aimed at a copy *inside* the resident region drew
  its objects. So the array's base is an ordinary live pointer; what failed was
  the destination. `safeadd2`, `safeadd3` and `grow24k` "worked" only because
  nothing ever read the bytes they appended — the graft layout is proven not to
  crash, and has never been shown to deliver data. A level edit must fit inside
  the resident image the file already has.
- **Judge a replacement's winding against the model it came from, not the mesh
  it replaces.** The exporter warned that the penguin encloses −2.052 where the
  arm encloses +0.090, which reads as inside out. It is not: every shipped
  character encloses **negative** (`crate/crash` −3,235,872, `warp/coco`
  −6,771,356) and the built penguin encloses exactly what the shipped penguin
  does, −34,429,474.667 to the unit. The arm is the exception — an open shell,
  where the sign means nothing. The warning is worth keeping and worth checking
  against the source before acting on it.
- **`shape_key_add` hands back a key at 1.0, not at zero.** Relative keys add
  together and the importer assigns an action for the *first* clip only, so
  every other clip's poses stayed fully on and piled onto it: `crate_snow`'s
  penguin drew **32.7 units tall against its own 2.4**, 30 of its 34 keys
  switched on, a fan of shards reaching out of the room. Every model with more
  than one clip was doing this. Nothing in any file was wrong — the export reads
  a key's coordinates, never its value, and the round trip stayed byte-identical
  throughout — so it only ever showed as a preview that could not be trusted,
  which is worse than a visible fault: it was read once as a broken *export*.
  `build_clips` zeroes each key as it makes it. A model brought in to be
  borrowed rather than animated should still have its keys cleared, which
  *Borrow Selected Mesh* does.
- **Import needs the model's sibling `.tex`.** Without it no material resolves
  to a slot and every mesh is rebuilt untextured — silently, until it is on
  screen, where it reads as a texture bug in the game. The importer now refuses
  the case, but the call still has to pass the pack.
- **Own the layout and a table grows where it stands, so nothing is stranded.**
  `modelwrite.relayout` re-emits a model region by region — header, mesh
  headers, each mesh's blocks and attachment, the three shared tables, each
  object-pool mesh, the tail — and recomputes every pointer from where the
  region lands. Hand it `replace` and a region is written from new bytes, so a
  longer colour table simply occupies more of the space it already had and the
  regions after it move on. That is the whole of the fix: `mdlwrite` appended a
  fresh copy and repointed the header, which left the shipped table in the file
  reachable by nothing.
  Measured over the archive: both tables grown by 64 entries each, every
  shipped entry still at its own index, **378 of 378 models** for a median
  **372 bytes**; a plain relayout that rebuilds nothing comes back saying the
  same thing in **400 of 400**. One mesh edited in each of 356 models costs
  **663,092 bytes less** than appending — 154 models smaller, 202 identical,
  **none larger** — and an edit that used to grow 180 of them by 1,023,718
  bytes now grows 109 by 376,738. `boss_oxide/arena` is **+2044 against
  +32,764**, which is the 30,528 stranded bytes gone; `boss_bear/arena` saves
  47,104. `mdlwrite._install_relaid` is the path, and it falls back to the old
  one when it cannot own the file.
- **A plan built only from what the header names silently drops data, and no
  round trip can see it.** Ten gaps across four models hold bytes nothing here
  resolves — `gamelogo_text`'s **7520** of strip words for a mesh its header
  does not declare, most of all. The reader does not read them, so a relaid
  `gamelogo_text` came back **6768 bytes below the file the disc shipped** with
  every check passing. `plan` now carries any gap that is not entirely zero,
  and 400/400 models keep every non-zero byte; the 38,540 bytes that *are* zero
  are padding and alignment reproduces them.
- **A rebuilt mesh's `+0x2C` block has to be carried, and losing it reads as
  the object spinning on the spot.** `_install_relaid` asked `landed` for the
  attachment's new home, but `landed` answers for a region's *start* and an
  attachment block's start usually is not one — it sits in the padding between
  two pool meshes, which `plan` carries as a region beginning at the previous
  mesh's end. Defaulting to zero wrote a null `+0x2C` for exactly the meshes
  being rebuilt. `boss_oxide/arena` came back with 14 of its 16 blocks, the two
  missing ones the two meshes the export touched, and on hardware that arena
  loaded and drew correctly while some of its objects **spun where they stood**.
  Nothing static saw it: the geometry comparison passed, the placements were
  byte-identical, the disc verified 992/992 against intent. `tools/relayout.py`
  now counts the blocks, and a block inside the span a rebuild overwrites is
  copied into that mesh's own blob rather than looked up — 271 of 271 rebuilds
  of a §8.4 carrier keep it.
- **Compare a rebuilt mesh corner by corner, matched on position.** Face order
  changes with re-striping and corner order changes with it, so the first two
  readings of the pinning measurement below said "worst 255 of 255" for *both*
  builds and hid which one was wrong. Matching each corner by its position and
  taking the best colour that corner carries in the source is what separated
  them.
- **Install several meshes in one call, and rebuild only what was edited.**
  `install_mesh` appends the colour table, the UV table *and* the vector pool
  on every call, and each earlier copy is then unreachable. Nine meshes through
  `mainmenu/models` left **983,128 of 1,396,026 bytes unreachable — 70 % of the
  file** — and the game hung on the loading screen. `install_meshes` shares one
  copy and brings the same import to 435 KB / 28 %. Deleting untouched meshes
  before exporting still helps, and for an edit that only re-times animation
  use `import_glb(animation_only=True)`, which leaves every mesh byte-identical.
- **Keyframes carry the vertex flag words** in their low two bits (§9.4). The
  game draws the animated pose, never the static records, so zeros there shred
  a model whose static data is byte-identical to the original. This shipped
  three broken discs.
- **A frame's weight and its second keyframe are one fact.** The archive holds
  `weight == 0` exactly when `key_b` is absent — 13,652 records each way, no
  exception — because a non-zero weight is what selects the game's blend
  decoder, and that decoder reads `key_b`. Dropping the second keyframe while
  keeping the weight sends every frame to blend toward the keyframe at offset 0,
  which is not one: the model came apart *differently on each frame*, by that
  frame's own weight, while every static check passed. The tell was that pausing
  drew it correctly — a paused frame is a weight-0 frame. `animwrite` now
  refuses both halves of the mismatch; all 1037 shipped clips pass.
- **The strip count is what the animation weighs.** A pose stores every vertex
  of the strip pool and a strip of n triangles costs n + 2 vertices, so striping
  is a size lever, not only a legality one. The same 648-triangle mesh went 495
  strips → 1638 pool → 747,672 bytes, and 161 strips → 970 pool: of the 532 KB
  that first build added, **516 KB was animation**.
- **The pool span *was* the wall a level edit hits, and the striper was most of
  it.** The wall is gone in the file — the relaid layout lets a pool mesh's
  region grow — but the striping measurement stands and still decides what an
  edit weighs. 494 of the 820 pool meshes across the 73 levels could not be
  rebuilt at all — the rebuild wanted more bytes than the mesh owns, by a median
  of only **1.093×**. The cause was measurable: against the shipped data this
  writer got **4.56 triangles a strip where the game gets 5.57**, and a strip of
  n triangles costs n + 2 pool entries, so strips are bytes. Seeding and
  continuing **least-connected first** — the standard tristrip heuristic, and
  the one thing this was missing — takes it to **5.16 a strip**, the pool from
  1.058× the shipped size to **1.021×**, and the meshes that fit from 326 to
  **393 of 820**.
  **That gap is closed and the striper is no longer the lever.** Measured over
  120 models with the key `build_blocks` actually uses, this writer gets
  **4.769 triangles a strip against the game's 4.548**, and its pool is **98.5 %
  of the size** — on `warp_room1` 9868 entries against 9826, 100.4 %. What is
  left to win is elsewhere: rebuilding *one* mesh of `warp_room1` costs a flat
  **1028 bytes** whatever the edit, because re-striping rotates every triangle's
  corners and its colour triples no longer match the runs the shipped table
  holds, so it spends 257 entries it does not need.
  **Key a strip by §5.1's flag, never by the texture slot.** One strip may
  sample several textures — 12 % of 47,139 shipped strips do, some five — and
  keying by slot takes this writer from 4.769 triangles a strip down to
  **3.486**, which is worse than the game by as much as the fixed version is
  better. What it costs is that a full-corpus rebuild now pushes two
  models past the 8192-entry colour limit by 52 and 56 entries: a different face
  order deduplicates the three-consecutive overlap slightly worse. That only
  shows in the sweep, which rebuilds every mesh of every model; a real edit
  rebuilds one.
- **`build_strips` is order-sensitive — chain before handing over.** It seeds a
  strip with a face's corners as they stand and continues along the edge its
  second and third corners make, taking the lowest-numbered unused face that
  carries it. Both are the caller's to choose. Left as a dump has them, 328 of
  648 faces became a strip of one (1.31 triangles each); chaining first and
  emitting along those chains gave 4.02, and the writer rediscovered the chains
  exactly. Rotating a triangle's corners is a cyclic permutation, so it cannot
  change the winding — only which edge the strip tries next.
- **Retargeting needs the two rest poses to have the same silhouette.**
  `correspondence` normalises each model by its own bounding box, one axis at a
  time, so what matches is *relative* position. The shipped crate Coco is a
  T-pose 1.56 wide at 48 % of the way down; the cutscene Coco that retargeted
  cleanly measures 1.73 at 48 %. A Crash 2 cutscene Coco, arms down and widest
  at the hips (0.88 overall), had its shoulders and hair mapped onto the shipped
  model's *hands* — the fastest-moving vertices in every clip — and the head
  tore apart while the legs, which map to legs, stayed clean. Check the widest
  band and where it sits before retargeting anything.
- **A cutscene model is not a character model**, in three measurable ways: it is
  posed for one shot (mirror mismatch 0.093 against 0.005–0.006 for every
  shipped character), it is lit for one shot (its colours top out at 124 where
  the hardware's neutral is 128 and Crash Bash's own characters use the full
  range up to 255), and it may have no mouth, because that shot never needed
  one. Symmetry can be restored with a mirror snap and brightness with a gain,
  but **collapse decimation undoes the symmetry again** — it collapses each side
  independently — so snap after decimating, not before.
- **A gain for a borrowed model is chosen on the percentile, not the mean.**
  Matching the shipped Coco's mean luminance wanted ×8 and drove 46 % of
  channels into the ceiling, because a quarter of the borrowed model's channels
  are zero and no gain lifts those. Protecting the single largest value below
  255 allowed only ×1.41 and left it as dim as it started. Putting the *90th
  percentile* on 255 gave ×2.06: that block is one flat level, so clipping it
  loses no distinction — only 54 channels carried a value of their own — and the
  drawn luminance went 49 → 88 against the shipped Coco's 138.
- **A mesh moved between packs carries its own art.** Matching the destination's
  existing pictures gets most of the way and not all: moving the cutscene's
  high-poly Coco into `chars/warp/coco.mdl`, two of her nine textures matched
  the warp pack pixel for pixel and the largest to 3.3 of 255 — but the worst was
  113.7, and that one is her **eye**, because the low-poly warp Coco has no eye
  texture at all and nothing in her pack scores under 111. The slots being
  replaced are sampled by the mesh being replaced and by nothing else, which is
  the one case §10.3 allows, so copy the source's pixels and palette into them
  instead of pointing at whatever looks closest.
- **A UV rescale is `(dest - 1) / (source - 1)`, not `dest / source`.** A 32×32
  texture's UVs run 0..31, so halving gives 0..16 — one column past the end of a
  16×16 slot. **35 of that Coco's 79 textured faces** sample outside their own
  slot under `dest / source` and none do under `(dest-1)/(source-1)`; the four
  that showed are the eyes, two triangles each, spanning their texture corner to
  corner, and they came back blank. A slot cannot be resized to avoid the
  rescale — a slot's size is fixed, and it is what picks the VRAM bucket
  (§10.4).
- **A swatch face needs its colour re-matched, not its palette number.** It is
  painted by one texel of the pack's palette-less swatch texture (§6.2), naming
  a palette and pointing its UVs at a cell — and neither the palette numbering
  nor the cell layout survives a move between packs. **279 of that Coco's 358
  faces are swatch**, and mapping the palette alone left them reading whatever
  sat at the source's cell — black hair. Matched by the colour each
  face means, over every cell read through every palette (125 reachable in the
  warp pack, 175 in the crate one), 231 land exactly and the worst is 8 of 255.
- **Magenta in a palette is a colour, not a key.** `0x0000` is the skip pixel
  and 10,823 of the disc's 11,234 palettes carry it; 120 palettes across 46 of
  the 400 packs also carry `0xFC1F`, and **all 120 carry `0x0000` as well** —
  none uses magenta in its place. So those texels draw, and a viewer showing
  them pink is showing what the file says. The cutscene pack Coco comes from is
  one of the 46: one palette of its 27 holds `0xFC1F` as colour 15, filling
  **33.4 % of the texels** of the single texture that uses it, and the six faces
  sampling it are the two mirrored triples at the sides of her head. Moving that
  palette to another pack still needs the entry translated to whatever the
  destination means by it; what was wrong was calling it that pack's key.
- **`texel * colour / 128` is a display value, not a linear one.** A renderer
  that treats it as scene-linear and encodes it for the screen brightens
  everything: measured against the console's own formula on a flat quad, 128
  under 128 should draw 128 and drew **188**, 32 drew 99. Blender's Standard
  view transform is exactly the sRGB curve, measured emission to file byte, so
  the preview shader applies its inverse — `((c + 0.055) / 1.055) ** 2.4`, with
  the value at zero taken off again so black stays black. Worst error over the
  range afterwards: 3 of 255.
- **Magenta out of a *reader* is a different thing entirely** — `to_rgba` fills
  with it when there is no palette to decode through, which is how a swatch
  texture comes out (§6.2: it carries none of its own, `0x80028EE8` compares
  against `0x7FFF` and skips the CLUT lookup). **23,413 faces across 225 models
  name the swatch image as an ordinary textured slot**, so a viewer that decodes
  it the plain way puts pink patches on a quarter of the game. Decode it through
  the palette the mesh names for its own swatch faces instead.
- **An external model must be welded before anything else is done to it.** A
  Sketchfab Suzanne arrived as a triangle soup: 1966 vertices for 968 triangles,
  and **1966 of its 2435 edges carried a single face**. Merging by distance
  collapses that to 505 vertices and 42 lone edges — the eye and mouth
  boundaries, which is what a closed surface should have. Until that is done
  nothing downstream has adjacency to work with: Blender's recalculate-outside
  has no neighbour to spread a facing through, the decimator no edge to
  collapse, and this project's striper no shared edge to chain on, so it writes
  one strip per triangle. The importer takes the winding as it arrives (§11.3),
  so an inconsistent soup is culled triangle by triangle and reads on screen as
  a cloud of shards. Weld, recalculate normals outside, *then* decimate — and
  view it in Blender with backface culling on, which is what the console does.
  Not every source needs it: `monkey.obj` arrived welded, 84 of 5946 edges lone.
- **A glTF accessor may be sparse, and the overrides are the data.** Blender
  writes them: re-exporting this project's own `mainmenu/models` export
  *unchanged* produced 15 sparse accessors and 18 with no `bufferView` at all.
  Reading the base and ignoring `spec["sparse"]` returned a morph target of
  zeros, so the pose it carried was dropped — three of that model's twelve
  clips came back playing something else while every static check passed. This
  is what "the animation plays partially and broken" was.
- **Split only the faces Blender cannot hold, never the whole mesh.** Two
  triangles over the same three welded corners are one face to Blender and two
  to the file, and it drops the second. The importer used to answer that by
  rebuilding the *entire* mesh a corner per vertex, which leaves the exporter no
  shared edge to chain along: `warp_room1` came back out of Blender in **3005
  strips where the core needs 1548**, 26,096 bytes of blocks for the same
  geometry. Splitting only the colliding faces takes it to 1903 strips and
  206,776 bytes against the core's 200,632, and the 355 that remain are exactly
  the **360 faces of that room which share their three corners with another** —
  each is its own strip because it can chain to nothing, and that is inherent
  to Blender's data model rather than a loss.
- **A mesh with no faces holds no index, so it needs no staging.**
  `mainmenu/models2`'s mesh 5 is empty -- zero faces, an empty vertex pool, all
  five block pointers on the same byte -- and requiring every mesh to be staged
  before the tables may be renumbered made it the single model in the archive a
  full rebuild refused. With empty meshes exempt it is **378 of 378**.
- **The Blender front end never carried §5.1's strip flag, and rebuilding every
  mesh is what finally showed it.** `read_scene` fills a payload's positions,
  colours, UVs, textures and blend and leaves `untextured` at `None` — the word
  does not appear in the file. §6.2 is emphatic that the flag is a *separate
  fact* from the texture entry's bit 15, because **33,097 faces across the
  archive carry the swatch bit inside a strip flagged textured**, and a strip
  that comes back with the wrong flag draws as the wrong primitive: flat
  shaded, no texture. On `mainmenu/models` that is **758 of 3498 triangles**
  moving out of textured strips, and on screen it is Crash's body panels and
  gloves, the bear's shoulders and arms and Uka Uka's cheeks drawing flat grey
  while the textured faces beside them are right.
  The core is fine — rebuilt through `payload_from_model` the whole archive
  keeps 258,643 of 258,875 triangles in textured strips, 28 models off by a
  handful. This is the add-on alone, and it was invisible because the exporter
  rebuilds only the meshes that changed: until `rebuild_tables` forced all 22
  through the writer, the affected meshes were copied verbatim.
  **No comparison in this project sees it.** `payload_bag` covers positions,
  colours, UVs, the texture entry and corner order; the swatch palette and cell
  are measured separately; the blend mode is measured separately. The strip
  flag is measured nowhere, which is why `tools/roundtrip.py` and
  `tools/native_roundtrip.py` both score clean on files that carry it wrong.
- **`rebuild_tables` renumbers, and what it is guilty of is still open.** The
  grey faces above were laid at its door and are not its doing. What it *is*
  measurably guilty of: every mesh must be re-striped, so `mainmenu/models`
  goes 1016 strips → **1501**, its pose pool 8067 → **9033**, its animation
  region 97,608 → **170,448** bytes and the file 276,712 → **359,748** for an
  edit that changed nothing, where the same save with the switch off is
  **byte-identical**. And it buys nothing: the rebuilt tables come back a
  median **+8 colour and +25 UV entries** *bigger* than shipped. Whether an
  outside consumer holds an index is unsettled — the menu disc cannot answer it
  while the strip flag is wrong in the same build.
- **Coverage measures nothing about exclusivity, and here is the measurement
  that proves it useless.** Over the whole archive, **378 of 378 models** have
  no *interior* gap in either shared table: every entry up to the last one any
  mesh reads is read by some mesh, and what is unreached is a trailing run. That
  looks like a licence to renumber and is not one — an outside consumer's index
  lands inside a fully covered range too, which is exactly how the menu came
  back drawing flat grey faces, twice. The seven §8.6 carriers are known to have
  such a consumer (their door sub-blocks) and they show no interior gap either,
  which settles it: this test cannot see one.
  What follows is not "rebuild the tables" but **"never renumber them"** — and
  that costs nothing, because a writer that owns the layout can grow a table in
  place. Pinning, snapping and stranding are consequences of *patching*, not of
  the table's content.
- **Rebuild only the meshes the file actually changed.** An untouched mesh
  re-striped comes back with every triangle's corners rotated, so its colour
  triples no longer match the runs the table holds and it spends entries it
  never needed: all 22 meshes of `mainmenu/models` rebuilt for *one* edited
  mesh wanted **8402 entries against the 8192 a 13-bit index can address**,
  while rebuilding the one mesh grows the table 5216 → 5488. `_reference_bags`
  decides it by exporting the shipped model and reading it back through the
  same path — comparing an incoming mesh against the model's stored arrays
  instead is comparing unlike things, since the exporter folds the swatch texel
  into the vertex colour and writes swatch cell UVs (positions then agree on
  6031 of 6031 triangles while the stored UVs agree on 2015). A mesh left alone
  also keeps its clips byte-identical. Deduplicate UV triples the way colours
  are deduplicated: the shipped files lean on the three-consecutive-entry
  overlap hard (2350 pairs for 6035 triangles) and a fresh triple per face
  costs seven times the table for the same geometry.
- **Strip flag bit 3 states the first triangle's winding** (§5.1), and bit 0 of
  the vertex flag alternates from there. A mesh must not contradict its own
  flag byte.
- **A strip list ends with `0xFFFF`** (§5.1) — 7960/7960 meshes, no exception.
  The high byte of a strip word is its triangle count, so the `0xFF00` this
  wrote is not a terminator: it reads as a strip of 255 triangles and the walk
  runs on past the block. The decoders take the vertex count from that walk, so
  it comes out far larger than the keyframes hold and the tail of the pose
  buffer keeps the previous draw's world-space vertices. Standing still the
  stale tail matches and nothing shows; walking, the model trails threads across
  the level that grow with the distance moved. `transplant_mesh` copies a
  shipped block and never carried it, which is why transplants were clean and
  everything built from scratch was not.
- **The hub is where the memory budget bites, not the arena.** The same model
  drew correctly in the crate game and came apart in `warp_room`, which loads
  far more at once. `chars/crate/coco` at 328,112 bytes failed there and at
  99,080 ran; the shipped file is 215,678 and a 747,672-byte build black-screened
  everywhere. Every check inside the file passes at every one of those sizes —
  that is what a budget overrun looks like from the inside, so test in the hub.
- **A frozen clip needs one keyframe, not one per key.** Every key holding the
  same pose is the same bytes repeated, and the pose pool is what a model
  weighs: freezing the 13 clips a key at a time cost 328,112 bytes where one key
  each costs 99,080. Point every frame at that key with `weight` 0.
- **An untouched mesh's `+0x2C` block has to be carried through a region
  rewrite.** `_rewrite_region` copies a mesh it is not rebuilding from `low` to
  `ptr_end`, and the attachment block sits *after* `ptr_end` — so it was left
  behind and the pointer went stale. Rebuilding `chars/crate/coco`'s mesh 0 read
  mesh 1's collision volume as zero records; 777 of the archive's 5990 meshes
  carry a block and 114 models have more than one that does.
- **Hand the writer *outward* corner order, never strip order.** Consecutive
  triangles in a strip wind opposite ways, and bit 0 of the third corner's
  vertex flag says which way this one does (§11.3): the outward order is
  `(a, c, b)` when it is set, with the colours and UVs reversed alongside,
  because the game writes vertex i, i+1, i+2 and UV 0, 1, 2 in step. Reading a
  shipped mesh back in strip order hands over 62 of `chars/crate/coco`'s 511
  triangles inside out, and the console *culls* those rather than drawing them.
  A comparison over sorted corners cannot see it — that is reflection-blind and
  scored 45,300/45,300 while it was happening.
- **A triangle's semi-transparency is in the colour index, not the colour.**
  Bits 13–15 of the `u16` are the GPU's blend: bit 15 turns it on and 13–14
  pick the ABR mode (§6.3), and 42,969 of the archive's 363,251 triangles carry
  them. A rebuild that writes only the table index draws every one of those
  opaque — 1949 of the 5827 meshes the corpus rebuilds lost the mode entirely,
  and nothing in a static comparison of positions or colours notices.
  `NewMesh.blend` states it per face; `None` means `_restore_blend` recovers it
  by corner position, which is what a front end with nowhere to put it needs.
- **A mesh with no textured face still indexes UVs.** All 1032 fully-untextured
  meshes in the archive carry a UV block, 887 of them exactly one entry per
  triangle, because an untextured face reads one texel of the pack's swatch
  image through the palette it names (§6.2). Gating the arrays on "some face
  names a slot" handed `NewMesh` a `None` for those and wrote `uv_index = 0`
  everywhere with one palette for the whole mesh: **604 of the 862** such meshes
  came back painting something else while every position matched to the unit.
  Mixed meshes were already right, 803 of 813, which is why nothing noticed.
- **A stated swatch entry beats a guessed one.** `_restore_swatches` matches
  faces by corner position and two faces can share their sorted corners, so the
  first one seen won the lookup for both — ten meshes lost their palettes that
  way, all of them 186-face menu heads of the kind that paint themselves in five
  colour schemes at once. It fills in only a face that arrived with no entry of
  its own.
- **Welding by position is not safe on an animated mesh.** 49 of the archive's
  357 animated meshes hold a pair of pool entries that sit together at rest and
  are driven apart by a clip, 128 pairs in all. Merge such a pair and both get
  one pose: a corner of `cutscene/level_shot12` went four units off on 280 of
  that clip's 429 frames with every static check passing. `weld_vertices` signs
  a vertex with its rest position *and* every pose the source states, and
  `build_strips` takes that identity so a strip cannot chain through the join
  either — sharing a pool entry is the same merge from the other end.
- **A pose is placed by the writer's own plan, not by proximity.**
  `build_blocks` reports which corner of which triangle each pool entry came
  from and the source says which vertex that corner is, so the map is exact.
  Nearest-neighbour matching against rest positions cannot tell apart two
  vertices at one position, and that is the failure above wearing a different
  hat. The distance path stays for a source with no vertices of its own.
- **A blended frame states its pair in either order, so do not rebuild one to
  get the file's own ordering back.** `chars/crate/coco`'s BREATHE frame 11 is
  key 1 → key 0 at weight 409, while every frame of `cutscene/level_shot12` runs
  ascending. Reading the pair back off two shape key values recovers the blend
  and not the order it was written in, so a comparison that decides whether a
  clip changed has to canonicalise — swap to the lower key and use
  `4096 − weight` — or every such clip looks edited and is rebuilt for nothing.
- **Rewriting a clip that did not change is not free.** Stripping the animation
  region and putting the same blobs back costs `chars/crate/coco` four bytes
  and grows `mainmenu/models` from 276,712 to 343,660 — a quarter again, on an
  edit that changed nothing. `import_payload` leaves the region alone entirely
  when nothing was installed and every clip was copied, which is also the only
  way an untouched import comes back byte-identical.
- **The glTF carries the facing; never re-derive it.** The game flips the sign
  of the NCLIP backface test per vertex flag bit 0 (§11.3), so an inverted
  parity culls a triangle rather than drawing it — and nothing on a static
  render shows it. The exporter therefore emits corners in *outward* order
  (flipping UVs and colours with them, as §11.3 requires) and the writer seeds
  every strip unflipped, bit 3 clear. The importer takes the winding as it
  arrives. It used to reorient each connected component so its faces pointed
  away from the component's own centre, which cannot survive a surface seen
  from inside: `mainmenu/models` mesh 6 is a room shell whose faces correctly
  point inward, all 875 came back inverted, and the menu lost its background.
  Measured against the shipped facing over the whole corpus: **363,007
  triangles, 0 flipped**; that same model scored 4856/6031 before.
  `tools/roundtrip.py` cannot catch this — it sorts each triangle's corners.
- **`mesh+0x2C` is the collision volume** for a character (§8.4) — a standing
  cylinder read live by gameplay. Zeroing it let the character walk through the
  crates. Carry the replaced mesh's own block through a transplant. A
  character's *second* mesh is its spin body, with a volume of its own.
- **A shipped model handed to a right-hand-rule renderer is inside out.** §11.3
  calls a triangle's outward order "pool order when bit 0 is clear and the
  reverse when it is set", and that order encloses a **negative** volume in
  every shipped character measured: `chars/crate/crash` mesh 0 at −3,235,872
  with 86 of 227 faces pointing away from its own centre, `crate/coco`,
  `warp/coco` and the cutscene casts all the same sign. So Blender, whose
  polygon normal follows the right-hand rule, reads those faces as pointing
  inward — and with backface culling on, which is what the console does, you
  see through the front of a model to the inside of its back. On screen that
  read as a room with no walls, characters whose faces showed while their backs
  were turned, and an Aku Aku with no face at all. The add-on therefore reverses
  the corners on the way in and back again on the way out, so the file's own
  convention is untouched and the round trip stays exact. **The glTF exporter
  carries the same inversion** — `chars/crate/crash` mesh 0 comes out at −0.1929
  with 65 of 227 outward — and has not been changed: a round trip through it is
  symmetric either way, and the discs built through that path drew correctly, so
  what the console calls its front face is not settled by this measurement.
- **Bit 15 of a texture entry decides what a face samples, not the strip's
  untextured flag.** `0x80017FB8` branches on it: set, and the draw takes the
  pack's *last* texture — the swatch — with the CLUT named by the low nine
  bits; clear, and those bits are a slot. The strip flag (§5.1) is a separate
  fact and the two disagree constantly: **33,097 faces** carry the swatch bit
  inside a strip flagged textured, while not one face has the strip flag
  without the bit. Reading the strip flag instead aims those at a texture slot
  — in `cutscene/level_shot12` that is slot 46 of a 46-texture pack, so 80 of
  Coco's 215 textured faces had no picture at all and Aku Aku's face went
  missing — and a rebuild writes them back as plain slots, losing the bit. Both
  facts have to travel: `NewMesh.untextured` carries the strip flag beside the
  entry, or a strip comes back the wrong primitive.
- **A zero texture-run entry means slot 0, not "no texture"** (§6.2). Of the 897
  meshes whose every strip flag says untextured, none writes a zero list; they
  name a swatch palette instead, and 1776 of the archive's 5989 meshes carry one.
  Clearing the list aims every triangle at a real slot with no CLUT behind it.
- **The UV table's length is the span `T(0x24)..T(0x28)`**, not the reader's
  count of entries — the two agree in only 168 of 373 models, because the reader
  stops at the last entry a triangle names. Copying by the count truncates the
  table and overwrites UVs that other meshes index; 205 models lose between 2 and
  4748 bytes that way.
- **Texture slots may only be taken when the mesh being replaced is their sole
  sampler** (§10.3). "No mesh samples it" proves nothing — the menu draws its
  character-select portraits from code, and overwriting those slots corrupted
  the select screen. `sole_sampler_slots` measures it: `warp_room1`'s arm
  0x501C is the sole reader of **none**, its four slots being read by 6, 13, 6
  and 7 other meshes, while `polar_polar`'s big arena mesh is sole reader of 14.
- **A rebuild must not move the shared tables past the shipped `i32@0x50` —
  measured, not explained.** `install_meshes` lays the colour and UV tables at
  the end of what it writes, and when the penguin went into
  `crate_jungle/arena` that put `T(0x20)` at `0x8a1c`, the shipped `i32@0x50`
  **to the byte**. Jungle Bash came back as a screen of VRAM garbage, which is
  what every mesh indexing a table it cannot read looks like.
  **The boundary is real, and it is stated in the *pack*, at `u32@0x14`.** That
  field and `model+0x50` hold the same number in **400/400** pairs, §10.4's
  `0x8002A62C` carries the pack's into the texture context at `+0x24`, and §10.1
  lists it as unidentified — "not a pointer, not the pixel byte total… differs
  between structurally identical packs". It is the companion model's resident
  size, and it is the one that binds. Six builds say so and nothing else fits:

  | build | data past the stated end | pack `0x14` | on hardware |
  | --- | --- | --- | --- |
  | `intro_eurocom` tall M | no | stale | runs |
  | `warp_room1` penguin, flat and textured | no | stale | runs |
  | `crate_jungle` penguin | yes | stale | screen of garbage |
  | the same, `0x14` corrected and nothing else | yes | agrees | **loads** |
  | `crate_jungle`, one mesh swollen | yes | stale | crash |

  A stale `0x14` on its own is harmless — three builds that run carry one,
  because everything they need still sits below the number it states. What is
  fatal is data *past* it. `import_payload` therefore moves the pack's field
  whenever the model's `i32@0x50` moves, and the refusal only stops what that
  cannot fix.
  **Growth is not the wall.** `crate_jungle/arena` grew by 6300 bytes of pure
  padding with nothing inside it moved, and the disc is clean; the same pack
  grew by six textures on its own and is clean too. Both hypotheses that
  survived static reading died on those two probes.
  **`i32@0x50` is not a boundary the game keeps — three readings say so.** The
  group loader at `0x800126C0` carves each entry out of the group's sector run
  and shrinks it with `0x80011498` to `row+4`, the **file table's byte size**;
  all four callers of that primitive are accounted for and none trims a model to
  `0x50`, so the whole entry is resident. Nothing resolves the field as a
  pointer either: scanning the image for this format's own idiom — a load of a
  field followed by an `addiu` of that same offset — finds **0 sites for
  `0x50`** and 0 for `0x08`, against 10 for the colour table, 12 for the clip
  directory and exactly **1** for the UV table. And the value is derived rather
  than chosen: `base + i32@0x50 == T(0x44) + 24×clips` in **399 of 400**, so it
  states where the clip directory ends and the refetched sub-files begin.
  So the rule above is a fence around observed behaviour whose cause is
  elsewhere. `tools/mips.py` is the disassembler these readings were made with;
  it reproduces the listings §2.1 and §10.4 were written from.
  A corpus sweep trips the check on 233 models because forcing every mesh
  through the writer is not a shippable edit, so the sweeps pass
  `check_resident=False` on purpose.
- **A pack can be made longer, so a borrowed model need take nobody's slot.**
  §10.4 closed VRAM placement — it was never in the file, the loader allocates a
  rect off the free list its size class names — so `texwrite.append_texture`
  adds a picture and its palette and every existing number still means what it
  meant. Three rules come with it. The record goes **before the last one**,
  because the last is where bit 15 sends a face (§6.2): the swatch is found by
  being last, not by its number, so appending after it would send every
  untextured triangle to the newcomer. That moves the swatch's own slot number
  on, and 21 of the 393 models with a growable table name their last texture
  directly, so `_append_textures` refuses those rather than repaint them by
  accident. And **take the source's colours verbatim rather than re-quantising**:
  a 4bpp picture has at most sixteen, median cut clusters on RGB alone, and the
  penguin's transparent texels were pulled into the nearest colour and came back
  opaque — worst channel 255 of 255 on three of its six pictures, 0 once the
  distinct RGBA values were used as the palette.
  **`out/crashbash-warp-textured-penguin.bin` runs**, so all three rules are
  confirmed on hardware at once: `warp_room1` loads with 176 textures where it
  shipped 170, the six appended ones draw the penguin's own art, and the room's
  own untextured triangles still find the swatch — which is the "insert before
  the last" rule doing its job, since the swatch moved from slot 169 to 175 and
  bit 15 still reaches it.
- **A borrowed mesh's texture entries name its *source* pack's slots, and
  appending pictures does not renumber them.** The add-on's `_carry_textures`
  builds a material per appended slot and repoints every face; a caller that
  appends the pictures and hands the payload over unchanged gets a mesh reading
  the destination's own slots 0..4 — in `boss_oxide/arena` a 32×64, a 64×64 and
  a 32×32 8bpp, so the penguin came back wearing someone else's pictures and
  read on screen as a black bird. Two discs went out that way, both built by a
  probe script of mine rather than through the front end. **Drive the front
  end**: what it does around `import_payload` is not decoration.
  The core refuses it now, on the one signal that separates the mistake from an
  intended edit: **a slot is being appended and no face of any staged mesh names
  it**. Nothing downstream could object before — `boss_oxide` has a slot 0, and
  the penguin's 16×16 UVs sit comfortably inside a 32×64 one, so even the
  out-of-range check had nothing to say.
- **A pinned UV table does not forbid textures, only arbitrary ones.** What a
  §8.6 carrier fixes is the table, and a triple in it is only three texel pairs
  — so an appended slot can be addressed through whatever triples the table
  already holds. `pinned_uv_triples` finds them and `snap_to_triples` moves each
  face onto the nearest. The set narrows three times and each narrowing was an
  export that refused: `warp_room1` has 2665 triples, **82** inside a 16×16
  picture, **23** that survive the writer rotating a triangle's corners, and
  **17** that also survive the reversal §11.3 applies to every other triangle in
  a strip. Snapping the penguin's 116 faces onto those 17 moves the worst by 33
  texels over its six coordinates, and on the console that draws as its own art
  with a smeared face — its eyes come back a black mask instead of two pupils.
  **Size an appended slot from the picture, not from `UNKNOWN_SIZE`.** A slot
  being added is not in the pack yet, so the exporter had nothing for it and put
  its UVs through 256×256 while the snap had been done in 16×16: 115 of the 116
  faces came back holding a triple the table does not have.
- **BGR555 `0x0000` is the hardware's skip-pixel.** A genuinely black texel
  needs the STP bit: `0x8000`.
- **Real triangle strips, not one strip per triangle.** No shipped mesh exceeds
  348 strips; a 431-strip mesh crashed the game.
- **Blender scenes must be at 30 fps before importing an export.** Blender
  resamples onto its scene grid and defaults to 24.

## Two draw paths, and an edit belongs to exactly one

Nothing above says what asks for a mesh to be drawn, and the archive holds two
separate answers that share only the by-id renderer at the bottom. Getting the
wrong one costs discs: a file can be perfect and draw nothing.

| | a level | a cutscene |
| --- | --- | --- |
| what draws | §8.5's placement list | §9.11's node graph |
| reached through | `model+0x18`'s sub-object → 160-byte records | `model+0x4C`'s root → typed children |
| the id it draws | record `+0x88` → object `+0x74` | entity `+0x7C`, filled per node type |
| built by | `0x8001D6B4`, drawn by `0x8001DD50` | `0x8001FE80`, drawn by `0x80019F1C` |
| adding something | a placement record | a node |

**All 64 cutscenes declare no sub-objects at all**, so the level path cannot run
in one under any circumstance — `0x8001DE18` reaches the array by indexing
`T(0x18)+4+4*i`, and with a count of zero there is nothing to dereference.
Symmetrically a level's geometry needs no node: 91 % of an arena's meshes have
none. Both are measured over the whole archive (399 models): cutscenes are 64
models with nodes and no placements; the warp rooms and demo hubs have both;
arenas split four ways; characters, fonts and loading screens have neither.

The two sections that follow own the rules of each side. Everything above them
is the file itself and holds for both.

## Editing a level

- **A mesh in the file is not a mesh on screen.** A level draws what its
  placement list (§8.5) names: `model+0x18` reaches a sub-object whose `+0x1C`
  counts 160-byte records and `+0x20` points at them, each record naming an
  object id the object table binds to a mesh. In `warp_room1` **none of the 42
  meshes in `model.meshes` is named by any object record or any of the 81
  placements** — the room the player walks in is object-pool meshes, whose
  headers start at `0x111f8`, immediately past the boundary, and which the
  reader numbers 42 upward. Geometry added to a plain mesh there is written
  correctly, verifies clean and never appears. Check reachability before
  editing a level mesh; a model whose meshes draw without any placement (the
  menu) is the other case, not the general one.
- **The placement list is live data, and it is how a level's set is changed.**
  Both fields answer to the file: taking `warp_room1`'s count from 81 to 80 —
  one byte — draws the room with its last object gone, and repointing the array
  at a copy inside the resident region draws exactly the records that copy
  holds. Rewriting a record in place **adds an object to the set** — spending
  the second of `warp_room1`'s two `0x5047` records on the green panel put a
  second panel in the room, between the POLAR PANIC and POGO PAINTER doors, with
  the first still at its own. Eleven bytes, nothing moved, the file the same
  size. Five objects are placed more than once in that room, ten records in all,
  and `placewrite.spare_records` is what names them.
- **Growing the list is not blocked; *relocating* it is.** That distinction was
  missed for a long time and the two were written down as one. Relocating has
  nowhere to go — the resident region's largest run of zeros is 1325 bytes
  against the 12,960 an 81-record array needs, and past the shipped resident
  size nothing is loaded, which is why the array copied beyond the old EOF with
  `0x50` grown drew nothing. But the array does not have to move. The four
  blocks that follow it can **slide one record further on**, into the padding
  the resident region already ends with: `warp_room1`'s `+0x14` block states a
  count of 0, so it uses 8 of its 544 bytes and the last 536 are zero.
  `append_placement` slides that span, stretches the sub-object's four pointers
  by 160, and writes record 82 where the span began. The file keeps its length,
  `i32@0x50` and `T(0x44)` keep their values, and both invariants the corpus
  states survive: `+0x0C`'s target is still the array's end (73/73) because
  array and block each grew by one record, and `+0x14`'s block still runs to
  `T(0x44)` (73/73). Read back, the room holds 82 placements with all 81
  originals byte-identical and nothing removed.
  **`out/crashbash-82nd-record.bin` runs**, so nothing outside the span points
  into it — which no static check could have settled, since the scan that looks
  finds 714 four-byte words resolving there and cannot tell a pointer from a
  vertex that lands there by chance. **`out/crashbash-three-objects.bin` runs
  too**, and it is the one that matters: its three records were authored in
  Blender by duplicating placements, and they spend `warp_room1`'s padding down
  to zero. So the slide is good to the last byte the level has, not just for the
  first record, and the room draws all 84 with its own 81 untouched.
  **Duplicating a placement in Blender is the gesture, and it needed a fix to
  work.** Blender copies custom properties with the object, so the copy claims
  the same `crashbash_placement` as the original; read literally that is two
  objects for one record, and the second overwrote the first — the obvious edit
  moved nothing and took the original's move with it. `_claims` now gives a
  record to one holder and makes every other claimant a new record, choosing the
  holder as the claimant still at rest, which is the original whenever the copy
  is the one that was dragged.
  `spare_capacity` says how much room a level has, and it is the padding at the
  end of its resident region divided by 160: **53 records across 8 models** —
  all five warp rooms, both demo hubs, and Oxide's chase level at 1746 spare
  bytes. `warp_room1` takes 3. The other 65 levels take none; an arena typically
  ends its resident region with 6 or 18 bytes of alignment. So a level's set can
  grow, but only into what its own file already left empty.
- **An object record's `+4` is the one field in this format that is not
  self-relative.** `0x8001DD20` adds it to the load-time base named through
  `+8`, so it is a plain offset from the start of that file, and reference 0 is
  this model — those are the object-pool meshes. Move the pool and every record
  has to move with it. **Nothing warns when this is missed**: the pool is a
  packed run of similar headers, so a stale offset still reads *a* mesh or falls
  short of the pool and `_read_objects` drops the entry without a word. Growing
  a table ahead of the pool was costing every level in the archive its meshes
  while the check reported 15 failures, because a comparison that skips a mesh
  the reader returned `None` for calls that model clean. **Count the meshes
  before comparing them.**
- **An object-pool mesh's blocks may not leave the pool.** The pool is one
  packed run: the next object mesh's header sits exactly four bytes past the
  previous mesh's `ptr_end` in **1802 of 1898** consecutive pairs. Rebuilding
  one the way a numbered mesh is rebuilt — blocks appended past the file's end,
  header repointed — leaves a hole in that run, and `warp_room1` built that way
  **boots to a black screen**, where the same graft on a numbered mesh boots and
  draws. `_write_in_place` puts the blocks back inside the span the mesh already
  owns and keeps its shipped `ptr_end` whatever the rebuild costs, so the run is
  undisturbed; when the rebuild does not fit, it refuses rather than build that
  disc. Fitting is not a given — this writer's striping is looser than the
  authoring tool's, so even a reshape with the same triangle count can want more
  room than the mesh has (`warp_room1`'s mesh 111: 844 bytes wanted against 788
  owned).
  **The relaid layout removes the fit constraint, and `out/crashbash-oxide-tall-pool.bin`
  runs.** It leaves no hole: the mesh's region grows where it stands, the pool
  meshes after it slide on, `_repoint_objects` moves every object record with
  them, and unchanged neighbours keep their exact spacing (1745/1745 pairs
  measured). All **1964 pool meshes across the 70 level models rebuild**, none
  refused, where `_write_in_place` refused the ones that did not fit — the glTF
  round trip went from 49,326 level triangles to **98,960**, and 32 of its 63
  level refusals disappeared. The disc carries `boss_oxide/arena`'s pool mesh
  `0x500C` scaled 1.8× taller in Blender and rebuilt here: its rebuild wants
  **2788 bytes against the 2444 it owns**, which is exactly what the old writer
  refused, and 19 of that arena's 26 pool meshes are in the same position. On
  screen it draws taller in all six of its placements, the room is otherwise
  itself, and the borrowed penguin still wears its own art.
- **A model from another pack can be put into a level, and the pool mesh it
  replaces is the whole budget.** `arena/crate_snow`'s penguin now stands in
  `warp_room1` and the disc runs. Three things had to be true at once, and each
  is a rule of its own. The geometry has to fit **the span the pool mesh already
  owns** (§8.3): 0x501C is the decorative arm, 444 triangles in 6592 bytes, and
  a 116-triangle body went in with its `ptr_end` unmoved and all five block
  pointers inside. The art cannot come with it — the penguin's slots mean other
  pictures in the warp pack — so **fold each face's texel into its vertex colour
  and make every face a swatch face** (§6.2) on a palette the level already has;
  the hardware's `texel * colour / 128` then draws the baked colour, once the
  cell's own value is divided back out of it. And a §8.6 carrier's **UV table is
  pinned**, so every triangle needs a triple already in it: the room's own mesh 1
  puts all 662 of its swatch faces on one triple, and pointing all 116 penguin
  faces at that one satisfies the lookup. Nothing new is needed below the
  resident end — the colour and UV tables never moved, and the penguin's indices
  land at 139..4380 of the shipped 4516.
  All three are the add-on's *Borrow Selected Mesh* now, and `pinned_swatch_cell`
  is the core's answer to "which cell will this model take": it reads the UV
  table's own three-in-a-row runs and picks the one nearest neutral among them,
  where `neutral_swatch_cell` picks the nearest neutral full stop and a pinned
  model refuses it. **Write that cell through the importer's own V flip** — a
  texel row and Blender's V run opposite ways, and setting it unflipped put
  every face on a cell the table does not hold, all 116 refused by the check
  doing exactly its job.
- **The seven §8.6 carriers renumber their tables like anything else — as long
  as their door previews are renumbered with them.** That is settled on
  hardware: `out/crashbash-warp-full-rebuild.bin` rebuilds `warp_room1` in
  full, every entry renumbered from index 0, and opens a door preview clean.
  §2.1's ledger read a real effect and named the wrong cause: repointing
  `T(0x24)` scrambles every textured surface in these rooms *because §8.6's
  preview sub-blocks are meshes of this model and index those tables*, and a
  preview is streamed in when a door opens, which is why the room is perfect
  until then. `mdlwrite.preview_meshes` finds them, `preview_runs` gathers what
  they name, and their triples join the same packing pass as the model's own
  faces. The block may move too, on the sector grid, with §8.1's rows following
  it — each row states a sub-block's start and end as **plain file offsets**,
  streamed by `0x800163E0` / polled by `0x80016450` / released by `0x8001636C`.
  `pin_tables` remains only for a caller that asks for it, and what it still
  emits is the **graft layout**: the file byte-identical through its old EOF
  except the rebuilt mesh's header and `0x08`/`0x50`, new blocks after the §8.6
  block under a grown sector-aligned `0x50`, colours mapped to the nearest
  existing entry, and every textured triangle needing its exact UV triple
  already in the table. That last one is why a pinned rebuild refuses a
  reshape: re-striping re-orders a triangle's corners, so its triple is no
  longer in a table that cannot grow (`warp_room1`'s boxing mesh 89 lost 16 of
  its 91 faces' triples), while a plain scale survives because it leaves the
  corner order alone.
- **The §8.6 sub-blocks are meshes of this model, and that is the whole of the
  outside consumer.** §8.6 already records their signature — 0x34 bytes,
  `i32@+0x00 == 0`, `i32@+0x04 == 0`, `u16@+0x0A == 4`, `i32@+0x14 == 32`, four
  ascending offsets — and that is a **mesh header** field for field:
  `MESH_HEADER_SIZE` is 0x34 and `0x14 + 32` puts the strip list immediately
  after it. Read as meshes they index the model's shared tables and they use
  them to the last triple: `warp_room1`'s five sub-blocks are 1156 triangles
  whose highest indices are **colour 4513 of 4516 and UV 2767 of 2770**;
  `demo_hub1`'s three are 541 triangles at 2228 of 2231 and 855 of 858.
  So nothing outside the *file* holds an index, and **no executable edit is
  needed or would help** — the indices are in the model, in a block this
  project can already parse. Renumbering a carrier's tables is safe exactly
  when these meshes are renumbered with everything else, and the reason the
  corruption waits for a door to be opened is that this is when they are
  streamed in and drawn.
- **A §8.6 carrier takes a full table rebuild, and the disc that proves it is
  `out/crashbash-warp-full-rebuild.bin`.** The previews were never an obstacle
  once they were understood: they are meshes of this model, so they are
  renumbered with everything else, and then the block is free to move and the
  room to grow. `warp_room1` rebuilt entirely — every mesh through this writer,
  both tables built from nothing but the meshes and the previews, **renumbered
  from entry 0** (4516 → 4546 colours, 2770 → 2795 UVs, the shipped numbering
  not kept even as a prefix) — loads, draws, and **opens a door preview clean**.
  All seven carriers rebuild that way. No pinning, no snapping, nothing
  stranded.
  **What the corruption actually was: renumbering under previews that still
  held the old numbering.** Two discs showed it and neither was evidence about
  the block, because both renumbered. The tell was *when* it showed — the room
  perfect until a door opened, then every textured surface in the level in
  garbage, the player character included, and that model is a different file.
  §2.1's "repointing `T(0x24)` alone scrambles every textured surface" was
  reading this consumer without knowing it.
  **And one bug hid behind it.** With the previews renumbered but the block
  moving, the room came back with *no previews at all*: `moved()` collapses any
  offset inside a replaced region onto that region's start, so four of
  `warp_room1`'s five §8.1 descriptor rows were written zero-length and the
  loader read nothing for them. A replacement that keeps its length has moved
  nothing inside itself — §8.6's block is renumbered in place — so its interior
  maps straight across.
- **Pinning is now only what a caller asks for, and what it costs is the
  colours.** Nothing needs it: the seven carriers rebuild in full once §8.6's
  preview meshes are renumbered alongside. A pinned rebuild maps
  each face onto an existing *triple* of consecutive entries, not onto three
  nearest colours, and `boss_oxide/arena`'s 5562 entries do not happen to hold
  the penguin's: measured corner by corner, worst **91 of 255** and **23 % of
  corners off by more than 32**, which drew as a black penguin with a grey
  chest. The same edit unpinned is **0 of 255** on all 348 corners. So pinning
  is for an edit whose colours the model already has — a reshape, a move — and
  a borrowed model wants the tables rewritten and the 32 KB.

## Editing a cutscene

- **A prop draws the id in its *keys*, and that is the whole of why adding to a
  cutscene did not work.** The per-tick handler reloads it every tick from the
  key covering that tick, and `node+0x14` is not on the path at all:

  ```
  8001EDAC  lhu   $v0, 8($s5)      ; s5 = the key covering this tick
  8001EDB4  sh    $v0, 0x74($s6)   ;   -> entity+0x7C
  80019F44  lhu   $a2, 0x74($s0)   ; and the draw asks for exactly that
  ```

  The two agree in **11,382 of 11,382** shipped prop keys and no prop changes
  mesh across its own track, so the corpus cannot tell them apart — only an edit
  can. `out/crashbash-eurocom-control2.bin` wrote `node+0x14` alone, one word,
  992/992 verified, and **the letter did not move**; a node copied from another
  prop keeps the template's keys and so draws the template's mesh, standing
  exactly where the template already stands. That is what "Cortex is not there"
  was, and it read as *nothing was added*. `scenewrite.append_prop` writes the id
  into every key it copies, `patch_scene` writes it the same way, and
  `scene.read_scene` reads a prop's mesh from key 0. Reading it the game's way
  changes nothing about the archive — 1209 props and 177 actors either way — and
  it is what made the next disc draw: the same edit that had produced nothing
  now raises a letter over the intro.
  Each drawing node type keeps the id somewhere else: **prop `key+0x08`** every
  tick, **actor `node+0x14` + `node+0x18`** at spawn (0x8001F300), **emitter
  `node+0x3C`** at spawn (0x800216A4). An actor's key has its *position* at
  +0x08 where a prop's has the id — the two key records are different shapes.
- **A shot's emitter names its mesh by *id*, and its position is in the
  parent's frame.** The reader reports a resolved index and a world position;
  writing either straight back is wrong. `mesh_index` has to go back as
  `0x2000 | (index + 1)` — writing the index instead made eight emitters vanish
  from a shot patched with nothing changed — and a sub-scene's emitter position
  has to have the parent frame taken off again, exactly as a track's keys do.
- **Adding a mesh and a node that draws it works, and the disc is
  `out/crashbash-eurocom-addprop.bin`.** `modelwrite.append_mesh` grows the
  header table like any other region, the new blocks go **inside `model+0x08`'s
  boundary** — appended past it the slot was perfectly formed and drew nothing,
  which is §2.1 and the resident rule both saying the same thing — `i32@0x54`
  reaches 29, and the game's own arithmetic `52 × 29 + 36` lands on the new
  header. `modelimport` orders it `append_prop` then `append_mesh`, so the node
  lands in front of the new blocks and the shot's region stays one piece.
  On screen the intro raises a **29th letter three times the size of the logo**,
  and everything else is where it was. Read back out of the disc: 990 of 992
  entries byte-identical, `intro_eurocom.mdl` 44,764 → 54,312 bytes and its pack
  the same 42,576 with only `u32@0x14` moved, since `i32@0x50` went 0xaedc →
  0xd428. All **28 shipped meshes identical** over 1588 triangles, the added
  mesh 67 triangles at 3× the M's extent, props 18 → 19, and the new node's
  **13 of 13 keys naming `0x201d`**.
- **A mesh no scene node names is not a spare slot — the shot is not the whole
  of what draws.** `scene.mesh_indices` says `intro_eurocom` draws 26 of its 28
  meshes, and 10 and 11 look free. They are not: mesh 10 is a **13127 x 13346 x
  13115 dome**, 424 swatch faces of dark blue, and mesh 11 is an 11775-unit grey
  shell. They are the backdrop, and §9.11.8's untraced path is what draws them.
  Borrowing a model into mesh 10 took the sky away: the intro came back with
  the *previous* screen showing through it and the letters drawing over
  whatever was left in the framebuffer. Four discs were needed to find that,
  because the file is right in every way this project can check — the other 27
  meshes come back with 1164 of 1164 triangles and 958 of 958 swatch faces
  identical.
  So `scene.mesh_indices` bounds what the shot draws and nothing more. Before
  taking a slot, look at what is in it.
- **A new slot is a header, not a copy of the blocks — and getting that wrong
  cost the shot on every cutscene with a clip.** `append_mesh` used to copy the
  template's blocks and needed somewhere to put them, so it took "the region
  ending at the layout boundary". That is the vector pool *only when the pool is
  not empty*: `intro_logo`'s is, so the copy landed in the **UV table's** region
  — and `_install_relaid` refuses a region it is already rewriting. **Every**
  added mesh in the archive therefore fell back to the appending path, which
  moves the tail and repoints only `0x44` and `0x50`, leaving `model+0x4C`'s
  root array naming where the root used to be. `intro_logo` came back with a
  child count of **2,691,088** at an offset that still resolved inside the file:
  the shot simply ceased to exist. `intro_eurocom` has **0 clips**, so its tail
  never moved — which is the only reason the M and the first Cortex ran.
  The header now aims at the template's own blocks, which is what the shipped
  files do anyway (`balls_crash/crystalarena` puts five pool-mesh headers over
  one block set), and `install_meshes` gives the slot blocks of its own. The
  relaid path then owns the file: `intro_logo` is **106,534 bytes against the
  141,644** the appending path wrote, 5160 over shipped. Measured over the
  archive, **60 of 64 cutscenes take a new mesh with mesh count, clip count,
  scene and every shipped mesh intact**; the 4 refusals are the `data*` models,
  which state no prop to copy.
  Two repairs went in beside it, both for pointers that cross a move:
  `modelwrite._repoint_roots` rewrites each root array entry on the relaid path,
  and `_rejoin_tail` now shifts every self-relative header field naming the tail
  rather than only `0x44`.
- **Only reading the scene back sees a lost shot.** Mesh count, clip count,
  every triangle of every shipped mesh, the placements and the disc's own
  992/992 verification all passed on a file whose cutscene had no nodes left.
  A check that adds a mesh has to read `scene.read_scene` on the result and
  compare the prop count, or it is measuring everything except the thing the
  edit was for.
- **A probe that stages a mesh and no clip freezes that clip, and it is the
  probe that is wrong.** Rebuilding a mesh whose clip the request says nothing
  about takes the resting path, so `intro_logo`'s 199-frame clip came back on
  one pose — pool 639 → 112, every frame drawing the last one. Through the
  add-on it is 199/199 frames identical and the pool is 639 either way, because
  the exporter reads the clips off the shape keys and hands them over. Compare a
  clip over its **drawn triangles at 8.8 precision** (`pose / GTE_SCALE_SMALL`,
  as `blender/roundtrip.py` does); comparing model units rounds a frozen clip
  and a live one closer together, not further apart.
- **An added mesh is placed by its object transform; every other mesh is not.**
  The importer stands each mesh at the origin and what draws it — a node's keys
  in a cutscene, a placement record in a level — puts it where it goes, so
  reading an object's transform would move it twice. A mesh being *added* has
  no such record until the file is written, so the object is the only statement
  of where it goes, and `read_mesh(place=True)` bakes it in. Without that the
  panel's "move it to place it" is a lie: the object moves and nothing in the
  file changes.
- **The gesture is the destination active, the newcomer merely selected.**
  `_target` reads the model from whatever the *active* object belongs to, so
  *Add Selected Mesh* follows *Borrow Selected Mesh* exactly — select what is
  coming in, shift-select any mesh of the model it joins. With the newcomer
  active, a second imported model resolves as the destination and the edit
  lands in the wrong file, which is not something any later check would catch.
- **Pick a discriminator that cannot be misread.** Two eliminations in the
  session that found the key id were wrong, and both failed the same way.
  Swapping one letter of the logo for another moves nothing on screen, because
  each letter's geometry sits at its own origin and the prop's keys place it;
  a duplicate of a letter is invisible inside the letter it duplicates. What
  settled it was a mesh three times the size of anything else in the shot.

## Verification

**Check a writer against the game's own data, never only against this
project's reader.** Reader and writer share assumptions, so a round trip
through them proves nothing — that is how the keyframe flag bug survived three
builds and a byte-identical static comparison. The real tests are: rebuild a
shipped model and reproduce its own strip list and vertex pool; rewrite all
1035 clips and get the original bytes back; re-read a built image with the same
parser and check every entry against intent — the replacement where staged, the
original everywhere else.

Three round trips, each over the shipped corpus and each covering what the
others cannot:

```bash
.venv/bin/python tools/roundtrip.py game/SCUS_945.70          # the glTF path
.venv/bin/python tools/native_roundtrip.py game/SCUS_945.70   # the import core
Blender --background --python blender/roundtrip.py -- game/SCUS_945.70 400
```

Where they stand, all three over the whole archive: the glTF path carries
**424,990 triangles with the worst corner at 0.0000** and 205 scenes identical;
the core carries **351,876** with every swatch face and every strip flag, no
mesh off; and Blender carries **378 of 378 models**, its only refusals the 22
font entries, which hold no meshes at all. A relayout that rebuilds nothing
reproduces **400 of 400**.

**Compare triangles canonically under rotation and not under reversal.**
Reversing a triangle turns it inside out and the console culls it (§11.3), so a
key built from sorted corners is blind to the one failure a static render also
cannot show. That blindness scored 45,300 of 45,300 on a corpus where 62 of one
model's 511 triangles were being handed over backwards. Compare colours, UVs and
the texture entry alongside, and compare a clip over the triangles it *draws*
rather than the pose arrays — a rebuild re-stripes, so the pool is a different
length and its order is its own.

Distrust previews. A flat-shaded preview cannot show sub-triangle texture, and
no static render can show draw-time flags. For those the emulator is the only
honest renderer, and the user runs it — so state plainly what has and has not
been checked on screen.

**A cutscene takes something that was not in it.**
`out/crashbash-eurocom-addprop.bin` runs: `intro_eurocom` with a **29th mesh**
and a **19th prop** that draws it, and on screen the intro raises a letter three
times the size of the logo while everything else stays where it was. That is
`modelwrite.append_mesh` and `scenewrite.append_prop` shown together, and with
them §9.11.11 — the id was written into all 13 of the new node's keys, and the
identical edit that wrote `node+0x14` alone had moved nothing. Read back out of
the disc, **990 of 992 entries are byte-identical**; the two that differ are the
model (44,764 → 54,312 bytes, `i32@0x54` 28 → 29, `i32@0x50` 0xaedc → 0xd428)
and its pack, at its own size with only `u32@0x14` following the resident end.
All **28 shipped meshes come back identical** over 1588 triangles.
So both halves of a cutscene edit have now been to a console: changing what a
node holds, and adding one.

**And a whole borrowed character, authored in Blender.**
`out/crashbash-eurocom-cortex-added2.bin` runs: `chars/warp/cortex` stands in
the Eurocom intro at the scale of its own letters, drawn by a prop node of his
own, and the logo drops around him. Everything about that disc came out of the
add-on's *Add Selected Mesh* — there is no probe script behind it — and four
things are shown at once that were separate before: a model **borrowed across
packs into a cutscene**, its **seven textures appended** (19 → 26, the swatch
correctly moved to 25 and nothing repainted), a **new mesh** (28 → 29) and the
**node that draws it** (13 of 13 keys naming `0x201d`). Read back out of the
disc, 990 of 992 entries are byte-identical and the 28 shipped meshes return
identical under `payload_bag` — position, colours, UVs and the texture entry,
canonical under rotation and not under reversal, so the facing survived too.
Adding a mesh forces the whole-model rebuild (44,764 → 83,280 bytes, colours
1162 → 1587 renumbered from entry 0), which is the layout this model was already
confirmed on.
So texture *appending* is now confirmed on two discs and in two packs, a level's
and a cutscene's.

**The Blender path has reached hardware.** `out/crashbash-eurocom-burst.bin`
runs: the intro's first emitter edited in Blender and exported through the
add-on, built by patching the original image, and confirmed on screen by the
user. Read back out of the built disc with the same parser, **1 of its 992
entries differs** and it is the same 44,764 bytes as the one it replaced —
`intro_eurocom`'s emitter 0 at lifetime 24 → 72, speed (100, 300) → (400, 1200)
and window end 110 → 170. So a scene-node field edit survives Blender, the
exporter, the DAT repack and the ISO patch, and the game's simulation runs on
what came back.

**The full table rebuild is confirmed across every kind of model.**
`out/crashbash-cutscene-and-levels.bin` carries `cutscene/intro_eurocom`,
`arena/polar_polar` and `arena/pogo_painter` rebuilt whole -- every mesh through
this writer, both tables built from nothing but the meshes, renumbered from
index 0 (1162 -> 1259, 354 -> 372, 555 -> 561 colours) -- and all three play
correctly. With `mainmenu/models` and `warp_room1` before them that is a
cutscene, two arenas, the menu and a hub room. Measured over the archive,
**378 of 378 models rebuild that way with every triangle identical**: 203,054
in the levels, 117,512 in the cutscenes, 45,300 in the characters, 93,373 in
the menu group. Nothing is refused. `tools/native_roundtrip.py` now covers the
seven §8.6 carriers too -- the level group went from 123 files and 92,790
triangles to **130 and 108,288**.

**A §8.6 hub room has too, tables and all.**
`out/crashbash-warp-full-rebuild.bin` runs: `warp_room1` laid out again from
its own regions with **both shared tables built from nothing but its meshes and
its door previews** — colours 4516 -> 4546, UVs 2770 -> 2795, every entry
renumbered from index 0, the shipped numbering not kept even as a prefix — and
pool mesh `0x501C` scaled 1.5x taller so the rebuild is visible. It loads, the
room draws, and **a door preview opens clean**, which is the exact screen that
turned every textured surface in the level to garbage twice before. Read back
out of the disc, 992 of 992 entries hold what was meant for them, all 1156
preview triangles still name the colour and the UV they named, and the only
mesh that differs is the one that was edited.

**And the artist's own path reaches the same screen.**
`out/crashbash-warp-blender-rebuild.bin` runs too, and that one was authored
end to end through the add-on: `warp_room1` imported into Blender and exported
straight back, all **114 meshes through this writer**, both tables built from
its meshes and previews and renumbered from index 0 (4516 -> 4568, 2770 ->
2802). So a full rebuild is not something only the core can do -- it is what
the export button does, with nothing to patch afterwards.

**A rebuilt mesh has too**, which is the harder half: `out/crashbash-tall-m.bin`
runs, and it carries `intro_eurocom`'s mesh 6 — the M of the logo — with 37 of
its 41 vertices scaled ×1.9 upward in Blender and the mesh put through
`mdlwrite` from scratch. On screen the M towers over the other six letters and
everything around it is where it was. That is the first time this project's
**striper and its winding** have been shown to a console: a strip list built
here rather than copied, and a corner order handed over as §11.3 states it. Both
are invisible to every check that is not a console — a strip list that walks
past its block draws stale world-space vertices only once the model moves, and a
triangle handed over backwards is *culled*, so it renders as nothing at all.

Read back out of that disc, **992 of 992 entries hold exactly what was meant for
them**. The export reported 1 mesh rebuilt and 27 untouched, the colour table
chained 1162 → 1208 of its 8192, and the entry came back 180 bytes *smaller*
than the shipped one — this striper is usually looser than the authoring tool's,
but on those 67 triangles it chained longer strips than the original and won
back more than the colour table cost.

**An object-pool mesh has too, and with it the whole from-scratch layout.**
`out/crashbash-oxide-tall-pool.bin` runs: `boss_oxide/arena` laid out again
region by region — no pinning, nothing stranded, 233,202 → 233,198 bytes with
two bytes unreachable — carrying pool mesh `0x500C` scaled 1.8× taller in
Blender and rebuilt by this striper. That rebuild wants 2788 bytes against the
2444 the mesh owns, which is exactly what `_write_in_place` refused, so the
pool run slid: every pool mesh after it moved and every object record moved
with it. It draws taller in all six of its placements and the room is otherwise
itself.

That disc also found what nothing static could. Its first build **loaded and
drew while some objects spun where they stood** — the two rebuilt meshes had
lost their §8.4 collision volumes to a lookup that defaulted to zero, and the
geometry comparison, the placements and the 992/992 verification all passed
regardless. The second build, with the blocks carried, is the one that runs.

Still never shown to a console from this path: a **clip** rebuilt here, a
**texture** repainted here, and a **placement** moved here. All three have
corpus round trips behind them.
