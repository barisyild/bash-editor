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
- **[README.md](README.md)** — the user-facing account.

`crashbash/` is the format library and imports no GUI code; `app/` is the
PySide6 front end. Readers are `formats/{mdl,anim,tex,sfx}.py`, writers are
`formats/{mdlwrite,animwrite,texwrite}.py`, glTF is `formats/gltf.py` out and
`formats/{gltfread,gltfimport}.py` back in. `build.py` repacks the DAT and
patches the EXE tables; `iso.py` masters or patches the disc image;
`retarget.py` moves animation between differently proportioned characters.

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
  (7/400). It emits the **graft layout**, hardware-proven by `safeadd2` and
  `safeadd3`: the file stays byte-identical through its old EOF except the
  rebuilt mesh's header and `0x08`/`0x50`, new blocks go after the §8.6 block
  under a grown sector-aligned `0x50`, colours map to the nearest existing
  entry, and textured triangles need their exact UV triple already in the
  table. *Why* `0x24` is pinned is still unfound: every reader traces to a live
  resolve that a byte-identical copy would satisfy, and a disc-wide sweep of all
  385 loads at that offset accounts for every one. Searched and not found is not
  the same as absent.
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
- **Strip flag bit 3 states the first triangle's winding** (§5.1), and bit 0 of
  the vertex flag alternates from there. A mesh must not contradict its own
  flag byte.
- **`mesh+0x2C` is the collision volume** for a character (§8.4) — a standing
  cylinder read live by gameplay. Zeroing it let the character walk through the
  crates. Carry the replaced mesh's own block through a transplant. A
  character's *second* mesh is its spin body, with a volume of its own.
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

Distrust previews. A flat-shaded preview cannot show sub-triangle texture, and
no static render can show draw-time flags. For those the emulator is the only
honest renderer, and the user runs it — so state plainly what has and has not
been checked on screen.
