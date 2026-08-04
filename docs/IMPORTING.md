# Importing a custom character

What it takes to put a model the game has never seen — geometry, textures and
newly authored animation — into `CRASHBSH.DAT` and have it run. Everything here
was proven the hard way while importing Spyro (from a COLLADA file, with its
own 256x192 atlas) into the main menu in place of Crash, and the rules below
are stated with the failure that taught each one, because every single one of
them shipped at least one broken disc first.

Three earlier swaps proved the narrower cases: the crate-arena penguin into
`chars/warp/crash.mdl` (whole-model replacement), the same penguin into the
menu's 22-mesh `models.mdl` (mesh transplant), and Coco over Crash with
Crash's clips retargeted onto her (animation transfer within one file).

The writers live in `crashbash/formats/`: `mdlwrite.py` (strips, transplant,
install), `animwrite.py` (clips), `texwrite.py` (pixels and palettes in
place), `gltfread.py` (poses back out of a .glb). `crashbash/build.py` repacks
the archive and `crashbash/iso.py` writes or patches the disc image.

## 1. Converting the source

- **Axes.** The format has y growing downward, feet at y = 0, characters
  facing −z. A Y-up source must negate **both y and z** — that is a half turn.
  Negating y alone is a mirror: it inverts every triangle's winding, the
  renderer culls the wrong faces, and half the model disappears.
- **Scale.** Match the replaced character's height in model units
  (`position / GTE_SCALE_SMALL`), drop the feet to y = 0, keep x centred on 0.
  Characters stand centred on the origin; aligning bounding-box corners
  instead shifts a narrower character sideways by half the width difference.
- **Weld before anything else.** A model downloaded from the web is often a
  triangle soup with no adjacency at all: a Sketchfab Suzanne arrived with 1966
  vertices for 968 triangles and **1966 of its 2435 edges carrying a single
  face**, where merging by distance collapses it to 505 vertices and 42 lone
  edges — the eye and mouth boundaries a closed surface should have. Everything
  downstream needs that adjacency. Blender's recalculate-outside has no
  neighbour to spread a facing through, its decimator no edge to collapse, and
  the striper below no shared edge to chain on, so it writes one strip per
  triangle. Since the importer takes the winding as it arrives (FORMAT.md
  §11.3), an inconsistent soup is culled triangle by triangle and reads on
  screen as a cloud of shards. Weld, recalculate normals outside, then decimate,
  and view it with backface culling on — that is what the console does. Not
  every source needs it: one `monkey.obj` arrived welded, 84 of 5946 edges lone.

## 2. Textures

- **Budget by tiles, not by the atlas.** Only the atlas regions triangles
  actually sample need to exist. Spyro's 256x192 atlas sounds hopeless next to
  64x64 pack slots, but its triangles sample seven 64x64 tiles, none straddles
  a tile boundary, and at half resolution seven tiles fit five slots — the
  64x64 slot holding four as quadrants, the face tiles (eye, teeth, claws)
  getting a slot and a 16-colour palette each.
- **Which slots may be taken.** Only those whose *only* sampler is the mesh
  being replaced. "No mesh samples it" proves nothing: the menu draws its
  character-select portraits from code, no geometry involved, and overwriting
  those "unused" slots corrupted the select screen (FORMAT.md §10.3).
- **Never move a slot.** Pack layout and VRAM placement are still unknown
  (§10.1); only pixels and palette values inside existing slots are safe.
- **Pure black vanishes.** BGR555 0x0000 is the hardware's skip-pixel. A
  genuinely black texel (a pupil) must carry the STP bit: 0x8000. And a pack the
  art comes *from* may not agree about that: 120 of the disc's 11,234 palettes
  mark unused area with opaque magenta instead (FORMAT.md §10.2). See *Moving a
  mesh from one pack to another* below.
- **Transparency punches holes.** Fill transparent atlas pixels with the
  tile's dominant opaque colour before quantising, or edge texels come out as
  skip-pixels.
