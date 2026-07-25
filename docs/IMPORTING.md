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
  genuinely black texel (a pupil) must carry the STP bit: 0x8000.
- **Transparency punches holes.** Fill transparent atlas pixels with the
  tile's dominant opaque colour before quantising, or edge texels come out as
  skip-pixels.
- **Detail smaller than a triangle cannot be baked.** Vertex-colour baking
  loses anything that lives inside a triangle's UV area — that is exactly how
  the eyes went missing. Faces need real texture.

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
   the new end; the mesh's `+0x2C` attachment pointer is zeroed, because its
   records describe the vertices of the mesh that left (§8.4).
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
