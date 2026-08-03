# Crash Bash 3D format — reference specification

Target build: **NTSC-U retail**, `SCUS_945.70`, MD5 `f620ac01cd60c55ab0e981104f2b6c48`,
paired with `CRASHBSH/CRASHBSH.DAT`, MD5 `8d4d2fb7d308fb97c462fa5ecf1121c1`
(73,220,096 bytes = 35,752 sectors).

Everything below is either read out of that executable's MIPS code or measured over the
whole archive. Where a statement is neither, it says so.

## How to read this document

Every field row carries one of three confidence markers:

| Marker | Meaning |
| --- | --- |
| **confirmed** | Backed by a disassembled instruction sequence in `SCUS_945.70`, **or** by a measurement that holds for every case in the corpus with no exceptions. |
| *likely* | Consistent with everything measured, and the only reading that fits, but with no code site proving it or with a corpus that is too small to be decisive. |
| ?unknown? | Read but not understood. The distribution is given; the meaning is not. |

### What a negative means here

**"No reader found" never means "nothing reads it."** A scan shows where a *shape* is absent.
It cannot show that a routine does not exist, and this document has been wrong that way three
times: it said no `GetClut` arithmetic existed anywhere on the disc — three exhaustive scans
agreed — and `0x800364FC` is `GetClut`, **called** rather than inlined (§10.4); it said
nothing read the sub-object's +0x10 block, and 0x80024B70 does (§8.5); and it generalised
from `warp.bin` that no mode overlay ever holds a raw model base, which `crate.bin` disproves
at 0x800B4BDC by reading a model's stamp, mesh count and first header (§14). The first two
were searches that had not found something; the third was a single sample generalised. All
three were written up as facts.

So these phrases are all shorthand for the same thing, and none of them is a claim that a
field is unused:

| Written | Means |
| --- | --- |
| "no reader found" | a search was run and found none; the search is stated where it matters |
| "unread", "not read on this path" | this document has not decoded it, or that particular routine does not touch it |
| "not read by any render pass" | the pass was read instruction by instruction and it is not there |

A **positive** claim needs a disassembled instruction sequence or a corpus measurement with
no exceptions. A **negative** claim is only ever "I could not validate that anything reads
this", and carries the limits of the search that failed — the region scanned, the instruction
shape looked for, and what would have escaped it. Every negative in §14 is written that way
on purpose.

### Corpus

| Quantity | Count |
| --- | --- |
| Archive entries | 992 |
| MDL models | 400 (399 stamp `0x0C160029`, 1 stamp `0x09160026`) |
| Mesh headers | 5990 |
| Meshes with a non-empty strip list | 5989 |
| Strips | 81,045 |
| Vertices | 525,341 |
| Triangles | 363,251 |
| Texture-run entries | 47,243 |
| Animated models | 225 |
| Animation clips | 1037 |
| Animation frame records | 49,167 |
| Animation keyframes | 13,652 |
| TEX packs | 400 |
| Textures | 15,160 (14,885 4-bit, 275 8-bit) |
| Palettes | 11,234 |

"5990/5990" in a row below means the measurement was run over every mesh header in every
model, not over a sample.

### The one convention you must implement first: self-relative pointers

Every pointer in an MDL — in the file header and in the mesh headers alike — is stored as a
**signed 32-bit displacement from the address of the field itself**, not from the start of
the file. The game resolves them with a fixed three-instruction idiom:

```
80017F30  lw    $v0, 0x24($a1)      ; v0 = the stored displacement
80017F38  addiu $v0, $v0, 0x24      ; v0 += the field's own offset
80017F3C  addu  $a1, $a1, $v0       ; a1 = base + that  ==  (base+0x24) + displacement
```

So for a field at file offset `F` holding value `V`:

```
target = F + V
```

This is used verbatim at every pointer site listed in this document. **confirmed** — an
exhaustive scan of `.text` for `lw rX,K(base)` followed within six instructions by
`addiu rX,rX,K` finds the idiom at these offsets and nowhere else:

| Field offset | Sites |
| --- | --- |
| 0x08 | *none* |
| 0x10 | 0x80019D54, 0x8001AB80, 0x8001CE44, 0x8001CEE0, 0x8001CF68, 0x8001DE6C, 0x800292A8 |
| 0x14 | 0x80017B74, 0x80017DA4, 0x80017F18, 0x80019D98, 0x8001C00C, 0x8001CE88, 0x8001CF24, 0x8001CFB8, 0x8001D018 |
| 0x18 | 0x80017968, 0x80017EF8, 0x8001DE28 |
| 0x1C | 0x8001599C, 0x800159E0, 0x80017EFC, 0x8001DEA8, 0x80029488 |
| 0x20 | 0x80017830, 0x8001784C, 0x80017964, 0x80017994, 0x80017B28, 0x80017B88, 0x80017D94, 0x80017DB4, 0x80017F14, 0x8001DE80 |
| 0x24 | 0x80017F30 |
| 0x28 | 0x80019C08 |
| 0x2C | 0x80015700 |
| 0x3C | 0x80016380, 0x800163F4, 0x80016458 |
| 0x44 | 0x800156A8, 0x80015F94, 0x800164B8, 0x80016594, 0x80016638, 0x80016738, 0x800167D8, 0x80016898, 0x80016994, 0x80016A54, 0x80016B88, 0x80019B34 |
| 0x4C | 0x8001EB10 and 12 further identical sites |

All values in this document are **little-endian**. `i16`/`u16`/`i32`/`u32` mean signed/unsigned
16- and 32-bit integers.

---

# 1. Container

## 1.1 CRASHBSH.DAT and the file table

`CRASHBSH.DAT` is a raw concatenation of 2048-byte sectors with no internal directory. The
directory is a **link-time-constant array inside the EXE**.

| Item | Value (NTSC-U) | Confidence |
| --- | --- | --- |
| File table VA | 0x8004E110 (EXE file offset 0x3E910) | **confirmed** |
| Record count | 992 | **confirmed** |
| Record size | 8 bytes | **confirmed** |
| Table end VA | 0x80050010 | **confirmed** |

| Offset | Type | Name | Meaning | Confidence |
| --- | --- | --- | --- | --- |
| +0x00 | i32 | `sector` | LBA within the DAT. Byte offset = `sector << 11`. | **confirmed** |
| +0x04 | i32 | `size` | Exact byte length; **not** sector-rounded. | **confirmed** |

```
; 0x80012678 — group-load completion callback, splits one CD read into entries
800126BC  lw    $a0, ($v0)          ; group.first_index
800126C0  lw    $s1, 4($v0)         ; group.count
800126C4  lui   $v0, 0x8005
800126C8  addiu $a1, $v0, -0x1ef0   ; a1 = 0x8004E110   <-- THE FILE TABLE
800126CC  sll   $v1, $a0, 3         ; index * 8         <-- 8-BYTE RECORDS
800126D4  addu  $s3, $v1, $a1
...
800126F4  lw    $a1, ($s0)          ; sector[i]         <-- FIELD +0x00
800126F8  lw    $v0, ($s3)          ; sector[first]
80012700  subu  $a1, $a1, $v0
80012708  sll   $a1, $a1, 0xb       ; (delta sectors) << 11   <-- 2048 BYTES/SECTOR
80012710  lw    $a1, 4($s0)         ; size              <-- FIELD +0x04
80012714  addiu $s0, $s0, -8        ; walk one record backwards
```

The sector reaches the CD at exactly one place:

```
; 0x80027790 — the only live path from an entry's sector to the drive
80027818  lw    $a0, ($s1)          ; entry.sector
8002781C  lw    $v0, 0x37b8($v0)    ; [0x800637B8] = disc LBA of CRASHBSH.DAT
80027820  addu  $a0, $a0, $s2       ; + relative sector within the group
80027824  jal   0x8003599c          ; CdIntToPos  (begins `addiu $a0,$a0,0x96`)
80027828  addu  $a0, $a0, $v0       ;   (delay slot) + DAT base LBA
80027834  jal   0x800355dc          ; CdControlB(CdlSetloc, &loc, 0)
80027848  jal   0x8003470c          ; CdRead(nsectors, buf, 0x80)
8002784C  addiu $a2, $zero, 0x80    ;   (delay slot) mode 0x80 -> 2048-byte data sectors
```

`0x800637B8` has exactly two references in the whole image: this read, and the write at
0x80027A24 fed by `CdSearchFile("\CRASHBSH\CRASHBSH.DAT;1")`. **confirmed**

### Who calls it, and why every sub-file starts on a sector boundary

That `$s2` is the reader's second argument — a **relative sector inside the entry** — so the
path can begin a read part-way through a file. It has exactly **two callers** in the executable
and none in `gameeng.bin` or the 14 mode overlays:

| Caller | Relative sector | What it reads |
| --- | --- | --- |
| 0x8001358C | `addu $a1, $zero, $zero` — always 0 | a whole entry from its start |
| 0x800124E0 | `sra $a1, $a1, 0xb` — a byte offset divided by 2048 | **any byte range inside an entry** |

The second one takes a three-word request — `+0x00` the entry, `+0x04` a byte offset, `+0x08` a
byte length — and turns it into sectors, rounding the length up with `addiu $a3, $v1, 0x7ff`
before its own `sra $a3, $a3, 0xb`. The offset gets no such rounding: it is shifted straight
down, so **a read can only begin on a 2048-byte boundary**.

That explains a measurement §8.2 records without a cause. Every one of the 1037 sub-file starts
is 0x800-aligned, and it has to be: this is the only way a sub-file is fetched, and the loader
cannot express a start that is not a whole sector. It also means the alignment of the §8.6 hub
block is **not evidence about that block** — anything loaded this way would look the same.

Sector size 2048 is also the unique multiplier that makes the table self-consistent: with
M = 2048 all 992 entries fit inside the DAT with zero overlaps and 840 bytes of slack;
M = 1024 produces 939 overlaps, M = 512 produces 940, and M ≥ 2328 overshoots EOF by
9.9 MB or more. **confirmed**

Entry-layout facts, measured over all 992 (all **confirmed**):

* Sectors strictly increase across all 991 consecutive pairs; no overlaps; no zero-length
  entries; no duplicate (offset, size) pairs.
* Sum of sizes = 72,069,664 bytes (98.43 % of the DAT). Last entry ends at 73,219,256.
* `stride == ceil(size/2048)` for 979 of 991 pairs; the other 12 reserve one extra sector.
  Those 12 all have `size mod 2048` in [2012, 2042]; the largest remainder among the other
  979 is 2006. Why the mastering tool reserves the extra sector is ?unknown?.

## 1.2 The group table (the actual load unit)

Immediately after the file table, at VA **0x80050010** (EXE file offset 0x40810), sit
**130 records of 12 bytes**, ending at 0x80050628.

| Offset | Type | Name | Meaning | Confidence |
| --- | --- | --- | --- | --- |
| +0x00 | u32 | `first_index` | First file-table index in the group | **confirmed** (0x80012B14, 0x800126BC) |
| +0x04 | u32 | `count` | Number of consecutive entries (range 1..136) | **confirmed** (0x800126C0) |
| +0x08 | u32 | `bytes` | CdRead length / buffer size for the whole group | **confirmed** (0x80012B1C, 0x800124C0) |

Measured: `first[i+1] == first[i] + count[i]` for all 129 consecutive pairs; the counts sum
to exactly 992; the record after the last one is 24 zero bytes.
`bytes == Σ ceil(size_i/2048)*2048` for **130/130** groups. **confirmed**

It is **not** `(sector[first+count] − sector[first]) * 2048`: eight groups contain interior
padding sectors and differ from the span form. Measured (`first`, difference in bytes):
0 → −8192, 141 → −2048, 153 → −2048, 446 → −2048, 606 → −4096, 716 → −2048, 819 → −2048,
825 → −2048. Implement the sum-of-rounded-sizes form.

This also fixes the entry count without any literal: the file table abuts the group table,
and `(0x80050010 − 0x8004E110)/8 = 992`.

> **Note.** The value 992 is *not* stored anywhere. An exhaustive scan finds zero aligned
> `u32 == 992`, zero `slti/sltiu/addiu/ori` with immediate 0x3E0, and the 76 bytes in front
> of the table are all zero. The count is *implied* by the adjacency above and is
> independently recoverable by walking the group chain, or by walking entries while the
> sector field strictly increases (it stops increasing exactly at index 992). **confirmed**

## 1.3 Identifying an entry's kind

There is **no magic-number table**. The leading `u32` of an entry means different things per
family, and the arithmetic-looking series 0x08 / 0x0C / 0x10 / 0x14 is a *length*, not a tag.

| Family | Leading u32 | What it is | Confidence |
| --- | --- | --- | --- |
| TEX (400) | 0x08 | byte length of the leading offset table = `4*(1+n_offsets)`; here 1 offset, and it equals the entry size exactly in 400/400 | **confirmed** |
| MDL (400) | 0x0C160029 (399) / 0x09160026 (1) | exporter build stamp; never compared by the EXE | *likely* |
| SFX (132) | 0x0C | 2 section offsets | **confirmed** |
| Music (28) | 0x10 (27) / 0x14 (1) | 3 / 4 section offsets | **confirmed** |
| TGA (5) | 0x00020000 | genuine Truevision header (idlength 0, cmap 0, type 2) | **confirmed** |
| Code overlays (15) | 0x5D..0x6A, or an absolute pointer | overlay id, or the first word of a pointer header | **confirmed** |
| Text overlays (12) | 7..18 | a per-minigame id (`159 − index`), followed by NUL-terminated strings | **confirmed** |

The robust discriminator for a container is **the validity of its own leading offset table**,
not the value of the first word: offsets strictly increasing and inside `[word0, size]`.
That test is valid for 560/992 entries and agrees with the shipped file list in **992/992**
cases.

The 5 TGAs satisfy `size == 18 + w*h*2 + 26` and end with `TRUEVISION-XFILE` (4 × 512×256,
1 × 512×240). **confirmed**

---

# 2. MDL file header (0x00 .. 0x57)

The header is **0x58 bytes**. It is a set of (count, self-relative pointer) pairs plus
standalone pointers. `T(x)` below means `x + i32@x` — the resolved target.