- **Detail smaller than a triangle cannot be baked.** Vertex-colour baking
  loses anything that lives inside a triangle's UV area — that is exactly how
  the eyes went missing. Faces need real texture.
- **When the atlas does not tile, pack UV islands.** Spyro's atlas happened to
  cut into whole 64x64 tiles; Pepsiman's did not — 165 of 562 triangles
  straddled tile boundaries. The general route: weld UV corners, flood the
  triangles into islands, and shelf-pack every island (a quarter turn allowed)
  at one uniform scale into the free slots, binary-searching the largest scale
  that fits — 34 islands went in at 0.31x. A triangle never leaves its island,
  so nothing straddles anything, and each slot quantises to its own 16
  colours. Give islands a one-texel replicated border, or nearest sampling
  bleeds the neighbour in. Known limit: islands are cut by bounding box, not
  by triangle mask, so background pixels inside the box downscale into the
  island — Pepsiman's white gloves came out dusky over the atlas's black
  ground.

## 3. Geometry

- **Real strips, not one strip per triangle.** No shipped mesh exceeds 348
  strips (median 2.33 triangles each); a 431-strip mesh crashed the game.
  Adjacency must be recovered by welding positions, since exported triangle
  soup carries none.
- **Chain on directed edges.** A strip presents its triangles alternately
  reversed, so the triangle that may follow is the one carrying the shared
  edge the *other way round*. Matching the undirected edge accepts
  wrong-winding neighbours and turns them inside out.
- **Order corners by the strip, not by the found edge.** The strip always
  presents `(s[k], s[k+1], s[k+2])`; on odd steps that is the reverse of the
  edge that located the triangle. UVs and colours are positional
  (FORMAT.md §11.3), so this ordering decides whether textures land straight.
- **Winding flags are a contract.** Bit 0 of the vertex flag alternates along
  the strip and the strip flag's bit 3 states the first triangle's value —
  42,267/42,267 shipped strips agree with themselves (§5.1). Rebuilding the
  game's own mesh 13 reproduces its pool 473/473 and its strip lengths 66/66,
  which is the test that settled every one of these rules: **check the writer
  against the game's own data, never only against the project's own reader** —
  reader and writer share assumptions, so round-tripping through them proves
  nothing.

## 4. Installing into the model

Order matters, and `model+0x08` is why (FORMAT.md §2.1): every mesh block and
table must sit inside the span it describes, and every animation blob after
it — 373/373 shipped models hold that invariant, and a build that violated it
crashed while one honouring it booted.

1. `strip_animation` — lift the blobs off the end.
2. `install_mesh` / `transplant_mesh` — new blocks land inside the span;
   colour and UV tables are appended as verbatim copy + new entries so every
   other mesh's indices keep meaning what they meant; `model+0x08` moves to
   the new end; the mesh's `+0x2C` attachment block needs care (§8.4): for a
   character it is the **collision volume** — a standing cylinder of the
   character's radius and height, read live by gameplay — and zeroing it let
   the crate game's character walk straight through the crates, which is how
   its meaning was found. Pass the replaced mesh's own block through the
   transplant when the stand-in is scaled to the same height; zero only when
   no valid volume exists.
3. `write_clips` — the blobs go back on, after the boundary.

## 5. Animation

- **Author in the built mesh's pool order.** Striping reorders and shares
  vertices. Read the installed model back and pose *its* pool; poses in the
  source's triangle order animated a shredded model while every static check
  passed, because the game draws the animated pose, never the static records.
- **Keyframes carry the winding.** A keyframe entry's low two bits are the
  vertex's flag word, and they are what the renderer sees at draw time
  (§9.4). Writing zeros there shipped three broken discs — including one
  whose static strip list and vertex pool were byte-identical to the
  original. Copy the built mesh's flag words into every clip.
