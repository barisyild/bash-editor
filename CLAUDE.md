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
- **In the seven §8.6 carriers the shared tables are pinned and the §8.6 block
  must keep its file offset** (§2.1's fourteen-probe ledger). Repointing `0x20`
  crashes the room; repointing `0x24` **alone** scrambles every textured
  surface — three bytes of the file, a byte-identical copy, everything else
  untouched — and doing it with `0x28` following or with `0x50` grown scrambles
  too. Moving the block loses the map previews, and §8.6's solve says why: each
  door's object record carries a **row index into the `T(0x3C)` descriptor
  table**, and those rows hold the sub-block's *file offsets*, streamed from
  disc by `0x800163E0` / polled by `0x80016450` / released by `0x8001636C`. So
  the block may move only if those rows move with it.
  Use `install_mesh(pin_tables=True)` / `import_glb(pin_tables=True)` — engaged
  automatically now, since a carrier announces itself by a non-zero `i32@0x38`
  (7/400). It emits the **graft layout**: the file stays byte-identical through
  its old EOF except the rebuilt mesh's header and `0x08`/`0x50`, new blocks go
  after the §8.6 block under a grown sector-aligned `0x50`, colours map to the
  nearest existing entry, and textured triangles need their exact UV triple
  already in the table — the writer now looks each one up and refuses the mesh
  when it is missing. It used to write `uv_base + f*3` instead, which aims every
  face just past the end of a table that cannot grow: `warp_room1`'s mesh 75
  came back with all twenty indices at 2770..2827 against a 2770-entry table,
  and on screen the slab drew with whatever followed the table for UVs — the
  texture simply gone. A scale survives the lookup because it leaves each
  triangle's corner order alone; a reshape that re-orders corners does not
  (boxing mesh 89 lost 16 of its 91 faces' triples). *Why* `0x24` is pinned is still unfound: every reader
  traces to a live resolve that a byte-identical copy would satisfy, and a
  disc-wide sweep of all 385 loads at that offset accounts for every one.
  Searched and not found is not the same as absent.
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
- **A shot's emitter names its mesh by *id*, and its position is in the
  parent's frame.** The reader reports a resolved index and a world position;
  writing either straight back is wrong. `mesh_index` has to go back as
  `0x2000 | (index + 1)` — writing the index instead made eight emitters vanish
  from a shot patched with nothing changed — and a sub-scene's emitter position
  has to have the parent frame taken off again, exactly as a track's keys do.
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
  vertex that lands there by chance.
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
- **Import needs the model's sibling `.tex`.** Without it no material resolves
  to a slot and every mesh is rebuilt untextured — silently, until it is on
  screen, where it reads as a texture bug in the game. The importer now refuses
  the case, but the call still has to pass the pack.
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
  rescale; pack VRAM placement is still unknown (§10.1).
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
- **Never reorder or drop the shipped colour table — grow it only at the end.**
  Rebuilding it from the meshes' own triples looked safe: `mainmenu/models`
  carries 5216 entries and its 22 meshes reach all 5216. That measurement does
  not say what it seems to. *Covered by the meshes* is not *reached only by the
  meshes* — because they cover the whole range, any index held anywhere else
  lands inside it too, so rewriting the table under it silently repaints
  whatever that is. On hardware the menu came back drawing flat bands of the
  wrong colour. The shipped entries stay exactly where they are; new triples
  chain onto the end.
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
  the select screen. Never move a slot; pack VRAM placement is still unknown.
- **BGR555 `0x0000` is the hardware's skip-pixel.** A genuinely black texel
  needs the STP bit: `0x8000`.
- **Real triangle strips, not one strip per triangle.** No shipped mesh exceeds
  348 strips; a 431-strip mesh crashed the game.
- **Blender scenes must be at 30 fps before importing an export.** Blender
  resamples onto its scene grid and defaults to 24.

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
Blender --background --factory-startup --python blender/roundtrip.py -- game/SCUS_945.70 120
```

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

**The Blender path has reached hardware.** `out/crashbash-eurocom-burst.bin`
runs: the intro's first emitter edited in Blender and exported through the
add-on, built by patching the original image, and confirmed on screen by the
user. Read back out of the built disc with the same parser, **1 of its 992
entries differs** and it is the same 44,764 bytes as the one it replaced —
`intro_eurocom`'s emitter 0 at lifetime 24 → 72, speed (100, 300) → (400, 1200)
and window end 110 → 170. So a scene-node field edit survives Blender, the
exporter, the DAT repack and the ISO patch, and the game's simulation runs on
what came back.

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

Still never shown to a console from this path: a **clip** rebuilt here, a
**texture** repainted here, a **placement** moved here, and an **object-pool**
mesh. The first three have corpus round trips behind them and the fourth refuses
rather than guess.