| Offset | Type | Name | Meaning | Confidence |
| --- | --- | --- | --- | --- |
| 0x00 | u32 | `stamp` | 0x0C160029 (399/400) or 0x09160026 (1/400, `models/arena/boss_oxide/chaselevel.mdl`). Neither byte pattern occurs anywhere in the EXE and no code site loads model+0x00. | *likely* (it is a stamp; what it encodes is ?unknown?) |
| 0x04 | i32 | `count_08` | Always 0 (400/400). Repeated as the first i32 at `T(0x08)`. | **confirmed** |
| 0x08 | i32 ptr | `ptr_pool_alias` | `T(0x08) == T(0x10)` in 400/400; the first 16 bytes there are zero in 400/400. **No EXE site resolves offset 0x08.** Dead as a pointer — but live as a **layout boundary**: across all 373 models with geometry, no mesh block, colour table or UV table lies past `T(0x08)`, every animation blob starts at or after it (223/223 animated models, gap ≥ 172 bytes), and the file ends exactly 4 bytes past the last blob (223/223). Empirically it is a load boundary too: a rebuilt model whose new geometry sat past it crashed the game, and moving the geometry inside it — blobs lifted off first, the field moved to the new end — was the change that made the same content boot. A writer must keep the invariant even though the reader is unidentified. | **confirmed** (invariant) / ?unknown? (reader) |
| 0x0C | i32 | `subfile_slots` | Range 0..14. `≥ i32@0x40` in 400/400, equal in 328 of the 373 models that have meshes. No EXE site found that reads it. Reading it as "allocated slots vs used slots" is a guess. | ?unknown? |
| 0x10 | i32 ptr | `ptr_pool` | The one field through which the game reaches this block. The game does **not** use the plain self-relative form here — see the note below. | **confirmed** |
| 0x14 | i32 | `count_18` | 0 in 327/400, 1 in 73/400. Repeated at `[T(0x18)]` in 400/400. Also a layout switch: `T(0x2C) == T(0x3C)` **iff** this is 0, 400/400. | **confirmed** |
| 0x18 | i32 ptr | `ptr_subobjects` | `[i32 count == i32@0x14]` then `count` self-relative i32 pointers, entry *i* at `T(0x18)+4+4*i`. All 73 entries in the corpus resolve inside the file, and each reaches the **placement list** that stands the level's set up — 2689 records naming an object and the transform to draw it under. See §8.5. | **confirmed** |
| 0x1C | i32 ptr | `ptr_objects` | Object table addressed by id namespace 0x5000; 12-byte stride where the code indexes it. Its leading records each name a **mesh header in the pool** — the level's own set, 1971 meshes over the 73 models that have one; the scene nodes follow them. See §8.3. Extent is not a multiple of 12 in general (mod 12 is 0 in 279 models, 4 in 70, 8 in 51). | **confirmed** (stride/base/object record) / ?unknown? (total layout) |
| 0x20 | i32 ptr | `ptr_colours` | Colour table: 4-byte `R G B 00` records. See §7.1. | **confirmed** |
| 0x24 | i32 ptr | `ptr_uvs` | UV table: 2-byte `(u, v)` records. See §7.2. | **confirmed** |
| 0x28 | i32 ptr | `ptr_vectors` | Shared 6-byte `(i16 x, y, z)` position pool; the fallback source for animation poses (§9.5). Degenerate (`T(0x28) == T(0x08)`, zero length) in 360/400. See §7.3. | **confirmed** |
| 0x2C | i32 ptr | `ptr_pool_hi` | `T(0x2C) == T(0x08) + 8` in 400/400. This is the address the game actually computes from field 0x10. **No EXE site resolves the file header's 0x2C** (the single 0x2C site, 0x80015700, is a *mesh* header). The span it opens, up to `T(0x3C)`, holds the object meshes of §8.3 — empty in the 327 models whose `i32@0x14` is 0. | **confirmed** |
| 0x30 | i32 | — | 0 in 400/400. No reader found. | **confirmed** (zero) / ?unknown? (purpose) |
| 0x34 | i32 | — | 0 in 400/400. No reader found. | **confirmed** (zero) / ?unknown? (purpose) |
| 0x38 | i32 | `count_3C` | 0 in 393/400. The 7 non-zero are `warp_room1..5/level.mdl` and `demo_hub1..2/level.mdl` (5, 6, 8, 7, 6, 3, 3). Repeated at `[T(0x3C)]` 400/400. The block stores **count+1** records. | **confirmed** |
| 0x3C | i32 ptr | `ptr_chunks` | `[i32 count]` then `count+1` records of 16 bytes. See §8.1. | **confirmed** |
| 0x40 | i32 | `count_44` | Number of 24-byte clip records. Range 0..14; 0 in 148 of the 373 models with meshes. | **confirmed** |
| 0x44 | i32 ptr | `ptr_subfiles` | Appended clip directory, 24-byte records: the **animation** table. See §8.2 and §9. | **confirmed** |
| 0x48 | i32 | `count_4C` | Number of i32 entries in the 0x4C array. Range 0..40. | **confirmed** |
| 0x4C | i32 ptr | `ptr_scene_roots` | **The scene root array of §9.11**, `i32@0x48` self-relative i32 pointers at stride 4, read by the scene spawner at 0x8001FF78 which indexes it by a root number. 688 entries over 187 models, all resolving inside the file. An earlier revision called this `ptr_ptr_array` and said the entries land in the 0x1C object table "at 0, 4 or 8 mod 12" — that was a residue taken with no containment test, and it is wrong: **0 of the 688 land inside the object records**, 556 land past the table entirely, and the 132 that touch it land exactly *on* `T(0x1C)`, the count word. See below. | **confirmed** |
| 0x50 | i32, **base-relative** | `resident_size` | `base + i32@0x50` is the end of the 0x44 directory in 399/400 and ≤ file size in 400/400. Equals the file size exactly for the 141 models with no sub-files; ≤ the first sub-file's start in 225/225. **Not** self-relative. No EXE site found that reads it. It is a multiple of 0x800 in **exactly 8 models** — the seven hub/warp rooms, where it equals `T(0x44)` and §8.6's block begins there, plus `chaselevel.mdl`, the one file where it is rounded up. **How the game treats it is now argued from two sides.** All 1037 animation blobs start past `base + i32@0x50` — and the game fetches every one of them **from disc, by explicit byte range, into a freshly allocated buffer** (§9.2's loader, all five `0x800133C8` callers read and each one a clip-blob fetcher). If the tail of the file stayed in RAM, that machinery would have nothing to do; its existence is evidence the tail is *not* kept. The warp-room probes say the same from the other side: pointers aimed past this boundary read garbage (a byte-identical UV copy there drew scrambled), while pointers aimed below it work — so the reading that fits is **`resident_size` = the part of the file kept in memory after init; everything past it is discard-and-refetch territory**. No loader code enforcing the boundary has been traced, so this stays behavioural: strong, convergent, and unproven at instruction level. | *likely* (stronger: convergent behavioural evidence, reader untraced) |
| 0x54 | i32 | `mesh_count` | Number of 0x34-byte mesh headers that follow at 0x58. 0 in 27/400 (legitimately). | **confirmed** |

> **The 0x10 anomaly.** The game does not apply the usual self-relative rule to file-header
> 0x10. It computes `model + [model+0x10] + 0x18`:
>
> ```
> 8001DEC4  lw    $v0, 0x10($v1)     ; v1 = model base
> 8001DECC  addu  $v0, $v0, $v1      ; base + value      (NOT base+0x10+value)
> 8001DED0  addiu $v0, $v0, 0x18
> 8001DED4  sw    $v0, 0x24($a0)     ; -> runtime instance +0x24
> ```
>
> That address equals `T(0x10) + 8`, which is exactly `T(0x2C)`. So 0x08, 0x10 and 0x2C are
> one address written three times with three different biases: over all 400 models
> `i32@0x10 − i32@0x08 == −8` and `i32@0x2C − i32@0x08 == −0x1C`, single-valued with no
> other value occurring. `T(0x08)` itself takes 258 distinct values in the range
> 0x58..0x2BB80, so the relation is not vacuous. **confirmed**

## 2.1 Header invariants (all measured over 400/400 models)

| Invariant | Result |
| --- | --- |
| `i32@0x04 == 0`, `i32@0x30 == 0`, `i32@0x34 == 0` | 400/400 each |
| `T(0x10) == T(0x08)` | 400/400 |
| `T(0x2C) == T(0x08) + 8` | 400/400 |
| `T(0x28) == T(0x08)` (degenerate vector table) | 360/400 |
| 16 zero bytes at `T(0x08)` | 400/400 |
| `[T(0x08)] == i32@0x04`, `[T(0x18)] == i32@0x14`, `[T(0x3C)] == i32@0x38` | 400/400 each |
| `T(0x1C) == T(0x3C) + 4 + 16*(i32@0x38 + 1)` | 400/400 |
| `T(0x18) == T(0x4C) + 4*i32@0x48` | 400/400 |
| `(T(0x2C) == T(0x3C))` iff `(i32@0x14 == 0)` | 400/400 |
| **`T(0x08) <= T(0x44)`** — the geometry span ends at or before the clip table | **400/400** |
| **`T(0x08) <= i32@0x50`** — and at or before the end of the resident image | **400/400** |

> **The last two are a writer's business, and breaking them cost a disc.** A writer that
> *appends* new geometry at the end of the file and moves `0x08` there breaks both at once. For
> most models that is merely wrong; for a warp room it is fatal, because with no clips to strip
> there is nothing between `T(0x44)` and EOF except §8.6's block, so the appended geometry lands
> **inside** it. `warp_room1` rebuilt that way did not load at all, and its §8.6 block came back
> altered. New geometry has to be *inserted* in front of `T(0x44)`, with `0x44` and `0x50` moved
> along by the inserted length — after which all 7 models carrying such a block get it back
> byte-identical.
>
> Note what this does **not** say. It is a corpus invariant, not a traced bound: no EXE site is
> known to read `0x50` (see its row below), so this is not evidence that the loader stops there.
> It says only that no shipped model puts geometry past either point, and that a build which
> does can fail to load.

### Seven models will not take a relocated colour or UV table

In **7 of the 400** — the five warp rooms and the two demo hubs — `i32@0x50` and `T(0x44)` are
the *same address*, and everything from there to the end of the file is §8.6's block.

> **An earlier revision of this section said these seven "cannot grow", which the measurements
> below refute.** Appending 2048 bytes to `warp_room1` loads perfectly well; so does moving
> `0x08`. What crashes is relocating the shared colour and UV tables, and *why* that crashes is
> **?unknown?** — no mechanism has been found, only the result. "No room to grow" was a story
> told over the evidence rather than read out of it, and it contradicted a probe already run.

| Model | `i32@0x50` = `T(0x44)` | §8.6 block to EOF |
| --- | --- | --- |
| `demo_hub1` | 86,016 | 12,932 |
| `demo_hub2` | 81,920 | 12,932 |
| `warp_room1` | 167,936 | 28,600 |
| `warp_room2` | 147,456 | 31,564 |
| `warp_room3` | 180,224 | 54,144 |
| `warp_room4` | 165,888 | 67,324 |
| `warp_room5` | 165,888 | 35,260 |

The finding came from a ladder of probes on `warp_room1`, each changing one thing more than the
last and each run in the emulator. It is worth keeping because it rules out most of what a
writer touches:

| Probe | What it changed | File size | Result |
| --- | --- | --- | --- |
| scene only | 224 bytes of placement fields, in place | 196,536 | **loads** |
| boundary | `0x08` alone, one byte of the file | 196,536 | **loads** |
| far boundary | `0x08` and `0x28` moved 126 KB to EOF; `0x20`/`0x24` untouched | 196,552 | **loads** |
| grow | 2048 zero bytes appended, not one pointer touched | 198,584 | **loads** |
| tables appended | colour/UV/pool copied to EOF, §8.6 block left in place | 220,140 | crashes |
| tables | the same, block re-appended after them, `0x44`/`0x50` follow | 221,112 | crashes |
| tables inside | the same, with `0x50` grown to cover the copies | 221,184 | crashes |
| one mesh | mesh 1 rebuilt from glTF | 228,640 | crashes |
| self-transplant | mesh 1 rebuilt from its own bytes, strips and pool identical | 243,640 | crashes |

That rules out a great deal. The geometry is not at fault — the self-transplant's strip list and
vertex pool were byte-identical to the original. The mesh headers are not — the table probes
never touched them. `0x08` is not: the far-boundary probe moved it 126 KB, and the vector-pool
pointer `0x28` with it, and loaded. Scene fields may be edited in place, §8.6's block may stay
or move, and `0x50` may stay or grow without changing the outcome. Nothing in the file even
points at the shared tables except the header's own `0x20`/`0x24`/`0x28`: a scan of every
self-relative i32 in `warp_room1` finds exactly **one** pointer to each.

| grow 24k | 24,576 zero bytes appended, not one pointer touched | 221,112 | **loads** |
| junk | the tables-appended probe's exact appended bytes, not one pointer touched | 220,140 | **loads** |

**The size hypothesis is dead, and so is the content one.** The grow-24k probe matches the
crashing table probes in size, is pure zero padding, and loads. No stored budget could enforce a
threshold anyway: none of the seven files' sizes, sector roundings or `0x50` values appears as a
u32 anywhere in the executable or the 16 overlays (the many hits on 196,608 = 0x30000 are the
instruction encoding `sll $zero, $v1, 0`, not data). The junk probe then killed the content
theory: it appends **exactly the bytes the tables-appended probe appended** — 18,064 of colour
table, 5,540 of UV, non-zero data sitting right behind §8.6's block — with the header
byte-identical to shipped, and it loads.

| uv move | `0x24`/`0x28` repointed to a byte-identical UV copy at EOF; `0x20` untouched | 202,076 | **loads — textures scrambled** |

**That closes the elimination, and it lands on `0x20` alone.** Junk and tables-appended are the
same size with the same appended content; the only difference is the four rewritten pointers.
Far-boundary moved `0x28` and `0x08` on their own — loads. Uv-move moved `0x24` (and `0x28`) on
their own — loads. The one pointer never moved in a loading probe is **`0x20`: repointing the
colour table crashes this file, by itself.**

**And the uv-move probe bought a second fact with its scrambled screen.** The relocated UV table
was byte-identical to the original; had the game fetched UVs by resolving `0x24` — its one known
read site, 0x80017F30 — the picture would have been pixel-for-pixel unchanged. It was garbled
instead: every textured surface sampled wrong texels while geometry, colours and the room itself
stayed intact. So **something consumes the UV table's position by another route, and it
demonstrably exists while remaining unfound**: base-relative scans (`lw` + `addu`) for `0x20`
and `0x24` find zero sites in the executable and all 16 overlays, and the self-relative idiom
finds only the draw-time sites already listed. This is the sharpest instance in this document of
a failed search not being evidence of absence — here the unfound reader shows up on screen.

The writer's rule that falls out is blunt: **in this file both shared tables are pinned.** Move
`0x20` and the room does not load; move `0x24` and every textured triangle samples garbage. Until
the resolving code is found, a writer must leave the colour and UV tables at their shipped
addresses and edit them in place. (Measured on `warp_room1`; the other six §8.6 carriers share
its layout, and extending the rule to them is inference, not measurement.)

**The assembly hunt for the resolver has a trail now.** `0x8001DE18` is a load-time context
builder that resolves header pointers *once* and caches the absolute addresses in a runtime
struct — `ctx+0x0C = T(0x18)`, the sub-object, its `+0x0C` and `+0x10` targets, the record
array, `T(model+0x1C)`, and `T(0x2C)` via the base-relative `0x10` read at `0x8001DEC4`. Its
last act writes **the model base into the file image itself, at `T(0x3C) + 0x0C`** — a runtime
pointer slot inside file data, the same pattern as the animation descriptor's `+0x14` resident
slot (§9.2). `0x8001D6B4`, called next, is §8.5's instance builder: it allocates 168-byte
runtime records through `0x80011654` and fills them from the file's placement records. Neither
touches `0x20`/`0x24`. Both init wrappers (`0x8001DFF8`, `0x8001E054`) finish by calling
`0x8001682C`, and that one is now read too: it is **§9.2's resident-blob scheduler**, walking
the clip descriptors at `T(0x44)`, allocating for each whose `+0x14` slot is zero and queueing a
read of `[start, end)` through `0x800133C8` — with its teardown twin at `0x80016928` freeing the
blobs and zeroing the slots. A clean instruction-level cross-confirmation of §9.2, and a no-op
in the seven §8.6 carriers, whose clip count at `0x40` is zero. So **the model-init chain has
been read end to end and none of it touches `0x20`/`0x24`** — the resolver, which the uv-move
probe proves exists, is not in the init path.

The per-frame packet path is read now too, and it is also innocent. The known `0x20` site at
`0x80017B88` traces back to `lw $v1, ($at 0x80056998); lw $v1, 0x0C($v1)` — the **current-owner
global**, dereferenced to the live model base, with `T(0x20)` resolved fresh per packet. A mesh
drawn through this path would follow a relocated colour table without complaint.

The level's own draw chain is mapped one link further. `warp.bin` references the owner global at
three sites, and `0x800BBE60` is its per-placement draw: it tests the placement flag's **bit 15**
(cross-confirming §8.5's drawn bit at instruction level, in the overlay as well as the engine),
fetches the model as `[struct+0x6C]+0x0C`, and calls **`0x8001D894`** with it. That function is
the per-instance transform setup: it takes the runtime instance's position from `+4/+8/+0x0C`,
subtracts the camera, multiplies through `0x800330CC`, loads the GTE rotation from the
instance's `+152` (or a global, on flag `0x2000000`), and — the line that ties the paths
together — **writes the owner into the `0x80056998` global per instance drawn**, from the ctx
the init wrappers stored at `0x8005AB50`. So level instances draw through the same owner-global
route as characters, and the consumer that pins the tables is in none of the code read so far.
The dispatch is read now, and the chain closes into a function this document already knew from
the inside. `0x8001D894` ends at `0x8001DAF4` — it is transform setup only: GTE rotation and
translation loads, and the ordering-table depth biased by the instance's `+104`, which
cross-confirms §8.5's `+0x9C` OT index at instruction level. The draw itself is back in
`warp.bin`, immediately after the call returns: `0x800BBEBC` is `jal 0x80019A60` with the **id**
from the instance's `+116` — the runtime copy of the record's `+0x88`, §8.5's copy confirmed —
and the flags or'd with `0x2000000`. So the level draws its meshes **by id**, through
`0x80019A60`, the same function whose interior §9.10 already quotes at `0x80019B34`/`0x80019B50`
fetching a clip's resident blob. The full chain is now: `warp.bin` bit-15 walk → `0x8001D894`
transform → `0x80019A60` draw-by-id → namespace dispatch (§2.3) → packet builders → live
`T(0x20)`.

`0x80019A60`'s namespace paths are read now too, and they hand back three decodes and one
correction to this section's own reasoning:

* The `0x2000` path bound-checks against `[model+0x54]` and takes its header at
  `model + 52·i + 36` — the **1-based** numbered-mesh bias of §2.3, now confirmed at
  instruction level rather than by corpus fit.
* The `0x5000` path resolves through `0x800159C4(id, model)`, the object resolver.
  Both paths then run `0x8001AF2C` (a bounds test on `T(mesh+0x10)`), `0x80019094`, and
  `0x800193A8(packet, vertices, T(mesh+0x14))` — the geometry builder of §12's table, its
  callers now traced.
* The `0x3000` namespace, unnamed until now, draws **no mesh at all**: it is a billboard. It
  perspective-transforms one point, builds its colour from the instance's `+118` nibble (or a
  neutral default), bound-checks against the owner global's `+0x46`, takes the **56-byte
  runtime texture descriptor** at `[owner+0x18] + 56·(id & 0xFFF)` — §6.2's descriptor array,
  reached from its other end — and hands it to `0x80029D28`.

And the correction: **the uv-move probe moved `0x28` as well as `0x24`, so its scrambled screen
does not separate the two.** The shipped layout ends the UV table exactly at `T(0x28)`, so a
fetch addressed from the table's end is as consistent with the scramble as one addressed from
`0x24` — and the far-boundary probe, which moved `0x28` alone, was only ever reported as
*loading*; nobody looked at its textures. Whether the scramble belongs to `0x24` or `0x28` is
**?unknown?** until that one observation is made.

**The packet builders are read now, and they settle the base-register question the site table
could not.** `0x800184F0` pops a packet from the global pool at `0x80056850` (an empty pool
returns null and the mesh is silently skipped — exhaustion is invisible, not fatal) and runs
`0x80017EE8` then `0x80017D90` into it **once**; the cache in `mesh+0x00` reuses those packets
every later frame. And `0x80017EE8`'s prologue resolves all five mesh-header pointers
mesh-relative, takes its swatch descriptors from `owner+0x10`, and at `0x80017F30` reads
**`model+0x24` with the model reached live as `[[0x80056998]+0x0C]`** — the reader §2.1's table
could not attribute is attributed: it is the model, resolved fresh at packet build.

**Which forces the explanation the probes had been circling.** Build-once packets, fed by a
live resolve of a byte-identical copy, must produce identical pixels — and the uv-move screen
was garbled. The one way both can be true is that **the copy was never in RAM: the game does
not load the whole file.** A load that stops at `T(0x44)` fits every probe run: colour or UV
pointers aimed past it read unloaded garbage — packets built from garbage colours crash, from
garbage UVs scramble; appended data nothing points at is invisible, which is why the grow and
junk probes load; and the far-boundary probe's relocated `0x08`/`0x28` targets are past the
boundary but nothing reads them in a clipless model, so it loads — with the **testable
prediction that its textures are intact**. The §9.2 blob loader fetching every animation blob
from past this boundary by explicit byte range is the same picture from the other side: past
`T(0x44)` is disc territory, fetched on request, not resident.

One probe keeps a residual: the relocated-block probe moved `0x44`, `0x50` and the §8.6 block
together, sector-aligned, and still crashed — under this hypothesis its tables were loaded and
consistent, so its crash wants the §8.6 *streamer* reading the block from an offset it did not
take from the moved header. Where that offset comes from is **?unknown?**, and that streamer is
§8.6's unfound reader itself. The hypothesis is behavioural — no loader code computing a
`T(0x44)`-bounded read has been traced — and it is marked as such.

The trace toward that loader reached the IO layer and stopped one link short. `0x80013034` —
the function the model-init wrappers hand their callback to — is an **async request enqueuer**:
it pops a node from the free list at `[0x80050F3C]`, fills seven fields (handle, three
arguments, and three more from the caller's stack, one of them the callback), and links it into
the queue at `0x80050628+0x0C/+0x10`; `0x8001316C` is the matching dequeuer. The read length is
not in this code — it travels inside the request for the **queue worker** to use. One link
further is read: `0x80013290` is the **synchronous load wrapper** — enqueue with a completion
flag at `0x80069E7C` and the destination buffer from the entry struct's `+4`, then spin on
`0x80012FFC`, servicing the drive through `0x8001231C` and waiting a frame through `0x8002BAE8`
each lap, with `0x80011544` as the post-load hook. The sector arithmetic is read: `0x800121F8`, the request's allocation stage, takes the **load
length from `[request+8]`**, rounds it up to whole 2048-byte sectors, and allocates — through
`0x80011654` on one path, and on the other through a fit-check that runs the scavenger and the
compactor before falling back to `0x80011748`; a request arriving with `[+24]` set brings its
own buffer, which is how the blob fetches pass their exact byte ranges. So the length is caller
data, not loader arithmetic. The synchronous wrapper `0x80013290` that forwards
`[entry_struct+4]` turns out to have **no caller and no address-taken site anywhere in the
executable or the 16 overlays** — two scans, jal targets and literal/`lui`-pair address builds,
both empty. Linked but apparently unreached; by this document's own rule that is recorded as "no
evidence found", not "unused". The live route is the **asynchronous** one: the model-init
wrapper calls `0x80013034` directly with a callback and `a2 = [owner+0x24]`, so the model's load
length is whatever fills `owner+0x24` — and **who fills it, with the file's full size or its
resident size, is the single untraced hop left.**

The table readers themselves are located and the derivation rule is instruction-level. The
file table lives at `0x8004E110` in the executable image — `(sector, byte_size)` per entry,
eight bytes each, exactly the rows §1.1 describes — and **eleven sites in the band
`0x80012600..0x80012D00` address it** through `addiu rX, $v0, -7920`. The group preloader among
them computes each entry's load length as **`(sector[i+1] − sector[i]) << 11`** — the
whole-sector span to the next entry, not the byte size — and hands the byte size at `row+4` to
a registrar (`0x80011498`) separately. Two consequences follow and are worth stating: lengths
derived from *sector differences* mean the loader's spans include each entry's inter-entry
padding, and a repacker that moves entries closer together (this project's packs tight) changes
those spans — harmless if nothing assumes slack, and the shipped gaps do carry stray non-zero
bytes (58 to 925 per gap behind the seven carriers, measured), so nothing about the gaps is
load-bearing on the shipped disc. Whether the model's own `owner+0x24` gets the sector-span or
the `row+4` byte size is the remaining read.

Three more links are instruction-level now. `0x80013650` is the entry-load front door — it
takes a handle and a callback and enqueues with **length `[handle+4]`**. `0x80011498` is a
**shrink-and-free primitive**: it trims a heap block to a given byte size and frees the
remainder as a new block (word-count rounded, only when ≥ 16 words are left over). And it has
exactly **four callers**: three are the table-driven group loaders trimming each entry's
sector-span allocation down to its `row+4` byte size — pruning the inter-entry padding — and
the fourth sits inside §9.2's blob loader at `0x80015FFC`, trimming a blob's buffer. **No
caller shrinks a model to its resident size.** The tidy story — load the sector span, truncate
to `i32@0x50`, let the compactor reuse the tail — has no instruction-level support: searched
and not found, which is not evidence of absence, but the honest state is that the *trigger* for
the tail becoming garbage is still untraced. What stands without it: the probes' behaviour, the
compactor's existence, and the blob machinery's refetch-from-disc — the tail is demonstrably
not usable memory, by a mechanism not yet read.

Two later reads sharpened the picture and then sharpened the problem. The level load is
**two-stage**: `0x80013650([owner+4], callback)` first — length `[handle+4]`, and the handle's
shape (seek from `[+0]`, length from `[+4]`) is the file table row's — then the callback
`0x8001DF74` unlocks the block through `0x80011544`, runs three inits on `owner+0x10`, and
enqueues the **second** load with `a2 = [owner+0x24]`, so that field is most plausibly the
*companion file's* length, not the model's. And the group preloader loads a **contiguous
sector run and splits it in place**: one read spanning the group, then per entry
`0x80011828(base, (sector[i] − sector[first]) << 11)` and the shrink primitive at the row's
byte size.

**Which leaves a paradox, stated as one.** If the model's load length is the table row's byte
size, the whole file — relocated tables included — is in RAM, the packet builders resolve
live, and the uv-move scramble has no mechanism again. Every explanation eliminated so far is
recorded above, and two more died on inspection: **this project's own tables are coherent** —
`build.py` patches the entry rows and the group `bytes` fields consistently, and documents that
its tight packing *removes* the shipped disc's span-versus-bytes disagreement rather than adding
one — and **the fallback allocator is not a separate region**: `0x80011654` and `0x80011748`
are near-twins on the same heap, differing only in that the first waits on the lock first. So
the eliminated list now reads: partial read by `0x50` or `T(0x44)` (probe-refuted or
untraced), stale caches (build-once but post-load), non-live pointer resolves (traced live),
incoherent rebuilt tables (audited), a clobbered temp region (allocators identical). The
scramble is real, reproducible, and **unexplained at the end of static reading** — the next
instrument is dynamic: a RAM watch on the loaded model while the emulator runs the probe, which
is an observation this project's tooling cannot make on its own.

Two late negatives and one filled hole close this account for now. **No overlay touches the
file table**: the `0x8004E110` scans, immediate pairs and literals both, come back empty across
all 16 — a §8.6 streamer, if it exists, goes through the executable's services rather than its
own table walk. And the probe matrix had a hole worth naming: **every probe that moved the §8.6
block also moved `0x20`/`0x24`**, so "the block cannot move" was never established
independently — it was an artifact of a theory since refuted. The block-shift probe fills the
hole: the block moved 2048 bytes with byte-identical content, `0x44` and `0x50` bumped to
follow, and exactly **two bytes** of the file below the cut differ — the second byte of each of
those words. **It loads.** The block is movable — shifted 2048 bytes with `0x44`/`0x50`
following, the room runs — and the fixed-offset-streamer story is dead. Note the limit of the
result: it does not prove a reader follows the header, only that nothing objects to the move
(no reader at all would also load). What it proves for a writer is what matters: **the §8.6
block may be relocated wholesale**, so the space in front of it can be opened.

**The model-side half of the reader is found, and it is data, verified 38/38.** The word at
`T(0x1C) + 12·slot + 12` — the one right after an object's 12-byte record — is that object's
**§8.6 sub-block index**. It is non-zero on exactly the door objects and zero everywhere else,
and across all seven carriers the non-zero values are exactly each file's sub-block inventory:
`warp_room1` slots 17, 18, 19, 20, 25 carry 1..5, `warp_room4`'s eight doors carry 1..8, and so
on — 38 of 38. The consuming chain is read at instruction level to the same depth: `warp.bin`'s
instance classifier at `0x800B5200` switches on the instance's kind byte (`+0x64`), and for
kind 3 it **clears the instance's bit-15 drawn flag itself** — the preview screen is a
placement hidden from the normal draw — then calls `0x80015984(id, model)`, the object-slot
word fetch, and stores the returned index in its 40-byte per-door struct at `+36`. So the
previews are per-door §8.6 sub-blocks, selected by an index the file carries beside the object
records. What remains unread is only the **locator** — the code that turns index *n* into the
sub-block's address, which the block-shift probe shows is not a walk from the live `T(0x44)`.

The hunt for the locator has its haystack mapped: the per-door array at `0x800BCCF4` (40-byte
records) is referenced from **27 sites** in `warp.bin`, and the index at `+36` is not read by
either of the overlay's two literal `lw +36` instructions — those belong to a different struct,
a cursor that bounces between bounds by a step and drives `0x8001E21C`/`0x8001E41C`, the shape
of an animation pump. The locator therefore computes its `+36` access from the array base, and
the 27 reference sites (clusters at `0x800B65xx`, `0x800B6Axx`, `0x800B7Axx`) are where it will
be found or excluded. The first cluster is read and excluded: `0x800B64E0..0x800B66xx` is door
*interaction* — the player-at-door check against the entry at `[door+20]`, key tests, and
hand-offs into `gameeng.bin` at `0x80095A04`/`0x80095E38`. That hand-off is itself the lead:
the door flow delegates into the engine overlay, which also carries eight owner-global
references — the preview machinery plausibly lives in `gameeng.bin`, and its eight sites are
the next haystack after `warp.bin`'s remaining clusters. The first delegate is read and
identified: `0x80095A04(n)` indexes a **156-byte per-slot array at `0x800A0E78`**, frees the
slot's instance list through `0x8001D334([slot+108])` and clears bit 15 of its `+12` flags — a
viewport shutdown, the door flow closing screens on entry. Not the locator; one more named
piece of the engine's slot machinery. The `0x800B6Axx` cluster is read and excluded too — door
state and lock flow (a key-type-5 test against the entry at `[door+20]`, the `[door+32]` flag,
HUD branches), with `+36` unread throughout. The remaining unread candidates are the
`0x800B7Axx` cluster and `gameeng.bin`'s seven other owner-global sites.

Where that locator's anchor lives is still ?unknown?, and the search log covers: the model
header (both movers lost the previews with `0x44`/`0x50` moved consistently), the file table
(no overlay references it), literal block offsets (none in `warp.bin`, `gameeng.bin` or
`menu.bin`), a per-room sector table (`[82, 72, 88, 81, 81]` as u32 or u16: absent; the one
`82` in `warp.bin` sits in a consecutive-integer id run), and `warp.bin`'s seven `+36`-shaped
struct writes (zeros, constants 12 and 4096, and one resolver result — none a load length).
One new address fell out of that last scan and is read now: `0x800B52A8` calls
**`0x80015984(id, model)`** per door and stores the result. The function is an object-slot
word fetch — `slot = (id − 1) & 0xFFF`, record at `T(model+0x1C) + 12·slot`, return the word
at `record + 12` — and its neighbour `0x800159C4` is the familiar resolver, same slot
arithmetic and then `0x8001DD20(record + 4)`. Two accessors to §8.3's object table, both
confirming the 12-byte stride and the 1-based slot at instruction level. What `warp.bin` does
with the fetched word is the open end of this lead: the per-door data trail now points at the
word *after* each door's object record.

That closes the writer's question for these rooms into a concrete safe recipe, every step of
which is probe-backed: placement and scene fields may be edited in place (scene-only probe); an
existing mesh may be rebuilt in place when its blocks fit the original footprint, which the
run-length texture encoding makes routine (the no-op rebuild's blocks now fit); the block may
be pushed outward to open room for new geometry (this probe); and new triangles must take their
colours and UVs **from the existing shared tables** — the dedup already reuses entries — so
that `0x20`/`0x24` never move, which is the one operation still forbidden. Growing the shared
tables themselves stays blocked until the pinning mechanism is understood.

The same page of code answered a question the residency hypothesis had left open. `0x800131B0`
is the allocator's pre-check, and on a failed fit it calls `0x80017640` — a scavenger that
frees packet caches — and then **`0x80011D28`, a heap compactor**, before retrying. A
compacting heap is the missing mechanism: a discarded file tail is not merely stale, it is
**moved over** as live allocations slide — which is why every pointer aimed past the resident
boundary read garbage immediately rather than old bytes. Behavioural attribution of the
compactor's role, instruction-level attribution of its existence.
| `i32@0x0C >= i32@0x40` | 400/400 |
| `base + i32@0x50 == T(0x44) + 24*i32@0x40` | 399/400 (`chaselevel.mdl` is +1740, rounded up to 0x26000) |
| `T(0x20) <= T(0x24)` | 400/400 — but **strict** `<` only 378/400 |

Everything past `T(0x44)` is animation blobs and **zero padding to the next 0x800**. A
byte-coverage walk over the archive leaves 1262 unclaimed spans in that region and **all 1262
are entirely zero**; 1037 end exactly on a 0x800 boundary and the other 225 end at the file
itself. That the bytes are zero is measured; that a rebuilt model may therefore pad freely is
an inference from it, and the alignment half of that rests on §9's own reading of a blob's
range rather than on anything read here.

Block order, identical in all 373 models that have meshes:

```
header(0x58) | mesh headers | mesh data ... | colours T20 | UVs T24 | [vectors T28] |
T08 = T10 | T2C | {T3C, T1C} | T4C | T18 | T44 | end(0x50) | appended sub-file payloads
```

The complete minimal model is `fonts/font1.mdl`, 120 bytes: header 0x58, zero meshes,
`T(0x20) == T(0x24) == 0x58` (both tables empty), 8 zero bytes, the 0x3C list
(`[0]` + 1 record = 20 bytes) ending at 0x74 `== T(0x1C) == T(0x4C) == T(0x18)`, the 0x18
count word, and `T(0x44) == base + i32@0x50 == 0x78 == 120`.

One model carries its geometry twice. `cutscene/gamelogo_text.mdl` has two mesh headers, both
pointing at the same data block, and a **byte-for-byte duplicate of that block** sitting
unreferenced between the headers and it — 7520 of 7520 bytes identical, same 86 strips, same
494 triangles, same bounds. A reader that walks headers never sees it; a byte-coverage walk
does. It is the only model in the archive with a span nothing points at.

Twenty-seven models declare **zero** meshes and are valid: `fonts/font1..22.mdl` (120 bytes
each), `models/arena/test/objects.mdl`, `models/arena/medieval_ring/arena.mdl`,
`models/arena/tank_jungle/arena.mdl`, `models/arena/tank_jungle/crystalarena.mdl`,
`models/arena/crate_jungle/arena.mdl`.

---

# 3. Mesh header (0x34 bytes)

Mesh header *n* is at `0x58 + 0x34*n`. The game addresses them by a **1-based id** with the
equivalent formula `model + 52*id + 0x24`:

```
; 0x80019D0C — id-addressed mesh lookup, with a bounds check against the mesh count
80019D0C  lw    $v0, 0x54($s1)      ; s1 = model base; mesh count
80019D10  andi  $v1, $s2, 0xfff     ; id = low 12 bits of the object id
80019D14  slt   $v0, $v0, $v1
80019D18  bnez  $v0, 0x80019ef8     ; id > count -> bail
80019D1C  sll   $v0, $v1, 1
80019D20  addu  $v0, $v0, $v1
80019D24  sll   $v0, $v0, 2
80019D28  addu  $v0, $v0, $v1
80019D2C  sll   $v0, $v0, 2         ; v0 = 52 * id
80019D30  addiu $v0, $v0, 0x24
80019D38  addu  $s0, $s1, $v0       ; s0 = model + 52*id + 0x24  ==  0x58 + 0x34*(id-1)
```

| Offset | Type | Name | Meaning | Confidence |
| --- | --- | --- | --- | --- |
| 0x00 | u32 | `runtime_slot` | **Not padding.** Zero in the file (5990/5990), and what fills it is now read: `0x80019094`, on a mesh's **first draw**, allocates its per-mesh **packet cache** through `0x80018694` and stores the pointer here; every later draw finds it non-zero and reuses the cache. The cache is double-buffered by frame parity (`+ 40·(frame & 1)`), a type word at its `+0x54` picks the packet builder (1 → `0x800180BC`, else `0x800184F0`), and spent packets recycle through a free list at its `+0x0C`/`+0x10`. So GPU packets are built once per mesh and reused across frames — which is why draw-time header reads alone do not describe everything the game remembers about a mesh. | **confirmed** |
| 0x04 | u32 | — | Zero in 5990/5990. No reader found. | **confirmed** (zero) / ?unknown? (purpose) |
| 0x08 | i16 | `triangle_count` | Number of triangles. Equals the sum of the strip list's high bytes in **5990/5990**. No render pass reads it — they derive the count from the strip list — but 0x8001C3F8 does, as a **dispatch gate** that never opens on shipped data. See below. | **confirmed** |
| 0x0A | i16 | `format` | Values: 4 (3112), 6 (2620), 7 (255), 5 (2), 2 (1). **No reader found, over every code blob on the disc**: 132 halfword loads at this offset, each base register traced, and not one arrives through the `addiu 0x58 / 0x34 / 0x24` a mesh address is built with. A **second, independent route** was searched since: all eight call sites of the id resolver 0x80015A18, following the mesh it returns, and they read only 0x08, 0x10, 0x14 and 0x28. See §14 for the searches and their limits. | **confirmed** (the trace) / ?unknown? (meaning) |
| 0x0C | i16 | — | Non-zero in 344/5990, in small families: 100 (138), 10 (68), 5 (51), 4 (29), 101–103 (22), 20–33 (16), others (20). **No reader found in the executable or in any of the 14 mode overlays.** The 138 meshes carrying 100 are exactly the backdrop domes each cutscene raises without a node (§9.11.6), and of the 342 that a scene could have claimed, **none is owned by a scene node** — correlation over the corpus, not a decoded meaning. | ?unknown? |
| 0x0E | i16 | — | Non-zero in 162/5990, and only where 0x0C is. Where 0x0C is 100 it is 0 (72) or 1 (65), which is the order the two domes stack — opaque sky, then the additive tint over it. Same status: correlation, no reader found. | ?unknown? |
| 0x10 | i32 ptr | `ptr_bounds` | 0x14-byte bounds block; the vertex pool starts at `T(0x10) + 0x14`. | **confirmed** |
| 0x14 | i32 ptr | `ptr_strips` | Strip list (§5). | **confirmed** |
| 0x18 | i32 ptr | `ptr_uv_index` | One u16 per triangle (§6.1). Also the end of the vertex block. | **confirmed** |
| 0x1C | i32 ptr | `ptr_texture_runs` | Run-length texture list (§6.2). | **confirmed** |
| 0x20 | i32 ptr | `ptr_colour_index` | One u16 per triangle (§6.3). | **confirmed** |
| 0x24 | i32 ptr | `ptr_end` | End of the mesh's own data. The word **at** that address is zero in **7961/7961** meshes, object meshes included — a byte-coverage walk over the archive leaves 6983 four-byte holes and every one is this word. That is a measurement and nothing more: no site has been found that reads it, and `mdlwrite` has never emitted one deliberately on discs that boot, so whether a writer *must* is untested. | **confirmed** (the pointer, and that the word is zero) / ?unknown? (whether anything needs it) |
| 0x28 | i32 ptr | `ptr_normals` | **Non-zero in exactly 300/5990**, and 283 of those meshes are under `models/arena/`. When set, `T(0x28) == vertex_pool + 8*vertex_count` in 300/300 and a second 8-byte-per-vertex array follows. **One reader traced**, 0x800B6EA0 in `dash.bin`, where a zero here skips the routine outright — see below. | **confirmed** (structure, and that it gates that routine) / *likely* (that they are normals) |
| 0x2C | i32 ptr | `ptr_attachments` | Non-zero in 777/5990. Live, id-addressed block (§8.4). | **confirmed** |
| 0x30 | u32 | — | Zero in 5990/5990. No reader found. | **confirmed** (zero) / ?unknown? (purpose) |

Mesh iteration, and the proof that +0x00 is a live pointer slot:

```
; 0x80016F84
80016F84  addiu $a0, $v0, 0x58     ; v0 = model base; first mesh header
80016F88  lw    $v1, 0x54($v0)     ; mesh count
80016FA0  lw    $v0, ($a0)         ; <-- mesh header +0x00, dereferenced
80016FA8  beqz  $v0, 0x80016fb4
80016FB0  sh    $a2, 0x56($v0)     ; store 1 at [that pointer + 0x56]
80016FB8  bne   $v1, $a1, 0x80016fa0
80016FBC  addiu $a0, $a0, 0x34     ; (delay slot) stride 0x34
```

The identical loop appears again at 0x8001702C. A writer must leave +0x00 zero on disk, not
treat it as reserved space.

### What reads `+0x08`: a two-triangle special case that never fires

Meshes are reached by id as well as by iteration. 0x80015A18 is the resolver: for an id whose
`& 0x7000` is 0x2000 it computes `$a1 + 0x34*(id & 0xFFF) + 0x24`, which for the 1-based ids of
§8.2 is exactly `model+0x58` at id 1 — the first mesh header. Eight sites across the executable
and the overlays call it, and one of them reads the triangle count:

```
8001C3E8  lw    $a1, 0xc($v0)    ; the object's model base
8001C3EC  jal   0x80015a18       ;   id at +0x74 -> the mesh header
8001C3F8  lhu   $a0, 8($v1)      ; the TRIANGLE COUNT
8001C400  bne   $a0, $v0, out    ;   leave unless it is exactly 2
8001C408  lw    $v0, 0x14($v1)   ; the strip list
8001C414  lbu   $v0, 0x15($v0)   ;   its byte at +0x15
8001C41C  bne   $v0, $a0, out    ;   leave unless that is 2 as well
8001C428  sw    $v0, 0x54($s0)   ; install 0x8001AA48 as the object's draw (§9.11.8)
8001C434  sw    $v0, 0x58($s0)   ;   and 0x8001C378 alongside it
```

So `+0x08` is not inert: it selects a specialised draw for a two-triangle mesh. **The gate never
opens on the shipped corpus.** 2267 of the 5990 meshes do carry a triangle count of exactly 2,
but the byte at `T(0x14)+0x15` is 0 in 2197 of them, 255 in 65 and 171 in 5 — **never 2**, so
not one mesh in the archive passes both tests and 0x8001AA48 is never installed by this route.

That is measured on this disc only. A path that no shipped asset exercises is still a path, and
a writer that changed a mesh's triangle count or its strip list could open it.

### What reads `+0x28`: a per-triangle query, gated on the pointer

`dash.bin` takes an object's resource id, resolves it to a mesh, and **abandons the whole
routine when `mesh+0x28` is zero**:

```
800B6E48  lhu  $a0, 0x74($s4)     ; the object's resource id (§9.11.8)
800B6E4C  jal  0x80015a18         ;   -> the mesh header
800B6E64  lw   $v0, 0x28($a0)     ; the normals pointer
800B6E6C  beqz $v0, 0x800b711c    ;   zero -> skip everything below
800B6E8C  lw   $v0, 0x14($a0)     ; the strip list
800B6E90  lw   $v1, 0x10($a0)     ;   and the bounds block
800B6EAC  addu $s2, $a0, $v0      ; s2 = T(0x28)
800B6EB0  lbu  $s3, 1($s6)        ; strip 0's triangle count, §5's high byte
800B6EB8  beq  $s3, 0xff, ...     ;   0xFF ends the list
```

What follows is a **rejection test against a point**. `$s1` is the vertex pool and `$s0` is
`$s1 + 0x10`, so the three reads at `($s1)`, `-8($s0)`, `($s0)` are one component of three
vertices at the 8-byte stride, and `-0xc($s0)`, `-4($s0)`, `4($s0)` are the component at +0x04.
Each is compared against a coordinate ± `$s5`, and a triangle with all three outside is
skipped; a survivor goes to 0x800B66C4 with the point and the vertex triple. Two components
and a radius is a horizontal query — finding which triangle a point is over.

Two other candidates were checked and **rejected**, which is why the count above says one:

* 0x80019C08 in the executable resolves `+0x28` self-relatively, but `$s1` there is
  `move $s1, $a1` at 0x80019A70 and the same register resolves `+0x40` and `+0x44` — fields a
  0x34-byte mesh header does not have. It is the **model** base, so this is `model+0x28`.
* 0x800B4F28 in `tank.bin` builds its base as `0x800DADB0 + 164*index`, a table of its own, and
  the `addu` that matched the pointer-resolution shape is an address computation.

The search was for a base register that resolves two or more self-relative pointers, so a
routine that took a mesh header and read **only** `+0x28` would not appear in it. Within that
shape, `dash.bin` is the only consumer in the executable, `gameeng.bin` and all 14 mode
overlays.

---

# 4. Bounds block and vertex pool

`T(0x10)` points at a **0x14-byte bounds block**, immediately followed by the vertex pool.
The game proves both facts in one place:

```
; 0x80019D54 — per-mesh culling then vertex transform
80019D54  lw    $v0, 0x10($s0)     ; s0 = mesh header
80019D5C  addiu $v0, $v0, 0x10
80019D60  addu  $s1, $s0, $v0      ; s1 = T(0x10) = the bounds block
80019D74  jal   0x8001af2c         ; visibility test, a0 = the bounds block
80019D8C  addiu $s1, $s1, 0x14     ; <-- +0x14: s1 is now the VERTEX POOL
80019D98  lw    $a2, 0x14($s0)
80019DA0  addiu $a2, $a2, 0x14
80019DA8  addu  $a2, $s0, $a2      ; a2 = the strip list
80019DA4  jal   0x800193a8         ; transform(a1 = vertices, a2 = strips)
```

## 4.1 Bounds block — ten i16

| Offset | Type | Name | Confidence |
| --- | --- | --- | --- |
| +0x00 | i16 | `min_x` | **confirmed** |
| +0x02 | i16 | `max_y` | **confirmed** |
| +0x04 | i16 | `min_z` | **confirmed** |
| +0x06 | i16 | `max_x` | **confirmed** |
| +0x08 | i16 | `min_y` | **confirmed** |
| +0x0A | i16 | `max_z` | **confirmed** |
| +0x0C | i16 | `centre_x` | **confirmed** |
| +0x0E | i16 | `centre_y` | **confirmed** |
| +0x10 | i16 | `centre_z` | **confirmed** |
| +0x12 | i16 | `radius` | *likely* |

The interleaved min/max order is not a guess. Measured over the 5989 meshes with vertices,
reading the pool at stride 8:

* `(f0, f4, f2)` == the true per-axis minimum and `(f3, f1, f5)` == the true maximum in
  **5986/5989**. The three exceptions are `models/adventure_items_void.mdl` mesh 3,
  `models/arena/boss_oxide/chaselevel.mdl` mesh 30, `models/arena/boss_oxide/arena.mdl`
  mesh 30.
* The alternative straight order `(minX,minY,minZ,maxX,maxY,maxZ)` matches only **261/5989**.
* `centre` is within ±1 of the box centre on every axis of every mesh (17,967/17,967
  components: exactly `(lo+hi)/2` in 9874, ±0.5 in 6290, ±1 in 1803).
* `radius >= max distance from centre to any vertex` (−1 tolerance) in 5987/5989; it is
  often much larger, so it is a conservative sphere radius, not a tight one.

Positions are `value / 256.0` in model units.

## 4.2 Vertex record — 8 bytes

| Offset | Type | Name | Meaning | Confidence |
| --- | --- | --- | --- | --- |
| +0x00 | i16 | `x` | position, ÷256 | **confirmed** |
| +0x02 | i16 | `y` | position, ÷256 (screen-space Y grows **down**) | **confirmed** |
| +0x04 | i16 | `z` | position, ÷256 | **confirmed** |
| +0x06 | u16 | `flags` | see below | **confirmed** (bits 0,1) |

The stride is proved by the transform loop, which loads two words and advances by 8:

```
; 0x800193A8 — the geometry pass
800193C4  addiu $t3, $a2, 1        ; a2 = strip list; t3 = its HIGH byte
800193D4  lw    $s4, ($a1)         ; a1 = vertex pool; word0 = (x, y)
800193D8  lw    $s3, 4($a1)        ;                   word1 = (z, flags)
800193DC  lbu   $v1, ($t3)         ; strip triangle count
800193E4  beq   $v1, $v0(0xff), 0x80019648   ; 0xFF ends the list
800193EC  mtc2  $s4, $zero, 0      ; -> GTE VXY0
800193F0  mtc2  $s3, $at,   0      ; -> GTE VZ0
800193F4  addiu $a1, $a1, 8        ; <-- VERTEX STRIDE = 8
800193FC  gte:RTPS
```

**Vertex flag bits.** `s2` below holds `word1`, so the flags sit in bits 16..31:

```
80019490  lui   $v0, 2
80019494  and   $v0, $s2, $v0      ; flags bit 1  (0x0002)
80019498  bnez  $v0, 0x800194e4    ; set -> draw with NO backface test
800194A0  gte:NCLIP
800194A8  swc2  $24, ($v0)         ; MAC0 -> [sp+4]
800194AC  lui   $v0, 1
800194B0  and   $s2, $s2, $v0      ; flags bit 0  (0x0001)
800194B4  beqz  $s2, 0x800194d4
800194BC  lw    $v0, 4($sp)
800194C4  blez  $v0, 0x800194e4    ; bit0 SET  : draw when nclip <= 0
800194CC  j     0x80019430         ;            otherwise skip
800194D4  lw    $v0, 4($sp)
800194DC  blez  $v0, 0x80019538    ; bit0 CLEAR: draw when nclip >  0
800194E4  ... build the primitive ...
```

| Bit | Mask | Meaning | Confidence |
| --- | --- | --- | --- |
| 0 | 0x0001 | Facing parity: flips the sign convention of the GTE `NCLIP` backface test for the triangle that **ends** on this vertex. This is what alternates within a strip. | **confirmed** |
| 1 | 0x0002 | Skip the backface test entirely — a double-sided triangle. | **confirmed** |
| 2 | 0x0004 | Set on 130 vertices. No reader found in the render path. | ?unknown? |
| 8 | 0x0100 | Set on 28 vertices. No reader found in the render path. | ?unknown? |

Observed values over 525,341 vertices:
`0 → 340,937; 1 → 168,623; 2 → 8,017; 3 → 7,606; 4 → 75; 5 → 55; 256 → 14; 257 → 14`.
Only bits 0, 1, 2 and 8 ever occur.

## 4.3 Vertex normals (`ptr_normals`, mesh +0x28)

This is the single most important correction to any existing reader.

Let `V` = the vertex count derived from the strip list (§5). Measured over all 5989 meshes
with a strip list:

```
(vertex-block span / V,  is mesh+0x28 non-zero)  ->  [((8, False), 5689), ((16, True), 300)]
mesh+0x28 target == vertex_pool + 8*V            ->  300/300, zero mismatches
(ptr_uv_index - vertex_pool) == stride * V       ->  5990/5990
```

So the block between the bounds and `ptr_uv_index` is **two consecutive arrays, not an
interleaved one**: `V` position records of 8 bytes, then — only when `mesh+0x28` is set — `V`
more 8-byte records that `T(0x28)` points at.

Contents of the second array, over all 7949 records in the 300 meshes:

| Property | Result |
| --- | --- |
| 4th i16 | 0 in 7949/7949 |
| zero vectors | 2586 (32.5 %) |
| `round(|v|)` of the other 5363 | 4096 (3147), 4095 (2214), 4094 (2) — **three distinct values, nothing else** |

Unit length in GTE 1/4096 fixed point with a zero fourth component: these are per-vertex
normals. Marked *likely* rather than **confirmed** because:

* No EXE site dereferences **mesh**+0x28. The only self-relative resolution at offset 0x28
  in the whole image is 0x80019C08, and its base register is the **model** base — that is
  the file-header vector pool (§7.3), a different field.
* The game does no GTE hardware lighting at all: an exhaustive scan for
  NCDS/NCDT/NCCS/NCS/NCT/NCCT/CDP/CC finds three hits in the entire image, two INTPL and one
  CDP at 0x8004C798 inside libgpu.

The *data* reading stands on the measurement regardless; the *runtime use* is ?unknown?.

---

# 5. Strip list

`T(0x14)` points at an array of u16. The **high** byte of each u16 is the strip's triangle
count; the **low** byte is a flag byte. A high byte of **0xFF** terminates the list. A strip
of `n` triangles consumes `n + 2` consecutive vertices from the pool, and strips consume the
pool in order with no gaps.

```
; 0x800193A8, the outer/inner strip walk
800193C4  addiu $t3, $a2, 1        ; t3 = &strip[0] + 1  -> the HIGH byte
800193DC  lbu   $v1, ($t3)         ; strip triangle count
800193E0  addiu $v0, $zero, 0xff
800193E4  beq   $v1, $v0, 0x80019648   ; 0xFF -> end of list
800193F8  move  $t0, $v1           ; t0 = triangle countdown
80019414  lbu   $v0, ($a2)         ; the LOW byte  = flags
80019424  andi  $v0, $v0, 1        ; bit 0
80019428  beqz  $v0, 0x80019634    ;   clear -> textured branch (POLY_GT3)
...                                ;   set   -> untextured branch (POLY_G3)
80019634  bgtz  $v0, 0x80019540    ; next triangle in this strip
8001963C  addiu $t3, $t3, 2        ; next strip
80019644  addiu $a2, $a2, 2
```

The same `+1` bias appears independently at 0x80017DB8 (`addiu $t3,$a0,1`), 0x80017F34
(`addiu $t5,$t7,1`) and 0x8001C1F0 (`addiu $t3,$v0,0x15` = strip list + 1).

**Corpus check:** the sum of the high bytes equals `i16@0x08` for **5990/5990** meshes, and
`Σ(count+2)` equals the vertex count implied by the pool span for **5990/5990**. There is no
ambiguity left in strip segmentation — a reader never needs to guess boundaries.

## 5.1 Strip flag byte (the low byte)

Over 81,045 strips it takes exactly **four** values:

| Value | Count |
| --- | --- |
| 0 | 33,851 |
| 1 | 23,043 |
| 8 | 14,067 |
| 9 | 10,084 |

| Bit | Mask | Meaning | Confidence |
| --- | --- | --- | --- |
| 0 | 0x01 | **Untextured.** The strip's triangles are built as gouraud-shaded, non-textured primitives (GP0 0x30/0x32) and never sample a texture. Set on 33,127 strips (40.9 %). | **confirmed** (0x80019424, 0x80017F88, 0x80017DF4) |
| 3 | 0x08 | **The winding of the strip's first triangle.** Equals bit 0 of the first triangle's vertex flag (`w[start+2] & 1`) in **42,267/42,267** strips measured, and bit 0 then alternates along the strip in **42,267/42,267**. Still **no reader** in `.text` (no `andi` with 8, 9, 0x0C, 0x0E or 0x0F in 0x80016000–0x8001E000) — the value is authoring-tool output kept consistent with the vertex flags, and a writer must keep it so. | **confirmed** (meaning) / ?unknown? (reader) |

Bits 1, 2 and 4–7 are never set.

Two measured bounds a writer should respect. No shipped mesh has more than **348 strips**
(`level_intro.mdl` mesh 1, over 5,989 meshes), and the median strip carries 2.33 triangles —
emitting one strip per triangle has no precedent anywhere in the corpus. And the flag byte
must agree with the vertex flags it announces: writing a parity that starts at 1 under a flag
byte with bit 3 clear makes a mesh contradict itself, which no shipped mesh does.

---

# 6. Per-triangle arrays

All three arrays are indexed by a flat triangle counter that runs across the whole mesh in
strip order. `ptr_uv_index` and `ptr_colour_index` advance by 2 bytes for **every** triangle,
including untextured ones; `ptr_texture_runs` advances only when its run countdown expires.

The decisive listing is the texture/UV pass:

```
; 0x80017EE8 — packet pass 2: command bytes, CLUT, tpage, UVs
80017EF4  lw    $a1, 0x6998($v0)   ; a1 = render context      [0x80056998]
80017EF8  lw    $v0, 0x18($a0)     ; a0 = mesh header
80017F08  addu  $t6, $a0, $v0      ; t6 = UV-INDEX array      (mesh 0x18)
80017F0C  addiu $v1, $v1, 0x1c
80017F10  addu  $t4, $a0, $v1      ; t4 = TEXTURE-RUN list    (mesh 0x1C)
80017F24  addu  $t3, $a0, $v0      ; t3 = COLOUR-INDEX array  (mesh 0x20)
80017F2C  addu  $t7, $a0, $v1      ; t7 = STRIP list          (mesh 0x14)
80017F1C  lw    $a1, 0xc($a1)      ; a1 = model base
80017F30  lw    $v0, 0x24($a1)
80017F3C  addu  $a1, $a1, $v0      ; a1 = UV TABLE            (model 0x24)
80017F34  addiu $t5, $t7, 1        ; t5 = strip HIGH byte
80017F40  lbu   $v1, ($t5)
80017F48  beq   $v1, 0xff, 0x800180b4
80017F50  addiu $a2, $t8, 7        ; a2 = packet cursor + 7
80017F54  addiu $a3, $a3, -1       ; per-triangle countdown
80017F5C  beq   $a3, -1, 0x800180a8      ; strip exhausted -> next strip

80017F64  addu  $t1, $t1, $v0      ; t1 += -1   RUN COUNTDOWN
80017F68  bgez  $t1, 0x80017f80    ;   still inside the current run
80017F70  lhu   $t2, ($t4)         ;   else fetch the next run entry
80017F74  addiu $t4, $t4, 2
80017F78  srl   $v0, $t2, 9
80017F7C  andi  $t1, $v0, 0x3f     ;   run = (entry >> 9) & 0x3F

80017F80  lbu   $v0, ($t7)
80017F88  andi  $v0, $v0, 1        ; strip flag bit 0
80017F8C  beqz  $v0, 0x80017fb4    ;   clear -> textured
...
80018094  addiu $a2, $a2, 0x28     ; next packet
80018098  addiu $t8, $t8, 0x28
8001809C  addiu $t6, $t6, 2        ; UV-INDEX  += 2   (every triangle)
800180A0  j     0x80017f54
800180A4  addiu $t3, $t3, 2        ; COLOUR-INDEX += 2 (every triangle)
```

Note that `t1` is **not** reset when the strip loop iterates at 0x800180A8 — a texture run
crosses strip boundaries freely. **confirmed**

## 6.1 UV index array (`ptr_uv_index`, mesh +0x18)

One `u16` per triangle. It is a **plain index into the model's UV table**, in units of
2-byte UV records, naming the first of **three consecutive** entries:

```
80018048  lhu   $v1, ($t6)         ; the u16 index, unmasked
80018050  sll   $v1, $v1, 1        ; * 2 bytes
80018054  addu  $v1, $a1, $v1      ; a1 = UV table
80018058  lhu   $v0, ($v1)         ; uv0
80018064  sh    $v0, 5($a2)        ;   -> packet +0x0C
80018068  lhu   $v0, 2($v1)        ; uv1
80018074  sh    $v0, 0x11($a2)     ;   -> packet +0x18
80018078  lhu   $v1, 4($v1)        ; uv2
80018090  sh    $a0, 0x1d($a2)     ;   -> packet +0x24
```

There is **no flag bit**: the maximum index over all 363,251 triangles is **2942 (0x0B7E)**;
bits 12–15 are never set. **confirmed**

The array occupies `2 * triangle_count` bytes starting at `T(0x18)`. The gap to
`T(0x1C)` is 0 in 4284/5990 meshes and a small positive number otherwise (2 in 582, then
multiples of 8); treat `T(0x1C)` as the hard end and `2*triangle_count` as the used length.

## 6.2 Texture run list (`ptr_texture_runs`, mesh +0x1C)

Run-length encoded `u16` entries. Each entry covers `((entry >> 9) & 0x3F) + 1` triangles.

| Bits | Name | Meaning | Confidence |
| --- | --- | --- | --- |
| 0–8 | `index` | Texture index within the sibling TEX pack — or, when bit 15 is set, a **palette** index. | **confirmed** |
| 9–14 | `run` | Additional triangles covered beyond the first. Max 63. | **confirmed** |
| 15 | `swatch` | Selects the alternate lookup path. Set on 14,566 of 47,243 run entries (30.8 %). | **confirmed** |

The two paths:

```
80017FB8  andi  $v0, $v1, 0x8000
80017FBC  beqz  $v0, 0x80018000          ; bit15 clear -> normal path
80017FC0  andi  $v1, $v1, 0x1ff

; ---- bit 15 SET: use the LAST texture of the pack, palette named by the low 9 bits
80017FC4  lw    $v0, ($t0)               ; t0 = ctx+0x10 -> texture COUNT
80017FCC  sll   $a0, $v0, 3
80017FD0  subu  $a0, $a0, $v0
80017FD4  lw    $v0, 8($t0)              ; ctx+0x18 -> texture descriptor array
80017FD8  sll   $a0, $a0, 3              ; a0 = 56 * count
80017FDC  addu  $a0, $a0, $v0
80017FE0  sll   $v0, $v1, 1
80017FE4  addu  $v0, $v0, $v1
80017FE8  lw    $v1, 0xc($t0)            ; ctx+0x1C -> CLUT descriptor array
80017FEC  sll   $v0, $v0, 2              ; 12 * palette_index
80017FF0  addu  $v0, $v0, $v1
80017FF4  lhu   $v0, 2($v0)              ; the CLUT id comes from the PALETTE table
80017FFC  addiu $a0, $a0, -0x20          ; a0 = &desc[count-1] + 0x18  (the LAST texture)

; ---- bit 15 CLEAR: ordinary texture lookup
80018000  andi  $v1, $t2, 0x1ff
80018004  sll   $v0, $v1, 3
80018008  subu  $v0, $v0, $v1
8001800C  lw    $v1, 8($t0)
80018010  sll   $v0, $v0, 3              ; 56 * index
80018018  addiu $a0, $v1, 0x18           ; a0 = &desc[index] + 0x18
8001801C  lhu   $v0, 0xe($a0)            ; the CLUT id comes from the TEXTURE itself
```

Both branches converge on `a0`, a runtime descriptor tail holding `clut` at +0x0E, `tpage`
at +0x0C and a UV origin at +0x10. So bit 15 means: *sample the pack's last texture (the
"swatch"), but colour it with palette `index`.* That is how one mesh paints itself in several
colour schemes from a single small image. **confirmed**

> **A zero entry is not "no texture" — it is texture slot 0.** This matters to a writer, and
> the corpus is unambiguous about it: of the **897 meshes whose every strip flag says
> untextured, not one writes a zero list**. 227 name a swatch palette from end to end and the
> other 670 mix swatch entries with texture ones. Over the whole archive **1,776 of 5,989
> meshes, in 340 models, carry at least one swatch entry** — 30 % of everything drawn.
>
> So "the mesh has no texture" is expressed by pointing every triangle at the swatch with a
> palette, never by clearing the list. A writer that fills it with zeros aims each triangle at a
> real slot with no CLUT behind it; `warp_room1`'s mesh 1 carries `0x8000 | 152` on 662 of its
> 663 triangles, and rebuilding it with zeros made a cutscene draw whatever was already in VRAM.
> The run *structure* cannot survive a rebuild — re-striping reorders the triangles it counts —
> but the palette can, so `mdlwrite` carries the entry the mesh's own list uses most.

Runtime descriptor strides, from the two accessors:

```
; 0x800160F8 — texture_descriptor(ctx, id)
80016100  lw    $a0, 0x18($a0)     ; ctx+0x18 = descriptor array
8001610C  andi  $v1, $a1, 0xfff
80016110  sll   $v0, $v1, 3
80016114  subu  $v0, $v0, $v1
80016118  sll   $v0, $v0, 3        ; 56 bytes per texture descriptor
80016120  addu  $v0, $a0, $v0

; 0x800160C4 — clut_descriptor(ctx, i)
800160CC  lw    $v0, 8($a0)        ; a0 was ctx+0x10
800160D8  sll   $v0, $a1, 1
800160DC  addu  $v0, $v0, $a1
800160E0  lw    $v1, 0xc($a0)      ; ctx+0x1C = CLUT array
800160E4  sll   $v0, $v0, 2        ; 12 bytes per CLUT descriptor
800160EC  addu  $v0, $v1, $v0
```

These 56- and 12-byte structures are **built at load time and do not exist in the file**. Which
TEX field lands in which descriptor slot was once unknown and is now the table in §10.4: the
builder at 0x8002926C fills every slot from the record, and the two the render pass reads
here — the tpage and the CLUT id — are written later still, by the VRAM allocator at
0x80028D40 out of the rect a texture is given.

## 6.3 Colour index array (`ptr_colour_index`, mesh +0x20)

One `u16` per triangle.

| Bits | Name | Meaning | Confidence |
| --- | --- | --- | --- |
| 0–12 | `index` | First of **three consecutive** entries in the model colour table — one per corner; the shading is gouraud. | **confirmed** |
| 13–14 | `abr` | PS1 semi-transparency mode, copied straight into bits 5–6 of the GPU draw-mode / tpage word. | **confirmed** |
| 15 | `translucent` | Turns the semi-transparency bit on in the primitive command byte (0x34→0x36, 0x30→0x32). | **confirmed** |

```
; 0x80017D90 — packet pass 3: colours
80017DEC  andi  $v0, $a3, 0x1fff   ; a3 = the colour index u16
80017DF0  sll   $v0, $v0, 2        ; * 4 bytes per colour record
80017DFC  addu  $a2, $t4, $v0      ; t4 = the colour table
...
80017E18  lw    $v1, ($a2)         ; colour 0
80017E1C  andi  $v0, $v0, 0x8000   ; bit 15
80017E2C  lui   $v0, 0x3200        ;   set   -> GP0 0x32 (gouraud tri, semi-transparent)
80017E30  lui   $v0, 0x3000        ;   clear -> GP0 0x30 (gouraud tri)
80017E3C  sw    $v0, 0xc($a3)      ; packet +0x0C
80017E40  lw    $v0, ($a2)         ; colour 1  (a2 was advanced by 4)
80017E48  sw    $v0, 0x14($a3)     ; packet +0x14
80017E4C  lw    $v0, 4($a2)        ; colour 2
80017E54  sw    $v0, 0x1c($a3)     ; packet +0x1C
```

and for textured triangles, the same array supplies the blend mode:

```
80018028  lhu   $v1, 0xc($a0)      ; base tpage from the texture descriptor
8001802C  lhu   $v0, ($t3)         ; the colour index u16
80018034  andi  $v1, $v1, 0xff9f   ; clear tpage bits 5-6
80018038  srl   $v0, $v0, 8
8001803C  andi  $v0, $v0, 0x60     ; colour-index bits 13-14 -> tpage bits 5-6
80018040  or    $v1, $v1, $v0
80018044  sh    $v1, 0x13($a2)     ; packet +0x1A = the tpage halfword
```

Distribution of bits 13–15 over 363,251 triangles:

| `value >> 13` | Count | Reading |
| --- | --- | --- |
| 0 | 320,282 | opaque |
| 4 | 481 | translucent, ABR 0 (B/2 + F/2) |
| 5 | 40,233 | translucent, ABR 1 (B + F) |
| 6 | 854 | translucent, ABR 2 (B − F) |
| 7 | 1,401 | translucent, ABR 3 (B + F/4) |

Bits 13–14 **never** occur without bit 15. **confirmed**

The array occupies `2 * triangle_count` bytes. `T(0x24) − (T(0x20) + 2*tri)` is 0 in 4704
meshes and 2 in 509 more; in the 777 meshes that have an attachment block (§8.4) the
remainder is that block.

---

# 7. Shared tables

## 7.1 Colour table (`model + 0x20`)

Records of 4 bytes: `R`, `G`, `B`, `0x00`. The fourth byte is where the GPU expects the
primitive's command code, which is why the game can `lw` a record and `or` the command in
(0x80017E34, 0x80017E60). The table runs from `T(0x20)` to `T(0x24)`. **confirmed**

`T(0x20) <= T(0x24)` in 400/400, with equality (an empty colour table) in 22 models — all of
`fonts/font*.mdl`. A reader that requires a strict `<` rejects those files.

A triangle's index names entry `i`, and the corners take `i`, `i+1`, `i+2`.

## 7.2 UV table (`model + 0x24`)

Records of 2 bytes: `u`, `v`. A triangle uses three consecutive records starting at its UV
index. The stored bytes are OR'd with a per-texture UV origin at runtime
(`lhu $a0,0x10($a0)` then `or` at 0x80018060 / 0x80018070 / 0x8001808C), so the file values
are page-local. **confirmed**

**The table's extent is stated by the file.** It ends at `T(0x28)` — measured over all 373
models that have UV data, `T(0x28) >= uv_start + 2*(max_index+3)` in **373/373**, with a gap
of 0 or 2 bytes in 305 of them (39/40 of the models whose 0x28 is non-degenerate). Deriving
the extent from the highest index used, as the current Python does, works but cannot detect
truncation.

## 7.3 Vector pool (`model + 0x28`)

Records of **6 bytes**: `i16 x, y, z`. Degenerate (zero length, `T(0x28) == T(0x08)`) in
360/400 models. Where present, it starts where the UV data ends and runs to `T(0x08)`.
Example: `models/arena/boss_bear/polar.mdl` holds 1239 records beginning (49, 142, −53),
(58, 137, 28), (83, 102, 32) — the same scale as mesh vertices.

It is a **compressed source pool for vertex positions**, indexed by a separate u16 array:

```
; 0x8001C1E0 — expand an index array into a mesh's 8-byte vertex records
8001C1E4  lw    $v0, 0x14($a0)     ; a0 = mesh header
8001C1F0  addiu $t3, $v0, 0x15     ; t3 = strip list + 1 (HIGH byte), same walk as §5
8001C210  lhu   $a0, ($t2)         ; t2 = the u16 index array
8001C214  addiu $t2, $t2, 2
8001C21C  srl   $v0, $a0, 2        ; index  = entry >> 2
8001C220  sll   $v1, $v0, 1
8001C224  addu  $v1, $v1, $v0
8001C228  sll   $v1, $v1, 1        ; * 6                <-- 6-BYTE RECORDS
8001C22C  addu  $v1, $a2, $v1      ; a2 = the vector pool
8001C230  lhu   $v0, ($v1)
8001C238  sh    $v0, ($a3)         ; -> x
8001C23C  lhu   $v0, 2($v1)
8001C244  sh    $v0, -4($t0)       ; -> y
8001C248  lhu   $v0, 4($v1)
8001C240  andi  $a0, $a0, 3        ; the low 2 bits of the entry are a flag
8001C24C  addiu $a3, $a3, 8        ; output stride 8 = a vertex record
```

and the pool itself is selected at 0x80019C08:

```
80019BF0  lw    $a2, ($a3)         ; a3 = an animation-supplied override block
80019BF8  beqz  $a2, 0x80019c08
80019C04  addu  $t2, $a3, $a2      ;   present -> use that pool
80019C08  lw    $v0, 0x28($s1)     ; s1 = model base
80019C10  addiu $v0, $v0, 0x28
80019C14  addu  $t2, $s1, $v0      ;   absent  -> use the model's 0x28 pool
```

So `model+0x28` is the **fallback** pool, and an animation blob can substitute one of its own.
**confirmed** for the record size, the `>>2` index and the fallback selection.

The framing question — how many vectors make one pose, and where the index arrays live — is
answered in **§9**: the index arrays are the keyframes inside an animation blob, and a pose is
however many entries the driven mesh has, so the pool is a deduplicated bag of positions
shared across every pose of every clip. That is why the span ÷ 6 is several times the model's
vertex count. The 208 clips that take this branch belong to 40 models, and across those 40 the
pool is exactly consumed: `6*(highest index + 1)` equals the whole span in 16 of them and
leaves a 2-byte alignment pad in the other 24. It is **not** a bind pose — see §9.9.

---

# 8. The file tail

Everything below `T(0x08)` is model *structure*, not geometry. It is documented here because
a writer must preserve it; a viewer can ignore it.

## 8.1 Chunk descriptor list (`model + 0x3C`)

`[i32 count == i32@0x38]` followed by **count + 1** records of 16 bytes. Record *i* is at
`T(0x3C) + 4 + 16*i`. The block ends exactly at `T(0x1C)` in 400/400.

| Offset | Type | Meaning | Confidence |
| --- | --- | --- | --- |
| +0x00 | u32 | start byte offset | **confirmed** |
| +0x04 | u32 | end byte offset (length = end − start) | **confirmed** |
| +0x08 | u32 | runtime pointer slot (freed/zeroed at 0x8001639C) | **confirmed** |
| +0x0C | u32 | runtime pointer slot (freed/zeroed at 0x800163CC) | **confirmed** |

```
; 0x800163E0 — load chunk `a1` of model `a0`
800163F0  sll   $a1, $a1, 4        ; 16-byte stride
800163F4  lw    $v0, 0x3c($v1)
800163FC  addiu $v0, $v0, 0x3c
80016400  addu  $v1, $v1, $v0      ; v1 = T(0x3C)
800163F8  addiu $a1, $a1, 4        ; skip the count word
80016404  addu  $s0, $v1, $a1      ; s0 = &record[i]
80016428  lw    $a1, ($s0)         ; start
8001642C  lw    $a3, 4($s0)        ; end
80016434  jal   0x800133c8         ; load(handle, start, ctx, length)
80016438  subu  $a3, $a3, $a1      ;   (delay slot) length = end - start
8001643C  sw    $v0, 0xc($s0)      ; stash the result in +0x0C
```

The model base is written into record 0's +0x08 slot at 0x8001DEE0.

## 8.2 Sub-file directory (`model + 0x44`) — the animation clip table

`i32@0x40` records of **24 bytes**, no count prefix (the count is in the header). Structurally
this is a directory of byte ranges the loader pulls into RAM; in the retail archive **every
one of the 1037 records is an animation clip**. The record is documented here as a directory
entry and in full, with its payload, in **§9.1**.

| Offset | Type | Meaning | Confidence |
| --- | --- | --- | --- |
| +0x00 | u32 | start byte offset, **absolute from the file base** (not self-relative) | **confirmed** |
| +0x04 | u32 | end byte offset | **confirmed** |
| +0x08 | u32 | frame count of the clip in that byte range; bounds-checked at 0x80019B70. Range 1..620 | **confirmed** (§9.1) |
| +0x0C | i32 ptr | self-relative pointer to the **mesh header the clip drives**; resolves to one in 1036/1037 | **confirmed** (§9.1) |
| +0x10 | u32 | name hash, `sum(name[i] * (i+1))` 1-based, computed by 0x8001534C over a caller-supplied string | **confirmed** (§9.1) |
| +0x14 | u32 | runtime pointer slot; 0 in the file. The loader writes the resident blob address here (0x80015FD8) | **confirmed** |

The 24-byte stride is confirmed twice independently — once by the loaders at 0x800164E8 /
0x800165C4 / 0x800168F8, and once by the id dispatcher:

```
; 0x800156A8 — the 0x4000 id namespace resolves to a sub-file record
800156A8  lw    $v1, 0x44($a1)     ; a1 = model base
800156AC  andi  $v0, $a0, 0xf80    ; record index = (id & 0xF80) >> 7
800156B0  sra   $v0, $v0, 7
800156B4  addiu $v1, $v1, 0x44
800156B8  addu  $v1, $a1, $v1      ; v1 = T(0x44)
800156BC  sll   $a1, $v0, 1
800156C0  addu  $a1, $a1, $v0
800156C4  sll   $a1, $a1, 3        ; 24 * index
800156CC  addu  $a1, $v1, $a1
```

Corpus: 1037 records total; `start < end <= filesize` in **1037/1037**; `+0x14 == 0` in
**1037/1037**; every start is 0x800-aligned in 1037/1037; and for the 225 models with
sub-files the last payload ends exactly 4 bytes before EOF in 225/225.

The alignment is **forced, not conventional**: the byte-range reader of §1.1 shifts a start
offset straight down by 11 to get a sector and rounds only the length up, so a sub-file that did
not begin on a 2048-byte boundary could not be fetched at all. A writer that moves a clip must
keep it 0x800-aligned.

## 8.3 Object table (`model + 0x1C`)

> `model+0x4C` used to be described here as a pointer array into this table. It is not — it is
> the **scene root array** of §9.11, and this project's own `crashbash/scene.py` has read it
> that way all along, off the spawner at 0x8001FF78. Measured against the object table:
> **0 of the 688 entries land inside an object record**, 556 land past the table entirely, and
> the 132 that touch it land exactly on `T(0x1C)` — the count word, not a record. The old
> "0, 4 or 8 mod 12" split was a residue computed without ever checking containment; its three
> figures sum to 688 because every entry has *some* residue mod 12.

The object table is addressed by the **0x5000** id namespace with a **12-byte stride** and a
1-based id:

```
; 0x800159C4
800159C8  addiu $v1, $a0, -1       ; a0 = id & 0xFFF; 1-based
800159CC  andi  $v1, $v1, 0xfff
800159D0  sll   $a0, $v1, 1
800159D4  addu  $a0, $a0, $v1
800159D8  sll   $a0, $a0, 2        ; 12 * (id-1)
800159E0  lw    $v0, 0x1c($a1)     ; a1 = model base
800159E8  addiu $v0, $v0, 0x1c
800159EC  addu  $a1, $a1, $v0      ; a1 = T(0x1C)
800159E4  addiu $a0, $a0, 4        ; + 4
800159F4  addu  $a0, $a1, $a0      ; -> record+4
800159F0  jal   0x8001dd20         ; reads that struct's +0 and +4
```

### What an object record names: a mesh, and which model it lives in

The resolver turns the record into an address:

```
; 0x8001DD20 — a0 = record + 4
8001DD20  lw    $v0, 4($a0)      ; [record+0x08]
8001DD28  addu  $v0, $v0, $a0    ; record+4 + [record+0x08] -- self-relative from +0x04
8001DD2C  lw    $v1, 4($v0)      ; the address parked one word further on
8001DD34  beqz  $v1, 0x8001dd48  ; nothing loaded there -> the object resolves to 0
8001DD3C  lw    $v0, ($a0)       ; [record+0x04] = a byte offset
8001DD44  addu  $v0, $v1, $v0    ; base + offset
```

| Offset | Type | Meaning | Confidence |
| --- | --- | --- | --- |
| +0x00 | i32 | not read on this path | ?unknown? |
| +0x04 | i32 | byte offset of a **mesh header**, inside the model named at +0x08 | **confirmed** |
| +0x08 | i32 ptr | self-relative **from +0x04**, landing on `T(0x3C) + 4 + 16*j + 4` — the chunk descriptor *j* of §8.1, whose +0x08 is the runtime pointer slot the resolver reads. Record 0's slot is the model's own base (written at 0x8001DEE0), so `j == 0` means "a mesh in this file". | **confirmed** |

That it is a mesh header is settled by the caller: the draw routine dispatches the two id
namespaces down separate paths that meet on the same load.

```
; 0x80019AD0 — s2 = id, s1 = model base
80019AD0  andi  $v1, $s2, 0x7000
80019AEC  addiu $v0, $zero, 0x2000
80019AF0  beq   $v1, $v0, 0x80019d0c    ; the numbered array
80019B08  addiu $v0, $zero, 0x5000
80019B0C  beq   $v1, $v0, 0x80019d3c    ; the object table

80019D0C  lw    $v0, 0x54($s1)          ; the mesh count
80019D10  andi  $v1, $s2, 0xfff
80019D14  slt   $v0, $v0, $v1
80019D18  bnez  $v0, 0x80019ef8         ; past the end: nothing is drawn
80019D2C  sll   $v0, $v0, 2             ; 0x34 * index
80019D30  addiu $v0, $v0, 0x24          ; 0x58 - 0x34: the id is 1-based here too
80019D38  addu  $s0, $s1, $v0

80019D3C  andi  $a0, $s2, 0xffff
80019D40  jal   0x800159c4              ; -> the object table
80019D44  move  $a1, $s1
80019D48  move  $s0, $v0
80019D4C  beqz  $s0, 0x80019ef8         ; unresolved: nothing is drawn

80019D54  lw    $v0, 0x10($s0)          ; both arrive here: mesh + 0x10, the bounds
80019D5C  addiu $v0, $v0, 0x10          ; block, and 0x14 further on the vertex pool
80019D60  addu  $s1, $s0, $v0
```

### This is where a level keeps its set

The headers those offsets reach lie in `T(0x2C) .. T(0x3C)`, the span §2.1 calls the pool:
empty in exactly the 327 models whose `i32@0x14` is 0, non-empty in the other 73 — every
arena, warp room, hub and the menu. Measured over those 73:

| | |
| --- | --- |
| object records before the scene nodes start | 2009 |
| … resolving into this file's own pool | 1971 (96,232 triangles) |
| … naming a model the level loads alongside its own | 38 |
| object meshes whose strip list matches their header's count | 1971 / 1971 |
| models where `j == 0` ⟺ the offset lands in this file's pool | 73 / 73 |
| meshes laid nose to tail, `ptr_end + 4 == the next header` | 1875 / 1971 |

The remaining 96 are runs of consecutive headers sharing one geometry block, the same way
the numbered array packs its headers together at 0x58.

Nothing counts the records. The array runs until the scene nodes start, and what ends it is
the reference field: a real record points at one of the `i32@0x38 + 1` chunk descriptors, so
a walk stops at the first that does not (73/73).

**And there is a zero word sitting exactly there.** A byte-coverage walk over the archive
leaves one four-byte hole in each of the 73 models with an object set, every one of them at
`T(0x1C) + 12 × records` — the first byte past the last record — and every one zero. That is
the word the walk above fails on, so the array is terminated in the file as well as by the
test. Measured 73/73 on both counts; no site was found that reads the word itself, which is
what one would expect of a value whose only job is to fail a check.

The 42 numbered meshes of `warp_room1/level.mdl` are its sky dome, its stars and the CRASH
BASH sign. The room — floor, stairs, lamp posts, the CRASHBALL and POLAR PANIC boards — is
77 objects, and their vertices are already in room coordinates, so they need no placement to
stand where the game stands them. A reader that walks only the numbered array shows a purple
dome with nothing under it.

The 0x4C array is `i32@0x48` self-relative i32 pointers, stride 4, ending exactly where the
0x18 block begins (400/400). All 688 corpus entries resolve inside the file, and **each names a
scene root** (§9.11), not a field of an object record: 0 of the 688 land inside the object
records, 556 land past the whole table, and the 132 that touch it land exactly on `T(0x1C)`.
The overall extent `T(0x4C) − T(0x1C)` is not a multiple of 12 (0 mod 12 in 279 models, 4 in
70, 8 in 51), so what sits between the object table and the root array is a heterogeneous
object graph rather than more object records. One record kind is now partly readable: the
scene **nodes** the
cutscene player walks, which carry a time window, a **play command** — loop start/end
frames and mode — at `+0x14`, and placement keys from `+0x30` at stride 0x4C; see the
looping part of §9.7. The rest of the graph is no longer unknown: §9.11.9 reads the type table
and finds **six kinds and no more**, §9.11.10 names the last of them, and `tools/coverage.py`
now accounts for the whole span between the object records and `T(0x4C)` down to 292 bytes of
four-byte alignment across the entire archive.

## 8.4 Mesh attachment block (`mesh + 0x2C`)

Non-zero in **777/5990** meshes. It sits between the colour-index array and `ptr_end`.

| Offset | Type | Meaning | Confidence |
| --- | --- | --- | --- |
| +0x00 | u16 | flags — 0 (769), 2 (6), 5 (2) | ?unknown? |
| +0x02 | u16 | record count — 1 (448), 4 (233), 2 (64), 3 (22), 6 (10) | *likely* |
| +0x04.. | 16 bytes × count | records | *likely* (size) / ?unknown? (contents) |

`ptr_end − target == 4 + 16*count` exactly in **769/777**; the other 8 have 16 or 40 bytes of
slack. Block sizes observed: 20 (446), 68 (239), 36 (64), 52 (16), 100 (10), 60 (2).
`target == ptr_colour_index + 2*triangle_count` in 699/777, `+2` in the other 78.

The field is live and is the target of the **0x2000** id namespace:

```
; 0x80015664 — the model id-type dispatcher.  a0 = u16 id, a1 = model base
80015664  andi  $v1, $a0, 0x7000          ; namespace = id >> 12
8001566C  beq   $v1, 0x3000 -> 0x80015744 ;   0x3000 -> NULL (no handler)
8001567C  beq   $v1, 0x2000 -> 0x80015718 ;   0x2000 -> MESH header, field 0x2C
80015690  beq   $v1, 0x4000 -> 0x800156a8 ;   0x4000 -> model 0x44 sub-file directory
80015698  beq   $v1, 0x5000 -> 0x800156f4 ;   0x5000 -> model 0x1C object table

80015718  andi  $v1, $a0, 0xfff
8001571C  sll   $v0, $v1, 1
80015720  addu  $v0, $v0, $v1
80015724  sll   $v0, $v0, 2
80015728  addu  $v0, $v0, $v1
8001572C  sll   $v0, $v0, 2                ; v0 = 52 * (id & 0xFFF)
80015730  addiu $v0, $v0, 0x24
80015738  addu  $v1, $a1, $v0              ; v1 = the mesh header
80015700  lw    $v0, 0x2c($v1)             ; <-- MESH HEADER +0x2C
80015708  bnez  $v0, 0x8001573c
8001570C  addiu $v0, $v0, 0x2c
80015740  addu  $v0, $v1, $v0              ; result = mesh + 0x2C + [mesh+0x2C]
80015744  move  $v0, $zero                 ; zero field -> NULL
```

The `bnez` guard is exactly the 5213-zero / 777-non-zero split measured in the corpus.
**confirmed** that the field is a live self-relative pointer; the record contents are
decoded below for playable characters and remain ?unknown? for the rest of the family.

### What 0x3000 is: a texture, and the dispatcher returns NULL because it is not in the model

The dispatcher has no handler for 0x3000 above, and that reads as a gap until you find what
*makes* one. 0x800158E4 does — given an object and an id in either mesh namespace, it produces
a 0x3000 id:

```
800158EC  lw    $a2, 0xc($a0)     ; a0 = the object; +0x0C -> its model
800158F8  beq   0x2000 -> 0x80015918   ; a1 = model + 0x34*(id & 0xFFF) + 0x24
80015908  jal   0x800159c4        ; 0x5000 -> the object table, same answer
80015940  beqz  $a1, -1           ;   no mesh -> -1
80015948  lw    $v0, 0x1c($a1)    ; mesh+0x1C
80015950  addu  $v0, $v0, $a1     ;   biased to the mesh, so +0x1C below lands on T(0x1C)
80015954  lhu   $v1, 0x1c($v0)    ; the FIRST entry of the texture run list (§6.2)
8001595C  andi  $v0, $v1, 0x8000
80015960  bnez  $v0, -1           ;   the swatch bit -> -1
80015964  andi  $v0, $v1, 0x1ff   ; else the texture index
8001596C  ori   $v0, $v0, 0x3000  ;   tagged 0x3000
```

Every step matches §6.2: bits 0–8 are the texture index, bit 15 marks a swatch entry whose low
bits are a *palette* index instead — which is exactly why that case returns −1 rather than a
texture. So **0x3000 names a texture in the sibling TEX pack**, and the dispatcher returns NULL
for it not because the namespace is unused but because the thing it names is not inside the
model file the dispatcher is walking.

The routine reads only the run list's first entry, so what it answers is "the texture this mesh
starts with", not "the textures this mesh uses". A mesh whose runs span several textures has
the rest of them invisible to it. Over the corpus all **5990** meshes carry a run list, and the
first entry has the swatch bit set in **1284** of them — so this routine answers −1 for a fifth
of the meshes in the game and a texture id for the other 4706.

**The records are gameplay volumes — for a character, its collision body.** Decoded as
8 × i16, every crate-minigame character carries exactly one record of the shape

```
[ox, oy, oz,  half_width,  -height,  depth,  unk,  0x4000|flag]
```

where field 4 is the mesh's own standing height to the unit in **8 of 8** crate characters
(Crash −502, Coco −619, Tiny −724, …), field 3 a body half-width (119–179 for the
characters), fields 0–2 a small centre offset, and field 7 carries the 0x4000 bit in 81 % of
all records corpus-wide. Field 4 matches the mesh height in only 677 of the 1717 records, so
the block is a family of purpose-dependent volumes rather than one fixed meaning — but for a
playable character it is the collision body, and the proof is behavioural **in both
directions**: replacing the crate character with a mesh whose `+0x2C` was zeroed made crates
stop colliding — the character walked straight through them — and carrying the replaced
mesh's own block through the same swap, everything else identical, brought the collision
back in play. **confirmed** for characters; tested on the NTSC-U crate minigame.

**It is a box, not a cylinder — and even that is a reading.** An earlier revision of this
section called it a standing cylinder and gave field 3 as a radius. Field 5 refutes that:
it is a **second horizontal extent**, non-zero in **349 of 1717** records and **different
from field 3 in 25** of those, which no circular cross-section can be. Two more measurements
point the same way. Field 3 is half the mesh's own width (median ratio **1.000** over all
1717) rather than half its diagonal (median **0.707**), so it is an inscribed half-extent and
not a radius that would wrap the mesh. And the largest single family — **324 records**, the
crates, every one of them `(−128, 0, −128, 128, −256, 128, 1792, 0)` — sits on a mesh whose
own extent is exactly `x[−128, 128] y[−256, 0] z[−128, 128]`, a 256-unit cube. A crate's
volume is its crate.

Where field 5 is zero the two horizontal extents are the same, which is why the character
records read equally well either way and why the cylinder went unchallenged. **No site has
been found that tests against the block**, so the shape is what the record describes, not a
proven test volume: the reader exposes both extents and the viewport draws the box, and if
the routine that reads it ever turns up it may yet round the corners.

The reading extends to the characters' second meshes. A crate character's mesh 1 is its
**spin body** — no clip drives it; the game swaps the display to it and rotates the entity in
code — and its record widens the radius from Crash's standing 128 to **307** with `unk` rising
from 64 to 1360: the spin attack's larger interaction volume. Replacing a character therefore
means replacing both meshes, each under the original's own block.

**A level's own geometry carries almost none of these.** Of the 1971 object meshes that make
up the 73 arenas, warp rooms and hubs (§8.3), only **35 have a `+0x2C` block at all**, and
they sit in seven models: `demo_hub1` (16), `boss_oxide/arena` (9), `boss_oxide/chaselevel`
(4), `demo_hub2` and `boss_bear` (2 each), and one apiece in the two `medieval_dragon`
arenas. Counting the numbered meshes of those same models adds 54 more, so 89 of the 777
blocks in the archive are in a level and 688 are on something else — characters, props and
cutscene actors. No arena floor, wall or warp-room stair has one.

So **no level carries collision as attachment volumes**: there is no volume list behind the
set, no `+0x2C` block on a floor or a wall. Nor do the minigames walk the level's meshes to
test against them — across the 14 mode overlays there is exactly **one** call to the object
resolver at 0x800159C4 (`warp.bin`, 0x800BB60C).

Where a floor is defined is still ?unknown?, but "not in this file" would now be too strong a
way to put it. §8.6 finds 242 KB in the seven hub and warp rooms that begins past the
resident image on a sector boundary, holds vertex-shaped records in room coordinates, and has
no traced reader. That is not a collision mesh until something is shown to read it as one —
but it is in the file, and it is the reason this paragraph no longer says otherwise.

When no valid block can be supplied, zero remains the safe state — 5,213 of the game's own
5,990 meshes have none — but for a character that costs its collision, not just a cosmetic.

## 8.5 Sub-object array (`model + 0x18`) — the level's placement list

`[i32 count]` then `count` self-relative i32 pointers. The count is `i32@0x14`, so the 73
models that have an object set have exactly one sub-object each and no other model has any.
The resolve, and the four fields of the sub-object the binder reads:

```
; 0x8001DE18 — a0 = runtime instance, [a0+8] = which sub-object
8001DE1C  lw    $a1, 8($a0)
8001DE24  sll   $a1, $a1, 2
8001DE28  lw    $v0, 0x18($v1)     ; v1 = model base
8001DE30  addiu $v0, $v0, 0x18
8001DE34  addu  $v1, $v1, $v0      ; v1 = T(0x18)
8001DE2C  addiu $a2, $a1, 4        ; skip the count word
8001DE38  addu  $a1, $v1, $a1
8001DE40  lw    $v0, 4($a1)        ; the entry
8001DE44  addu  $v1, $v1, $a2
8001DE48  addu  $v1, $v1, $v0      ; self-relative resolve
8001DE4C  sw    $v1, 0x10($a0)     ; -> the sub-object

; the sub-object's own header, same self-relative convention
8001DE50  lw    $v0, 0xc($v1)  -> instance +0x28   (and +4 -> instance +0x2C)
8001DE6C  lw    $v0, 0x10($v1) -> instance +0x30
8001DE80  lw    $v0, 0x20($v1) -> instance +0x14   ; the record array
8001DE94  lw    $v0, 0x1c($v1) -> instance +0x18   ; how many records (raw, not resolved)
```

| Offset | Type | Meaning | Confidence |
| --- | --- | --- | --- |
| +0x00, +0x04, +0x08 | i32 | 0x2000 in 73/73. Not read on this path. | **confirmed** (constant) / ?unknown? (purpose) |
| +0x0C | i32 ptr | Target is the **end of the record array** in 73/73 — `records + 160*count` to the byte — and is itself `[i32 count]` then `count` self-relative i32 pointers. See below. | **confirmed** |
| +0x10 | i32 ptr | A second block: `[i32 count]` then `count` records of 16 bytes. See below. | **confirmed** |
| +0x14, +0x18 | i32 ptr | Same value in 73/73, so two targets 4 bytes apart. **+0x14's target is the last block in the file**: it runs from there to `T(0x44)` in 73/73, and its first two words are a count and `4 × count` in 73/73, followed by that many i32 — of which 77 are ascending 4-aligned in-block values and 30 are a negative last word. See below. | **confirmed** (extent, header and the array's shape) / ?unknown? (what the values mean, and what reads it) |
| +0x1C | i32 | **Record count.** Read raw into instance +0x18 and used as the loop bound. | **confirmed** |
| +0x20 | i32 ptr | **The record array**, 0x14 in 73/73 — it always starts at sub-object +0x34. | **confirmed** |
| +0x24 | i32 | 0 (40), 0x01000000 (20), 13 distinct values. | ?unknown? |
| +0x28..+0x30 | i32 | 0 in 73/73. | **confirmed** (zero) / ?unknown? (purpose) |

### The record: what is drawn, and where it stands

The loader strides the array by **160 bytes** and copies six things out of each record. It is
worth reading the offsets off the two bases it sets up — `t1` is the record array and
`a2 = t1 + 0x77`, so `-0x73($a2)` is record +0x04:

```
; 0x8001E0A8 — a0 = the instance 0x8001DE18 filled in
8001E0AC  lw    $t0, 0x1c($a0)     ; the runtime record array, stride 0xA8
8001E0B0  lw    $a3, 0x18($a0)     ; the count, from sub-object +0x1C
8001E0B4  lw    $t1, 0x14($a0)     ; the file record array, stride 0xA0 = 160
8001E0C4  addiu $t2, $v0, -0x57d0  ; a 160-byte template at 0x8005A830
8001E0D8  addiu $a1, $t0, 0x5c
8001E0DC  addiu $a2, $t1, 0x77
8001E0E8  ... copy the template over the runtime record ...
8001E124  lw    $v0, ($t1)         ; record +0x00  -> runtime +0x00   (the flag word)
8001E130  lw    $t6, -0x73($a2)    ; record +0x04  -> runtime +0x04   } the position,
8001E134  lw    $t7, -0x6f($a2)    ; record +0x08  -> runtime +0x08   } three i32
8001E138  lw    $t8, -0x6b($a2)    ; record +0x0C  -> runtime +0x0C   }
8001E148  lw    $t6, -0x4f($a2)    ; record +0x28  -> runtime +0x30   } 32 bytes: one
8001E168  lw    $t6, -0x3f($a2)    ; record +0x38  -> runtime +0x40   } libgte MATRIX
8001E188  sw    $t4, -8($a1)       ; runtime +0x54 = 0x8001DD50, the draw handler
8001E198  lhu   $v0, 0x11($a2)     ; record +0x88  -> runtime +0x74   the id
8001E1A4  lhu   $v0, 0x27($a2)     ; record +0x9E  -> runtime +0x8A
8001E1B0  lhu   $v0, 0x25($a2)     ; record +0x9C  -> runtime +0x68
8001E1BC  lbu   $v0, -3($a2)       ; record +0x74..+0x77, four bytes -> runtime +0x64..+0x67
8001E1EC  addiu $v0, $t0, 0xa8     ; runtime +0x5C = the next record: a linked list
8001E1FC  addiu $a2, $a2, 0xa0     ; (delay slots) stride 160 through the file array
```

That the 32 bytes at +0x28 are a **libgte `MATRIX`** — `short m[3][3]`, two bytes of padding,
`long t[3]` — is not read off the copy width alone. Over all 2689 records the 3×3 has
orthonormal rows *and* columns to one part in a thousand in **2689/2689**, the padding
halfword at +0x3A is zero in **2689/2689**, and `t[3]` at +0x3C equals the separate position
vector at +0x04 in **2689/2689**. 1985 of the rotations are the identity; the other 704 turn
the piece.

| Offset | Type | Meaning | Confidence |
| --- | --- | --- | --- |
| +0x00 | u32 | Flag word, and both live bits are now read. **Bit 15 set in 2689/2689**, tested at 0x8001DD6C before anything is drawn. **Bit 25** says the record carries its own threshold at +0x9C — see below. The word is 0x02008000 (1900) or 0x00008000 (789). | **confirmed** (bit 15) / ?unknown? (bit 25) |
| +0x04 | i32 ×3 | Position, in the same units as a vertex. | **confirmed** |
| +0x18 | i32 ×3 | 4096, 4096, 4096 in 2689/2689 — a scale of 1.0 that no code site reads. | *likely* (scale) |
| +0x28 | MATRIX | 3×3 rotation in 3.12 fixed point, pad, then `t[3]` repeating +0x04. | **confirmed** |
| +0x48 | MATRIX | A second one, byte-identical to the first in 541/2689. Not read by the loader. | **confirmed** (shape) / ?unknown? (use) |
| +0x74..+0x77 | u8 ×4 | **Searchable tags.** 0x8001DCE0 walks the runtime chain and returns the first record whose *n*-th of these bytes matches a value, so a placement can be found by them. In a warp room the first two are a unique two-part key; see below. +0x76 is 0 in 2688/2689 and +0x77 in 2689/2689. | **confirmed** |
| +0x88 | u16 | **The id of what is drawn.** | **confirmed** |
| +0x9C | u16 | **The ordering-table index the placement draws into** — its depth bucket — bounds-checked against the segment's last index before the textured path is entered. Flag bit 25 says the record carries one; without it the index is 1. See below. | **confirmed** |
| +0x9E | u16 | **Traced end to end, and never exercised.** Copied to runtime +0x8A; 0x80019A9C reads it back and writes it to the global at 0x80056AC4, gated on flag bit 30; 0x800190E4 is that global's only reader and tests it for non-zero as one condition among several on a draw path. The data never takes the path: the field is **0 in 2689/2689** records and **bit 30 is clear in 2689/2689**. | **confirmed** (where it goes and what tests it) / ?unknown? (what it would mean) |

### The +0x14 block: the last thing in a level, and nothing found reads it

A byte-coverage walk over the archive is what turned this up. After everything else in a
model is accounted for, exactly **73 spans** are left in `T(0x18)..T(0x44)` — one per level
with a sub-object — and each begins at the sub-object's **+0x14 target** and ends at
**`T(0x44)`**, both in 73/73. It is the last block before the clip table, 82,300 bytes over
the archive, up to 5144 in `dash_dot`.

Its head is a counted array of i32, and the array holds **two different kinds of value**. The
first word is a count and the second is `4 × count` in **73/73** — the array's byte length.
Then `count` i32 follow, 107 across the corpus, and they split by sign:

| | count | multiple of 4 | inside the block | position |
|---|---|---|---|---|
| non-negative | 77 | **77/77** | **77/77** | never last |
| negative | 30 | 2/30 | — | **always last, one per block** |

The 77 are strictly ascending within their block and every gap between neighbours is a
multiple of 8 — 224 up to 1456. Four levels make the point on their own: `tank_metal`,
`tank_jungle`, `tank_desert` and `tank_swamp` all carry `[524, 804, 1084, 1364, x]`, four
words marching at an exact stride of 280, and **the negative last word is the only one of the
five that differs between them** (−21, −190, −131, −97). Aligned, ordered, in-range and
regularly spaced is what an offset table looks like; 28 of the 30 negatives are not even
4-aligned, so whatever the last word is, it is not the same kind of thing as the other four.
Neither reading is confirmed — see the closing paragraph.

The count is 0 in **43 of the 73** models, and **36 of those blocks are exactly 8 bytes** —
the two head words and nothing else. The other seven zero-count blocks still run 236 to 1616
bytes, so the count does not size the block. The `+0x18` pointer lands inside it in 73/73,
four bytes past where `+0x14` does, which puts it on the byte-length word: read from there the
head is a length-prefixed array, the same shape the DAT uses for its own section table (§1.1).

What the 77 reach is unread. Sampling `dash_dot/arena` at its first gives −5214, 0, −5551,
4897 — coordinate-scale, and the negative last words are coordinate-scale too (−21 to −3096),
but that is a look at one place and not a decode.

**Nothing found reads it, and the binder demonstrably does not.** 0x8001DE18 resolves the
sub-object's +0x0C, +0x10, +0x1C and +0x20 and passes over +0x14 and +0x18 entirely — that
much is read off the instruction sequence in §8.5 rather than inferred. Who consumes the
block is a search that has not succeeded, so **I could not validate that anything uses it,
and that is not evidence it is unused**: 30 of the 73 models put a non-zero count there, and
a level would not carry up to 5 KB of it for nothing.

### The +0x10 block: a count and 16-byte records

Reached as the instance's +0x30, and read at 0x80024B70. An earlier revision said nothing
read it back; that was a search that had not found one, and the search was too narrow.

```
80024B70  lw    $v0, 0x30($v1)     ; v1 = the instance -> the block
80024B78  lw    $v0, ($v0)         ; its first word is a COUNT
80024BB4  jal   0x80011654         ; allocate...
80024BB8  sll   $a0, $a0, 2        ;   (count*7) << 2 = 28 bytes each
80024C20  addiu $a2, $t0, 0x14     ; t1 starts at 4, so the array is block+4
80024C40  lw    $v0, 0xc($a1)      ; record+0x0C
80024C48  and   $v0, $v0, -4       ;   masked to a multiple of four
80024C50  addu  $a0, $a1, $v0      ;   self-relative from +0x0C
80024C60  andi  $v0, $v0, 0x4000   ; the target's +0x02, bit 14, gates a copy
80024C7C  sll   $v0, $v0, 3        ;   into 24-byte slots indexed by its +0x04
80024DBC  addiu $t1, $t1, 0x10     ; the file record is 16 bytes
80024DC4  addiu $a2, $a2, 0x1c     ;   the runtime one 28
```

The corpus agrees without exception. The count is 1..27 in 73/73, the array always fits, and
of the **473 records in the archive every one** has a `+0x0C` that resolves inside its own
block — none has the low two bits set that the mask would discard. 339 of the 473 targets
carry the 0x4000 the loop tests for.

| Offset | Type | Meaning | Confidence |
| --- | --- | --- | --- |
| +0x00 | i32 | record count | **confirmed** |
| +0x04.. | 16 bytes × count | records; +0x00..+0x08 is a three-word payload copied out whole, +0x0C a self-relative pointer into the same block | **confirmed** (shape) / ?unknown? (what the payload is) |

### The block after the records is a searchable list

The binder hands the +0x0C target to the instance as +0x28, and the pair of library routines
that read it settle its shape without settling what it holds. One searches it, the other
indexes it:

```
; 0x8001E48C — a0 = the key being looked for; returns the entry, or 0
8001E490  lw    $v1, -0x7530($v0)  ; the current instance, a global at 0x80058AD0
8001E4A8  lw    $a1, 0x28($v1)     ; -> sub-object +0x0C's target
8001E4B0  lw    $a2, ($a1)         ; [+0x00] is a COUNT
8001E4B4  slt   $v0, $v1, $a2      ; loop v1 = 0 .. count-1
8001E4C0  addiu $a1, $a1, 4        ; (delay slot) step, first turn skipping the count
8001E4C4  lw    $v0, ($a1)
8001E4CC  addu  $v0, $a1, $v0      ; self-relative resolve, stride 4
8001E4D0  lw    $v0, 0xc($v0)      ; the field the search matches on
8001E4D8  beq   $v0, $a0, 0x8001e4ec ; hit

; the tail, entered by falling through -- not a separate routine
8001E4EC  bgez  $v1, 0x8001e4fc    ; miss -> return 0
8001E504  lw    $v0, 0x2c($v0)     ; instance +0x2C is the array's first pointer slot
8001E508  sll   $v1, $v1, 2
8001E510  lw    $v1, ($v0)
8001E518  addu  $v0, $v0, $v1      ; return the resolved entry
```

Measured: **217 pointers over the 73 models**, every one resolving inside its own file and
every one landing between `T(0x18)` and `T(0x44)`. The count is 1 in 63 models and 2, 3, 13,
15, 22 or 52 in the other ten. Consecutive targets sit 104 bytes apart in 124 of the 144
gaps. `[target+0x00]` is 2 in 194 of 217, and `[target+0x0C]` — the field the search compares
— takes 1 (73, so every model has a key-1 entry), 203 (19), 0 (18), 204 (18), 2, 3, 101, 102
and a tail of small values.

### One entry is the level's camera

Nothing in `SCUS_945.70` reads an entry's fields, but `overlays/gameeng.bin` does, and it is
readable: sweeping candidate link addresses and scoring how many of its own `jal` targets
land on a function prologue puts it at **0x80078C90** — 88 hits against 15 for the runner-up,
all 571 in-band targets inside the image, and the file then ends at 0x800D7148, which is one
word past the 0x800D7144 its own header word +0x10 holds. It calls 0x8001E48C three times,
with key 1 twice and key 9 once, and what it does with the answer is build a camera:

```
; 0x80096184 (gameeng.bin) -- s1 = the entry, s0 = 0x80051640
80096184  jal   0x8001e48c
80096188  addiu $a0, $zero, 1       ; (delay slot) look up key 1
80096194  addiu $a1, $s1, 0x30      ; the entry's first point
80096198  addiu $a0, $s1, 0x4c      ; its second
800961A4  addiu $a2, $s0, 0x54      ; -> camera+0x54, the Euler angles
800961AC  jal   0x800153b4          ; the same routine the cutscene camera uses (§9.11.6)
800961BC  lw    $a3, 0x30($s1)      ; and the first point again, as three i32
800961C8  sw    $a3, 0xc($s0)       ;   -> camera+0x0C..0x14, the eye
800961D8  sw    $v0, 0x18($s0)      ; H = 0x200, the projection distance
```

`0x80051640` is the camera §9.11.6 already decodes — 0x8002AF94 loads its +0x74 matrix into
GTE control registers 0..4, its translation into 5..7 and its +0x18 into control 26, which is
`H`. The second caller derives a heading from the same two points instead of a matrix, with
`ratan2` at 0x8001463C:

```
; 0x80097158 (gameeng.bin)
80097168  jal   0x8001e48c
8009717C  lw    $v1, 0x58($a1)
80097180  lw    $a0, 0x3c($a1)
80097184  lw    $v0, 0x50($a1)
80097188  lw    $a1, 0x34($a1)
8009718C  subu  $a0, $v1, $a0       ; [+0x58] - [+0x3C]
80097190  jal   0x8001463c          ; ratan2
80097194  subu  $a1, $v0, $a1       ; [+0x50] - [+0x34]
80097198  addiu $v0, $v0, -0x400    ; a quarter turn off
8009719C  andi  $v0, $v0, 0xfff     ; 12-bit angle, 0x1000 to the turn
```

| Offset | Type | Meaning | Confidence |
| --- | --- | --- | --- |
Every one of the eleven call sites was followed, and between them they touch **ten** of the
entry's 104 bytes:

| Offset | Type | Meaning | Confidence |
| --- | --- | --- | --- |
| +0x0C | i32 | The key the search matches. 1 exists in 73/73. | **confirmed** |
| +0x30..+0x38 | 3 × i32 | Point A, read as a triple at 0x800961BC and landing in the camera's eye slot. **213 of 217 lie inside their own model's bounds.** | **confirmed** (a position) / *likely* (the eye) |
| +0x3C, +0x40 | i32, u16 | Read by `warp.bin` 0x800B7FE4 and `menu.bin` 0x800B598C, and **0 in 217/217** — so code reaches them and never finds anything there. | **confirmed** (always zero) |
| +0x4C..+0x54 | 3 × i32 | Point B, 0x1C past A, differenced against it by 0x800153B4. | **confirmed** (a position) |
| +0x58 | i32 | A non-negative scalar in 217/217, and **equal to the distance between A and B to the unit in 137 of 217**, within 2 % in 146. Consistent with a cached distance; 71 entries disagree by more, up to 36 %, so it is not simply that. | **confirmed** (non-negative, and what it usually equals) / ?unknown? (why the rest differ) |
| +0x74 | i32 | Read by `warp.bin` 0x800B8488 alongside +0x58. | **confirmed** (that it is read) / ?unknown? (meaning) |

The other ~78 bytes are touched by none of the eleven.

### The keys, and who asks for them

The list is a **table of named points**, and the names are the keys. 0x8001E48C has **eleven
call sites** across the disc — six in `warp.bin`, three in `gameeng.bin`, one each in
`oxide.bin` and `menu.bin` — and between them they ask for four things:

| Key | Asked by | What it reads |
| --- | --- | --- |
| 1 | `gameeng.bin` 0x80096184, 0x80097168; `warp.bin` 0x800B97B4 | +0x30 as the camera eye (§8.5 above), +0x34/+0x3C/+0x50/+0x58 for a heading |
| 2 | `warp.bin` 0x800B53BC, 0x800B8488, 0x800B99D0 | +0x30 as a triple, +0x58 |
| 9 | `gameeng.bin` 0x80097254 | the same heading pair as key 1 |
| 0x65..0x70 | `warp.bin` 0x800B5404, in a twelve-iteration loop | +0x30/+0x34/+0x38 |

That last row is the useful one. `warp.bin` walks `s1` from 0 to 11, skips a slot whose
`[s2+0x14]` is zero, and looks up `s1 + 0x65` — **keys 101 to 112**. The corpus matches
exactly: keys in that range occur in **seven models and no others**, and those seven are
`warp_room1..5` and `demo_hub1..2`, which are precisely the rooms `warp.bin` drives. Nor does
every room carry all twelve — 101, 102, 103, 111 and 112 are in all seven, 104/105/107/108 in
five, 106 in four, 109 in two and **110 in none** — which is what a loop that tests before it
asks is for.

So a warp room stores up to twelve named points and the overlay reads their positions. What
the room does with them is `warp.bin`'s business and not traced here; the remaining keys
(0, 3, 203, 204) have no call site among the eleven, so **I could not validate what asks for
them, which is not evidence nothing does**.

Two things are deliberately not claimed. The heading at 0x80097158 reads +0x34/+0x3C and
+0x50/+0x58 — the *second* and *fourth* words of each point rather than the first and third —
so either the points carry more than three words or the heading is taken in a different
frame; nothing read so far settles which. And the cutscene path passes 0x800153B4 its eye as
`a0` (§9.11.6) while `gameeng.bin` passes the point that ends up in the eye slot as `a1`, so
one of the two takes the difference the other way round. Everything else in the entry is
?unknown?.

### The tags at +0x74, and how a warp room finds a door

The engine can look a placement up by these bytes. `0x8001DCE0` takes a byte index and a
value, walks the runtime records along the `+0x5C` chain the loader built, and returns the
first whose selected tag matches:

```
8001DCE0  lw    $v1, 0x1c($v0)    ; the runtime record array
8001DCEC  addu  $v0, $v1, $a0     ; a0 selects which of the four bytes
8001DCF0  lbu   $v0, 0x64($v0)
8001DCF8  beq   $v0, $a1, ...     ; a1 is the value wanted
8001DD00  lw    $v1, 0x5c($v1)    ; otherwise the next record
```

`warp.bin` uses the first two, and bounds them:

```
800B51EC  lbu   $v0, 0x65($s2)
800B51F4  addiu $s1, $v0, -1
800B51F8  sltiu $v0, $s1, 0xc     ; +0x75 is 1..12
800B5204  lbu   $v0, 0x64($s2)
800B520C  addiu $v1, $v0, -1
800B5210  sltiu $v0, $v1, 5       ; +0x74 is 1..5
```

The corpus says what they key. In `warp_room1` every placement carrying a tag has a
**unique (+0x75, +0x74) pair**, and the pattern is plainly a room's doors:

| +0x75 | +0x74 present | objects |
| --- | --- | --- |
| 1..5 | 1, 2 and 4 | three placements each — 0x5003/0x5002/0x5012 for slot 1, and so on |
| 10, 11 | 1 and 2 | two each |
| 20 | 5 | one |

Every warp room does the same, with `+0x75` running 1..N for its own N level slots —
five in `warp_room1`, six in `warp_room2` and `warp_room5`, seven in `warp_room3`, eight in
`warp_room4` — and 10, 11, 12 and 20 for the ones that are not level slots. `+0x74` is
always 1, 2 or 4 on a level slot, so a slot is three placements the game can address
separately.

What each part *is* on screen is ?unknown? — that would need the emulator, not the file. What
is settled is the addressing: two tags, a unique key, and an engine call that finds a
placement by them.

### Flag bit 25 and the threshold at +0x9C

The draw path reads the record once more, right before it reaches for a texture:

```
; 0x80019E98 — s4 = the flag word, s3 = the runtime record
80019E98  lui   $v0, 0x200
80019E9C  and   $v0, $s4, $v0     ; bit 25
80019EA0  beqz  $v0, 0x80019eac
80019EA4  addiu $a2, $zero, 1     ; (delay slot) clear -> the threshold is 1
80019EA8  lh    $a2, 0x68($s3)    ; set   -> the record's own, from +0x9C
80019EB4  lh    $v0, 0x46($v0)    ; against the render context's +0x46
80019EBC  sltu  $v0, $a2, $v0
80019EC0  beqz  $v0, 0x80019ef8   ; not smaller -> nothing is drawn
80019ED8  lw    $v0, 0x18($v1)    ; smaller -> the texture descriptor array
80019EE0  jal   0x80029d28        ;   and on into the textured path
```

The corpus confirms the pairing without an exception. **Bit 25 clear ⟺ +0x9C is zero**: 789
records have the bit clear and all 789 have a zero there, 1900 have it set and all 1900 have
a non-zero. So the bit is not a mode, it is a "this record states its own threshold" marker,
and the loader's copy to runtime +0x68 is what the compare reads.

**It is an ordering-table index.** What the threshold is measured against gives the units.
0x80018EFC writes `ctx+0x46` in the run that fills the rest of the context, and the few
instructions before it say what the number is:

```
80018DAC  lui   $a1, 0xff
80018DB0  ori   $a1, $a1, 0xffff   ; 0x00FFFFFF -- the OT tag's next pointer
80018DB4  lui   $a2, 0xff00        ; 0xFF000000 -- and its length byte
80018DB8  lw    $a0, 0x1c($s1)     ; the segment's length
80018DBC  lw    $v0, 0x18($s1)     ;   and its start
80018DC0  lw    $a3, -0x4974($s5)  ; the ordering table itself
80018DF8  sll   $v1, $v1, 2
80018E04  addu  $v1, $v1, $v0      ; table + 4*(start + length)
80018E08  lw    $v0, -4($v1)       ; ... - 4: the deepest slot
80018E14  or    $v0, $v0, $s0      ; link the primitive in
80018E18  sw    $v0, -4($v1)
80018EEC  sw    $v1, 0x40($s3)     ; ctx+0x40 = table + 4*start
80018EF0  lhu   $v0, 0x1c($s1)
80018EF8  addiu $v0, $v0, -1
80018EFC  sh    $v0, 0x46($s3)     ; ctx+0x46 = length - 1, the deepest index
```

The two masks are the PS1 ordering table's tag format to the letter, and the
`lw` / `and` / `or` / `sw` around them is a primitive being linked into it. So `[s1+0x18]`
and `[s1+0x1C]` are the segment's start and length, `ctx+0x40` is its base and `ctx+0x46` its
last valid index — and the `sltu` at 0x80019EBC is a **bounds check on a depth bucket**.

That makes a placement's `+0x9C` the ordering-table index it draws into: how deep in the
depth sort the piece is placed. The values fit — 256, 512, 384, 128, 51 — and so does the
awkward one: 93 records hold 0xFFB4, which `lh` sign-extends into a value `sltu` can never
find smaller, so those placements are bounds-checked out and draw nothing.

And the segment length is **not in this format at all**, which is the right way for that
question to end. `s1` is 0x80018B08's own argument, stored to `ctx+0x10` on entry, and the
routine has **45 callers** — one in `oxide.bin`, six in `warp.bin`, sixteen in `menu.bin`, one
in `crate.bin` and twenty in `gameeng.bin` — each handing it a static render descriptor from
its own data (gameeng's 0x8008FBA0 passes 0x8009EAC4). So the ordering table's segment is
chosen by whatever pass is drawing, per view, and an index's absolute scale is a property of
the renderer rather than of the file. A placement states which bucket it wants; the engine
states how many there are.

### The id is the same id the draw dispatcher takes

The handler the loader installs walks straight into §8.3's dispatcher, with the record itself
as the fourth argument so the transform travels with the id:

```
; 0x8001DD50 — s1 = the runtime record
8001DD64  lw    $a2, ($s1)         ; the flag word
8001DD68  lw    $v0, 0x6c($s1)     ; the owner
8001DD6C  andi  $v1, $a2, 0x8000
8001DD70  lw    $s0, 0xc($v0)      ; the model base
8001DD74  beqz  $v1, 0x8001dda0    ; bit 15 clear -> nothing is drawn
8001DD90  lhu   $a0, 0x74($s1)     ; the id, from record +0x88
8001DD98  jal   0x80019a60         ; a1 = model, a2 = flags, a3 = the record
```

`0x80019A60` is the prologue of the routine whose body at 0x80019AD0 splits on `id & 0x7000`
(§8.3). So a placement record names either an **object** (0x5000, 2644 records) or a **clip
and a frame** (0x4000, 45 records), and every one of the 2689 resolves inside its own table.
The clip form packs two fields, which is why a bare `id & 0xFFF` misreads it:

```
; 0x80019B1C — the 0x1000 and 0x4000 namespaces share this path
80019B1C  andi  $v1, $s2, 0xf80
80019B20  lw    $v0, 0x40($s1)     ; the clip count
80019B24  sra   $a1, $v1, 7        ; clip index = (id & 0xF80) >> 7
80019B28  slt   $v0, $a1, $v0      ; bounds-checked against it
80019B40  sll   $v0, $v0, 3        ; 24 * index into T(0x44)
80019B58  bgez  $s4, 0x80019b6c    ; flag bit 31 clear ...
80019B60  lh    $a1, 0x72($s3)     ;   ... set: the live frame cursor (§9.7)
80019B6C  andi  $a1, $s2, 0x7f     ;   ... clear: frame = id & 0x7F
```

### This is what stands a level up

Measured over the 73 models that have one:

| | |
| --- | --- |
| placement records | 2689 |
| … drawn (flag bit 15) | 2689 / 2689 |
| … naming an object, 0x5000 | 2644 |
| … naming a clip and a frame, 0x4000 | 45 |
| … with a rotation that is not the identity | 704 |
| … with a non-zero position | 2120 |
| records sharing an id with another record | 668 |
| … pairs of those with the same transform | **0** |
| objects with a mesh in this file | 1971 |
| … that a record names | 1875 |

The 668 figure is what settles that the transform composes with the object's own
coordinates rather than replacing them: `boss_oxide/arena.mdl` draws 26 objects from 178
records, `polar_polar/arena.mdl` 16 from 54, and no two records that share an id share a
transform, so the copies cannot be meant to land on one another. The correlation runs the
way that implies, too — of the objects a record moves, 1404 are authored within 600 units of
the origin and are placed by their record, while 449 are authored out in room coordinates
and their record leaves them alone.

**A reader that ignores this list draws a level's set piled on the origin.** Pogo Painter's
play grid is 72 records over a handful of tile objects; without them the arena has a hole in
the middle and its props stand in one heap at the centre.

96 objects are named by no record at all, in 13 of the 73 models — 16 in each
`balls_crash` arena, 6 to 9 in each warp room. What draws them, if anything, is ?unknown?:
the scene nodes of §9.11 are the other thing in the file that names meshes, and over the
whole corpus **no mesh is both named by a node and named by a record** (122 have a node,
1861 have a record, the sets do not meet).

## 8.6 The block a hub appends after its clip table

Found by walking the archive byte by byte and asking what nothing claims. Seven models keep
something past `T(0x44)` with **no clips at all**, so the payload that would be an animation
blob in any other file is this instead: `demo_hub1`, `demo_hub2` and `warp_room1..5`, 242 KB
between them, and nothing else in the archive has one.

**It begins on a CD sector boundary, exactly where `i32@0x50` points.** `i32@0x50` is a
multiple of 0x800 in exactly **8 of the 400 models**. Seven are these; in all seven
`i32@0x50 == T(0x44)` exactly, and the block's start is 0x800-aligned both inside the file and
at its absolute offset in the DAT. The eighth is `arena/boss_oxide/chaselevel.mdl`, the one
model §2.1 already records as rounding its 0x50 up.

> An earlier revision drew a conclusion from that and it was wrong. It said the block sits
> *past the resident image* and so nothing could reach it, which would have explained every
> failed search below in one stroke. It does not: **all 1037 animation blobs in the archive
> also start past `base + i32@0x50`**, and the game plainly reads those. Being past the
> boundary rules nothing out. Nor does the alignment argue *for* the block being streamed: §1.1
> shows the byte-range reader can only start on a 2048-byte boundary, so **everything** the game
> loads this way is 0x800-aligned and the property distinguishes nothing.
> The alignment measurement stands; the inference built on it does
> not, and the failed searches are still just failed searches.

### The block is a sequence, not one structure

The header below is the **first of several**. The same 0x34 bytes recur inside the block, always
on a **2048-byte** boundary, and the signature that finds them is `i32@+0x00 == 0`,
`i32@+0x04 == 0`, `u16@+0x0A == 4`, `u16@+0x0C == 4`, `i32@+0x14 == 32`, and four ascending
offsets `p0 < p1 ≤ p2 < p3` that land inside the block.

> **The alignment was 4096 here until it was measured.** An earlier revision of this section
> looked for sub-blocks only at 4096-byte boundaries because the first several happened to sit
> there. That found 27 of them and left up to 36,864 bytes per model apparently unreachable, and
> the section went on to explain that leftover as material the four header offsets merely
> prefixed. Scanning every 4-byte offset instead finds **38**, every one still starting on a
> multiple of 2048 — the eleven that were missed sit at *odd* multiples, which a 4096 scan can
> never land on. The unreachable material was sub-blocks all along.

The index at `+0x0E` is what makes that check possible, and it is the reason to trust the new
count rather than the old one: it runs **1..N with no gaps in every one of the seven files**.
Under the 4096 scan it read 1, 6, 8 in `warp_room4` and 1, 2, 5, 6 in `warp_room5` — the gaps
were the missing sub-blocks announcing themselves, and they line up exactly with which files had
bytes left over. The three files whose index sequence was already complete were precisely the
three with nothing unreached.

A sub-block's own extent is what its header declares: from its 2048-aligned start to
`p3 + (p3 − p2)`, the end of its last array. Everything else in the block is one of two other
things — the run from that end to the next 2048 boundary, or a region no sub-block reaches at
all. All three columns are byte counts and the three add up to the block exactly, 7/7.

| Model | Block | Sub-blocks | Indices at `+0x0E` | Inside a sub-block | Slack | Reached by none |
| --- | --- | --- | --- | --- | --- | --- |
| `demo_hub1` | 12,932 | 3 | 1–3 | 11,796 | 1,136 | **0** |
| `demo_hub2` | 12,932 | 3 | 1–3 | 11,796 | 1,136 | **0** |
| `warp_room1` | 28,600 | 5 | 1–5 | 27,068 | 1,532 | **0** |
| `warp_room2` | 31,564 | 6 | 1–6 | 27,988 | 3,576 | **0** |
| `warp_room3` | 54,144 | 7 | 1–7 | 50,996 | 3,148 | **0** |
| `warp_room4` | 67,324 | 8 | 1–8 | 65,092 | 2,232 | **0** |
| `warp_room5` | 35,260 | 6 | 1–6 | 31,564 | 3,696 | **0** |

**38 sub-blocks in all, and the block is fully accounted in 7 of 7**: every byte of all 242 KB is
either inside a sub-block or slack before the next 2048 boundary. Nothing is unreached, and no
two extents overlap — the 748-byte overlap an earlier revision reported in `warp_room3` was an
artifact of the same 4096 assumption, which mis-sited that file's sub-blocks entirely.

**The slack is entirely zero.** Measured as the run from a sub-block's declared end to where the
*next sub-block starts* — rather than to the next 2048 boundary, which is what an earlier
revision measured and which silently counted the next sub-block's own header as slack —
it comes to **16,456 bytes, not one of them non-zero**. So wherever `p3 + (p3 − p2)` fits, it is
consistent with being the true end, and the claim in an earlier revision that four sub-blocks
left 5,158 non-zero bytes behind them was an artifact of that mis-measurement.

Where it does not fit is the real residue, and it runs the other way from what that revision
said: the rule **over**-reads, never under-reads. In **11 of the 38** the declared end runs past
the next sub-block's start — or past the end of the block itself — by 188 to 1,844 bytes:

| | Overrun |
| --- | --- |
| `demo_hub1`/3, `demo_hub2`/3 (last) | 292 |
| `warp_room1`/5, `warp_room2`/6, `warp_room5`/6 (last) | 428, 424, 420 |
| `warp_room3`/7, `warp_room4`/8 (last) | 808, 1,844 |
| `warp_room3`/1, `warp_room3`/5 | 448, 300 |
| `warp_room4`/4, `warp_room4`/5 | 188, 608 |

Seven of the eleven are the last sub-block of their file, where the overrun is past the block's
own end, so the file itself refutes the length there. The last array cannot be `p3 − p2` bytes
long in any of the eleven, and how long it really is is ?unknown?. Everything up to `p3` is
accounted for in all 38.

Clipping each extent at the next sub-block and adding it all up closes the block completely:
across the seven files' 242 KB there is **not one non-zero byte outside a sub-block**. The
16,456 bytes that fall outside one are zero, every one of them. What the sub-blocks *mean* is
still open — see the reader searches below — but where they begin and end no longer is.

Counts recur across models — 167 and 206 each appear in three of the seven — but **no two
sub-blocks are byte-identical**, so what repeats is the size of the thing, not the thing.

Its first header, and the shape every one of the 27 shares:

| Offset | Type | Measured |
| --- | --- | --- |
| +0x00, +0x04 | i32 | 0 in 7/7 |
| +0x08 | u16 | a count, 167..943 over the sub-blocks. It counts the **index** entries, not the vertices: the equal arrays hold `[+0x08] − 1` or `− 2` u16 each, while the number of 8-byte slots between the vertex start and `p0` minus `[+0x08]` takes **21 different values across the 27** measured before the count rose to 38 — so it is unrelated to the vertex array's length. |
| +0x0A, +0x0C | u16 | **4 and 4 in 38/38.** They are the high halves of the i32 at +0x08 and +0x0C, so the words read `(4 << 16) \| count` and `(index << 16) \| 4`. |
| +0x0E | u16 | **The sub-block's number within its file**, running 1..N with no gaps in 7/7 — 1–3, 1–3, 1–5, 1–6, 1–7, 1–8, 1–6. This is the field that catches a missed sub-block: a gap in the sequence is one the scan did not find, and finding all 38 closed every gap and every unreached byte at once. |
| +0x10 | i32 | **Where the vertex array starts, biased by 0x24.** Across all 27 sub-blocks the 8-byte group at `[+0x10] + 0x24` is vertex-shaped and the group before it is not — **27/27** — and `p0 − ([+0x10] + 0x24)` is a multiple of 8 in **27/27**, so the array runs from there in whole records. Values 104..300. |
| +0x14 | i32 | **32 in 7/7** |
| +0x18..+0x24 | i32 ×4 | four ascending offsets, every one landing inside the block in 38/38. **The first and last gaps are equal in 36 of the 38** — `p3 − p2 == p1 − p0` exactly — so a sub-block holds two arrays of the same size with a smaller one between them. The two exceptions are `warp_room4` index 7 (992 against 1028) and `warp_room2` index 6 (332 against 464), and they matter: the equality was part of the search signature until they were found, which is why both of those sub-blocks were invisible and their files looked incomplete. Dividing an equal gap by 2 gives `[+0x08] − 1` or `− 2`, and the one-or-two shortfall is unexplained. |
| +0x28..+0x30 | i32 | Zero in most sub-blocks. `+0x28` is non-zero in **10 of the 38** — 2044 three times, then 2384, 2752, 2844, 3156, 3160, 4836, 5096 — and `+0x2C` in **2** (2712, 8744). Every value lands inside its own sub-block. An earlier revision recorded these as "0 in 7/7", which was true only of the first sub-block of each file. ?unknown? |

What the offsets reach has the **shape** of geometry, which is as far as measurement goes.
The first is 8-byte records laid out like a vertex record (§4.2) — `demo_hub1` opens
`636, −171, 3169, 0` then `557, 4, 3065, 1` — and the other three are lists of small ascending
integers, the shape an index list has. Those records **start well before `p0`**, not at it: at
`p0 − 48` `demo_hub1` is still reading `(783, −57, 2740, 1)`, `(791, −103, 2739, 0)`,
`(624, −19, 2818, 0)`, and three more follow `p0` itself before the small integers begin. Where
the run *starts* is the `+0x10` rule above — `demo_hub1`'s first sub-block begins its vertices
at 160 with `[+0x10]` at 124, `warp_room1` at 164 with 128, and the bias is 0x24 in 27 of 27.

One alignment trap is worth recording, because it inverted the reading once. The records are
8-byte aligned to **where the array starts**, not to the 0x34 header, and `p0` is not always a
multiple of 8 from the block start — `warp_room1`'s is 6204. Grouping from 0x34 instead shifts
every record by four bytes and turns a solid run of vertices into noise: the same region read
that way scored 4 % vertex-shaped, and read from the right anchor it is an unbroken run of 250.

Between 42 and 109 of those records per model, and
their coordinates fall inside the room rather than around it: `warp_room1` spans
x[3, 1892] y[−3949, 1893] z[−119, 1894] where its drawn extent reaches ±19000 on the sky dome
alone.

**Everything above is corpus measurement.** Nothing here is traced to code, and five
separate routes to the block have now been searched without success:

* **`base + [base+0x50]`**, the resident-image end. Looked for `lw rX, 0x50(rY)` followed by
  an `addu` putting it back on the same base — the shape of resolving it — across the
  executable and all 15 overlays. **Zero sites.**
* **`T(0x44)`**, which is where the block begins when a model has no clips, and which is a
  landmark real code does resolve: **11 sites** do, one being the 0x4000 id branch at
  0x800156A8 and the rest a family of clip lookups at 0x80015F94..0x80016B88. Every one of
  them bounds its walk by `[model+0x40]`, the clip count — and that is **0 in all seven**
  models with this block, so the loops run zero times and never reach it.
* **The +0x0C list.** All eleven call sites of the entry lookup are read above; none yields
  an address in this region.
* **A 2048-strided cursor**, which is what walking these sub-blocks would look like given that
  every one of the 38 starts on a multiple of 0x800. Searched for `addiu rX, rX, 0x800` with
  the same register on both sides across the executable and all 15 overlays: 15 sites in the
  executable and 172 across the overlays, three of them in `warp.bin`. **None is a cursor.**
  They bias a value rather than step a pointer — `warp.bin`'s 0x800B9604 is
  `lh $v0, 2($v0)` then `addiu $v0, $v0, 2048` then `sh $v0, 18($a2)`, which is 0.5 added in
  12-bit fixed point, and the other two have the same shape. A stride would have to be a
  pointer that is then dereferenced; none of these is.

* **`warp.bin` itself**, the obvious suspect, since it drives exactly these seven rooms. It
  is not the reader, and the reason is structural: **the overlay never holds a raw model
  base.** A model is always reached as `[owner+0x0C]`; eleven sites in `warp.bin` load a
  `+0x0C` and then read fields through it, three of those touch an offset an MDL header uses,
  and all three were read — 0x800B5EDC and 0x800B72D0 index two-field runtime structs, and
  0x800B70F8 hands its pointer straight to the engine at 0x8001E41C and works on what comes
  back. Everything the overlay knows about a level arrives through the engine's runtime
  records: it resolves an object once (0x800BB60C), draws once (0x800BBEBC) and asks the
  +0x0C list six times. It never has the file, so it cannot be reading this out of it.

So: **I could not validate that anything reads it, and that is not evidence it is unused.**
242 KB in seven files, with a header identical in 7/7 down to its constants and two
equal-sized arrays inside it, is not what dead space looks like. The tempting reading — §8.4
shows a level carries no collision volumes, so a floor must be described somewhere, and this
is room-shaped data in the only files that are rooms — remains **untested**.

Ruling `warp.bin` out does **not** narrow the search to the engine, either. The obvious next
step was to say a mode overlay never sees a raw model, and §14 records why that is false:
`crate.bin` at 0x800B4BDC reads a model's stamp, mesh count and first header directly. All ten
such sites have since been followed and none reads an undocumented field — but the search
space stays the whole disc.

Two tidy explanations have now been offered for this block and both were wrong: that a mode
overlay could not see it, and that it sits past what gets loaded. Neither survived contact
with a measurement. The block is simply not accounted for.

---

# 9. Animation

Animation is **55.5 % of all MDL bytes** — 17,669,330 of 31,821,508 over the 400 models — and
it is *vertex* animation, not skeletal. There is no joint, no bind pose, no weight per bone.
A clip stores whole poses as index arrays into a pool of 6-byte vectors, and the runtime
expands one pose per displayed frame into a side buffer that the rasteriser reads instead of
the mesh's own vertex pool.

| Quantity | Count |
| --- | --- |
| Models carrying animation | 225 of 400 |
| Clips (24-byte descriptors) | 1037 |
| Frame records | 49,167 |
| Distinct keyframes | 13,652 |
| Frames that copy a keyframe / that blend two | 13,652 / 35,515 |
| Vertices decoded over every frame of every clip | 18,197,611 |
| Blob bytes | 17,669,330 (326 .. 222,972 per clip) |
| Vertices per animated mesh | 6 .. 643 |
| Frames per clip | 1 .. 620 |

Every count in this chapter comes from decoding all 1037 clips of the retail archive.

## 9.1 The clip descriptor table (`model + 0x44`)

`i32@0x40` records of **24 bytes** at `T(0x44)`, no count prefix. §8.2 describes the same
table as a generic "sub-file directory"; that is what it is structurally — a byte range the
loader pulls into RAM — but every one of the 1037 records in the corpus is an animation clip.

| Offset | Type | Name | Meaning | Confidence |
| --- | --- | --- | --- | --- |
| +0x00 | u32 | `start` | First byte of the blob, **absolute from the file base**, not self-relative. 0x800-aligned in 1037/1037. | **confirmed** |
| +0x04 | u32 | `end` | One past the last byte. `start < end <= filesize` in 1037/1037. | **confirmed** |
| +0x08 | u32 | `frame_count` | Number of 16-byte frame records in the blob. Range 1..620. The draw path bounds-checks the requested frame against it. | **confirmed** |
| +0x0C | i32 ptr | `ptr_mesh` | Self-relative pointer to the **mesh header this clip drives**. Resolves to a mesh header in 1036/1037 (§9.10). | **confirmed** |
| +0x10 | u32 | `name_hash` | `sum(name[i] * (i+1))`, 1-based, over a NUL-terminated ASCII name the caller supplies at runtime. 243 distinct values across the 1037 clips. | **confirmed** (that it is that hash) / *likely* (which word produced it) |
| +0x14 | u32 | `resident` | Runtime pointer slot: the loader stores the resident blob address here. 0 on disk in 1037/1037. | **confirmed** |

The loader is the clearest single proof of the record layout: it reads +0x00 and +0x04 as a
byte range, allocates that many bytes, and stores the buffer in +0x14.

```
; 0x80015F60 — load the clips of model a0 whose names appear in the list a1
80015F90  lw    $v1, 0x40($s3)      ; clip count
80015F94  lw    $v0, 0x44($s3)
80015F9C  addiu $v0, $v0, 0x44
80015FA0  addu  $s4, $s3, $v0       ; s4 = T(0x44)
80015FA4  sll   $v0, $s2, 1
80015FA8  addu  $v0, $v0, $s2
80015FAC  sll   $v0, $v0, 3         ; 24 * (count-1)          <-- 24-BYTE STRIDE
80015FBC  jal   0x80012e28          ; allocate
80015FCC  lw    $a1, ($s1)          ; record +0x00 = start
80015FD8  sw    $s0, 0x14($s1)      ; the buffer -> record +0x14   <-- RESIDENT SLOT
80015FF0  lw    $v0, 4($s1)         ; record +0x04 = end
80015FF4  lw    $a1, ($s1)
80016000  subu  $a1, $v0, $a1       ; length = end - start
80015FFC  jal   0x80011498          ; read that byte range into the buffer
80016008  addiu $v0, $s1, -0x18     ; walk one record backwards
```

and then, over the same table, frees every clip whose name the caller did *not* ask for:

```
8001603C  jal   0x8001534c          ; hash(the caller's name string)
80016044  lw    $v1, -4($s1)        ; record +0x10                 <-- NAME HASH
8001604C  beq   $v0, $v1, 0x80016090  ;   match -> keep the blob
80016064  lw    $s0, ($s1)          ;   no match: free it and
80016080  sw    $zero, ($s1)        ;   zero the resident slot
80016084  addiu $s1, $s1, 0x18
```

The hash itself is a plain weighted sum, which is why it collides so freely:

```
; 0x8001534C — hash(a0 = NUL-terminated string)
8001534C  move  $a2, $zero          ; accumulator
8001535C  addiu $a1, $zero, 1       ; multiplier, starts at 1
80015360  lbu   $v0, ($a0)
80015368  mult  $v0, $a1
8001536C  addiu $a0, $a0, 1
80015374  addiu $a1, $a1, 1
80015380  addu  $a2, $a2, $v0       ;   acc += byte * position
```

Three more routines (0x80016274, 0x800164A8, 0x80016584) take a string and scan the table for
a matching +0x10, so a clip really is addressed by name at runtime.

**The names themselves are not in the MDL, and not in the EXE — they are in the code
overlays, and each overlay states its own list.** Every `overlays/modes/*.bin` opens with a
count word and then a table of NUL-terminated names padded to 4-byte alignment, ending where
a pointer array begins:

```
warp.bin+0x00  6a 00 00 00                 count
        +0x04  "BREATHE\0"                 the mode's animation names,
        +0x0C  "MEDIUM\0\0"                each padded to a 4-byte boundary
        +0x14  "ATTACK\0\0"
        +0x1C  "JUMP\0\0\0\0"
        +0x24  "KICK\0\0\0\0"
        +0x2C  80 0b 49 c8 ...             pointers: the table has ended
```

Thirteen tables carry **35 distinct names**, from `crate.bin`'s twelve down to `oxide.bin`'s
four. This is a far better source than scanning an overlay for identifier-shaped strings,
which drags in the credits (`WONG`, `PATEL`, `GALLAGHER` all collide with real clip hashes)
and cannot tell a coincidence from a name. Four further words — `LOSE`, `LOSE_BREATHE`,
`START`, `WIN_BREATHE` — appear as strings elsewhere in the overlays but in no head table.
The 39 together name **701 of the 1037 clips**, and only two hashes stay ambiguous:
`BANK`/`HOLD` and `BARGE`/`SLIDE`, both members of each pair genuinely in the tables.

Words this project previously guessed at and that appear **nowhere** in the game — `BOUNCE`,
`FLY`, `HOP`, `LAUGH`, `OPEN`, `SINK`, `SLEEP`, `STOP`, `SWING`, `TURN`, `WALK` — have been
dropped. Dropping `HOP` settles the old `HIT`/`HOP` collision in `HIT`'s favour. The
distribution over the surviving list still reads like an animation set:

| Word | Clips | Word | Clips | Word | Clips |
| --- | --- | --- | --- | --- | --- |
| BREATHE | 114 | ATTACK | 19 | FLIP | 9 |
| WIN | 108 | TAUNT | 18 | LOSE | 8 |
| HIT | 55 | BANK / HOLD | 18 | WIN_BREATHE | 8 |
| RUN | 40 | TAUNT_A | 16 | TRANS | 8 |
| FALL | 34 | LIGHT | 16 | RECOIL | 8 |
| JUMP | 31 | PICKUP | 12 | MINE | 8 |
| MEDIUM | 26 | SWIM | 11 | IDLE1 | 8 |
| DIE | 25 | HOLD_THROW | 10 | SKATE | 8 |
| BARGE / SLIDE | 23 | HOLD_SLOW | 10 | TAZING | 8 |
| PUSH | 21 | IDLE_A | 10 | DAZED | 8 |

Against the head tables alone, only `BANK`/`HOLD` and `BARGE`/`SLIDE` collide; every other
name in the list is the single word in the game that produces its hash. That still does not
make a name *the* name of a given clip — a mode's table says which names that mode asks for,
not which clip in which model answers — so keep treating the hash as the identity. But the
words are now the game's own, not a wordlist this project invented. **confirmed** that the
hash is of a name and that these 39 words are the ones the game hashes; *likely* for the
pairing of a particular word with a particular clip.

## 9.2 The blob

The loader copies `[start, end)` into one buffer, so **everything inside a blob is
self-relative** and the buffer relocates freely. The region is tiled exactly, in this order,
with no gaps and nothing unaccounted for:

```
blob+0x00  i32          pointer to the position pool (0 = use the model's, §9.5)
blob+0x04  16 bytes  x frame_count      the frame records          (§9.3)
           variable                     the auxiliary blocks       (§9.8)
           round4(0x14+2V) x keyframes  the keyframes              (§9.4)
           6 bytes   x pool_count       the position pool          (§9.5)
```

**confirmed.** The strict test — `4 + 16*frames + Σ(aux sizes) + keyframes*stride +
(end − pool) == end − start` — holds in **1037/1037** clips. Component checks, each over the
whole corpus: the first item any record points at begins at exactly `blob + 4 + 16*frames`
(1037/1037); every auxiliary block lies below every keyframe (192/192 clips that have any);
consecutive keyframes are exactly one stride apart (12,615/12,615 gaps); a blob-local pool
starts exactly at `last keyframe + stride` (829/829) and its span is a multiple of 6
(829/829).

Blobs sit past the end of the descriptor table (225/225 models), ascend within the file
(225/225), and each starts on a 0x800 boundary (1037/1037). The bytes between two
blobs are zero in 812/812 gaps; 794 of those gaps are exactly the padding to the next
boundary and 18 reserve one extra sector. The last blob ends 4 bytes before the end of the
DAT entry in 225/225 animated models.

## 9.3 Frame record (16 bytes)

Record *f* is at `blob + 4 + 16*f`. **One record is one displayed frame, not one authored
key** — the timeline is pre-baked (§9.7).

| Offset | Type | Name | Meaning | Confidence |
| --- | --- | --- | --- | --- |
| +0x00 | i32 ptr | `key_a` | Self-relative pointer to a keyframe. Never 0. | **confirmed** |
| +0x04 | i32 ptr | `key_b` | Second keyframe, or 0 when the frame sits exactly on `key_a`. | **confirmed** |
| +0x08 | i32 | `weight` | Blend weight, `0x1000` = 1.0, loaded into GTE IR0. 0 exactly when `key_b` is 0 (49,167/49,167 records agree both ways). Observed range 0 and 0x1D..0xFE2 — never 0x1000. | **confirmed** |
| +0x0C | i32 ptr | `aux` | Self-relative pointer to an auxiliary block, or 0. Non-zero in 5354 of 49,167 records. No read of it was found on the draw path (§9.8). | **confirmed** (that it is a pointer) / ?unknown? (contents) |

```
; 0x80019B1C — the animation branch of the id dispatcher (id namespaces 0x1000 / 0x4000)
80019B1C  andi  $v1, $s2, 0xf80     ; clip index = (id & 0xF80) >> 7
80019B20  lw    $v0, 0x40($s1)      ; clip count -> bounds check
80019B34  lw    $v1, 0x44($s1)
80019B40  sll   $v0, $v0, 3         ; 24 * index (after v0 = index*3)
80019B4C  addu  $s0, $v1, $v0       ; s0 = the descriptor
80019B50  jal   0x80015ab0          ; a3 = *(descriptor+0x14) = the resident blob
80019B70  lw    $v0, 8($s0)         ; frame count -> bounds check
80019B8C  lw    $v0, 0xc($s0)
80019B94  addiu $v0, $v0, 0xc
80019B98  addu  $s0, $s0, $v0       ; s0 = the mesh header this clip drives
80019B9C  sll   $v0, $a1, 4         ; 16 * frame
80019BA0  addiu $v0, $v0, 4         ;      + 4                <-- SKIPS blob+0x00
80019BA4  addu  $t1, $a3, $v0       ; t1 = &record[frame]
80019BE0  lw    $v0, ($t1)          ; +0x00 keyframe A
80019BEC  addu  $v0, $t1, $v0       ;   resolved self-relatively
80019C34  lw    $t0, 0x18($t1)      ; the NEXT record's +0x08  <-- only right at stride 16
```

The `+4` bias appears independently in the script-side resolver, which reaches the same
address from a different id namespace:

```
; 0x80015A78 — id 0x4000 -> the frame record it names
80015A78  lw    $a1, 0x14($a1)      ; the descriptor's resident slot
80015A88  lw    $v1, ($a1)
80015A94  andi  $v0, $a0, 0x7f      ; frame = id & 0x7F
80015A98  sll   $v0, $v0, 4
80015A9C  addiu $v0, $v0, 4
80015AA4  addu  $v0, $v1, $v0       ; blob + 4 + 16*frame
```

So `blob+0x00` is a standalone field belonging to the blob, not field +0x00 of record 0. The
data agrees: validating all 49,167 records under four candidate origins, `blob+4+16f` passes
in 49,167/49,167 records and 1037/1037 clips, while `blob+0`, `blob+8` and `blob−4` pass in
1,925, 3,232 and 0 records and no clip at all.

## 9.4 Keyframe

A keyframe is **the same 0x14-byte bounds block a mesh header carries (§4.1)**, followed by
one `u16` per vertex.

| Offset | Type | Meaning | Confidence |
| --- | --- | --- | --- |
| +0x00 | i16 × 10 | bounds block, interleaved exactly as §4.1: `minX maxY minZ maxX minY maxZ cx cy cz radius` | **confirmed** |
| +0x14 | u16 × V | one entry per vertex: `entry >> 2` indexes the position pool, `entry & 3` is the vertex flag word | **confirmed** |

`V` is **not** stored. Both decoders obtain it by walking the driven mesh's own strip list —
`count + 2` vertices per strip, high byte 0xFF ends the list, identical to §5 — so a decoded
pose is in exactly the same order as that mesh's static vertex pool and can be substituted
for it slot for slot.

```
8001C1E4  lw    $v0, 0x14($a0)      ; a0 = mesh header
8001C1E8  addiu $t4, $zero, 0xff
8001C1F0  addiu $t3, $v0, 0x15      ; strip list + 1 = the HIGH byte
8001C1F4  lbu   $v0, ($t3)
8001C1FC  beq   $v0, $t4, 0x8001c268 ; 0xFF ends the list
8001C204  addiu $v0, $v0, 2         ; count + 2 vertices in this strip
```

Keyframes are laid out back to back at a fixed stride of `round4(0x14 + 2*V)`. Measured:
every gap between consecutive keyframes equals that stride in **12,615/12,615** cases (the 8
clips with a single keyframe have no gap to test), and the padding byte pair present when `V`
is odd is zero in all 441 such clips.

The low two bits are the same flag word the static vertex record stores as its fourth i16
(§4.2). Over every keyframe of every clip, `(entry & 3)` equals the mesh's own flag word for
that vertex slot in **5,352,530/5,352,530** vertices, and in **1036/1036** clips whose
descriptor names a mesh — every vertex of every keyframe, no exceptions. That is
also why two bits are enough — every animated mesh's flags are ≤ 3; the 158 static vertices
in the corpus with a flag word above 3 all belong to meshes that carry no animation.

**The corollary for a writer is severe.** The game draws the *animated* pose (§9.6) — it never
reads the static vertex records back — so at draw time the winding bit comes from these two
bits, not from the mesh. A writer that emits keyframes with zeroed flags puts every triangle
on one winding and shreds the model on screen, while every static-data check still passes:
this exact failure shipped three broken discs during the editor's development, including one
whose static strip list and vertex pool were byte-identical to the original. Clips must carry
the driven mesh's own flag words, slot for slot.

The bounds block is genuine, though nothing in the animation path reads it (both call sites
add 0x14 and move on). Decoding each keyframe and comparing: the stored box is the exact
axis-aligned box of that pose in **11,876** of 13,652 keyframes, contains the pose in 12,993,
and contains it to within 2 units (2/256 of a model unit) in **13,651**. The single outlier is
the truncated clip of §9.10.

## 9.5 The position pool

Records of **6 bytes** — `i16 x, y, z`, no padding — the same records §7.3 documents at
`model+0x28`. There are two sources and `blob+0x00` chooses:

```
80019BF0  lw    $a2, ($a3)          ; a3 = the blob base; blob+0x00
80019BF8  beqz  $a2, 0x80019c08
80019C04  addu  $t2, $a3, $a2       ;   non-zero -> the blob's own pool
80019C08  lw    $v0, 0x28($s1)      ; s1 = model base
80019C10  addiu $v0, $v0, 0x28
80019C14  addu  $t2, $s1, $v0       ;   zero -> the model-wide pool at 0x28
```

829 of 1037 clips carry their own pool; the other 208 fall back to `model+0x28`. A blob-local
pool starts exactly where the last keyframe ends (829/829) and runs to `end`; the model-wide
pool runs from `T(0x28)` to `T(0x08)`. Both are fully used — every index a keyframe emits
lands inside in 828 of the 829 local pools (the exception is §9.10), and across the 40 models
whose clips share `model+0x28`, `6*(max index + 1)` accounts for the entire span exactly in 16
and leaves a 2-byte alignment pad in the other 24.

This closes the framing question §7.3 left open: **one pose is not a fixed number of
vectors.** The pool is a deduplicated bag of positions and a keyframe's `u16` array is what
groups them into a pose, which is why the span ÷ 6 is several times the mesh's vertex count.

## 9.6 Decoding a frame

Two routines, chosen per frame by the stored weight (`0x80019CA4 lw $v0,8($t1)` then `beqz`),
both writing 8-byte vertex records — `i16 x, y, z, flags`, the layout of §4.2 — to a fixed
buffer at **0x80056AC8**.

**Weight 0 — copy (0x8001C1E0).** For each vertex: read the keyframe entry, fetch pool record
`entry >> 2`, store its three halfwords and `entry & 3`.

```
8001C210  lhu   $a0, ($t2)          ; t2 = the keyframe's u16 array
8001C214  addiu $t2, $t2, 2
8001C21C  srl   $v0, $a0, 2         ; pool index = entry >> 2
8001C220  sll   $v1, $v0, 1
8001C224  addu  $v1, $v1, $v0
8001C228  sll   $v1, $v1, 1         ; * 6                       <-- 6-BYTE RECORDS
8001C22C  addu  $v1, $a2, $v1       ; a2 = the pool
8001C230  lhu   $v0, ($v1)
8001C238  sh    $v0, ($a3)          ; -> x
8001C23C  lhu   $v0, 2($v1)
8001C244  sh    $v0, -4($t0)        ; -> y      (t0 = a3 + 6)
8001C248  lhu   $v0, 4($v1)
8001C254  sh    $v0, -2($t0)        ; -> z
8001C240  andi  $a0, $a0, 3
8001C250  sh    $a0, ($t0)          ; -> flags
8001C24C  addiu $a3, $a3, 8         ; output stride 8
```

**Weight non-zero — blend (0x8001C0F0).** Keyframe A's position goes into GTE IR1..IR3, B's
into the far-colour registers RFC/GFC/BFC, the weight into IR0, and one `INTPL` produces the
result:

```
8001C0FC  mtc2  $a0, cop2r8         ; weight -> IR0
8001C15C  lhu   $t4, ($a0)          ; A.x/y/z from the pool
8001C168  mtc2  $t4, cop2r9         ; -> IR1
8001C16C  mtc2  $t5, cop2r10        ; -> IR2
8001C170  mtc2  $t6, cop2r11        ; -> IR3
8001C178  lh    $a0, ($v0)          ; B.x/y/z from the pool
8001C184  .word 0x48C4A800          ; ctc2 -> cop2c21  RFC
8001C188  .word 0x48C3B000          ; ctc2 -> cop2c22  GFC
8001C18C  .word 0x48C2B800          ; ctc2 -> cop2c23  BFC
8001C198  .word 0x4A980011          ; gte:INTPL, bit19 (sf) = 1, bit10 (lm) = 0
8001C1A4  mfc2  $t4, cop2r9         ; read IR1..IR3 back
8001C1B0  sh    $t4, ($t2)          ; store at stride 8, flags from KEYFRAME B
8001C174  andi  $a1, $t1, 3
8001C1BC  sh    $a1, 6($t2)
```

(The COP2 destination is written `cop2rN` above; a capstone dump prints those registers with
GPR aliases — `mtc2 $t4, $t1` is a write to cop2 data register 9, IR1. The three `ctc2` words
are given raw because capstone does not decode them: 0x48C4A800 is `ctc2 $a0, cop2c21`.)

`INTPL` with `sf = 1` evaluates `MAC = (clamp16(FC − IR) * IR0 + (IR << 12)) >> 12` with an
**arithmetic** shift, so per axis:

```
out = A + ((B - A) * weight >> 12)          # >> floors; it does not round
```

That is the whole of the interpolation — positions only. Flags are copied, never blended, and
A and B agree on them in 12,845,033/12,845,033 vertex pairs anyway.

The arithmetic is worth getting exactly right. Over the 35,515 blended frames
(38,535,099 coordinates): every result lies between its A and B values (38,535,099/38,535,099);
neither GTE clamp ever fires (`|B − A|` and the results both stay inside i16, 0 cases); and
flooring differs from round-to-nearest on **10,071,343** coordinates (26 %) and from
truncate-toward-zero on 8,147,278. A reader that rounds is wrong on a quarter of the data.
**confirmed**, with one external premise: that GTE `INTPL` with `sf=1` shifts arithmetically
is documented hardware behaviour, not something the ROM states. The ROM proves only that the
operation is `INTPL sf=1 lm=0` — an exhaustive scan finds exactly two `INTPL` words in the
image, 0x8001C09C and 0x8001C198, and both are the identical word 0x4A980011.

(0x8001C09C belongs to a second, similar routine at 0x8001C008 that blends an already
expanded 8-byte vertex array toward a single constant vector. It has no `jal` caller anywhere
in the image and is not part of the animation path.)

**The decoded buffer is never written back into the mesh.** The rasteriser takes the vertex
array as an argument, and the animated path passes 0x80056AC8 where the static path passes
`T(mesh+0x10) + 0x14`:

```
80019CFC  lui   $v0, 0x8005
80019D00  addiu $s1, $v0, 0x6ac8    ; the decoded-vertex buffer
80019D9C  move  $a1, $s1            ; -> 0x800193A8's vertex argument
80019D54  lw    $v0, 0x10($s0)      ; the static path, for comparison
80019D8C  addiu $s1, $s1, 0x14
```

The buffer is a 0x2038-byte zero region ending at the vtable 0x80058B00 — room for exactly
**1031** vertices, comfortably above the largest animated mesh (643 vertices,
`models/chars/dino/tiny.mdl` mesh 0) and well below the largest static mesh (2428), which
never uses it. A viewer should do the same: draw from the decoded pose and leave the mesh's
own pool alone.

## 9.7 The timeline and playback

**The timeline is pre-baked: one record per displayed frame.** The evidence is in the records
themselves. Every frame with a non-zero weight repeats the previous record's keyframe A
(35,515/35,515); the frames with weight 0 are exactly and bijectively the distinct keyframes
(1037/1037 clips); and within a run of interpolated frames the weight is
`floor(4096 * j / L)` for run length `L` in 13,637 of 13,652 runs. The 15 exceptions are all
final runs that were cut short and match the same ramp for a slightly larger `L`.

The clock confirms the reading. The frame cursor is a single 16.16 fixed-point value at
`instance+0x70` (low half fraction, high half integer frame), written as one word:

```
8001F2FC  sw    $a1, 0x70($s6)      ; the 16.16 cursor
80019B60  lh    $a1, 0x72($s3)      ; the draw path reads the integer half
80019C20  lhu   $a0, 0x70($s3)      ;   and the fraction
```

and the sequence tick advances the owning entity's clock by a single global 16.16 constant:

```
800200D0  lw    $v0, 0xc($s1)       ; entity clock
800200D4  lw    $v1, -0x749c($v1)   ; [0x80058B64]
800200DC  addu  $v0, $v0, $v1
800200E0  sw    $v0, 0xc($s1)
8001EA78  lui   $v0, 1              ; the ONE writer of 0x80058B64
8001EA7C  sw    $v0, -0x749c($v1)   ;   = 0x00010000 = exactly 1.0
```

An exhaustive scan for `lw`/`sw` with immediate 0x8B64 finds exactly those two sites. So the
cursor advances exactly one blob frame per tick, the fraction stays zero, and the sub-frame
blend path at 0x80019C20–0x80019C98 — which would blend the current and next records' weights
and read `record[f+1]` — never runs. It could not safely run on the last frame anyway: no
blob reserves a sentinel record, and in the 845 clips that carry no auxiliary block the first
keyframe begins immediately after the record array, with a zero-byte gap.

**Looping** is a property of the clip's caller, not a flag in the clip — and the caller
can loop a *sub-range*: the wrap runs over `[start, end]`, so a clip can play its intro
once and then cycle from, say, frame 6 for ever. `t0` below is a **play command**:

```
8001F244  lw    $v0, 0x10($t0)      ; command +0x10: mode word
8001F24C  beqz  $v0, 0x8001f288     ;   0 -> loop, else clamp
8001F254  lw    $a0, 8($t0)         ; command +0x08: end frame
8001F258  lw    $v1, 4($t0)         ; command +0x04: START frame
8001F27C  addiu $v0, $a0, -1        ; clamp: hold at (end-1)
8001F298  addiu $v0, $v0, 1         ; loop: start + (elapsed mod (end - start + 1))
8001F2A0  div   $zero, $a2, $v0
8001F2C8  mfhi  $a1
8001F2D8  addu  $a1, $a1, $v1
```

**Where the command comes from.** For node-driven scenes it is authored **in the model
file**, inside the object graph of §8.3. The same node update resolves the graph through
the `model+0x4C` pointer array, selects the node whose time window covers the scene tick,
and takes the play command embedded in the node record:

```
8001F134  lw    $v0, 0x4c($v1)      ; the §8.3 pointer array
8001F144  sll   $v1, $a1, 2         ;   indexed
8001F184  lw    $v0, 4($v1)         ; node +0x04: window start (ticks)
8001F198  lw    $v0, 8($v1)         ; node +0x08: window end
8001F1C8  addiu $t0, $v1, 0x14      ; t0 = node + 0x14  <-- THE PLAY COMMAND
8001F1CC  addiu $a1, $v1, 0x34      ; placement keys from node + 0x30,
8001F1FC  addiu $a1, $a1, 0x4c      ;   stride 0x4C (§9.7 key copy)
```

So, node-relative: `+0x18` loop/play start frame, `+0x1C` end frame, `+0x24` mode
(0 = loop, else hold at end). Scanning the corpus object graphs for that signature —
a sane time window at `+0x04/+0x08`, `start <= end < the model's largest frame count`,
mode ≤ 2, and a plausible first key segment at `+0x30` — finds **262 commands**, all in
cutscene and arena models. 89 of them start past frame 1: starts of 4 (11), 7 (10),
8 (21), 9 (19), 10 (10) and a tail to 27, mode 0 (loop) in 248 of 262. The pair that
answers what a sub-range loop is for: `models/arena/dash_toxic/{arena,crystalarena}.mdl`
both carry `frames 6..113` of a 119-frame clip — the first six frames are an intro the
cycle never revisits. Field roles are **confirmed** (the dispatcher above); the in-file
rows are *likely* individually, being signature-matched rather than parsed from a full
graph walk. For gameplay characters the same wrap code is fed by the minigame overlays,
so their loop points live in code, not in the `.mdl`.

The data is authored for it in half the corpus: of the 1029 clips with two or more frames,
486 end on an interpolated frame, and in **486/486** of those the final record's keyframe B is
frame 0's keyframe A — the tail run blends back into the first pose. The other 543 end sitting
on a keyframe, where wrapping would cut, and read as one-shots.

**There is no root motion in the animation data.** Neither decoder nor the dispatcher adds a
translation, and the keyframe's bounds block is skipped without being read. Object placement
is a separate key track the same node update copies into `instance+0x04..0x1C`
(0x8001F41C–0x8001F450: `[key+0x08..0x10]` -> translation, `[key+0x20..0x2C]` -> rotation),
which 0x8001D894 turns into the GTE matrix before the mesh is drawn.

One flag is worth knowing about because it breaks the "one clip drives one mesh" rule: when
bit 22 (0x00400000) of the instance flag word is set, the mesh id at `instance+0x8E` overrides
the descriptor's +0x0C (0x80019BA8–0x80019BDC, using the same `52*id + 0x24` form as §3).

## 9.8 The auxiliary block (record + 0x0C) — contents unknown

5354 of the 49,167 records point at one, in 192 of the 1037 clips. The blocks are packed
between the record array and the first keyframe, and their size follows from their own first
two halfwords:

```
size = 4 + 8 * u16@+0x00 + 16 * u16@+0x02
```

That reproduces the gap to the next block (or to the first keyframe) in **4734/4734** blocks.
Observed `(n0, n1)`: (1,0) ×2837, (3,0) ×682, (0,1) ×349, (2,0) ×192, (4,1) ×105, (52,0) ×104,
(14,0) ×68, (1,1) ×59.

**No read of +0x0C was found on the draw path.** The only reader found in the image is the
0x4000 id namespace, which hands the block to script code:

```
800156C8  jal   0x80015a78          ; blob + 4 + 16*(id & 0x7F)
800156DC  lw    $v0, 0xc($v1)       ; that record's +0x0C
800156E4  beqz  $v0, 0x80015744     ;   zero -> NULL
800156E8  addiu $v0, $v0, 0xc
800156F0  addu  $v0, $v1, $v0       ; resolved self-relatively
```

so a 0x4000 id names a *(clip, frame)* pair and yields that frame's block — the same role
mesh+0x2C plays for 0x2000 ids (§8.4).

What can be said about the contents without guessing: the 4734 blocks hold 16,958 of the
8-byte records and 602 of the 16-byte ones. Read as `i16`, the 8-byte record's first three
fields span −3021..2944, the same order of magnitude as mesh coordinates (which are ÷256), and
its fourth is 0 in 65 % of records and never exceeds 512. They are **not** mesh vertices —
neither the count nor the values line up with any pose. Blocks are usually per-frame (4734
distinct for 5354 pointing frames) but not always: `models/arena/boss_bear/bigbear.mdl` has
just four distinct 20-byte blocks shared by 273 frames across all its clips, and each of the
four is used by both copy and blended frames. Anything past that is ?unknown? and is
deliberately not guessed at here.

## 9.9 Frame 0 is not a bind pose

Frame 0 has weight 0 in every clip, so it is always a plain copy of a keyframe — but that
keyframe is a different pose from the mesh's own vertex pool. Decoding frame 0 and comparing
with the static positions: identical in only **39 of 1037** clips. Scanning every frame of
every clip, only 43 clips contain any frame that reproduces the static pool exactly. The
static pool at `mesh+0x10` and the clip's poses are two independent things; an animated mesh
simply never draws its own pool while a clip is playing.

## 9.10 Two anomalous clips (the format itself is not in doubt)

* `models/arena/boss_oxide/chaselevel.mdl` clip 0 — the only model with the odd `0x09160026`
  stamp — is **truncated**. Its pool starts exactly where the layout predicts (0x269DC = last
  keyframe + stride) but the keyframe entries reach pool vector 428, which needs 2574 bytes
  where the descriptor leaves 1290; 128 of frame 0's 500 indices fall past the end of the
  159,466-byte DAT entry. It is the only index-range failure among the 829 clips with their
  own pool, and the only keyframe whose stored bounds miss the decoded pose by more than 2
  units (195). A reader must clamp pool indices rather than assume every referenced vector
  exists.
* `models/arena/medieval_mallet/arena.mdl` clip 0 has a +0x0C that resolves to `T(0x2C)`, not
  a mesh header — the only such case in 1037. Its blob is nonetheless perfectly regular: with
  `V = 12` recovered from the 44-byte keyframe stride it tiles exactly, four keyframes at
  0x6104/0x6130/0x615C/0x6188 and a 48-vector pool at 0x61B4. Whether the clip is dead data or
  reaches a different code path is ?unknown?.

## 9.11 Scene nodes: props, actors and visibility

A cutscene is not a pile of meshes at the origin. The object graph of §8.3 carries **nodes**,
each of which puts one thing on stage for a stretch of scene time, and the same node update
that §9.7 describes walks them. A node opens with a **type word**, and the type decides where
its key array begins and how long a key is:

| Type | Keys at | Stride | Id namespace | What it drives |
| --- | --- | --- | --- | --- |
| 3 | node+0x30 | 0x4C | 0x4000 / 0x3000 | an **actor**: a clip, the frames to loop (§9.7), and a placement track |
| 0 | node+0x24 | 0x50 | 0x2000 | a **prop**: one mesh, moved and turned but not posed |
| 2 | node+0x1C | 0x28 | 0x1000 | 12 in the corpus, contents unread |

Across the corpus, 1530 type-0 nodes, 68 type-3 and 12 type-2 have a key array that opens on
their window and tiles it to the tick. The handlers are separate functions in the table at
0x80058B00 — 0x8001F0D4 for type 3, 0x8001EAA4 for type 0, 0x8001EDFC for type 2 — and each
walks its own stride:

```
8001EBD8  addiu $a0, $a0, 0x50    ; type 0, keys from node+0x24
8001EF24  addiu $a0, $a0, 0x28    ; type 2, keys from node+0x1C
8001F1FC  addiu $a1, $a1, 0x4c    ; type 3, keys from node+0x30
```

A prop names its mesh outright. The id at node+0x14 sits in the **0x2000** namespace, which
the dispatcher at 0x80015A48 resolves as `52 * id + 0x24` from the model base — the mesh
header stride, 1-based — so `0x2000 | n` addresses mesh `n - 1`.

**Every key carries a scale**, three components in the rotation's own 4096 = 1.0 fixed point,
at `key+0x3C` for an actor and `key+0x40` for a prop, and the handlers interpolate it between
keys exactly as they do position — each field paired with the same field one stride on:

```
8001F3B0  lw $a0, 0x3c($s4)     ; this key's scale
8001F3B4  lw $a1, 0x88($s4)     ;   against the next key's (0x3C + 0x4C)
8001F454  lw $v0, 0x3c($s4)     ; and the three components land at
8001F45C  sw $v0, 0x20($s6)     ;   entity+0x28..0x30  (s6 = entity + 8)
```

Uka Uka swells and shrinks through his own cutscene on nothing but this track, pulsing
between 0.88 and 1.06 — a scene played without it stands rigid.

**Rotation is slerped, not stepped.** Position and scale go component by component through
the scalar interpolator at 0x80015304; the quaternion goes whole, through a routine of its
own that opens on the two quaternions' dot product:

```
8001F324  addiu $a0, $s4, 0x20    ; this key's quaternion
8001F328  addiu $a1, $s4, 0x6c    ;   and the next key's, one stride on
8001F32C  addiu $a2, $s6, 0x10    ; into entity+0x18
8001F350  jal   0x80020b44        ;   -> 0x80020680: dot product, then slerp
```

Hold the rotation between keys instead and a character snaps from one facing to the next
rather than turning through it.

### 9.11.1 A key list ends at a zero duration — **certain**

Nothing else bounds it: not the node's window, not the keys running consecutively. The
first key's duration is tested before any search, and the search stops at the *next* zero.
Both handlers say it in the same shape, the actor's here and the prop's at 0x8001EB98:

```
8001F1B8  addiu $a2, $v1, 0x30    ; the key array
8001F1BC  lw    $v0, 4($a2)       ; the first key's duration
8001F1C4  beqz  $v0, 0x8001f210   ;   zero -> no search at all: this key, held
8001F1FC  addiu $a1, $a1, 0x4c    ; otherwise walk,
8001F200  lw    $v0, ($a1)        ;   reading the next duration
8001F208  bnez  $v0, 0x8001f1d0   ;   and stop when it is zero
8001F210  move  $a1, $a2          ; the chosen key -> $s4, the pose source
```

So the zero-duration record is a **key**, not a terminator to discard: it is the endpoint the
previous segment interpolates towards, and the pose the node holds once the tick passes it.
100 nodes across the game are nothing but one such key — a node standing still for its whole
window.

Measured on the corpus: 129 scenes, 177 actors, 1790 props, 8393 keys. Requiring a non-zero
duration instead loses all 100 static nodes and the final pose of every other track. In
`level_intro_cortexlab` that is the whole of Cortex: the node holding him on stage for ticks
149..271 is one of the hundred, so he never appears, and the node that carries him off at
272..287 loses the key he shrinks into — so he grows into the sky instead of dwindling out of
it.

### 9.11.2 A node's id is biased by its first played frame — **certain**

The id at node+0x14 is not what the node plays. The handler adds the play range's start to it
and stores the sum as the entity's animation id:

```
8001F2FC  lhu   $v0, ($t0)        ; t0 = node+0x14, the id, unsigned
8001F300  lhu   $v1, 4($t0)       ;   + node+0x18, the range's first frame
8001F308  addu  $v0, $v0, $v1
8001F30C  sh    $v0, 0x74($s6)    ;   -> entity+0x7C  (s6 = entity + 8)
```

That is why ids like `0x3FFF`, `0x3FF7` and `0x3F32` look like they name nothing: each is
biased by its own play start, and the sum lands in the 0x4000 vertex animation namespace,
where §9.1's decoder splits it — `(id & 0xF80) >> 7` indexes the descriptor table at
`model+0x44`, bounds-checked against the count at `model+0x40`:

```
80019B00  addiu $v0, $zero, 0x4000  ; the vertex animation namespace
80019B1C  andi  $v1, $s2, 0xf80     ; the id's clip field
80019B20  lw    $v0, 0x40($s1)      ;   against the clip count
80019B24  sra   $a1, $v1, 7
80019B34  lw    $v1, 0x44($s1)      ; descriptor[clip], stride 24
```

Every actor node in the game — 177 of 177 — resolves this way, in namespace and inside the
count. `level_ending_evil_shot2` carries `0x3F32` and plays 206..423: 0x3F32 + 206 = 0x4000,
clip 0. `evil_shot3` carries `0x3FF7` and plays 9..205: 0x3FF7 + 9 = 0x4000. Both name the
same clip index in their own file — the ranges are numbered across a cutscene cut over
several `.mdl` files, and the bias absorbs exactly that.

This settles what `0x3FFF` is. It is not a camera marker and it does not "name nothing": it is
an id one short of `0x4000`, carried by a node that starts at frame 1. Reading it as a camera
put the viewpoint inside the lead's head; reading it as unnamed and guessing the clip from
range lengths happened to work on the files that were checked, and is now gone.

### 9.11.3 The played frame is zero-based — **certain**

The handler counts from the node's window, not from the play range, and subtracts the range's
start again before storing:

```
8001F218  lw    $a0, 0xc($t1)     ; the scene clock, 16.16
8001F224  subu  $a2, $a0, $v0     ;   - the window's start
8001F22C  subu  $a2, $a2, $v1     ;   - node+0x20, a start delay
8001F230  bgez  $a2, 0x8001f244   ; before that -> frame 0
8001F244  lw    $v0, 0x10($t0)    ; node+0x24: zero loops, anything else once
8001F2A4  div   $zero, $a2, $v0   ;   looping is a modulo of span+1
8001F2E8  move  $a1, $a0          ;   and both clamp at span-1
8001F2F4  subu  $a1, $a1, $v0     ; back to zero-based
8001F2F8  sw    $a1, 0x70($s6)    ;   -> entity+0x78, 16.16
```

`span` is `play_end - play_start`. The mode word is tested only against zero (0x8001F24C), so
`20` and `9` and every other non-zero value mean the same thing: play once and hold the last
frame. There is no small enum there.

**The untargeted id `0x3FFF` marks the shot's lead, not a camera.** One node per cutscene
carries it, spanning the shot. In `cutscene/uka/data.mdl` the node with a readable id plays a
20-frame clip over ticks 0..19 while this one plays frames 1..272 over 20..311 — the whole
performance.

### 9.11.4 A model holds several scenes, not one — **certain**

`model+0x48` is a count and `model+0x4C` an array of self-relative pointers, and each entry is
a **root**: a whole scene with its own clock, spawned on its own. The spawner takes an index,
not a model:

```
8001FF78  lw    $v0, 0x4c($v1)   ; the root array
8001FF88  sll   $v1, $a1, 2      ; a1 = the root INDEX
8001FF98  addu  $s3, $v0, $v1    ;   -> the root
8001FFB4  lhu   $v0, 0xc($s3)    ; root+0x0C, its first tick
8001FFBC  sh    $v0, 0xe($s1)    ;   -> the context's clock
8001FFC0  lw    $v0, 8($s3)      ; root+0x08, its last
8001FFD4  addiu $s2, $s3, 0x1c   ; and only then its children
```

So a root carries a clock range of its own — `+0x0C` first tick, `+0x08` last — and its
children's windows are ticks on **that** clock. Reading every root's children as one list puts
scenes that never coexist on the same timeline.

**Node type 5 is what enters another root.** It is a one-shot trigger: it fires the first time
the clock reaches its window start, guarded by a flag so it never fires twice, and its id is a
root index handed to the same spawner:

```
8001FDA8  lw    $v0, 4($a3)      ; the node's window start, 16.16
8001FDB4  slt   $v1, $v1, $v0    ;   not yet -> nothing
8001FDC8  andi  $v0, $v0, 0x8000 ; already fired -> nothing
8001FDD4  lw    $v0, 0x2c($a3)   ; node+0x2C..0x34 -> the entity's position
8001FDF8  lhu   $v0, 0x38($a3)   ; node+0x38, 0x3C, 0x40 -> three angles
8001FE1C  lw    $v0, 0x60($a3)   ; node+0x60..0x68 -> its scale
8001FE54  lw    $a1, 0x14($a3)   ; node+0x14, the ROOT INDEX
8001FE58  jal   0x80020cc4       ;   -> spawn it, with a clock of its own
8001FE68  ori   $v0, $v0, 0x8000 ; and mark it fired
```

`0x80020CC4` is `0x8001FE80` again for a nested context: same root lookup, same `sw $zero,
0xc($s2)` then `sh root+0x0C, 0xe($s2)` clock start. So the child's tick *t* shows at parent
tick `t + trigger.window_start − child_root.start_tick`, and the whole child sits at the
trigger's own transform.

Measured: 186 models carry a root table and 109 have more than one root. There are exactly 14
type-5 nodes in the game, ids 1 and 2 only, and every one of them is in a cutscene. Every
cutscene's root 0 is the shot — and root 0's declared range is the shot's place in the
cross-file numbering, `crashplain` 0..148, `cortexlab` 149..297, `welcome3` 298..358,
`welcome2` 359..461, consecutively. Every *extra* root in a cutscene is `[0..19]` with nine
children, named by a type-5 node whose window is exactly those 20 ticks, unit scale, and one
non-zero angle — the middle one, a yaw at 4096 to the turn.

In `level_intro_cortexlab` the trigger fires at tick 272, where Cortex shrinks away: the
sub-scene is the pink sphere that swallows him. Read as literal scene ticks, those nine props
land at 0..19 — before the shot's own clock even starts — so the effect that ends the scene
plays over its beginning, or not at all.

The 485 non-zero roots outside cutscenes are named by no trigger; gameplay code enters them
directly, and nothing in the file says when.

### 9.11.5 Only three node types draw — **certain**

A node's type indexes a table of four function pointers at 0x80058B00 — constructor, per-tick
update, draw, and one more — and three of the six populated rows have **no draw**:

| type | constructor | per tick | draw | fourth | what it is |
| --- | --- | --- | --- | --- | --- |
| 0 | 80021A1C | 8001EAA4 | 80021990 | — | prop |
| 1 | 80021604 | 8001F828 | 80021330 | 8002141C | (8 nodes, all in `intro_eurocom`) |
| 2 | 80021940 | 8001EDFC | **null** | — | fills the block at 0x80051640 |
| 3 | 80021798 | 8001F0D4 | 80021770 | — | actor |
| 4 | 80021708 | 8001F4F8 | **null** | — | interpolates one value over its window |
| 5 | 8002128C | 8001FCDC | 80021238 | 8002120C | sub-scene trigger (§9.11.4) |

And what a drawing node draws is one resource, the one its id names — nothing iterates the
model's mesh list:

```
80019F44  lhu  $a2, 0x74($s0)   ; entity+0x7C, the id
80019F7C  beqz $s2, 0x8001a0bc  ;   names nothing -> draws nothing
80019F8C  andi $v0, $v0, 0x8000 ; and the visibility bit, as ever
```

So **a mesh no node owns is not in the shot**. `level_intro_crashplain` carries three Crash
meshes — 0, 3 and 9, all with the same 2.4 × 2.0 × 0.9 extent — and its graph spawns one:
mesh 3, asleep on the grass at 0.6 scale. Meshes 0 and 9 are stock standing poses that the
shot never uses, and drawing them as scenery stood a full-size Crash next to the sleeping one.
Types 2 and 4 are present in that file and own nothing, which is why they cannot be the
missing owner: they have no draw slot at all.

Across the archive 73% of a cutscene's meshes have a node against 9% of an arena's, because an
arena's geometry is drawn by the level renderer and its node graph is only the moving parts.
Nothing in the file distinguishes the two; this editor uses "does the scene cast an actor",
which is exact over the archive — all 55 shots with a character, none of the 72 arena scenes —
but it is a guess, not a rule of the format.

### 9.11.6 Node type 2 is the shot's camera — **certain**

It fills the struct the frame renderer projects through. Its keys carry two points a stride
apart; the handler interpolates both, takes the angles from their difference, and keeps the
second as the eye:

```
8001F074  addiu $a0, $sp, 0x10   ; key+0x14, the eye
8001F078  addiu $a1, $sp, 0x20   ; key+0x08, what it looks at
8001F07C  jal   0x800153b4       ;   -> Euler angles from eye - target
8001F080  addiu $a2, $s6, 0x54   ;      into camera+0x54
8001F090  sw    $t0, 0xc($s6)    ; the eye -> camera+0x0C..0x14
8001F09C  sw    $zero, 8($s6)    ; and the offset at +0x00..0x08 is cleared
8001EF6C  lw    $v0, 0x18($a2)   ; node+0x18, the projection distance
8001EF74  sw    $v0, 0x18($s6)   ;   -> camera+0x18
```

`s6` is 0x80051640, and 0x8002AF78 hands that address to 0x80014540 — the routine that turns
the angles at +0x54 into the MATRIX at +0x74 the GTE is loaded with. The key stride is 0x28
and the list ends at a zero duration like every other (§9.11.1):

| Offset | Type | Meaning |
| --- | --- | --- |
| +0x00 | i32 | start tick |
| +0x04 | i32 | duration; zero ends the list |
| +0x08..0x10 | 3 × i32 | the point the camera looks at, model units |
| +0x14..0x1C | 3 × i32 | the eye |
| +0x20..0x27 | 8 bytes | unread; the first is the projection distance × 0.8 |

56 of the 129 scenes carry one, 76 cameras in all, and a file may hold several with windows
that do not overlap — `level_ending_good_shot3` cuts at tick 259 from a distance of 303 to 609.

**The field of view is `2 * atan(240 / H)`.** The GTE takes `H` straight from camera+0x18 and
its vertical offset is half the viewport:

```
80018E5C  lw   $v0, 0x14($s1)   ; the viewport height
80018E78  sra  $v0, $v0, 1      ;   halved -> OFY
80018E88  ctc2 $t5 -> OFY
80018E8C  lw   $t3, 0x18($s7)   ; the projection distance
80018E94  ctc2 $t3 -> H         ;   unscaled
```

The half-height is 240, which the disassembly gives the shape of but not the value. Measured:
at 120 the cast overflows the frame in 181 of 198 camera samples across the cutscenes; at 240
the median subject fills 0.94 of the frame height. On screen it is not close — at 240
`level_intro_crashplain` opens on the whole plain with Crash small in it, and at 120 on a
close-up of his head. So H = 320, which 52 of the 76 cameras use and which the camera is
initialised with (0x800143A8, `0x140`), is a 73.7° shot.

**The backdrop follows the camera.** Every cutscene carries two unowned domes ~50 units across,
centred on the origin and shared across files, and `level_intro` puts its eye 44 units out —
outside a dome of radius 25. Drawn where the file puts them the sky becomes a ball in front of
the lens, so they have to be centred on the eye and drawn without depth, at the far end of the
ordering table. The code that raises them is **still unfound** — no node owns them — so this
part is inference from the geometry, unlike everything above it.

### 9.11.7 Node type 1 is a particle emitter — **certain**

It does not place a mesh, it sprays them. The constructor takes a block of `node+0x24`
40-byte records and hands each new particle the node's mesh id; the draw walks the live ones;
the per-tick handler integrates each and retires it:

```
800216D4  lw   $v0, 0x10($s2)   ; s2 = node+0x14; +0x10 is the whole budget
800216E4  jal  0x800115d8       ;   and the record block is that many x 40
800216A4  lhu  $v0, 0x3c($s3)   ; node+0x3C, the mesh every particle draws
800216B0  sh   $v0, 0x74($v1)   ;   -> the particle's own entity+0x7C
80021354  lw   $s1, ($s0)       ; the draw walks a linked list at entity+0x0C,
80021360  lw   $v0, 0x54($s0)   ;   each with its own draw at +0x54
8001F9A8  addu $v0, $v0, $v1    ; each tick, position += velocity >> 8
8001FA08  addu $v0, $v0, $v1    ;   velocity += the acceleration
8001FA68  mult $v0, $v1         ;   then damped, 256 meaning no damping
8001FAEC  slt  $v1, $v1, $a0    ; and once the age passes the lifetime
8001FB04  and  $v0, $v0, $v1    ;   bit 15 goes out: the particle is dead
```

A new particle is drawn from the node through the generator at 0x80015590, four times: a
speed between two bounds, a starting spin, then a yaw and a pitch each around a centre. Speed
times the sine table at 0x80068BD4 gives the velocity (0x8001F738, `>> 4`).

| Offset | Meaning |
| --- | --- |
| +0x18..0x20 | where the emitter stands |
| +0x24 | the whole spray, and the size of the record block |
| +0x28 | how many leave each tick |
| +0x2C | lifetime, in ticks |
| +0x30 | the last tick that spawns |
| +0x34, +0x38 | speed bounds |
| +0x3C | the mesh, in the 0x2000 namespace |
| +0x44, +0x48 | yaw centre and spread, 4096 to the turn |
| +0x4C, +0x50 | pitch centre and spread |
| +0x54, +0x5C, +0x64 | acceleration, skipped at zero |
| +0x58, +0x60, +0x68 | damping, skipped at 256 |
| +0x6C, +0x70 | a ramp into `particle+0x76` |
| +0x74, +0x78 | a ramp into the particle's **scale**, three words at 0x8001FC5C |
| +0x7C | spin per tick |

All 76 emitters in the game name a mesh in range. `intro_eurocom` has eight, one per letter of
the logo, each opening six ticks after its letter lands: 16 particles at 4 a tick, living 24
ticks, 360° of spread, an acceleration of 7 along the console's down axis, growing over 4
ticks and shrinking away from tick 12. That is the burst of stars that falls from each letter.

**The spray cannot be reproduced frame for frame.** The generator's state lives at 0x800517B8,
which is not in the file — it is whatever the console had reached by the time the shot ran. A
reader can match the distribution and nothing finer.

### 9.11.8 What draws a mesh no node owns — **certain, but not yet traced to the data**

The node graph is not the only thing that draws (§9.11.5 said as much, and the backdrop domes
of §9.11.6 prove it). The other path is an **object list**, and it is fully mapped:

```
8001DB90  lw   $s0, 0x1c($s2)   ; the list head on the scene context
8001DBA0  lw   $v0, 0x54($s0)   ;   each object's draw slot
8001DBB4  jalr $v0
8001DBBC  lw   $s0, 0x5c($s0)   ;   and the next one
```

The draw installed in that slot is 0x8001DD50, which reads the object's resource id and hands
it to the same renderer the entity path uses:

```
8001DD6C  andi $v1, $a2, 0x8000  ; the visibility bit again
8001DD90  lhu  $a0, 0x74($s1)    ; the object's resource id
8001DD98  jal  0x80019a60        ;   -> 0x80019094 -> the polygon writer
```

Objects are built by 0x8001D6B4: `[ctx+0x18]` of them, 0xA8 bytes each, from a source array at
`[ctx+0x14]` with a stride of 0xA0, copying the id from source `+0x88` into object `+0x74` and
installing the draw. And the context is filled straight from the model:

```
8001DE1C  lw   $a1, 8($a0)      ; the caller supplies a SUB-OBJECT INDEX
8001DE28  lw   $v0, 0x18($v1)   ; v1 = the model; +0x18 is self-relative
8001DE34  addu $v1, $v1, $v0    ;   -> the table
8001DE40  lw   $v0, 4($a1)      ; [T(0x18) + 4 + 4*index], the pointer for that entry
8001DE48  addu $v1, $v1, $v0    ;   -> the sub-object record
8001DE90  ctx+0x14 = record + [record+0x20] + 0x20   ; the source array
8001DE9C  ctx+0x18 = [record+0x1C]                   ; how many
```

`record+0x20` is the placement record array of §8.5 — 0x14 in 73/73, always sub-object +0x34 —
and 0x8001D6B4's 0xA0 stride and its `+0x88` id are that section's stride and its id field. **So
the object list is the placement list**, reached only through a sub-object, and the two sections
describe one thing from two ends.

That matters for what a model with **no** sub-objects can do here: the routine indexes
`T(0x18)+4+4*i` and dereferences it, so with a count of zero there is no entry to reach and this
path cannot run at all. An earlier revision of this section read the block below as the source
array. It is not; it is data sitting in the space an empty pointer array leaves.

### Two models put something in that space, and nothing found reads it

Of the 327 models that declare no sub-objects, **325 leave exactly zero bytes** between the
count word and `T(0x44)`. `intro_eurocom` leaves 160 there and `cutscene/gamelogo_text.mdl`
leaves 260 — the whole set, and the last unclaimed bytes in the MDL corpus after the duplicate
of §3.

The stride is 20 and that is measured, not assumed: of the strides that divide both lengths,
only 20 makes the leading word settle into runs — **three distinct values in three contiguous
runs in both files**, against 11 values in 41 runs at stride 4, and 5 in 6 at stride 16. The
record reads `(flags, index, value, 0, 0)`.

| | records | flags seen | second field | third field |
| --- | --- | --- | --- | --- |
| `intro_eurocom` | 8 | `0x40080000` ×4, `0x40000000` ×2, `4` ×2 | 0,1,2,3,10,11,12,2 | 0 throughout |
| `gamelogo_text` | 13 | `0x40000000` ×4, `1` ×5, `2` ×4 | 0,1,2,3,0,1,11,10,2,0,1,2,3 | 3584, 512, 1536, 2560 |

**The second file refutes reading the second field as a mesh index.** It is in range for 8 of 8
in `intro_eurocom`, whose 28 meshes cover every value used — but `gamelogo_text` has **two**
meshes and indexes up to 11, so only 6 of its 13 are in range. The `0x40000000` correspondence
is likewise a single-file observation: in `intro_eurocom` those two records do sit at 10 and 11,
the indices of its backdrop domes, but `mesh+0x0C` is exactly 100 on mesh 10 alone — mesh 11
holds 0x10064, the same 100 in its low half — and in `gamelogo_text` no mesh carries 100 at all
while four records still flag `0x40000000`.

`gamelogo_text`'s third field is the one place a value looks like anything: 3584, 512, 1536 and
2560 are 315°, 45°, 135° and 225° on the 4096-to-the-turn scale used everywhere else in the
format (§9.11.7). That is a coincidence worth recording and not a decoding.

**No reader was found for either block**, and the one routine that reaches this region needs a
sub-object entry these models do not have. Neither self-relative nor absolute pointers into the
spans exist in `gamelogo_text`; `intro_eurocom` has 23 words that land inside its span, but 7 of
them land unaligned and 23 is below the ~40 a 160-byte window would collect by chance in a 44 KB
file, so that is noise and not a route. Neither block is a duplicate of anything: each occurs
once in its own file and nowhere else in the archive. **I could not validate what reads them,
and that is not evidence they are unused.**

What is **not** settled is where the 64 cutscenes get their object list, since **none of them
declares a sub-object**. Nor is `mesh+0x0C` read anywhere: the immediate 100 appears 9 times in
the executable and 32 times across the 14 mode overlays, and not one of those sites is near a
mesh-header load. The mesh-by-index entry point at 0x8001CE00 that would suit a backdrop is
dead code — no `jal`, no `j`, no word, no `lui`/`addiu` pair reaches it anywhere in the image
or the overlays. The rest of the pipeline is dispatched through the class table at 0x800527C4,
which is filled at boot (0x8001492C, `draw_object` in slot +0x58) and indexed through registers
that cannot be followed statically.

| Offset | Type | Meaning |
| --- | --- | --- |
| +0x00 | u32 | start tick |
| +0x04 | u32 | duration; consecutive keys tile the node's window |
| +0x0C..0x14 | 3 × i32 | position, model units |
| +0x24..0x30 | 4 × i32 | quaternion, 4096 = 1.0 |
| +0x40..0x48 | 3 × i32 | scale, 4096 = 1.0 |

**A node's window is its mesh's visibility.** Outside it the handler clears bit 15 of the
entity's flag word, or zeroes the word outright, and the draw path tests that bit:

```
8001EDBC  lw   $v0, 8($s7)        ; the entity flag word
8001EDC4  and  $v0, $v0, $v1      ; v1 = 0xFFFF7FFF -- clear bit 15
8001F4CC  sw   $zero, 8($a0)      ; or drop the word entirely
80021258  andi $v0, $a2, 0x8000   ; and the draw path asks for it
```

So a mesh that a node owns is drawn only while one of its windows is open; a mesh no node
owns is scenery and always drawn. `level_ending_evil_shot4` is the case that makes it plain:
19 meshes, one actor, and **137 prop tracks** over meshes 1 and 5..17. At tick 1133 six of
them are open, at 1523 only two. Ignore the tracks and all fourteen props stand on the origin
at once, which is exactly what the scene looks like when they are missed.

**The type word is a table index.** `0x80058B00` is not a list of handlers but an array of
**16-byte records**, one per node type, and the spawner indexes it with the node's own type:

```
8001FFF0  lw    $v0, ($s2)          ; the model+0x4C array, self-relative
8001FFF8  addu  $a0, $s2, $v0       ;   -> the node
8001FFFC  lw    $v0, ($a0)          ; node+0x00 : the type word
80020004  sll   $v0, $v0, 4         ;   16 bytes per record   <-- STRIDE 16
80020008  addu  $v0, $v0, $s4       ;   s4 = 0x80058B00
8002001C  jalr  $v0                 ; slot +0x00 : construct the entity
```

Slot `+0x00` builds the entity, slot `+0x04` is the per-tick update — the handlers above.
Types 0 to 5 are populated; 6 and 7 are zero.

**The camera struct is decoded.** The frame renderer itself rebuilds the camera's matrix
every frame, at 0x80018C54 calling 0x80014540, and that function names every source field:

```
80014554  lw    $v0, 0x38($s0)      ; +0x38 : countdown, decremented to zero
80014570  addiu $a0, $s0, 0x54      ; +0x54..0x58 : three i16 ANGLES (4096 = a turn)
80014574  addiu $s1, $s0, 0x74      ; +0x74 : the MATRIX
80014578  jal   0x8003278c          ;   RotMatrix -- the sine table at 0x80068BD4
80014580  lbu   $v0, 0x94($s0)      ; +0x94 : byte flag --
80014590  jal   0x8003264c          ;   apply the SCALE at +0x64..0x6C (0x1000 = 1.0)
80014598  lw    $v0, ($s0)          ; +0x00..0x08 : three i32, scaled and stored to
80014608  sw    $t0, 0x88($s0)      ;   the matrix translation at +0x88..0x90
```

With the draw path (§above) this closes the projection equation:

```
screen = RotMatrix(angles at +0x54) · (world − eye at +0x0C..0x14)
         + scale · offset at +0x00..0x08,
         projected with H at +0x18, centred by OFX/OFY from the viewport
```

Every term has an instruction: the eye subtraction at 0x8001D920, the compose at 0x8001DA28,
H into GTE control 26 at 0x80018E94, OFX/OFY from the viewport's rectangle at 0x80018E84.
The menu is the worked example: it initialises its cameras (0x80014388 — called **from the
overlay**, at 0x800B37E4), leaves the eye and angles at zero, writes 400 to H and 0x400 or
0x578 into the +0x08 offset — an identity camera pushing the world 4 to 5.5 units deep,
which is exactly how menu models, authored around the origin, land on screen.

**A camera is computed, not stored.** Searching every code image for the one signature a
camera setter must have — two of the three angle halfwords at `+0x54..0x58` written on one
base — finds eight sites, and `menu.bin`'s at 0x800B5978 writes all three and every other
field besides. It takes an object and derives the whole camera from it:

```
800B598C  jal   0x8001e48c        ; find the object with this id
800B59A0  lw    $v1, 0x58($s0)    ; yaw = atan2(obj+0x58 - obj+0x3C,
800B59A8  lw    $v0, 0x50($s0)    ;              obj+0x50 - obj+0x34) - 0x400
800B59B4  jal   0x8001463c        ;   0x8001463C is atan2; 4096 is a full turn
800B59C4  sh    $v0, 0x54($s1)    ; camera yaw
800B59C8  sh    $zero, 0x56($s1)  ; pitch and roll are always zero
800B59D0  lhu   $v0, 0x40($s0)
800B59E4  sw    $v0, 0x18($s1)    ; H = (i16)(obj+0x40) >> 2 + 0x200
800B59F0  sw    $v0, 0xc($s1)     ; eye = obj+0x30..0x38
800B5A14  sw    $zero, ($s1)      ; offset = (0, -(obj+0x34), obj+0x58)
800B5A1C  sw    $v0, 4($s1)
800B5A28  sw    $v0, 8($s1)
```

So there is nothing per-scene to read: the camera is a **function of one object's fields**,
recomputed every frame. A pitch of zero throughout says these shots are level — the camera
turns and dollies but never tilts. The object arrives from 0x8001E48C, which walks the
count-prefixed self-relative array the current context keeps at `+0x28` and returns the entry
whose `+0x0C` matches the id it is given; `menu.bin`'s only call passes **id 1**. Which object
that is for a cutscene, and whether the cutscene path uses this setter or one of the other
seven, is **?unknown?** — the wrapper at 0x800B5A40 is reached neither by a call nor through
any pointer visible in the images, so its caller is resolved at run time.

**The graph is a tree, not a flat list.** `model+0x48` is 1 for both cutscenes measured, and
its single `model+0x4C` pointer resolves to the *first* record of the object-graph span —
`0xCB5C` in `level_ending_good_shot3`, which is `T(0x1C)` itself. The nodes this section
reads are found by walking the span and matching shape; the game reaches them from that root.
**How the root enumerates its children is no longer unknown** — `spawn_order` quotes the
spawner doing it at 0x8001FFD4: a root states its child count at `+0x00` and their
self-relative pointers start at `+0x1C`. A node the walk reaches is a node the game
constructs, and walking every root of every model finds 3333 of them.

**Cameras are overlay-owned.** Counting calls per overlay: `menu.bin` initialises two
cameras and renders through three; `warp.bin` initialises one, renders through two, and is
the one caller of the follow function 0x80013F44, which eases the eye at `+0x0C` and H at
`+0x18` through its *arguments*. That last point bounds what a static sweep can prove:
absolute-address sweeps see only pointers formed by `lui/addiu`, and a camera passed in a
register is written invisibly. Two earlier negatives in this section's history overreached
exactly there — "nothing fills a MATRIX at +0x74" (0x80014540 does, through a register) and
"nothing writes through the camera pointer" (the follow function does, through an argument).
What still holds: no node **entity** can *be* the camera struct — a node entity keeps its
quaternion at +0x18..0x24 where the camera keeps H — and no node constructor makes one.
A copy bridge in overlay code, entity fields into camera fields, is not excluded. What is
excluded is the `0x3FFF` node: that is the shot's lead actor, and reading it as the camera
put the viewpoint inside his head while hiding him from the rest of his own cutscene.

What does write a camera is overlay code. A gameplay mode builds a target camera on the stack
and eases the live one toward it: `warp.bin` at 0x800B812C calls 0x80013F44, which closes a
quarter of the gap in the screen distance at `+0x18` and a fraction of the gap in position
each tick. The menu initialises its camera at 0x800B37E4, writes `0x190` (400) to `+0x18`,
and copies a 44-byte transform from the global `0x8005A9A0` into `camera+0xBC`.

An overlay owns **several** cameras, one per view it draws: extracting the second argument at
every call of the renderer gives `menu.bin` three — `0x800B9538` (seven calls), `0x800B9F1C`
(one) and one more reached through a register — and `warp.bin` two, `0x8009F734` (five calls,
and it lies below the overlay's own image) and `0x800BF1A8`. Sweeping each overlay for every
absolute access to those structs shows the same shape every time: `+0x08` and `+0x18` are
written, `menu.bin` fills `+0xBC..0xD4` as well, and **not one of them is ever given a
position at `+0x0C..0x14` or a matrix at `+0x74`**. Those fields are filled through pointers
the code resolves at run time, which is where a purely static read of this executable stops.

The data agrees with the code. A 33° lens framing `level_ending_good_shot3`'s cast would have
to stand about 7.5 units back — raw `(-47, -247, 2061)` at tick 200 — and a sweep of every
int32 triple in that file finds **no position anywhere near it**: nothing in the model stands
further back than z ≈ 5.5. The camera is not in the file, from either direction.

**How the shot is actually projected.** The frame renderer loads the GTE straight from the
camera struct, and the matrix at `+0x74` is a full libgte `MATRIX` — rotation *and*
translation, feeding control registers 0..7:

```
80018E84  .word cop2               ; control 24, 25 : OFX, OFY -- the screen centre,
80018E88  .word cop2               ;   computed from the viewport at a0 and divided by 640
8001E8C   lw    $t3, 0x18($s7)     ; control 26 : H, the projection distance
80018E94  .word cop2
80018E98  addiu $v0, $s7, 0x74     ; the camera MATRIX
80018E9C  lw    $t4, ($v0)         ;   -> control 0..4 (rotation)
800018EB4 lw    $t6, 0x10($v0)     ;   -> control 5..7 (translation)
```

So three runtime numbers decide framing: `H` from `camera+0x18`, the screen centre from the
**viewport** struct the renderer takes as its first argument, and the MATRIX. The menu's own
source block for that matrix — the 44 bytes at `0x8005A9A0` it copies into the camera — is
built at 0x800239E4 as **zero position with unit scale**, an identity camera at the origin.
Reproducing a cutscene's framing therefore needs the values only run time holds. The decoded
struct says exactly which: break at the entry of 0x80014540 during a cutscene and read the
camera's `+0x0C..0x14` (eye), `+0x54..0x58` (angles), `+0x00..0x08` (offset) and `+0x18` (H).
Four numbers, and the shot is reproducible. **confirmed** (the type table, the handlers, the
id resolution, the scale track, the visibility bit, the camera struct and its rebuild, and
the negatives as scoped above) / **?unknown?** (the cutscene's camera values).

### 9.11.10 Node type 4 is the fade — **certain**

The last type the spawner constructs that this document could not name. Its constructor keeps
two fields of the node, and its handler ramps between them across the node's own window:

```
; 0x80021708 — the constructor; a0 = the node
80021744  addiu $v0, $zero, 4
80021748  sw    $v0, 4($v1)      ; the instance's type
80021750  lw    $v0, 0x14($a0)   ; node+0x14 -> instance +0x08
8002175C  lw    $v0, 0x18($a0)   ; node+0x18 -> instance +0x0C

; 0x8001F4F8 — the handler, once the node's window is found to hold the tick
8001F594  slt   $v0, $a3, $v0    ; tick < window start -> nothing
8001F5A8  slt   $v0, $v0, $a3    ; tick > window end   -> nothing
8001F5C0  lw    $a2, 8($v1)      ; the window's end
8001F5C4  lw    $a3, 4($v1)      ;   and its start
8001F5CC  lw    $a0, 8($t0)      ; the level to ramp from
8001F5D0  lw    $a1, 0xc($t0)    ;   and the one to ramp to
8001F5E4  jal   0x80015304       ; interpolate across the window
8001F5F0  sw    $v0, -0x74a0($v1); -> 0x80058B60
```

`0x80015304` is the interpolation, not an assumption about one — `subu $a1, $a1, $a0` then
`mult $a1, $a3` and a divide by `$a2` is `from + (to − from) × elapsed / span`.

Two measurements make it a fade rather than a nameless ramp. **The levels are only ever
0 or 4096** — 1.0 in the 1.12 the rest of the graph uses — and over all 44 nodes the pair
takes exactly three shapes: `(4096, 0)` fifteen times, `(0, 4096)` fifteen, and `(4096, 4096)`
fourteen. Ramp down, ramp up, hold. All 44 sit in cutscene models: `uka` 12, `aku` 12,
`aku_uka` 6 and `cutscene` 14.

And the value it writes is composited, not merely stored. Both readers — 0x800201D4 in the
executable and 0x80094EF8 in `gameeng.bin` — fold it into the render context's +0x14 as
`1 − (1 − ramp)(1 − level)`, which is how two fades combine, and the render pass then scales a
colour by it:

```
; 0x800191F8
800191F8  lw    $a0, 0x14($a2)   ; the fade level
80019200  beqz  $a0, ...         ;   zero -> nothing to draw
8001921C  subu  $v1, $v1, $a0    ; 1 - fade
80019220  mult  $v1, $a1         ;   scales the colour at ctx+0x08
```

| Offset | Type | Meaning | Confidence |
| --- | --- | --- | --- |
| +0x00 | i32 | 4, the type | **confirmed** |
| +0x04, +0x08 | i32 | the window, first and last tick | **confirmed** |
| +0x14, +0x18 | i32 | the level to ramp from and to, 4096 being opaque | **confirmed** |

### 9.11.9 There are six node types, and the table says so — **certain**

An earlier revision read the block at 0x80058B00 as a list of eighteen handlers and inferred
eighteen kinds of track. It is not a list. It is a table of **16-byte entries indexed by the
node's own type**, and the spawner says so:

```
8001FFE8  lui   $v0, 0x8006
8001FFEC  addiu $s4, $v0, -0x7500   ; s4 = 0x80058B00, the table
8001FFF0  lw    $v0, ($s2)          ; a child pointer
8001FFFC  lw    $v0, ($a0)          ; the child's +0x00 -- its type
80020004  sll   $v0, $v0, 4         ;   x 16
80020008  addu  $v0, $v0, $s4       ;   -> its row
8002000C  lw    $v0, ($v0)          ;   the constructor
8002001C  jalr  $v0
```

Read that way it holds six rows and two empty ones:

| Type | +0x00 | +0x04 | +0x08 | +0x0C | What it is |
| --- | --- | --- | --- | --- | --- |
| 0 | 0x80021A1C | 0x8001EAA4 | 0x80021990 | — | prop (§9.11.5) |
| 1 | 0x80021604 | 0x8001F828 | 0x80021330 | 0x8002141C | particle emitter (§9.11.7) |
| 2 | 0x80021940 | 0x8001EDFC | — | — | camera (§9.11.6) |
| 3 | 0x80021798 | 0x8001F0D4 | 0x80021770 | — | actor (§9.11.5) |
| 4 | 0x80021708 | 0x8001F4F8 | — | — | names another node; see §14 |
| 5 | 0x8002128C | 0x8001FCDC | 0x80021238 | 0x8002120C | sub-scene trigger |
| 6, 7 | 0 | 0 | 0 | 0 | none |

The +0x04 column is the per-tick handler this document already decodes for three of them;
+0x00 is the constructor the spawner calls. **The corpus agrees that six is all there is**:
walking every root of every model finds 3333 nodes and not one whose type field falls outside
0..5.

## 9.12 The 20-byte rows two cutscenes put before the clip table

Byte coverage is what turned these up: after everything else in the archive is accounted for,
**two models** have anything left, and this is one of the two things left in them. `intro_eurocom`
carries 160 bytes and `gamelogo_text` 260, in both cases ending exactly at `T(0x44)` and dividing
evenly into **20-byte rows** — 8 and 13 of them.

| Offset | Type | Measured |
| --- | --- | --- |
| +0x00 | i32 | Five distinct values over the 21 rows: `0x40000000`, `0x40080000`, 1, 2, 4. The two large ones are the **0x4000 animation namespace** in the high half (§2.3), with 0 and 8 in the low. |
| +0x04 | i32 | A small index, 0..12. It counts 0,1,2,3 within a run and then restarts — `gamelogo_text` reads 0,1,2,3 / 0,1,11,10,2 / 0,1,2,3, so the rows group. |
| +0x08 | i32 | 3584, 512, 1536, 2560 on `gamelogo_text`'s first four rows; **0 in the other 17**. |
| +0x0C | i32 | Zero in 20 of 21 rows. |
| +0x10 | i32 | **Zero in 21/21** — which is what the coverage walk tests before claiming the span, so a file with something else there stays visibly unclaimed. |

**No reader has been found and none was searched for beyond the byte layout**, so what these
drive is ?unknown?. The 0x4000 namespace in `+0x00` and the restarting index in `+0x04` are
suggestive of clip references, and that is a resemblance, not a decoding. That only two of 400
models carry them at all is itself unexplained.

They are also **unreachable by pointer**: a scan of every self-relative i32 in both files finds
**zero** resolving into `gamelogo_text`'s rows from outside them, and `intro_eurocom`'s 23
apparent hits are all either misaligned targets or the value 4096 — quaternion-one noise from
scene keys. So whatever reads these rows, if anything does, reaches them positionally — the one
fixed landmark being that they end exactly at `T(0x44)` in both files. Searched, not found, and
not evidence of absence.

## 9.13 `gamelogo_text` ships its mesh block twice

The other leftover, and it is not a structure. `gamelogo_text` holds **7520 bytes at 0xC0 and a
byte-identical copy of them at 0x1E20**, back to back. Both of its mesh headers resolve every
one of their pointers — bounds, strips, uv index, texture, colour index — into the **second**
copy, and both name the same `ptr_end`. Nothing in the file reaches the first.

So it is dead weight an exporter left behind, not data. It is worth recording because it was the
single largest unclaimed span in the archive, and reading it as an undiscovered structure would
have been the natural mistake: it is 7520 bytes of perfectly well-formed mesh material.

---

# 10. TEX packs

A `.tex` entry is a palette table followed by texture records with their pixel data inline.
Every pack in the corpus satisfies `u32@0x00 == 8` and `u32@0x04 == entry.size`.

## 10.1 Pack header (0x20 bytes)

| Offset | Type | Name | Meaning | Confidence |
| --- | --- | --- | --- | --- |
| 0x00 | u32 | `offset_table_bytes` | 8 = `4*(1+1)`: one section offset follows. Not a magic. | **confirmed** |
| 0x04 | u32 | `size` | Section-0 end offset; equals the entry size exactly in 400/400. | **confirmed** |
| 0x08 | i16 | `texture_count` | | **confirmed** |
| 0x0A | i16 | `palette_count` | | **confirmed** |
| 0x0C | u32 ptr | `ptr_textures` | **Self-relative**: `0x0C + value` == the first texture record, i.e. the end of the palette table, in **400/400**. | **confirmed** |
| 0x10 | u32 ptr | `ptr_palettes` | **Self-relative**: `0x10 + value == 0x20` in **400/400**. | **confirmed** |
| 0x14 | u32 | — | Multiple of 4 in 400/400, range 120..228,744, 272 distinct values. It is **not** a pointer (`0x14 + value` is outside the file for 198/400), not the pixel byte total, not the VRAM-unit total, not the pixel+palette total, and it differs between structurally identical packs (22980 / 25096 / 22760 for `uka` data / data2 / data3). | ?unknown? |
| 0x18 | u32 | `ptr_animation` | **Absolute** offset of the animation block (§10.5), 0 when the pack has none — 314/400 have none. In the 86 that do, the value equals the end of the palette+texture walk in **86/86**. Note it is absolute, unlike 0x0C and 0x10. | **confirmed** |
| 0x1C | u32 | — | 0 in 400/400. | **confirmed** (zero) / ?unknown? (purpose) |

The palette table therefore starts at **0x20**, not 0x24.

## 10.2 Palette table

`palette_count` variable-length records, back to back from 0x20:

| Offset | Type | Meaning | Confidence |
| --- | --- | --- | --- |
| +0x00 | i32 | colour count — only **16** (10,994) or **256** (240) occur | **confirmed** |
| +0x04 | u16 × count | BGR555 colours | **confirmed** |

BGR555 → RGBA8 uses the standard PS1 expansion; a fully zero entry (all 15 colour bits clear
and the STP bit clear) is the hardware's "skip this pixel" encoding, i.e. transparent, not
black.

## 10.3 Texture record (20 bytes + pixel data)

Records run back to back from `0x0C + u32@0x0C`, each immediately followed by its pixels.

| Offset | Type | Name | Meaning | Confidence |
| --- | --- | --- | --- | --- |
| +0x00 | i16 | `vram_width` | Width in **16-bit VRAM units**. Pixel width = `vram_width * 4` at 4bpp, `* 2` at 8bpp. | **confirmed** |
| +0x02 | i16 | `height` | Rows. | **confirmed** |
| +0x04 | u8 | — | 0 in 15,160/15,160. | **confirmed** (zero) / ?unknown? (purpose) |
| +0x05 | u8 | — | 0 in 13,332/15,160; otherwise 1..5 mostly. | ?unknown? |
| +0x06 | u8 | `used_width` | **≤ `vram_width * 2` (the row's byte count) in 15,160/15,160**, with equality in 11,890. No read of it found — see below for the search. | **confirmed** (the bound) / *likely* (a used-area width) |
| +0x07 | u8 | `used_height` | **≤ `height` in 15,160/15,160**, with equality in 10,186. No read of it found — see below for the search. | **confirmed** (the bound) / *likely* (a used-area height) |
| +0x08 | u32 | — | **0 in 15,160/15,160**. `0x80029450` copies it into `descriptor+0x0C`, which nothing then reads — the page lives at descriptor+0x24 and is allocated, not stored (§10.4). | **confirmed** (where it goes) / ?unknown? (what it would mean) |
| +0x0C | i16 | `palette_field` | Bit 0 = bit depth (0 → 4bpp, 1 → 8bpp; 14,885 / 275). Bits 1–15 = palette index. The value `0x7FFF` means "no palette of my own" — the swatch texture, 355 of 15,160, and `0x80028EE8` compares against exactly that constant before it would look one up. | **confirmed** |
| +0x0E | i16 | `variants` | 0 (14,985) or 3 (175). **Bit 1 gates a sibling lookup**: `0x80028E24` tests it and, when set, indexes a descriptor `56*n` further on (§10.4). Set only in warp rooms (2 apiece) and the crate object packs (7). | **confirmed** (the gate) / ?unknown? (what the variants are) |
| +0x10 | u32 | `variant_count` | The bound that lookup compares the selector at `0x8005A640` against. Where +0x0E bit 1 is set it is 2 (134) or 7 (41), and the run of that many descriptors fits inside the pack in **175/175**. Where the flag is clear it is loaded and never used, which is why it takes 19 unrelated values. | **confirmed** (the bound) / ?unknown? (the selector) |
| +0x14 | u8[] | `pixels` | `vram_width * height * 2` bytes. 4bpp packs two pixels per byte, **low nibble first** (leftmost). | **confirmed** |

**No read of +0x04..+0x07 was found, and this time the search is enumerable rather than
argued.** The one routine known to walk these records is the descriptor builder of §10.4,
whose `$s0` strides by `0x14 + 2*w*h`; after it runs nothing else is known to hold a record
pointer, because the descriptor carries the *pixel* address at its +0x30 instead. Listing
every load through that walker inside the loop at 0x80029320..0x8002947C gives nineteen
instructions and six distinct offsets:

```
80029338 lhu +0x0C   80029348 lw  +0x0C   8002935C lhu +0x0E   80029368 lw  +0x10
80029374 lhu +0x02   80029390 lhu +0x00   800293C4 lbu +0x00   80029418 lbu +0x02
80029444 lw  +0x08   ... and ten more, all at +0x00, +0x02, +0x08, +0x0C, +0x0E or +0x10
```

Not one is at +0x04, +0x05, +0x06 or +0x07. Their bounds against the record's own dimensions
still hold over the corpus, so the "used sub-rectangle" reading remains the only one that
fits the numbers.

And there is no other way to hold a record. A record's address comes from the pack header's
+0x0C, resolved self-relatively; scanning every code blob on the disc for that shape —
`lw rX, 0x0C(rY)` with an `addiu rX, rX, 0x0C` within three — turns up **five sites in all**,
and four of them belong to structures this document already reads: the mesh dispatcher
(0x800156DC), the clip table (0x80019B8C), the sub-object binder (0x8001DE50) and the +0x10
block walk (0x80024C40). The fifth is 0x80029310, the builder. So the enumeration above is
not one routine's habits; it is every read of a texture record the shipped code performs.
A routine that recovered a record by subtracting 0x14 from a descriptor's pixel pointer would
still escape it, and nothing suggests one does.

Walking `palette_count` palettes then `texture_count` records does **not** land exactly on the
file size, and the reason is the animation block: a flipbook's frames are full replacement
images stored past the records (§10.5). Follow those and the walk accounts for **99.98 %** of
the archive's 15.2 MB, with nothing left but a 4-, 8- or 12-byte run of zeros at EOF — 44, 263
and 51 packs respectively, and 42 packs with no tail at all.

**A slot's liveness cannot be proven from the meshes.** "No mesh samples it" is not evidence a
slot is free: the game also draws textures straight from code, with no geometry involved. The
case that proved it — `models/mainmenu/models.tex` slots 103–116 and 123–124 are referenced by
no mesh in the file, and they are the character-select portraits; overwriting them corrupted
the select screen. The only safe slots to take when replacing a mesh are the ones whose **only
sampler is the mesh being replaced** (for the menu's mesh 13: slots 30, 31, 33, 34, 35 and
their palettes), since their one user leaves with it. Replacing pixels and palette values
inside a slot is safe either way (§10.1's unknowns are about *placement*, which never moves);
what a slot is *for* is the question the data cannot answer.

## 10.4 The loader: how a pack becomes runtime descriptors

This is the routine §14 spent three exhaustive scans failing to find, and it was missed
because the search was aimed at the wrong structure. The descriptors do **not** hang off the
render context at 0x80056998. They hang off a second context at **0x80055684**, which is what
the accessors of §6.2 are handed — `0x8002C774` passes it to `0x800160F8` — and which is
reached by only three sites in the whole image.

`0x80017070` hands it to `0x80016CA0`, which registers `0x80016D98` as its load callback;
that callback calls `0x8002A5A4` with the context's own tail at `+0x10`, and `0x8002A5A4`
sizes the two descriptor arrays straight out of the pack header:

```
; 0x8002A5A4 — s0 = context+0x10, a1 = the loaded pack
8002A5CC  sw    $v0, 0x10($s0)     ; the pack base       -> context+0x20
8002A5D0  lhu   $v0, 8($v0)        ; pack+0x08, the texture count
8002A5D8  sll   $a0, $v0, 3
8002A5DC  subu  $a0, $a0, $v0
8002A5E4  sll   $a0, $a0, 3        ; (delay slot) 56 * count
8002A5E0  jal   0x800115d8         ; allocate            -> context+0x18
8002A5F4  lhu   $v0, 0xa($v0)      ; pack+0x0A, the palette count
8002A5FC  sll   $a0, $v0, 1
8002A600  addu  $a0, $a0, $v0
8002A608  sll   $a0, $a0, 2        ; (delay slot) 12 * count
8002A604  jal   0x800115d8         ; allocate            -> context+0x1C
8002A62C  lw    $v0, 0x14($v1)     ; pack+0x14           -> context+0x24
8002A634  jal   0x8002926c         ; and fill them
```

Two things fall out immediately. **The 56- and 12-byte strides §6.2 derived from the
accessors are confirmed by the allocation itself**, and **`pack+0x14` has a reader** — the
field §14 lists as taking 272 distinct values and matching no landmark is carried into the
context at +0x24. What is done with it there is still ?unknown?, but "no reader" was wrong.

`0x8002926C` then walks both tables. The palettes first, each descriptor 12 bytes:

```
800292C8  lhu   $v1, ($a0)         ; the palette's own entry count (§10.2)
800292D0  subu  $v0, $a0, $v0      ; its offset from the pack, biased by +4
800292D4  sh    $v1, ($a2)         ; CLUT descriptor +0x00 = the count
800292D8  sw    $zero, -4($a1)     ;                 +0x04 = 0
800292DC  sw    $v0, ($a1)         ;                 +0x08 = the offset
800292F0  addiu $v0, $v0, 4        ; next palette: 4 + 2*count bytes
```

Then the textures, `s0` walking the records and `s1` the descriptor's +0x30. Every field of
a texture descriptor comes from the record — this is the whole map:

| Descriptor | From | Note |
| --- | --- | --- |
| +0x00 | `record+0x0C & 1` | the bit depth, exactly §10.3's bit 0 |
| +0x02 | `(record+0x0C >> 1) & 0x7FFF` | and exactly §10.3's palette index |
| +0x04 | `0x80063B1C + 12 * f(w, h, depth)` | `f` is 0x80028994, which halves the width at 4bpp and then shifts down to a bucket |
| +0x08 | `record+0x00 << 2` at 4bpp, `<< 1` at 8bpp | the width in texels |
| +0x0A | `record+0x02` | the height |
| +0x0C | `record+0x08` | §6.2 reads this as the **tpage** |
| +0x10, +0x11 | `width − 1`, `height − 1` | wrap masks |
| +0x12 | `record+0x0E` | the 0-or-3 field |
| +0x14 | `record+0x10` | the 19-value `flags` |
| +0x1C, +0x1E | `record+0x00`, `record+0x02` | the raw dimensions again |
| +0x20, +0x34 | 0, −1 | |
| +0x30 | the record's own offset from the pack | biased by +0x14 |

and the record stride closes §10.3 from the code rather than from measurement:

```
80029460  mult  $v1, $v0            ; vram_width * height
80029470  sll   $v0, $t0, 1         ;   * 2
80029474  addiu $v0, $v0, 0x14      ;   + the 20-byte header
8002947C  addu  $s0, $s0, $v0       ; (delay slot) the next record
```

### Where a texture lands in VRAM: assigned at load, not stored

The pack states no placement — nor should it, because the game allocates one. `0x80016D98`
follows the parse with `0x80029560`, and that is the whole answer.

**The earlier scans missed it for a reason worth recording.** They looked for `GetClut`
*inlined* — an `srl ,4` beside an `sll ,6`. The game calls it:

```
; 0x800364FC — GetClut(x, y), exactly the libgpu macro
800364FC  sll   $v0, $a1, 6
80036500  sra   $a0, $a0, 4
80036504  andi  $a0, $a0, 0x3f
80036508  or    $v0, $v0, $a0
```

and it builds the tpage by hand with a shift pattern the scan did not match. So the negative
result was real and the conclusion drawn from it was wrong.

Palettes first. Each is uploaded and given a CLUT id:

```
; 0x80029560 — s1 walks the 12-byte CLUT descriptors, s6 is the pack
800295B0  lhu   $v0, ($s1)         ; the palette's entry count
800295B8  sltiu $v0, $v0, 0x11     ; 16 or fewer -> the small allocator
800295C4  jal   0x80028420         ;   otherwise 0x80028A00
800295DC  sw    $v0, 2($s0)        ; +0x04 = the VRAM rect it got
800295E8  lhu   $v0, ($v0)         ; the rect's x
800295FC  lhu   $v0, 2($v0)        ;   and y
80029618  lw    $a1, 6($s0)        ; +0x08, the palette's bytes in the pack
80029620  jal   0x8002f014         ; LoadImage -- upload it
80029630  jal   0x800364fc         ; GetClut(x, y)
80029638  sh    $v0, ($s0)         ; +0x02 = the CLUT id
```

Then the textures, one call to `0x80028D40` each:

```
; 0x80028D40 — s0 = the texture descriptor
80028D5C  lw    $a0, 4($s0)        ; its size-class bucket, from 0x80028994
80028D60  jal   0x800282f8         ; pop a free rect off that bucket's list
80028D6C  sw    $v0, 0x20($s0)
80028D78  sh    $v0, 0x18($s0)     ; the rect's x
80028D8C  sh    $a0, 0x1a($s0)     ;   and y
;   8bpp                                     4bpp (0x80028DD0)
80028DA0  ori  $v0, $v0, 0xa0        80028DE8  ori $v0, $v0, 0x20
80028DA4  andi $v1, $v1, 0x380       80028DE0  andi $v0, $v0, 0x3ff
80028DA8  sra  $v1, $v1, 6           80028DE4  srl  $v0, $v0, 6
80028DB4  sll  $v1, $v1, 2           80028DF4  sll  $v0, $v0, 2
80028DC0  sh   $v0, 0x24($s0)        80028E00  sh   $v1, 0x24($s0)
80028E6C  jal   0x8002f014         ; LoadImage -- upload the pixels
80028E88  sh    $v1, 0x28($s0)     ; the page-local UV of the top-left corner,
80028EAC  sh    $v0, 0x2a($s0)     ;   and the other three, from the +0x10/+0x11
80028EC8  sh    $v0, 0x2e($s0)     ;   masks the builder wrote
80028EE8  addiu $v0, $zero, 0x7fff ; a palette index of 0x7FFF names none (§10.3)
80028EF8  lw    $v1, 0xc($s2)      ; otherwise index the CLUT descriptors
80028F0C  sh    $v0, 0x26($s0)     ;   and take the id GetClut gave it
```

`0xA0` is `(tp=1) << 7 | (abr=1) << 5` and `0x20` is `(abr=1) << 5` with `tp=0`, so both
branches are `getTPage` to the letter. **Placement is a free-list allocation by size class**:
`0x80028994` picks a bucket from the texture's own dimensions, the table of bucket heads sits
at `0x80063B1C` at stride 12, and `0x800282F8` pops the first free rect. Nothing about it is
in the file, which is why no field of a TEX pack ever named a page.

That settles the whole descriptor, and it settles what the render pass reads. Its callers
hand it **`descriptor + 0x18`**, not the descriptor — `0x8002C780` does `addiu $a0, $v0, 0x18`
right after the accessor returns — so §6.2's "tpage at +0x0C, clut at +0x0E, UV origin at
+0x10" are this structure's +0x24, +0x26 and +0x28.

| Descriptor | Written by | Meaning |
| --- | --- | --- |
| +0x00 | builder, `record+0x0C & 1` | bit depth |
| +0x02 | builder, `record+0x0C >> 1` | palette index; 0x7FFF means none |
| +0x04 | builder, `0x80063B1C + 12*f(w,h)` | the VRAM size-class bucket |
| +0x08, +0x0A | builder | width in texels, height |
| +0x0C | builder, `record+0x08` | zero in every shipped record, and **not** the page |
| +0x10, +0x11 | builder | `width − 1`, `height − 1`, the wrap masks |
| +0x12, +0x14 | builder, `record+0x0E`, `record+0x10` | the variant gate and its bound |
| +0x18, +0x1A | `0x80028D40` | the allocated rect's x and y |
| +0x1C, +0x1E | builder | the raw `vram_width` and `height` |
| +0x20 | `0x80028D40` | the rect itself |
| +0x24 | `0x80028D40` | **the tpage**, `getTPage` of the rect |
| +0x26 | `0x80028D40` | **the CLUT id**, from the palette's own descriptor |
| +0x28..+0x2E | `0x80028D40` | the four page-local UV corners |
| +0x30 | builder | the record's pixel offset in the pack |
| +0x34 | builder | −1 |

**What this means for an editor.** A UV in a model is page-local and stays correct however
VRAM is arranged, so nothing has to be reproduced. But the page a texture gets is decided by
its **size class**, so a replacement that changes a texture's dimensions changes which bucket
it draws from and where every later texture lands. That is the reason behind the empirical
rule of §10.3, not a separate one.

## 10.5 Animation block (`u32@0x18`)

Two tables, each announced by a self-relative pointer in the block's own 8-byte header and each
starting with its record count. Either may be absent. Present in 86 of 400 packs, holding
**136 flipbooks over 1,137 frames** and **108 scrollers**.

| Offset | Type | Name | Meaning | Confidence |
| --- | --- | --- | --- | --- |
| +0x00 | u32 ptr | `ptr_flipbooks` | Self-relative; 0 when there are none. | **confirmed** |
| +0x04 | u32 ptr | `ptr_scrollers` | Self-relative; 0 when there are none. | **confirmed** |

### Flipbook record (0x14 bytes), after a u32 count

A texture whose pixels are swapped for one of a run of stored frames.

| Offset | Type | Name | Meaning | Confidence |
| --- | --- | --- | --- | --- |
| +0x00 | u32 ptr | `ptr_frames` | **Self-relative from the record start**, to an array of `frame_count` self-relative pointers, one per frame. | **confirmed** |
| +0x04 | i32 | `texture` | Index into the pack's own texture list. In range in **136/136**. | **confirmed** |
| +0x08 | i32 | `frame_count` | 4..16. | **confirmed** |
| +0x0C | i32 | `delta` | Frames per tick, 24.8 fixed point. 128 (0.5 → 15 fps) is the commonest of 12 values; the range is 6..152. | *likely* |
| +0x10 | i32 | `cursor` | Runtime accumulator; **0 in 136/136** as shipped. | *likely* |

Two measurements identify the frame blobs beyond doubt:

* Each frame is **exactly** `vram_width * height * 2` bytes — the named texture's own pixel
  length — in **136/136** flipbooks. A frame is a drop-in replacement: same size, same bit
  depth, same palette.
* **Frame 0 is byte-identical to the texture's own pixels in 136/136.** The texture as stored
  is the first frame of its own animation.

### Scroller record (0x10 bytes), after a u32 count

A texture that slides under its own UVs, so a surface appears to flow without the model moving.

| Offset | Type | Name | Meaning | Confidence |
| --- | --- | --- | --- | --- |
| +0x00 | i32 | `texture` | Index into the pack's texture list. In range in **108/108**. | **confirmed** |
| +0x04 | i32 | `delta` | Texels per tick, 24.8 fixed point. 21 distinct values, −2048..+256, mostly multiples of 4. | *likely* |
| +0x08 | i32 | `cursor` | Runtime accumulator; **0 in 108/108**. | *likely* |
| +0x0C | i32 | — | **0 in 108/108**. | **confirmed** (zero) / ?unknown? (purpose) |

**Which axis a scroller moves along is ?unknown?.** No record in the game sets more than one
component, and the model's UV table is untouched, so the data cannot say. The images argue for
the horizontal one: measuring how smoothly each texture's opposite edges join, a scrolling
texture's left and right edges are ~3× more continuous than its top and bottom (median seam
0.35 against 1.00, normalised by the mean step inside the image on that axis), and ~3× more
continuous than an average texture's horizontal seam (1.19). That is also the axis a PS1
texture window wraps most naturally. Treat it as *likely*, not settled.

**Mesh-swap animation is a separate thing and is not declared here.** 119 groups of meshes in
the game share identical geometry while each uses one distinct texture (`mdl.find_texture_flipbooks`);
those are driven by gameplay code writing a display id, not by any table in the data.

## 10.6 The tail: every pack ends on an 8-byte boundary

This was the last unclaimed byte in the TEX corpus — 2892 of them, spread over 358 of the 400
packs, which the coverage walk kept reporting as a hole. It is a run of zeros after the last
structure in the file, and it is now measured exactly:

| Zeros needed to reach a multiple of 8 | Zeros actually there | Packs |
| --- | --- | --- |
| 0 | 0 | 42 |
| 0 | 8 | 263 |
| 4 | 4 | 44 |
| 4 | 12 | 51 |

**400 of 400 packs have a length that is a multiple of 8**, and the run never exceeds 12 bytes
and is entirely zero in 358/358. So the tail is the alignment pad, plus a further eight zero
bytes in 314 of the 400 — and **which 314 is settled**:

| | packs | extra beyond the alignment pad |
| --- | --- | --- |
| `u32@0x18` non-zero — the pack has an animation block | 86 | **0** |
| `u32@0x18` zero — it has none | 314 | **8** |

The match is exact both ways, 400/400, and eight bytes is the size of §10.5's animation block
header: the two pointers, to the flipbooks and to the scrollers. A pack that declares no
animation still carries that header, zeroed, with nothing pointing at it. Read that as the
builder always emitting the header and only filling `+0x18` when it has something to put in it
— the biconditional and the size are measured, the intent is the obvious reading of them.

**No reader was traced for these bytes and no reader would need to be** — nothing points at
them, and a pack with none of them (42 of them) loads the same way. That is a search that came
back empty, not proof the loader ignores them. `tools/coverage.py` claims the run only after
testing that it is zero and shorter than 16 bytes, never by position, so a real structure
sitting at the end of some future file still shows up as a hole.

---

# 11. Rendering

## 11.1 The GPU packet

Both primitive kinds occupy **0x28 bytes** in a flat array, so the three passes below can
index the same array by triangle number. Untextured triangles waste four bytes.

**Textured — GP0 0x34 / 0x36 (POLY_GT3), tag length 9:**

| Offset | Contents | Written by |
| --- | --- | --- |
| +0x00 | OT tag; length byte 9 at +0x03 | 0x80018080, 0x800195F0 |
| +0x04 | `R0 G0 B0` \| `0x34`<<24 (`0x36` when translucent) | 0x80018088 / 0x80017E64 |
| +0x08 | screen `x0, y0` (GTE SXY0) | 0x80019620 |
| +0x0C | `u0, v0` \| clut<<16 | 0x80018064 / 0x80018024 |
| +0x10 | `R1 G1 B1` | 0x80017EB4 |
| +0x14 | screen `x1, y1` | 0x80019624 |
| +0x18 | `u1, v1` \| tpage<<16 (ABR patched from the colour index) | 0x80018074 / 0x80018044 |
| +0x1C | `R2 G2 B2` | 0x80017EC0 |
| +0x20 | screen `x2, y2` | 0x80019628 |
| +0x24 | `u2, v2` | 0x80018090 |

**Untextured — GP0 0xE1 draw-mode word plus GP0 0x30 / 0x32 (POLY_G3), tag length 8:**

| Offset | Contents |
| --- | --- |
| +0x00 | OT tag; length byte 8 |
| +0x04 | `0xE1000000` \| dither/ABR bits — the GPU draw-mode command |
| +0x08 | `0x00000000` (GP0 NOP) |
| +0x0C | `R0 G0 B0` \| `0x30`<<24 (`0x32` when translucent) |
| +0x10 | screen `x0, y0` |
| +0x14 | `R1 G1 B1` |
| +0x18 | screen `x1, y1` |
| +0x1C | `R2 G2 B2` |
| +0x20 | screen `x2, y2` |
| +0x24 | unused |

## 11.2 Three passes over the same array

| Pass | Address | Reads | Writes |
| --- | --- | --- | --- |
| Geometry | 0x800193A8 | mesh 0x10 (vertices), mesh 0x14 (strips) | screen XY, OT insertion, backface/near-far culling |
| Texture & UV | 0x80017EE8 | mesh 0x14, 0x18, 0x1C, 0x20; model 0x24 | command bytes, tag lengths, clut, tpage, all three UVs |
| Colour | 0x80017D90 | mesh 0x14, 0x20; model 0x20 | the three RGB words, translucency bit, ABR |

Both the second and third pass write the primitive command byte and the ABR bits, so they are
partly redundant; the third pass runs last (it applies translucency, which the second sets
unconditionally to the opaque code). That ordering is *likely*, not proved.

Single-primitive variants of the same builders exist at 0x800177F4 and 0x8001795C.

## 11.3 Corner correspondence

The three passes write corner *i* of the position, colour and UV in lockstep, so the
correspondence is **positional**, not by index:

* positions: strip vertices `k`, `k+1`, `k+2`
* colours: table entries `c`, `c+1`, `c+2`
* UVs: table entries `t`, `t+1`, `t+2`

Rotating a triangle's corners without rotating its colours and UVs the same way lands the
texture rotated or mirrored. Consecutive triangles inside a strip wind opposite ways — the
game handles it by flipping the sign of the `NCLIP` test per vertex flag bit 0 (§4.2), not by
reordering. An exporter that wants consistent winding must flip every other triangle **and**
swap the matching attributes.

## 11.4 Degenerate triangles

A strip stitches to the next run by repeating a vertex position. Such triangles are present in
the arrays and must keep their slot in the flat triangle numbering, but they collapse to zero
area and produce nothing visible.

**Degeneracy is a property of the pose, not of the mesh.** Comparing the set of zero-area
triangles in a mesh's static pool with the set in each decoded animation frame (§9): the two
sets differ in **133 of 1036 clips** and **13,864 of 49,151 frames**, and in **31 clips** a
triangle that is degenerate in the static pool is pulled apart by some frame — up to 12 of
them in a single frame. A reader may drop degenerate triangles for display, but it must not
drop them from the *arrays*, and a viewer that culls them once against the static pose will
punch holes in the animation.

---

# 11.9 The engine functions read while tracing

One line per function actually read at instruction level, so the next search starts from a map
instead of from scratch. Addresses are NTSC-U EXE unless marked as an overlay.

**IO and loading**

| Address | Role |
| --- | --- |
| `0x80013650` | Entry-load front door: enqueue with length `[handle+4]`; handle shaped like a file-table row |
| `0x80013034` | Async IO enqueuer: 7-field request from free list `[0x80050F3C]` into queue `0x80050628+0x0C/0x10` |
| `0x8001316C` | Matching dequeuer |
| `0x80013290` | Synchronous wrapper (spin + completion flag `0x80069E7C`) — **no caller found anywhere** |
| `0x80012FFC` | Queue poll |
| `0x8001231C` | CD state machine: dispatches completions to `[req+16]` callback or result slot |
| `0x800121F8` | Request allocation stage: length `[req+8]` rounded to whole sectors; `[+24]` = caller's buffer |
| `0x80012690`-band | Group preloader: reads one contiguous sector run, splits per entry at `(sector[i]−sector[first])·2048` |
| `0x80016434/66B8/68E0/6BDC/6C64` | The five `0x800133C8` callers — every one a clip-blob fetcher (§9.2) |
| `0x80016928` | Blob teardown: frees blobs, zeroes `+0x14` slots |

**Heap**

| Address | Role |
| --- | --- |
| `0x8004E0F0` | The heap; file table at `0x8004E110`, group table after it |
| `0x80011654` / `0x80011748` | The two allocators — near-twins on the same heap |
| `0x800131B0` | Alloc pre-check: fit → scavenger → compactor → retry |
| `0x80011498` | Shrink-and-free: trims a block, frees the remainder (4 callers: 3 group trims + blob loader) |
| `0x80011544` | Block unlock |
| `0x80011D28` | **Heap compactor** |
| `0x80017640` | Scavenger: frees packet caches |

**Model init and draw**

| Address | Role |
| --- | --- |
| `0x8001DE18` | Ctx builder: caches absolute pointers; writes model base into file at `T(0x3C)+0x0C` |
| `0x8001D6B4` | §8.5 instance builder: 168-byte runtime records |
| `0x8001682C` | §9.2 resident-blob scheduler (no-op when `0x40` is 0) |
| `0x8001D894` | Per-instance transform setup; sets owner global; OT depth from instance `+104` (§8.5 `+0x9C`) |
| `0x8001DAF8` | Engine-side draw-all-instances |
| `0x80019A60` | **Draw-by-id**: §2.3 namespace dispatch — `0x2000` headers at `model+52·i+36` (1-based), `0x5000` via `0x800159C4`, `0x3000` billboard from descriptor `[owner+0x18]+56·id` → `0x80029D28`, `0x1000/0x4000` clips |
| `0x80019094` | Packet cache get-or-build; fills `mesh+0x00`; double-buffered by frame parity |
| `0x80018694` | Cache-struct allocator (pool `0x80056860`) |
| `0x800184F0` / `0x800180BC` | Packet builders: pool `0x80056850`, then `0x80017EE8` + `0x80017D90` once per mesh |
| `0x80017EE8` | Texture&UV packet builder; resolves `model+0x24` **live** at `0x80017F30` via `[[0x80056998]+0x0C]` |
| `0x80017B08` | Colour/strip builder prologue; same live owner-global resolve |
| `warp.bin 0x800BBE60` | Per-placement draw: bit-15 test, model from `[struct+0x6C]+0x0C`, calls `0x8001D894` then `0x80019A60` |
| `warp.bin 0x800BA410` / `0x800BBD24` | Mode loop / packet patcher (owner-global users) |

**Globals**: `0x80056998` current owner (`+8/+12` params, `+0x10` descriptor array, `+0x40`
arena, `+0x46` bound); `0x8005AB50` current ctx; `0x80056850/60` packet pools; `0x80069E7C` IO
completion flag.

# 12. Where the shipped Python disagrees with this spec

Read as a to-do list. Line numbers are from the files as of this writing.

Thirteen of the items this section used to list have since been fixed. They are recorded as
short "fixed since" notes rather than deleted, so a reader comparing an older checkout can tell
which is which.

### `crashbash/formats/mdl.py`

| Line | Issue | Corpus impact |
| --- | --- | --- |
| 66 | `STRIP_FLAG_UNTEXTURED = 0x01` is the only documented strip flag. | Bit 3 is set on **24,151/81,045** strips (29.8 %) and is undocumented. (No reader found — see §5.1.) |
| 714–751 | `_read_shared_tables` derives the UV table's extent from the largest index used. | The file states it: the table ends at `T(0x28)`, valid in **373/373**. Using the stated bound would also make truncation detectable. It would also stop the UV table from running into the position pool, which is now known to be live data (§9.5). |
| 106–107 | `unk13` / `unk14` do not name their byte offsets. | They are mesh 0x0C and 0x0E. |
| — | Nothing in `mdl.py` reads `model+0x40` / `0x44`, so a `Model` never mentions that it animates. | `crashbash/formats/anim.py` reads the table separately and takes a parsed `Model`; that split is deliberate, but `Model` should at least carry the clip count. 225/400 models are affected. |
| 377–397 | `decode_texture_runs` — `(entry >> 9) & 0x3F`, countdown never reset. | **Correct**; matches 0x80017F78/0x80017F7C exactly. |
| 52–57 | `COLOUR_INDEX_MASK 0x1FFF`, `COLOUR_BLEND_SHIFT 13`, `COLOUR_FLAG_TRANSLUCENT 0x8000`. | **All correct**; match 0x80017DEC, 0x8001803C, 0x80017E1C. |
| 356–374 | `read_strip_list` — high byte = count, low byte = flags, 0xFF terminates. | **Correct**; matches 0x800193C4/0x800193DC/0x800193E4. |
| 332–515 | `segment_strips`, `segment_strips_exact`, `refine_strips` — geometric strip reconstruction. | Dead safety net on retail data: the strip list accounts for the stated triangle count in **5990/5990** meshes, so `strips_exact` is always true. Harmless, and now documented as a fallback at the call site. |

**Fixed since the previous revision of this document** (verified against the current files):
the vertex stride now switches on `mesh+0x28` and reads the normal array separately (line 615);
the "header padding is not zero" warning no longer fires on 0x00/0x28/0x2C (596);
a mesh count of 0 is accepted (696); `colour_start <= uv_start` is non-strict (729); the
phantom bit-15 flag on the UV index is gone; the vertex-flag comment names bits 1, 2 and 8
(631); the module docstring covers the whole mesh header including 0x28 and 0x2C; and
`read_model` now reads the object table as well as the numbered array, which is where a level
keeps its set — 1971 meshes and 96,232 triangles the reader used to walk straight past (§8.3).
They live in `Model.objects`, apart from `Model.meshes`, because the two arrays are addressed
differently and `install_mesh` / `transplant_mesh` index the numbered one.

`read_mesh` also opens the attachment block at mesh+0x2C into `Mesh.volumes` — 1717 records
over 812 meshes, read with no warning anywhere in the corpus — and the viewport draws them as
boxes behind a **Volumes** toggle. `mdlwrite` still carries the block as opaque bytes,
which is right: a writer must move it unchanged, not rebuild it from a decoded reading that
holds for characters and not for the rest of the family.

Reading the object table was only half of it: the objects still stood where their own vertices
put them, which piled a level's set on the origin. `read_model` now also reads the placement
list of §8.5 into `Model.instances`, and `Model.draw_list()` pairs every mesh with the
transform to draw it under — the numbered meshes and the 96 unnamed objects at identity, and
everything else once per record. `mdlwrite` has not been taught about either list; a writer
that moves a mesh block still has to keep the placement records pointing at the right object.

### `crashbash/formats/anim.py`

New since the previous revision, and the reference implementation of §9. Checked line by line
against this chapter:

| Line | Status |
| --- | --- |
| 128–134, 268–302 | Keyframe stride, index encoding and the blend `A + ((B − A) * w >> 12)` — **correct**, and the shift is a Python `>>` on `int32`, which floors like the GTE (§9.6). |
| 253–266 | The pool is held as `int32` so the blend cannot wrap. Correct, and necessary: the intermediate `(B − A) * w` overflows `int16` constantly. |
| 273–284 | Pool indices are clamped with a warning rather than raising. Needed by exactly one clip (§9.10); harmless everywhere else. |
| 84–89 | `_CANDIDATE_NAMES` is a hand-written guess list. **The game's own names are in the archive** — the mode overlays hold the literals the hash is computed over (§9.1). Eleven of the 28 words in the list (`BOUNCE`, `FLY`, `HOP`, `LAUGH`, `OPEN`, `SINK`, `SLEEP`, `STOP`, `SWING`, `TURN`, `WALK`) occur in no overlay at all, and the list misses `TAUNT_A`, `PICKUP`, `HOLD_THROW`, `HOLD_SLOW`, `RECOIL`, `IDLE1`, `SKATE`, `DAZED` and `MINE`, which do. Sourcing the candidates from `overlays/modes/*.bin` would replace a guess with a measurement. |
| 344–353 | `_vertex_count_from_stride` is ambiguous by one when `V` is odd, and says so. It is used for one clip only. |
| 62–63, 459–461 | The shared-pool extent is taken as `T(0x28)..T(0x08)`. Correct — §9.5 confirms the pool is exactly consumed across all 40 models that use it. |

### `crashbash/formats/tex.py`

| Line | Issue |
| --- | --- |
| 19 | `HEADER_SIZE = 0x24`. The palette table starts at **0x20**. The constant is only used as a minimum-size check so nothing breaks, but it is misleading. |
| 152 | `reader.skip(4 * 5)  # skipToTex, skipToPal, skipToUnk, ptrNext, zero`. 0x0C and 0x10 are **self-relative pointers** obeying the MDL convention (400/400 each) — to the texture record array and to the palette table. Following them instead of hard-coding the layout is what lets a repacked file be read. 0x14 and 0x18 are genuinely unknown; 0x1C is zero in 400/400. |
| 172–175 | The four bytes at record +0x08 that are skipped are **0 in 15,160/15,160** — worth asserting rather than skipping silently. |
| 46–57 | `unk01..unk04` = record +0x04..+0x07; `unk22` = record +0x0E (values 0 or 3); `flags` = record +0x10 (19 distinct values). `unk01` is zero in 15,160/15,160, and `unk03`/`unk04` are bounded by the record's own width and height (§10.3) — worth naming even though no reader was traced. |
| 65–79 | `NO_PALETTE = 0x7FFF` / `is_swatch`. Consistent with the EXE: the bit-15 texture-run path targets the pack's **last** texture and takes its CLUT from the palette table (0x80017FC4–0x80017FFC). 355 textures carry the marker. |

### `crashbash/archive.py`

| Line | Issue |
| --- | --- |
| 63–74 | `_MAGIC_KINDS` still treats the first `u32` as a magic; `_classify` now validates the ambiguous kinds against their contents, which is what fixed the counts below. The magic table itself remains a hint. |
| — | `sfx1`/`sfx2`/`sfx3` is not three formats; it is one container with 2, 3 or 4 sections. `sfx3` has exactly one member. |
| — | `overlays/gameeng.bin` (index 991) falls through to `bin` although it is a MIPS code overlay like the 14 `overlays/modes/*.bin` that get `code`. Its leading word is an absolute pointer (0x80092BDC), so no fixed magic can catch it. It matters more now: the overlays are where the animation clip names live (§9.1). |
| 1–7 | The docstring omits the group table at VA 0x80050010 — 130 records of `{first_index, count, bytes}`, the unit the game actually loads. Not modelled at all. |
| 182 | `Entry.offset` is precomputed as `sector * 2048`. Keeping the raw sector would match the game (which stores the sector and shifts by 11 at every use) and make group-span arithmetic expressible. |
| 35–51 | Eleven of the twelve build entries could not be checked: only the NTSC-U EXE is present on disk. |

**Fixed since the previous revision:** `by_group('texture')` now returns 400 and
`by_group('audio')` 160; index 151 (`overlays/text/crate.bin`) classifies as `bin`, as do
143 and 147; the `map` kind, which had zero real members, is gone.

### `tools/psxdis.py`

**Both previously listed issues are fixed:** `Exe.scan` is now a real method (line 26) — every
exhaustive scan quoted in this document runs through it — and `file_va()` (line 22) converts an
EXE file offset correctly, leaving `off`/`va` as the text-relative pair they always were.

---

# 13. Refuted during verification

Plausible-looking readings that are **wrong**. A reader that implements any of these will
produce subtly broken output.

| Claim | Verdict | Corrected statement |
| --- | --- | --- |
| "Mesh header 0x0A is only ever 4, 6 or 7." | **Refuted** | Over 5990 meshes: 4 (3112), 6 (2620), 7 (255), **5 (2)**, **2 (1)**. Value 5 in `models/arena/medieval_keg/objects.mdl` mesh 13 and its sibling; value 2 in `models/mainmenu/models2.mdl` mesh 5 — which is also the only mesh with an empty strip list. |
| "There are 5989 meshes." | **Refuted** | There are **5990** mesh headers; 5989 have a non-empty strip list. |
| "Mesh header 0x00/0x04/0x28/0x2C/0x30 is zero padding." | **Refuted** | 0x04 and 0x30 are zero 5990/5990 and unread. But 0x00 is a runtime pointer slot the loader fills and the game dereferences (0x80016FA0); 0x28 is non-zero in 300 and points at a per-vertex normal array; 0x2C is non-zero in 777 and is the target of the 0x2000 id namespace. |
| "The vertex block is 16 bytes per vertex when 0x28 is set." | **Refuted (as interleaving)** | The block is two consecutive 8-byte arrays: `V` positions, then `V` normals. `T(0x28) == vertex_pool + 8*V` in 300/300. A reader that strides positions by 16 will read every other vertex and then read normals as positions — which is exactly why a naive AABB check fails on those 300 meshes (bounds match the true extremes in 5986/5989 when read at stride 8, but only 5686/5989 at stride 16). |
| "Bit 15 (or 13) of the per-triangle UV index is a flag." | **Refuted** | Max index over 363,251 triangles is **2942 (0x0B7E)**. Bits 12–15 are never set. It is a plain index. |
| "0x80015700 works on an unrelated struct, not a mesh header." | **Refuted** | `base + 52*id + 0x24` **is** the mesh header (identically `0x58 + 0x34*(id-1)`), and the base register there is the MDL file base delivered by the dispatcher at 0x80015664. That site is the live reader of mesh +0x2C. |
| "The bounds are `minX,minY,minZ,maxX,maxY,maxZ`." | **Refuted** | The interleaved order `minX,maxY,minZ,maxX,minY,maxZ` matches the true extremes in **5986/5989**; the straight order matches only **261/5989**. |
| "The first u32 of an archive entry is a magic number." | **Refuted** | For 560/992 it is the byte length of the leading offset table (`4*(1+n)`), which is why 0x08/0x0C/0x10/0x14 form an arithmetic series. For 400 models it is an exporter stamp the EXE never compares. The `map` kind has zero real members. |
| "There are 33 distinct leading u32 values." | **Refuted** | **31**, measured: 0x08 ×401, 0x0C160029 ×399, 0x0C ×133, 0x10 ×28, 0x00020000 ×5, plus 25 singletons and 0x14 ×1. |
| "`by_group('texture')` gives the texture count." | **Refuted** | It gives 401. Only 400 satisfy `word0 == 8 and word1 == size`; index 151 is a text overlay. |
| "The entry count 992 must come from outside the EXE." | **Refuted** | 992 is stored as no literal anywhere (0 aligned `u32 == 992`, 0 immediates 0x3E0, 76 zero bytes in front of the table) — but it is *implied* by the file table abutting the group table at 0x80050010, and is recoverable by walking the group chain or the strictly-increasing sector field. The table address itself is materialised at 11 `lui/addiu` sites. |
| "`group.bytes == (sector[first+count] − sector[first]) * 2048`." | **Refuted** | `group.bytes == Σ ceil(size_i/2048)*2048` (130/130). **Eight** groups — first index 0, 141, 153, 446, 606, 716, 819, 825 — contain interior padding sectors and differ from the span form by 2048–8192 bytes. (An earlier count of six missed groups 819 and 825.) |
| "`T(0x20) < T(0x24)` in all models." | **Refuted** | Strict `<` holds in 378/400. Non-strict `<=` holds 400/400; 22 models have both tables empty at the same address. |
| "Every strip flag bit above bit 0 is unused." | **Refuted** | Bit 3 is set on 24,151/81,045 strips. (Its *meaning* is still unknown — but "unused" is wrong.) |
| "The `model+0x44` table is a generic sub-file directory with an unknown +0x08." | **Refuted** | Every one of the 1037 records is an animation clip: +0x08 is the frame count (bounds-checked at 0x80019B70), +0x0C points at the mesh the clip drives, +0x10 is the hash of its name. See §9.1. |
| "`model+0x28` is the bind pose." | **Refuted** | It is a shared *position pool* that animation keyframes index, and only 208 of 1037 clips use it at all. Decoding frame 0 of every clip reproduces the mesh's static positions in **39/1037** — there is no bind pose anywhere in an MDL. See §9.5, §9.9. |
| "The animation blend rounds to the nearest unit." | **Refuted** | GTE `INTPL` with `sf=1` shifts arithmetically, so the blend floors: `A + ((B−A)*w >> 12)`. Flooring differs from round-to-nearest on **10,071,343 of 38,535,099** interpolated coordinates (26 %). See §9.6. |
| "The animated pose replaces the mesh's vertex pool." | **Refuted** | The decoders fill a separate 0x2038-byte buffer at 0x80056AC8 and the rasteriser takes the vertex array as an argument (0x80019D9C vs 0x80019D8C). `mesh+0x00` is a primitive-cache slot and is never written by the animation path. See §9.6. |
| "The block at 0x80058B00 is eighteen track handlers, so the graph holds eighteen track types." | **Refuted** | It is a table of **16-byte entries indexed by node type**, which is how the spawner reads it (`sll $v0, $v0, 4` before `jalr`). Six rows are live and two are null, and walking every root of every model finds 3333 nodes with no type outside 0..5. The "eighteen handlers" were the four columns of six rows. See §9.11.9. |
| "A frame record is 16 bytes starting at the blob base." | **Refuted** | Record *f* is at `blob + 4 + 16*f`; `blob+0x00` is the blob's pool pointer. Under the shifted reading only 1,925 of 49,167 records validate and no clip validates completely. See §9.3. |
| "The texture descriptors hang off the render context at 0x80056998." | **Refuted** | They hang off a second context at **0x80055684**, which only three sites in the image reference and which `0x8002C774` is what hands to the accessors of §6.2. Nothing anywhere on the disc — the EXE or any of the 15 code overlays — stores to +0x18 or +0x1C of 0x80056998. Aiming three exhaustive scans at the wrong structure is why §14 concluded for two revisions that the loader was not in `SCUS_945.70`; it is, at 0x8002926C. See §10.4. |
| "The `mesh+0x2C` volume is a standing cylinder and field 3 is its radius." | **Refuted** | Field 5 is a second horizontal extent — non-zero in 349 of 1717 records and **different from field 3 in 25** of them, which a circle cannot be. Field 3 is half the mesh's width (median ratio 1.000) and not half its diagonal (0.707), so it is an inscribed half-extent rather than a wrapping radius; and the 324 crate records describe exactly their mesh's own 256-unit cube. See §8.4. |
| "TEX record +0x08 is the tpage." | **Refuted** | Mine, and wrong. `0x80029450` does copy it into `descriptor+0x0C`, but that is not the page: the render pass is handed `descriptor+0x18`, so what §6.2 calls +0x0C is descriptor **+0x24**, written by `0x80028D40` from a VRAM rect the allocator hands out. Nothing reads +0x0C afterwards. See §10.4. |
| "Nothing on the disc computes a CLUT id, so the arithmetic must be unlike libgpu's." | **Refuted** | `0x800364FC` *is* `GetClut`, letter for letter — `(y << 6) \| ((x >> 4) & 0x3F)`. Three scans missed it because they looked for the macro **inlined**, an `srl ,4` beside an `sll ,6`; the game calls it instead, and builds the tpage by hand with a shift pattern the same scan did not match. A negative from a shape scan bounds where a shape is, not where a routine is. |
| "A level's objects are drawn where their own vertices sit." | **Refuted** | They are drawn once per record of the placement list at `model+0x18`, each under that record's own rotation and position: 2689 records over 1971 objects, 2120 of them moved off the origin. 668 records share an id with another record and **no two of those share a transform**, so the copies cannot be meant to coincide. See §8.5. |
| "The 0x4000 id namespace indexes its table the way 0x5000 does." | **Refuted** | 0x5000 is `(id & 0xFFF) − 1`; 0x4000 packs two fields, `clip = (id & 0xF80) >> 7` and `frame = id & 0x7F` (0x80019B1C). Reading a 0x4000 id as `id & 0xFFF` makes the 45 clip placements in the corpus, all of them id 0x4000, look like an out-of-range index −1 instead of clip 0 frame 0. |

---

# 14. Unknown

Stated precisely, with the measurement that bounds each one.

**How much is left, measured.** `tools/coverage.py` marks every byte a structure in this
document accounts for and prints what nothing claims. Over the 31.8 MB of MDL in the archive
it now reaches **100.00 %**, TEX likewise — every byte has a *named owner*, which is a claim
about location, never about meaning. The last two holders were `gamelogo_text`'s dead
duplicate mesh block (§9.13 — both headers point at the second copy, nothing at the first)
and the **20-byte rows** of §9.12 in the two cutscenes, which a later pointer scan showed to
be **unreachable by any self-relative i32 in their own files** — positional access or none.
Both are claimed by the tool only under a byte test, so a future file with something else
there still surfaces as a hole. Neither claim says the bytes are unused — only that **what
reads them could not be validated**.

The tool walks the **TEX** corpus too, and there it now reaches **100.00 %** of 15.2 MB. The
last 2892 bytes were the zero tail of §10.6: every pack's length is a multiple of 8 in 400/400,
and the run of zeros that gets it there is 4 bytes in 44 packs, 8 in 263 and 12 in 51, never
more than 12 and entirely zero in 358/358. The eight bytes that sit on top of the pad in 314 of
them turned out to be an **empty animation-block header**: `u32@0x18` is zero in exactly those
314 and non-zero in exactly the other 86, a two-way match over 400/400, and eight bytes is the
size of that header. The walker claims the tail by testing the bytes, never by position, so a
structure at the end of a future file would still surface as a hole.

The audit is worth running for what it catches rather than for the number. It found the mesh
terminator of §3, the padding rule of §2.1 and the hub block of §8.6 — and then it found a
**bug in this project's own reader**: `_root_offset` bounded the root array by `len − 0x40`,
and the array sits close to the end of the file, so 75 models were rejected outright and
their scenes never read. Bounding each read by what it needs instead took the corpus from
2217 nodes to **3333** and from 130 models with a playable scene to **186**. Unclaimed bytes
are not only undocumented format; they are also where a reader is quietly skipping something.

**MDL file header**

* **0x00** — the stamp encodes *something* (the one file with 0x09160026 is also the one file
  that breaks the 0x50 rule), but n = 1 for the alternate value and neither constant appears
  anywhere in the EXE. A date reading (0x0C/0x16/0x29 vs 0x09/0x16/0x26) fits the shape and
  nothing confirms it.
* **0x0C** — no reader found, searched disc-wide. A model base is always reached as
  `[owner+0x0C]`, so a read of this field is the two-step shape `lw model, 0x0C(owner)` then
  `lw ?, 0x0C(model)`. Over the executable and all 15 overlays that shape occurs **14 times**,
  and every one is a linked-list walk — 0x800286A4 chains the VRAM allocator's free rects,
  0x80011CC0 walks a list node — not a model. `>= i32@0x40` in 400/400 and equal in 328/373;
  "capacity vs used" is a guess. Distribution: 0 (155), 5 (63), 2 (51), 1 (36), 6 (30),
  7 (21), 3 (15), 8 (8), …
* **0x30, 0x34** — zero in 400/400; no reader found. Being zero everywhere, a reader could
  exist and never show its hand, so "padding" is the reading that fits and not a validated
  one.
* **0x50** — the arithmetic is unambiguous (`base + value` lands on the end of the 0x44
  directory) and no code reading it has been found. The *meaning*, though, moved during the
  warp-room probes: an earlier revision here said the blobs living past it and being read
  proved it marks nothing resident, and that argument now cuts the other way — the blobs are
  read **from disc, by explicit byte range, into fresh allocations** (§9.2, all five callers
  traced), which is machinery with nothing to do if the file's tail stayed in memory. Together
  with nine hardware probes, `resident_size` is behaviourally supported and its reader is
  still untraced; see the upgraded row in §2.1 and the load-path map in §11.9.
* **0x1C object table** — the mystery here is smaller than this entry used to make it sound.
  The 12-byte object records are read (§8.3) and what follows them is the node graph, which
  §9.11 reads and `tools/coverage.py` now accounts for down to 292 bytes of inter-node
  alignment over the whole archive. What is still unexplained is only the *boundary*: no
  header field states where the records stop and the graph starts, so a reader finds it by
  the chunk-descriptor test of §8.3, and the block's total extent is not a multiple of 12
  (mod 12 = 0 in 279 models, 4 in 70, 8 in 51).
* **0x18 sub-object header, the fields the binder does not read** — +0x00/+0x04/+0x08 are
  0x2000 in 73/73 with no reader, +0x14 and +0x18 hold the same value in 73/73 and land
  4 bytes apart near the end of the file, +0x24 takes 13 values. The array itself and the
  placement records are **closed**, see §8.5.
* **The rest of a +0x0C list entry.** The list is **closed**, and so is most of the key space:
  all eleven call sites of the lookup are read in §8.5, and they ask for key 1 (the camera, in
  73/73 models), key 2, key 9, and **keys 101..112**, which `warp.bin` walks in a twelve-slot
  loop and which occur in exactly the seven rooms it drives. What is left open is narrower
  than it was: the ~0x50 bytes of the 104-byte record outside +0x0C, +0x30 and +0x4C, and the
  four keys (0, 3, 203, 204) that no call site among the eleven asks for. **I could not
  validate what reads those, which is not evidence nothing does.** The record itself is
  better mapped than it was: all eleven sites were followed and they touch ten of the 104
  bytes (§8.5), including two — +0x3C and +0x40 — that code reads and that are **0 in
  217/217**. The remaining ~78 bytes no site among the eleven touches.
* ~~**The block at sub-object +0x10**~~ — **closed**, see §8.5. It is `[i32 count]` then that
  many 16-byte records, read at 0x80024B70 through the instance's +0x30, and all 473 records
  in the archive resolve their +0x0C inside their own block. What the three-word payload
  means is still open.
* **What draws the 96 objects no placement record names**, in 13 of the 73 models — 16 in
  each `balls_crash` arena, 6 to 9 in each warp room. Two hypotheses have been tested and
  both fail. **Not the scene nodes**: over the whole corpus no mesh is both named by a node
  and named by a record (122 have a node, 1861 have a record, the sets do not meet). **Not a
  sibling model's list either**: an object entry can point into a neighbouring file (§8.3),
  so a level next door could in principle place them — checked every object entry in every
  model against every other model's placement list, and **0 of the 96** are claimed that way.
  I could not validate what draws them, and that is not evidence nothing does.
* **The unread bytes of a placement record.** The loader at 0x8001E0A8 consumes +0x00,
  +0x04..+0x0C, +0x28..+0x47, +0x74..+0x77, +0x88, +0x9C and +0x9E; the rest of the 160 is
  copied by nothing. One of those is now followed the whole way: **+0x9E** reaches the global
  at 0x80056AC4 through runtime +0x8A, and 0x800190E4 tests that global for non-zero on a
  draw path — but the field is 0 in 2689/2689 records and the flag bit that would carry it is
  clear in 2689/2689, so no shipped asset ever takes the path. Knowing where a field goes and
  finding the data never sends it there is a different result from not knowing, and it is
  recorded as one. That includes the second MATRIX at +0x48 — identical to the first in
  541 of 2689 records and therefore not simply a duplicate — and the scale triple at +0x18,
  which is 4096, 4096, 4096 in every record measured.
* ~~**0x28 vector pool framing**~~ and ~~**0x44 record +0x08 / +0x0C**~~ — **closed**, see §9.
  The pool is the position source animation keyframes index; +0x08 is a frame count and +0x0C
  points at the mesh a clip drives.

* **Why the colour and UV tables are pinned in `warp_room1`** — the largest unknown this
  document currently carries, and the best-instrumented. Repointing `0x20` crashes the room
  and repointing `0x24`/`0x28` scrambles every textured surface, established by a ladder of
  eleven hardware probes whose one-variable deltas are tabulated in §2.1. The mechanism is
  **unexplained after static reading**: every explanation tried is listed there with what
  killed it, thirty-one engine functions are mapped in §11.9, and every traced consumer
  resolves both fields live — which is precisely what makes the scramble a paradox. The open
  observations that would decide it are named in §2.1. I could not validate the mechanism;
  the behaviour itself is validated eleven times over.

**MDL mesh header**

* **0x0A** ("format"), **0x0C** (non-zero in 344) and **0x0E** (non-zero in 162) — the
  distributions are known and the meanings are not. The search for a reader has been run
  twice: first over 0x80014000–0x8001F000, then **over every code blob the disc ships** — the
  executable, the 14 mode overlays and `gameeng.bin`. A mesh header can only be reached three
  ways (§3, §8.3), all of which materialise the address with an `addiu` of 0x58, 0x34 or 0x24,
  so each load's base register was followed back looking for that shape:

  | Offset | Loads, disc-wide | Off `$sp` | No mesh arithmetic within 24 | Candidates |
  | --- | --- | --- | --- | --- |
  | 0x0A | 132 | 6 | 122 | 4 |
  | 0x0C | 102 | 7 | 95 | **0** |
  | 0x0E | 128 | 6 | 120 | 2 |

  Every candidate was read. The four at 0x0A are one routine copied into four mode overlays,
  an `lwl`/`lwr` pair at +0x09/+0x06 straddling **6-byte records** — the shape of the vector
  pool (§7.3), not of a 0x34 header. The two at 0x0E are `lh $a3, 0xe(...)` inside the prop
  and actor handlers, reading the per-tick context the same way the type-4 handler does
  (§9.11.10). 0x0C has no candidate anywhere.

  **No reader was found, and that is now the strongest form of the statement available from
  static analysis**: not one site on the disc reaches these offsets through anything shaped
  like a mesh address. It is still a failed search — a base arriving through a pointer or
  more than 24 instructions earlier would not appear — but the shape it looked for is the
  only one the game uses to reach a header. That is what makes the `0x0C == 100` correlation
  of §9.11.6 a description of the backdrop domes rather than a cause of them.
* **0x04, 0x30** — zero in 5990/5990; no reader found. Same caveat as the header's 0x30/0x34:
  a field that is zero in every shipped asset would look identical whether it is read or not.
* **0x2C block contents** — the 16-byte records are decoded for characters (§8.4) and not for
  the rest of the family. The `u16` at +0x00 is 0 (769), 2 (6) or 5 (2), and no reader was
  found for the block's fields beyond the pointer itself — but the collision case is proven
  behaviourally, so something does consume them.
* **Whether the +0x28 normals are consumed at all** — no dereference of mesh+0x28 was found,
  and the game issues no GTE lighting instruction anywhere (3 COP2 lighting-family hits in the
  entire image, all in libgpu). **I could not validate that anything reads them, which is not
  the same as their being dead.** Software shading needs no GTE opcode, and 300 meshes carry
  a normal per vertex, which is an expensive thing for an exporter to emit for nothing.

**Per-triangle flags**

* **Strip flag bit 3** (24,151/81,045 strips) — no reader found, and the search is wider than
  it was. The old one covered 0x80016000–0x8001E000 only, which turns out to have cut off a
  candidate: **0x8001E7EC** is an `andi` of 8 four bytes past the old upper bound. Re-run over
  every code blob on the disc, masks 8/9/0x0C/0x0E/0x0F/0x18/0x38/0x78/0xF8, it finds **127**
  sites, one of them in the model region. That one was read: `[s0+0x0C] & 8` where `s0` is a
  runtime instance, so it tests an instance flag word and not a strip's flag byte. **I could
  not validate that anything reads bit 3; that is not evidence it is unused.** A test done on
  a byte already loaded into a register, or through a mask this list does not contain, would
  not appear.
* **Vertex flag bits 2 and 8** (130 and 28 vertices) — no reader found, searched the same way.
  Masks 4/5/6/7/0x100/0x104/0x300/0x700 over every code blob give **139** sites, three of them
  in the model region: 0x8001C4D0 and 0x8001C5F8 mask the global at 0x8005B690, and 0x8001E8CC
  masks an instance's `+0x0C`. None reads a vertex's fourth `i16`. Same caveat as above.

**TEX**

* ~~**VRAM placement**~~ — **closed**, see §10.4. It was never in the file: the game allocates
  a rect off a free list chosen by the texture's size class (`0x80028994` picks the bucket,
  `0x80063B1C` holds the heads, `0x800282F8` pops), computes the tpage inline at
  `0x80028D90`/`0x80028DD0` and the CLUT id through `0x800364FC`, which is `GetClut` letter
  for letter. Three scans had missed it because they searched for that macro *inlined* rather
  than called. A model's UV is page-local and stays correct however VRAM is arranged; what a
  replacement must not change is a texture's **size class**, since that is what picks the
  bucket. The selector at `0x8005A640` that chooses between a texture's variants is the one
  loose end — zero in the shipped image and stored nowhere on the disc, the EXE and all 15
  overlays only ever read it.
* **Header 0x14** — multiple of 4 in 400/400, range 120..228,744, 272 distinct values. Not a
  pointer, not any pixel/palette/record total tested, and it differs between structurally
  identical packs. It is no longer unread, though: `0x8002A62C` carries it into the texture
  context at +0x24 (§10.4). What reads it there is the open half.
* ~~**Header 0x18**~~ — **stale, and closed elsewhere in this document.** §10.1 reads it as
  `ptr_animation`, an absolute offset to the animation block, and the 86 non-zero values are
  the end of the palette-plus-texture walk in **86/86**. This entry predates that.
* **Record +0x04..+0x07** — what they *mean* is open; the search for a reader is now as tight
  as static analysis gets. A record can only be addressed through the pack header's +0x0C,
  and **five sites on the whole disc** resolve a +0x0C self-relatively — four of them belong
  to other structures, and the fifth is the builder of §10.4. Every load through the
  builder's walker is enumerated in §10.3: nineteen instructions at six offsets, none of them
  these four. Their bounds against the record's own dimensions still hold in 15,160/15,160,
  so "the used sub-rectangle of a padded block" remains the only reading that fits.
* ~~**Record +0x0E** and **+0x10**~~ — **closed** as far as the code goes, see §10.3 and
  §10.4: +0x0E bit 1 gates a sibling lookup and +0x10 bounds it. What the variants *are* is
  still open, and so is the selector that picks one.
* ~~**The pack tail**~~ — **closed.** The residual was an artefact of a walk that stopped at
  the texture records and never followed a flipbook's frame table into its replacement
  images, which is where the "up to 32,904 bytes" came from. Walking the animation block
  properly (§10.5) accounts for **99.98 %** of the 15.2 MB TEX corpus, and every one of the
  358 spans left is **at EOF and entirely zero**: 4 bytes in 44 packs, 8 in 263, 12 in 51.
  The other 42 packs end exactly on their last structure. It is alignment padding.

**Animation** (§9 — the container, the poses and the playback model are now read; what is left)

* **The auxiliary block at frame record +0x0C.** Its size rule holds in 4734/4734 blocks and
  its only reader hands it to script code, but the contents are undecoded. The 8-byte records
  are the same order of magnitude as mesh coordinates, yet they are not vertices, and one
  model shares four blocks across 273 frames of every clip — so "a per-frame payload" does not
  fit either. See §9.8.
* **Why `models/arena/medieval_mallet/arena.mdl` clip 0 names `T(0x2C)` instead of a mesh**
  (1 of 1037). Its blob is internally perfect. Whether the clip is dead data or reaches a
  different code path is untested.
* **The runtime instance struct** whose +0x70/+0x72 hold the 16.16 frame cursor, whose +0x8E
  holds the mesh-id override, and whose flag word bits 22 and 31 gate them. The *readers* are
  traced (§9.7); the writers of +0x8E were not.
* **How a multi-mesh model is driven.** Each clip names exactly one mesh, but 60 of the 225
  animated models spread their clips over several: the distribution of distinct driven meshes
  per model is 1 (165 models), 2 (33), 3 (12), 4 (4), 5 (3), 6 (2), 7 (4), 9 (1), 11 (1).
  Whether those play together, and what selects them, is outside the file.

**Hierarchy**

* Nothing in this document places one mesh relative to another: a 33-mesh model still has no
  documented joint or parent relationship. Placement itself is no longer missing — §9.11
  reads it, and a node names its own target, a mesh or a clip, rather than depending on an
  outside binding.
* ~~**The type-2 node**~~ — **stale, and closed elsewhere in this document.** §9.11.6 reads
  it as the shot's camera and §9.11.9's table names it the same way; this entry survived a
  revision that decoded it. What remains open about a camera is its *values* for a cutscene
  (below), not the node.
* ~~**Node type 4**~~ — **closed**, see §9.11.10. It is the fade: a window and two levels in
  1.12, ramped by 0x8001F4F8 into the render context's fade slot. Every node type the spawner
  constructs is now named.
* ~~**The rest of the track dispatch table**~~ — the earlier reading of it was wrong and is
  corrected in §9.11.9: the table holds **16 bytes per node type**, not one handler each, so
  its "eighteen handlers" are the four columns of six types rather than eighteen kinds of
  track. Types 6 and 7 are null, and the corpus agrees — 2217 nodes and not one outside
  0..5. What is unread is three columns of the six rows, not eighteen behaviours.
* **A cutscene's camera path.** §9.11 settles that it is not in the MDL; where the overlay
  keeps it is not traced. `menu.bin` never writes the camera's position field directly, so
  the cutscene path either copies a block in (as its own screens do, from 0x8005A9A0) or
  eases toward a target the way `warp.bin` does. Reproducing a shot's framing needs that
  answer. A **level's** camera is now traced and is in the MDL — `gameeng.bin` builds it from
  a +0x0C list entry's two points (§8.5) — but that is a different path from a cutscene's.

**Container**

* The ~40-byte fudge in the DAT builder: an extra sector is reserved when `size mod 2048`
  lands in roughly [2012, 2047]. The boundary is pinned only to within 6 bytes (max
  non-padded remainder 2006, min padded remainder 2012).
* Whether the eight groups with interior padding sectors are safe at runtime.
  `group.bytes` sizes both the buffer and the CdRead, but the splitter at 0x800126F4 places
  entry *i* at `(sector[i] − sector[first]) * 2048`, which for group 0 reaches sector 6519
  while the buffer holds 6516.
* **What a mode overlay can see — and the generalisation that does not hold.** `warp.bin`
  never holds a raw model base: eleven of its sites load a `[owner+0x0C]`, three touch an
  offset an MDL header uses, and all three turn out to be runtime structs or a pointer handed
  straight back to the engine. It was tempting to generalise that to the other thirteen, and
  **that generalisation is false.** Running the same test over all fourteen finds ten sites
  reading a model-only offset, and two of them settle it: `crate.bin` at 0x800B4BDC loads
  `[owner+0x0C]` and then reads `[model+0x00]`, `[model+0x54]` and `[model+0x58]` — the
  stamp, the mesh count and the first header. `papu.bin` 0x800B5130 and `mallet.bin`
  0x800B5ECC do the same kind of thing. So a mode overlay **can** read an MDL directly, and
  the search space for an unread field's reader is the whole disc rather than two files.

  All ten were then followed, and **none reads a model field this document does not already
  record.** `crate.bin` takes the mesh count and dereferences mesh 0's runtime slot at
  `model+0x58` (§3), testing `[slot+0x1A]` against 1, 4 or 5. `papu.bin` and `mallet.bin`
  read 0x10, 0x18 and 0x54; `oxide.bin` reads 0x1C; `menu.bin` reads 0x4C. Two sites *looked*
  like they read the header's 0x30 and 0x34 — the fields listed above as having no reader —
  and both were the scanner's fault rather than the game's: at `polar.bin` 0x800C2250 the
  reads go through `$a1`, loaded separately at 0x800C224C, and the code **writes back** to
  `[a1+0x2C]`, which a file image zero in 400/400 could not survive. The same misattribution
  produced `warp.bin`'s apparent 0x04 and 0x08. A register-attribution bug in a scan is its
  own kind of false positive, and it is worth the same suspicion as a false negative.
* The **code overlays** are readable now, which is how §9.1's name tables and §9.11's node
  handlers were found, but only their entry conditions are mapped. `overlays/modes/*.bin` is
  raw MIPS with no header stating its link address; the address is recovered by sweeping
  every candidate base and scoring how many of the overlay's own `jal` targets land exactly
  on a function prologue. For `warp.bin` and `menu.bin` alike the winner is
  **0x800B32B4** — 24 of 36 internal calls, against 4 for the runner-up — which also means
  only one mode overlay is resident at a time. The same sweep puts `overlays/gameeng.bin` at
  **0x80078C90** (88 prologue hits against 15 for the runner-up, all 571 in-band `jal`
  targets inside the image, and the image then ending at 0x800D7148 — one word past the
  0x800D7144 the file's own header word +0x10 holds). That places it below the mode overlays
  and makes it the resident engine they sit on top of; §8.5 reads its camera setup. What
  each overlay does beyond that is unread.
* The internal structure of the 12 `overlays/text/*.bin` beyond "leading id, then 4-byte
  aligned NUL-terminated strings". They contain absolute 0x800Axxxx pointers, so something
  relocates them at load.
* **Every non-NTSC-U build.** Only `SCUS_945.70` is present on disk. The other eleven
  `(table_offset, file_count)` pairs in `archive.py`, and whether the group table exists in
  the same place in those builds, are untested.

---

# 15. Credits, and where this differs from CTR-tools

The container work here builds directly on **CTR-tools by dcxdemo**
(<https://github.com/dcxdemo/ctr-tools>). Specifically, from `bash_dat` and its companion
data:

* the discovery that `CRASHBSH.DAT`'s directory lives in the EXE as `(sector, size)` pairs;
* the **build → (MD5, table offset, entry count)** table, all twelve rows of which are
  reproduced in `crashbash/archive.py` and only one of which could be re-verified here;
* the `bash_filelist.txt` name list, which is what makes any of the per-family measurements
  in this document nameable.

Where this specification departs from CTR-tools, and why:

| Topic | CTR-tools | Here | Why |
| --- | --- | --- | --- |
| **Strip list byte order** | Low byte = triangle count, high byte = flags. | **High byte = triangle count, low byte = flags.** | The game reads the count with `lbu` from `strip_list + 1` and the flags with `lbu` from `strip_list + 0`: 0x800193C4 / 0x800193DC vs 0x80019414, and independently 0x80017DB8/0x80017DC4 and 0x80017F34/0x80017F40. Under this reading the counts sum to the stated triangle count in **5990/5990** meshes and the strip spans exactly fill the vertex pool in **5990/5990**. The reversed reading is what scrambles the exports. |
| **First u32 = a magic number** | A magic → kind table, with kinds `map`, `sfx1`, `sfx2`, `sfx3`. | It is the byte length of the leading offset table for 560/992 entries, an exporter stamp for the models, and an id or pointer for the overlays. | The arithmetic series 0x08/0x0C/0x10/0x14 is `4*(1+n_sections)`. Validating the offset table itself agrees with the shipped file list in **992/992** cases; the magic table misclassifies 12. |
| **`map` file kind** | Nine magics map to `map`. | No such kind exists. | All 9 matches are `overlays/text/*.bin` string tables. |
| **Texture count** | 401 entries classified `tex`. | **400.** | Index 151 is `overlays/text/crate.bin`; it fails `word0 == 8 and word1 == size`, which the other 400 pass. |
| **Audio split** | Three formats `sfx1/sfx2/sfx3`. | One container with 2, 3 or 4 sections; 160 entries, not 162. | The differing first word is the section-table length. `sfx3` has exactly one member. |
| **Group table** | Not modelled. | 130 records of `{first_index, count, bytes}` at VA 0x80050010, the unit the game actually CdReads. | Verified: chain is contiguous, covers 0..991 exactly, and `bytes == Σ ceil(size/2048)*2048` in 130/130. |
| **Mesh header extent** | 0x10..0x24 pointers; the rest padding. | 0x00 is a runtime slot, 0x28 is a normal-array pointer (300 meshes), 0x2C is a live attachment pointer (777 meshes). | See §3, §4.3, §8.4. |

This document is not a criticism of CTR-tools — it is a much wider tool covering several
games, and the container work it did is what made this possible. The corrections above are
Crash Bash specific.