- **Timelines are free.** A clip is keyframe poses plus displayed frames that
  each blend two keyframes with a 12-bit weight; `keyframe count` and pacing
  are the author's choice as long as the frame count matches what the caller
  expects. Spyro's four menu clips (breath, head, tail, wing — hinges picked
  from the mesh's own proportions, smooth falloff along the body) are written
  from nothing; nothing of Crash's motion survives.
- **Retargeting between proportions transfers rotation, not position.** A
  deformation cage stretches short limbs into long ones; copied displacements
  drive a short arm through the body; per-vertex rotation fitting shatters a
  low-poly mesh. What worked for Coco: cluster the source's vertices by their
  motion (the animation implies its own skeleton — arms, hands, head and
  torso fall out symmetrically), fit one rigid motion per segment, and turn
  the target about **its own** joints with smoothed weights. For hand-made
  results, pose in Blender instead and read the shape keys back with
  `gltfread.py`, which matches vertices by rest position so re-ordering tools
  cannot break it.
- **Set the Blender scene to 30 fps before importing the export.** Blender
  resamples animation onto its scene's frame grid and its default is 24 fps: a
  clip taken in at 24 and saved comes back with poses up to 43 raw units off,
  and changing the fps after the import re-times the keys and makes it worse.
  With the scene at 30 fps from the start, a full session — our export,
  Blender import, Blender save, our import — reproduces every pose to
  **0.00 raw units**, with mesh, material and clip names all surviving. That
  session is the editor's own round trip (File → Export model as glTF…, edit,
  File → Import model from glTF…), verified against Blender 5.2.
- **Tick Data → Attributes in Blender's glTF export.** The PS1 blend
  brightens as well as darkens — `texel * colour / 128` runs to 2.0 — and
  glTF's own colour channels stop at 1: Blender quantises `COLOR_0` into a
  clamped byte attribute on import, and its default export clamps a second
  colour set the same way. The only channel measured to carry the full range
  through a Blender pass to the last bit is the exporter's
  `_CRASHBASH_COLOR` attribute, and Blender writes it back only when
  Attributes is ticked. The importer prefers that attribute, recovers every
  colour byte exactly from it — verified corpus-wide, 400 models,
  331,885 faces, zero mismatches, and through a live Blender round trip on
  the shot3 cutscene cast, whose corners are over 128 on more than half —
  and warns rather than dimming silently when it is missing. Without it the
  clamp crushes 128..255 to 128, which visibly drains any model whose baked
  lighting runs hot: that is exactly the washed-out Cortex a user reported
  from Blender, and the measurement that settled it.
- **Blender's display needs `tools/blender_colours.py`, run once after the
  import.** Data arriving intact does not make the viewport draw it: the
  materials still read the clamped byte channel, and Blender shades in
  linear space while the game multiplies in gamma space, so the correct
  linear multiplier is `m^2.2`, not `m`. The script rebinds every material
  to `_CRASHBASH_COLOR`, inserts that gamma, and sets the view transform to
  Standard — AgX, the default, is a filmic look that desaturates flat-shaded
  PS1 colour on sight. With all three in place the viewport matches the
  game; with none of them Cortex's golden hat renders cream.

## 6. Disc and verification

- `build.py` repacks the DAT tight and rewrites both EXE tables; entries keep
  their index, groups their membership.
- `iso.py` patches the original image in place — a file that no longer fits
  its span moves to the end of the image, its directory record and the volume
  length following it. Nothing else on the disc moves.
- **Verify from inside the image**, with the same parsers, against intent:
  the replacement where staged, the original everywhere else. And distrust
  previews — a flat-shaded preview cannot show sub-triangle texture, and no
  static render can show draw-time flags. The screen is the only honest
  renderer for those; emulate early.

## 7. What the screen still shows

Sorting shimmer around detailed areas is the console, not the import: the PS1
has no z-buffer, draws back-to-front through an ordering table, and maps
textures affinely, so near-coplanar surfaces and animated low-poly faces
flicker in every era-authentic title. Spyro's eyes are painted into the head
triangles (measured: no overlay quads exist in the source), so what remains
there is ordinary PS1 behaviour.

## The shot on the way back

The exporter writes the byte offset of every scene record it emits (§9.11), and
`formats/scenewrite.py` is the other half: it writes each field back exactly
where it was read from. Nothing is resized, so no count, size or offset moves,
and the region whose record kinds are still unread (FORMAT §8.3) is never
rebuilt — only read past. That is why this works without a decoded object graph.

**Order matters.** The patch runs on the file as exported, *before*
`install_mesh` moves the layout boundary (FORMAT §2.1). The offsets in `extras`
were recorded against those bytes; patch after a rebuild and every one of them
is stale. Because the patch resizes nothing, the geometry pipeline then runs on
the patched bytes exactly as it would have on the originals.

**Verified the way this project verifies a writer** — against the game's own
data, not against its own reader. Reading the shot and writing it straight back
reproduces the shipped bytes in **205 of 205** models that carry a placement list
or a scene: 2689 placement records, 5819 track keys, 173 camera keys. A single
edit is just as narrow — moving one placement 100 units in x changes one byte,
the x word going 35 to 25635, a delta of 25600 = 100 × 256 in the file's 8.8
fixed point.

**Sub-scenes, the case that needed care.** A sub-scene's keys are read onto the
parent's clock *and* into the parent's frame, so writing one back means running
both backwards. `extras` carries the clock shift per track and per camera, and
it carries the parent's own placement; `_Frame` inverts the placement — the
basis transposed, the translation subtracted, the scale divided out, the
quaternion conjugated — and the shift is subtracted from the tick. With the
placement carried, **nothing is skipped**: all 5819 track keys and 173 camera
keys write back byte for byte. A file exported before the placement was carried
still has parented keys reported as skipped rather than written wrong.

## The whole trip, measured

`tools/roundtrip.py` exports every entry, imports the file it just wrote, and
compares what the two models draw. Geometry cannot be compared byte for byte —
import re-derives the strip list, so the bytes legitimately differ — and it is
compared as sorted triangle corners instead:

| Group | Files | Triangles | Same count | Worst corner | Scenes | Byte-identical | Scene only |
| --- | --- | --- | --- | --- | --- | --- | --- |
| level | 134 | 196,700 | 196,700 | **0.0000** | 113 | 113 | 5 |
| cutscene | 64 | 119,220 | 119,220 | **0.0000** | 64 | 64 | 0 |
| character | 104 | 45,300 | 45,300 | **0.0000** | 0 | 0 | 0 |
| models | 98 | 93,373 | 93,373 | **0.0000** | 28 | 28 | 0 |

Sorting before comparing is the whole reason that column reads zero. A rebuilt
mesh returns its triangles in a different order, and comparing the two lists in
sequence measures the re-ordering: the first version of this tool reported
corner errors of 5 to 20 units on models that had lost nothing at all.

**Five entries have no numbered meshes** — `arena/test/objects`,
`medieval_ring/arena`, `tank_jungle/arena`, `tank_jungle/crystalarena`,
`crate_jungle/arena`, with 1, 16, 8, 4 and 15 object meshes respectively and
nothing at all in `model.meshes`. The `_meshNN` names the export writes and the
import matches on never exist for them, and the writers cannot install into that
pool (FORMAT §8.3) in any case.

They are not failures, though, and treating them as such hid a real one. The
scene patch runs before the mesh matching, so by the time the importer found
nothing to match, a valid edit to their **56 placement records** was already
complete — and raising threw it away. The importer now returns that result with
a warning saying the geometry was left as it was, and only a file that changed
nothing at all is an error. The tool counts them in their own column rather than
as a rebuild that never ran, which is what "same count" would otherwise have
silently claimed.

## Three ways a rebuild goes wrong, all found on a real disc

These are recorded together because they were found together, in one build that
edited four models and broke three of them.

**Pass the sibling `.tex`.** `_material_slots` resolves a material name like
`tex_014_64x64_4bpp` to a pack slot, and without a pack it resolves nothing.
Every primitive then comes back with `slot = None`, every mesh is written
untextured, and the result looks like a texture bug in the game rather than a
missing argument. The importer now counts materials whose names carry a slot and
refuses the import if none resolved, but a caller still has to hand it the pack —
`app/window.py` reads it from the entry of the same name.

**Insert, do not append.** A model keeps its geometry inside `T(0x44)` and inside
`i32@0x50`, 400/400 each (FORMAT §2.1). Appending at EOF and moving `0x08` there
breaks both, and for the seven models with an §8.6 block it writes the new
geometry straight into that block, because with no clips to strip there is
nothing else between `T(0x44)` and the end of the file. `warp_room1` rebuilt that
way did not load. `mdlwrite` now splits at `T(0x44)`, builds in front of the
tail, and moves `0x44` and `0x50` by the inserted length.

**Bring only the meshes you edited.** Each `install_mesh` appends a full copy of
the colour and UV tables, so a glTF that still contains all 42 of `warp_room1`'s
meshes appends them 42 times — 196 KB became 1.3 MB. Deleting the untouched
meshes in the modelling tool before exporting brings it back to 229 KB, and the
importer rebuilds only what is present, so nothing else changes.

One measurement trap is worth carrying with them. After a rebuild the triangles
come back in a different order, so "the last N triangles" is not the geometry you
added — checking a new colour that way reported the wrong value twice. Compare
the colour *table* against the original instead: each of the four edits added
exactly one entry, matching what was set to within the rounding.

## Bringing a model in from another game

Crash Bandicoot 2's cutscene Coco, dumped by CrashEdit as an OBJ with vertex
colours and ten textures, into `chars/crate/coco`. Four discs were built before
it drew, and each failure named something the earlier imports had not reached.

**The strip count, not the triangle count, is what a model weighs.** The first
build kept all 648 triangles and came back at 747,672 bytes against the shipped
215,678 — and it black-screened. Of the 532 KB it added, only 16 KB was
geometry: **516 KB was animation**. A pose stores every vertex of the strip
pool, a strip of n triangles occupies n + 2 vertices, and the writer's striper,
left to seed itself, made 495 strips of those 648 triangles for a pool of 1638
against the shipped 319. That also put it past the 348 strips no shipped mesh
exceeds. Two faults, one cause.

**Chain the strips yourself before handing them over.** `build_strips` seeds a
strip with a face's corners as they stand and continues along the edge its
second and third corners make, taking the lowest-numbered unused face that
carries it — so the chains it finds are decided by the order the faces arrive in
and by how each seed happens to be rotated, both of which belong to the caller.
Left as the dump had them, 328 of the 648 faces became a strip of one. Chaining
first and emitting along those chains took it to 161 strips, 4.02 triangles
each, and the writer rediscovered them exactly. Rotating a triangle's corners is
a cyclic permutation and cannot change its winding; it only changes which edge
the strip tries next.

**A frame's weight and its second keyframe are one fact.** Freezing the
animation — every frame on one pose — looked like a way to skip retargeting
entirely, and it collapses each clip to a single keyframe, which took the file
to 99,084 bytes, less than half the shipped model. But the frames kept the
weights they had been carrying, and `weight != 0` is what selects the game's
blend decoder, which reads a `key_b` that is no longer there. The model came
apart *differently on every frame*, in proportion to that frame's weight. The
diagnosis came from the screen: paused, it drew perfectly, because a paused
frame is a weight-0 frame. `animwrite` now refuses the mismatch in both
directions, and all 1037 shipped clips pass the check.

**Retargeting needs the two rest poses to have the same silhouette.**
`crashbash.retarget` finds correspondence in a normalised box, one axis at a
time, so what matches is relative position. Measured across the widest
horizontal band and where it sits:

| model | widest band | at | mirror mismatch |
| --- | --- | --- | --- |
| shipped `chars/crate/coco` (the clips' own body) | 1.56 | 48 % | 0.006 |
| the disc's cutscene Coco, which retargeted cleanly | 1.73 | 48 % | 0.005 |
| the Crash 2 cutscene Coco | 0.88 | at the hips | 0.093 |

The Crash 2 model stands with its arms down, so its shoulders and the sides of
its hair normalise onto the shipped model's *hands* — the fastest-moving
vertices in every clip. The head tore into long wedges while the legs, which map
to legs, stayed clean. Turning the arms out about their own shoulders moved the
widest band back to 48 % and the tearing stopped.

**A cutscene model is not a character model**, and the three ways it differs are
all measurable. It is posed for one shot: mirror mismatch 0.093 where every
shipped character measures 0.005–0.006. It is lit for one shot: 1016 of its
channel values sit at 124, only 5.8 % reach the hardware's neutral 128 at all,
and it drew at 36 % of the shipped Coco's luminance with no highlight anywhere.
And it may simply lack what the shot did not need — this one has no mouth, in
the source as much as in the import.

Symmetry comes back with a mirror snap, which moves positions without deleting
faces, so the bag on one hip survives. But **collapse decimation undoes it
again**, because it collapses each side independently: 0.010 before decimating
became 0.033 after. Snap again afterwards and it returns to 0.008.

**Brightness is a percentile, not a mean.** The hardware draws a blended polygon
as `texel * colour / 128`, so 128 is neutral and 255 doubles; Crash Bash's
characters paint into that upper half and this one does not. Matching the
shipped Coco's *mean* luminance wanted ×8 and drove 46 % of channels into the
ceiling — a quarter of this model's channels are zero and no gain lifts those.
Protecting the largest value below 255, a lone 181, allowed only ×1.41 and left
it as dim as it started. Putting the **90th percentile** on 255 gave ×2.06: that
block is a single flat level, so mapping all of it to 255 loses no distinction —
54 channels carried a value of their own — and the drawn luminance went 49 → 88
against the shipped Coco's 138. Past the knee the cost is immediate: ×2.25 takes
clipping from 5.8 % to 24 % as the whole block goes over.

**One trap that has nothing to do with the format.** Importing the same OBJ
twice into one Blender session suffixes every material with `.001`, `.002`.
Matched raw, not one name resolves to a slot, every face falls through to the
swatch branch, and the mesh is written untextured — silently, until it is on
screen. Strip the suffix and treat an unknown material as an error, never as a
fallback.

What the model itself needed, in the end, was **welding and nothing else**: a
CrashEdit dump is a triangle soup with no adjacency, which neither the striper
nor the normal pass can work without. Decimation, arm posing and symmetry were
each a response to a downstream problem — size, retargeting, and the shot's own
pose — and with the animation frozen none of them applies. All 648 triangles go
in as authored.

**Confirmed on screen**: 648 triangles in 161 strips, a 99,084-byte model, every
clip frozen on the model's own pose, and the colours lifted by ×2.06. What the
import still costs is the motion — she stands in one pose whatever she is doing
— and a mouth the source never had.

## Moving a mesh from one pack to another

The disc already holds better versions of its own characters: the cutscene model
`level_intro_shot_group_teamgood.mdl` carries a high-poly Coco, and putting her
into `chars/warp/coco.mdl` and `chars/crate/coco.mdl` is a transplant between two
models that do not share a texture pack. The geometry is the easy half. Four
separate things about the art have to be carried across, and each of them was
found by looking at the result on hardware.

**Carry the source's own pixels; do not point at the nearest picture.** Matching
the two packs by image looked like enough — both dress the same character — and
it very nearly is. Of the nine slots the cutscene Coco samples, two match the warp
pack's pixel for pixel (both 8x8) and her largest, a 64x64, matches at 3.3 of 255.
But the worst is 113.7, and that one is her **eye** — a 32x32 eyeball with a green
iris and a white highlight, for which the warp pack simply has no counterpart:
nothing in it scores under 111. That is the shape of the problem. The low-poly
character a cutscene model replaces does not carry the same features, so the
slots with no counterpart are exactly the ones that matter most, and a better
matcher cannot invent them. Every slot being replaced is sampled by the mesh
being replaced and by nothing else, which is the one case §10.3 allows, so the
source's art is simply written into them. Every pair here is 4bpp with sixteen
colours, so the palette copies whole and only the picture is resampled — a slot
cannot be resized, pack VRAM placement being unknown (§10.1).

**A UV rescale is `(dest - 1) / (source - 1)`, not `dest / source`.** The
destination's slots are smaller — a 64x64 and six 32x32 sources landing on a 32x32
and six 16x16 — so the UVs have to shrink with them. A texel coordinate on a 32x32
texture runs 0..31; halving that gives 0..16, and 16 is one column past the end of
a 16x16 slot. Measured over the mesh: **35 of its 79 textured faces** put a corner
outside the texture it names under `dest / source`, and none do under
`(dest-1)/(src-1)`, which lands 31 on 15 exactly. The four that showed on screen
are the eyes — two triangles each, and each one spans its texture corner to
corner, so there is no margin to absorb the error. They came back blank.

That is worth stating as a rule: the faces this breaks are the ones whose UVs
reach the edge of their slot, and a face that uses its whole texture is usually
a face that matters — an eye, a mouth, a sign. A rescale that is one texel wrong
is invisible everywhere else.

**A swatch face's colour must be carried, not its palette number.** 279 of that
Coco's 358 faces are swatch faces (§6.2): flat-coloured, painted by one texel of
the pack's palette-less swatch texture, naming a palette and pointing their UVs at
a cell. Neither the palette numbering nor the cell layout survives a move between
packs, so mapping the palette alone leaves each face reading whatever happens to
sit at the source's cell — that is where the black hair came from, the ears being
a separate fault below. `Transplant.swatch_face`
takes, per source face, the destination palette and cell that give the colour it
*meant*: matched over every cell of the destination swatch read through every
palette, 125 colours reachable in the warp pack and 175 in the crate one, 231 of
the 279 faces land exactly and the worst is 8 of 255 out.

**A pack's colour key is not universal.** One palette of the cutscene pack's 27
holds opaque magenta, `0xFC1F`, as its colour 15, and that colour fills 33.4 % of
the one texture reading it — the part a character pack would have left at
`0x0000`, which the hardware skips (§10.2). Copy the palette across without
translating the entry and the magenta draws. The six faces sampling that texture
are two mirrored triples at the sides of Coco's head, so it appeared as pink
patches beside her ears.

The rest is the ordinary route. The destination's clips are poses of its own
vertex count, so they are retargeted rather than copied — `crashbash.retarget`
fits a rotation per moving part and blends, which keeps a limb its own length —
and a clip driving some other mesh is re-emitted from its original poses instead.
The replaced mesh's own §8.4 collision volume is carried through: the crate Coco
has one and it survives the rebuild, the warp Coco has none to carry. The order
is strip → transplant → write clips (§2.1).

**Both Cocos were confirmed on screen, eyes included, and the whole pipeline
reproduces.** Re-running it from the shipped archive gives back all four staged
entries — `chars/warp/coco.{mdl,tex}` and `chars/crate/coco.{mdl,tex}` — with the
same nine picture distances and the same 279 swatch faces of 358 remapped (231
exact, worst 8 of 255; 125 colours reachable in the warp pack, 175 in the crate
one). Compared not against the staged files but against the entries read back out
of the disc image that was actually tested, all four are byte for byte identical.
