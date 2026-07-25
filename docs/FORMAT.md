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
| 0x08 | i32 ptr | `ptr_pool_alias` | `T(0x08) == T(0x10)` in 400/400; the first 16 bytes there are zero in 400/400. **No EXE site resolves offset 0x08.** Dead in the shipped build. | **confirmed** |
| 0x0C | i32 | `subfile_slots` | Range 0..14. `≥ i32@0x40` in 400/400, equal in 328 of the 373 models that have meshes. No EXE site reads it. Reading it as "allocated slots vs used slots" is a guess. | ?unknown? |
| 0x10 | i32 ptr | `ptr_pool` | The one field through which the game reaches this block. The game does **not** use the plain self-relative form here — see the note below. | **confirmed** |
| 0x14 | i32 | `count_18` | 0 in 327/400, 1 in 73/400. Repeated at `[T(0x18)]` in 400/400. Also a layout switch: `T(0x2C) == T(0x3C)` **iff** this is 0, 400/400. | **confirmed** |
| 0x18 | i32 ptr | `ptr_subobjects` | `[i32 count == i32@0x14]` then `count` self-relative i32 pointers, entry *i* at `T(0x18)+4+4*i`. All 73 entries in the corpus resolve inside the file. | **confirmed** |
| 0x1C | i32 ptr | `ptr_objects` | Object table addressed by id namespace 0x5000; 12-byte stride where the code indexes it. Extent is not a multiple of 12 in general (mod 12 is 0 in 279 models, 4 in 70, 8 in 51). | **confirmed** (stride/base) / ?unknown? (total layout) |
| 0x20 | i32 ptr | `ptr_colours` | Colour table: 4-byte `R G B 00` records. See §7.1. | **confirmed** |
| 0x24 | i32 ptr | `ptr_uvs` | UV table: 2-byte `(u, v)` records. See §7.2. | **confirmed** |
| 0x28 | i32 ptr | `ptr_vectors` | Shared 6-byte `(i16 x, y, z)` position pool; the fallback source for animation poses (§9.5). Degenerate (`T(0x28) == T(0x08)`, zero length) in 360/400. See §7.3. | **confirmed** |
| 0x2C | i32 ptr | `ptr_pool_hi` | `T(0x2C) == T(0x08) + 8` in 400/400. This is the address the game actually computes from field 0x10. **No EXE site resolves the file header's 0x2C** (the single 0x2C site, 0x80015700, is a *mesh* header). | **confirmed** |
| 0x30 | i32 | — | 0 in 400/400. Never read. | **confirmed** (zero) / ?unknown? (purpose) |
| 0x34 | i32 | — | 0 in 400/400. Never read. | **confirmed** (zero) / ?unknown? (purpose) |
| 0x38 | i32 | `count_3C` | 0 in 393/400. The 7 non-zero are `warp_room1..5/level.mdl` and `demo_hub1..2/level.mdl` (5, 6, 8, 7, 6, 3, 3). Repeated at `[T(0x3C)]` 400/400. The block stores **count+1** records. | **confirmed** |
| 0x3C | i32 ptr | `ptr_chunks` | `[i32 count]` then `count+1` records of 16 bytes. See §8.1. | **confirmed** |
| 0x40 | i32 | `count_44` | Number of 24-byte clip records. Range 0..14; 0 in 148 of the 373 models with meshes. | **confirmed** |
| 0x44 | i32 ptr | `ptr_subfiles` | Appended clip directory, 24-byte records: the **animation** table. See §8.2 and §9. | **confirmed** |
| 0x48 | i32 | `count_4C` | Number of i32 entries in the 0x4C array. Range 0..40. | **confirmed** |
| 0x4C | i32 ptr | `ptr_ptr_array` | `count_4C` self-relative i32 pointers, stride 4. All 688 corpus entries resolve inside the file and land inside the 0x1C object table at 0, 4 or 8 mod 12 (401 / 163 / 124). | **confirmed** |
| 0x50 | i32, **base-relative** | `resident_size` | `base + i32@0x50` is the end of the 0x44 directory in 399/400 and ≤ file size in 400/400. Equals the file size exactly for the 141 models with no sub-files; ≤ the first sub-file's start in 225/225. **Not** self-relative. No EXE site reads it. | *likely* |
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
| `i32@0x0C >= i32@0x40` | 400/400 |
| `base + i32@0x50 == T(0x44) + 24*i32@0x40` | 399/400 (`chaselevel.mdl` is +1740, rounded up to 0x26000) |
| `T(0x20) <= T(0x24)` | 400/400 — but **strict** `<` only 378/400 |

Block order, identical in all 373 models that have meshes:

```
header(0x58) | mesh headers | mesh data ... | colours T20 | UVs T24 | [vectors T28] |
T08 = T10 | T2C | {T3C, T1C} | T4C | T18 | T44 | end(0x50) | appended sub-file payloads
```

The complete minimal model is `fonts/font1.mdl`, 120 bytes: header 0x58, zero meshes,
`T(0x20) == T(0x24) == 0x58` (both tables empty), 8 zero bytes, the 0x3C list
(`[0]` + 1 record = 20 bytes) ending at 0x74 `== T(0x1C) == T(0x4C) == T(0x18)`, the 0x18
count word, and `T(0x44) == base + i32@0x50 == 0x78 == 120`.

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
| 0x00 | u32 | `runtime_slot` | **Not padding.** Zero in the file (5990/5990) because the loader fills it; the mesh-iteration loop dereferences it. | **confirmed** |
| 0x04 | u32 | — | Zero in 5990/5990. No reader found. | **confirmed** (zero) / ?unknown? (purpose) |
| 0x08 | i16 | `triangle_count` | Number of triangles. Equals the sum of the strip list's high bytes in **5990/5990**. Not read by any render pass — the runtime derives the count from the strip list. Redundant but exact. | **confirmed** |
| 0x0A | i16 | `format` | Values: 4 (3112), 6 (2620), 7 (255), 5 (2), 2 (1). Only 5990 samples; no reader identified. | ?unknown? |
| 0x0C | i16 | — | Non-zero in 344/5990. | ?unknown? |
| 0x0E | i16 | — | Non-zero in 162/5990. | ?unknown? |
| 0x10 | i32 ptr | `ptr_bounds` | 0x14-byte bounds block; the vertex pool starts at `T(0x10) + 0x14`. | **confirmed** |
| 0x14 | i32 ptr | `ptr_strips` | Strip list (§5). | **confirmed** |
| 0x18 | i32 ptr | `ptr_uv_index` | One u16 per triangle (§6.1). Also the end of the vertex block. | **confirmed** |
| 0x1C | i32 ptr | `ptr_texture_runs` | Run-length texture list (§6.2). | **confirmed** |
| 0x20 | i32 ptr | `ptr_colour_index` | One u16 per triangle (§6.3). | **confirmed** |
| 0x24 | i32 ptr | `ptr_end` | End of the mesh's own data. | **confirmed** |
| 0x28 | i32 ptr | `ptr_normals` | **Non-zero in exactly 300/5990.** When set, `T(0x28) == vertex_pool + 8*vertex_count` in 300/300 and a second 8-byte-per-vertex array of unit normals follows. When zero there are no normals. | **confirmed** (structure) / *likely* (that they are normals) |
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
| 3 | 0x08 | Set on 24,151 strips (29.8 %). **No reader.** An exhaustive scan of `.text` finds no `andi` with an immediate of 8, 9, 0x0C, 0x0E or 0x0F anywhere in the model/render region 0x80016000–0x8001E000. | ?unknown? |

Bits 1, 2 and 4–7 are never set.

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

These 56- and 12-byte structures are **built at load time and do not exist in the file**. The
file-side data that produces them is in the TEX pack (§10); which TEX field lands in which
descriptor slot is ?unknown? — see §13.

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

## 8.3 Object table (`model + 0x1C`) and pointer array (`model + 0x4C`)

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

The 0x4C array is `i32@0x48` self-relative i32 pointers, stride 4, ending exactly where the
0x18 block begins (400/400). All 688 corpus entries resolve inside the file and land **inside
the 0x1C table** at 0, 4 or 8 mod 12 (401 / 163 / 124) — i.e. each names a *field* of a
12-byte object record. The overall extent `T(0x4C) − T(0x1C)` is not a multiple of 12
(0 mod 12 in 279 models, 4 in 70, 8 in 51), so the block is a heterogeneous object graph
rather than a flat array. The internal layout beyond +0x00/+0x04/+0x08 is ?unknown?.

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
?unknown?.

## 8.5 Sub-object array (`model + 0x18`)

`[i32 count]` then `count` self-relative i32 pointers. The resolve and the sub-object's own
header:

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
8001DE80  lw    $v0, 0x20($v1) -> instance +0x14   (a COLOUR table, same offset as the model)
8001DE94  lw    $v0, 0x1c($v1) -> instance +0x18   (raw, not resolved)
```

73 entries exist in the corpus, all resolving inside their file. The sub-object's contents
were not chased further — ?unknown?.

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

**The names themselves are not in the MDL, and not in the EXE.** They are string literals in
the per-minigame code overlays: `BREATHE` occurs in all 14 `overlays/modes/*.bin`.
Hashing every identifier-shaped string (`[A-Z][A-Z0-9_]{2,19}`, containing a vowel) found in
the overlays — 1079 distinct candidates — matches 755 of the 1037 clips. That is *not* proof
for any individual clip: the hash is a sum with only 243 distinct values here, and a control
that shifts every stored hash by ±1 before matching still "resolves" 431 and 539 clips
respectively. What survives that control is the concentration on animation vocabulary:

| Word | Clips | Word | Clips | Word | Clips |
| --- | --- | --- | --- | --- | --- |
| BREATHE | 114 | PUSH | 21 | WIN_BREATHE | 8 |
| WIN | 108 | ATTACK | 19 | LOSE | 8 |
| HIT | 55 | TAUNT | 18 | SKATE | 8 |
| RUN | 40 | TAUNT_A | 16 | DAZED | 8 |
| FALL | 34 | PICKUP | 12 | RECOIL | 8 |
| JUMP | 31 | SWIM | 11 | IDLE1 | 8 |
| DIE | 25 | HOLD_THROW | 10 | MINE | 8 |
| BARGE / SLIDE | 23 | HOLD_SLOW | 10 | LOSE_BREATHE | 2 |

`HIT`, `DIE`, `WIN`, `LOSE`, `BARGE`/`SLIDE`, `SWIM` and `MINE` share their hash with at least
one other overlay string, so they are the *plausible* member of a collision set, not a
decoded name. Treat the hash as the identity and any word as a hint. **confirmed** that the
hash is of a name; *likely* for the individual words.

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
| +0x0C | i32 ptr | `aux` | Self-relative pointer to an auxiliary block, or 0. Non-zero in 5354 of 49,167 records. The draw path never reads it (§9.8). | **confirmed** (that it is a pointer) / ?unknown? (contents) |

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

**Looping** is a property of the clip's caller, not a flag in the file:

```
8001F244  lw    $v0, 0x10($t0)      ; per-clip mode word
8001F24C  beqz  $v0, 0x8001f288     ;   0 -> loop, else clamp
8001F27C  addiu $v0, $a0, -1        ; clamp: hold at (end-1)
8001F298  addiu $v0, $v0, 1         ; loop: elapsed mod (end - start + 1)
8001F2A0  div   $zero, $a2, $v0
8001F2C8  mfhi  $a1
```

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

**The draw path never reads +0x0C.** The only reader in the image is the 0x4000 id namespace,
which hands the block to script code:

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
| 0x18 | u32 | — | 0 in 314/400. The 86 non-zero values match no landmark tested. | ?unknown? |
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
| +0x06 | u8 | `used_width` | **≤ `vram_width * 2` (the row's byte count) in 15,160/15,160**, with equality in 11,890. | **confirmed** (the bound) / *likely* (a used-area width) |
| +0x07 | u8 | `used_height` | **≤ `height` in 15,160/15,160**, with equality in 10,186. | **confirmed** (the bound) / *likely* (a used-area height) |
| +0x08 | u32 | — | **0 in 15,160/15,160.** | **confirmed** (zero) / ?unknown? (purpose) |
| +0x0C | i16 | `palette_field` | Bit 0 = bit depth (0 → 4bpp, 1 → 8bpp; 14,885 / 275). Bits 1–15 = palette index. The value `0x7FFF` means "no palette of my own" — the swatch texture, 355 of 15,160. | *likely* |
| +0x0E | i16 | — | 0 (14,985) or 3 (175). | ?unknown? |
| +0x10 | u32 | `flags` | 19 distinct values; 1 (14,643), 2 (192), 16 (66), 8 (43), 7 (41), 12 (39), … | ?unknown? |
| +0x14 | u8[] | `pixels` | `vram_width * height * 2` bytes. 4bpp packs two pixels per byte, **low nibble first** (leftmost). | **confirmed** |

Walking `palette_count` palettes then `texture_count` records does **not** land exactly on the
file size: the residual is 8 bytes in 263 packs, 12 in 51, 32 in 12, and occasionally tens of
kilobytes. Something else lives at the end of a pack — ?unknown?.

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
(631); and the module docstring covers the whole mesh header including 0x28 and 0x2C.

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
| "A frame record is 16 bytes starting at the blob base." | **Refuted** | Record *f* is at `blob + 4 + 16*f`; `blob+0x00` is the blob's pool pointer. Under the shifted reading only 1,925 of 49,167 records validate and no clip validates completely. See §9.3. |

---

# 14. Unknown

Stated precisely, with the measurement that bounds each one.

**MDL file header**

* **0x00** — the stamp encodes *something* (the one file with 0x09160026 is also the one file
  that breaks the 0x50 rule), but n = 1 for the alternate value and neither constant appears
  anywhere in the EXE. A date reading (0x0C/0x16/0x29 vs 0x09/0x16/0x26) fits the shape and
  nothing confirms it.
* **0x0C** — no reader. `>= i32@0x40` in 400/400 and equal in 328/373; "capacity vs used" is
  a guess. Distribution: 0 (155), 5 (63), 2 (51), 1 (36), 6 (30), 7 (21), 3 (15), 8 (8), …
* **0x30, 0x34** — zero in 400/400, no reader. Padding or fields no retail asset uses.
* **0x50** — the arithmetic is unambiguous (`base + value` is the resident-image end) but no
  code reads the field, so whether the encoder meant "size" or "pointer to an 80-byte
  trailer" is inference only.
* **0x1C object table** — 12-byte stride confirmed where the code indexes it, but the block's
  total extent is not a multiple of 12 (mod 12 = 0 in 279 models, 4 in 70, 8 in 51) and no
  header field explains its length. Internal layout beyond +0x00/+0x04/+0x08 unread.
* **0x18 sub-object targets** — the sub-object header uses the same self-relative convention
  at +0x0C, +0x10, +0x1C, +0x20 (0x8001DE50–0x8001DE9C). Not chased further.
* ~~**0x28 vector pool framing**~~ and ~~**0x44 record +0x08 / +0x0C**~~ — **closed**, see §9.
  The pool is the position source animation keyframes index; +0x08 is a frame count and +0x0C
  points at the mesh a clip drives.

**MDL mesh header**

* **0x0A** ("format") — the distribution is known; the meaning is not, and I found no site
  that reads it. That is not the same as proving it is never read: I did not trace the base
  register of the eight halfword loads at offset 0x0A inside 0x80014000–0x8001F000.
* **0x0C** (non-zero in 344) and **0x0E** (non-zero in 162) — no reader identified.
* **0x04, 0x30** — zero in 5990/5990, no reader.
* **0x2C block contents** — the 16-byte records are unread. The `u16` at +0x00 is 0 (769),
  2 (6) or 5 (2).
* **Whether the +0x28 normals are consumed at all** — no site dereferences mesh+0x28, and the
  game issues no GTE lighting instruction anywhere (3 COP2 lighting-family hits in the entire
  image, all in libgpu). They may be dead data, or consumed by software the tracing missed.

**Per-triangle flags**

* **Strip flag bit 3** (24,151/81,045 strips) — no reader. Exhaustive scan for `andi` with
  immediates 8/9/0x0C/0x0E/0x0F over 0x80016000–0x8001E000 returns nothing.
* **Vertex flag bits 2 and 8** (130 and 28 vertices) — no reader in the render path. The two
  `lui 4 / and` sites in the model region (0x80019824, 0x80019A14) operate on an instance
  mode word, not on vertex flags.

**TEX**

* **VRAM placement.** This is the biggest gap in the whole format, and it is now bounded a
  little better: **the descriptor is not built in `SCUS_945.70`.** A UV pair in a model is
  page-local and is OR'd at runtime with a per-texture origin from a 56-byte descriptor whose
  tpage (+0x0C), CLUT id (+0x0E) and UV origin (+0x10) the render pass only ever *reads*.
  Three exhaustive scans back that up: `.text` contains no `GetClut`-shaped arithmetic at all
  (no `srl rX,4` within three instructions of an `sll rY,6`; only 33 `sll ,6` in the whole
  image, two of them in the model region and both allocation sizes); there is no absolute
  `lw`/`sw` against the render context's +0x10/+0x18/+0x1C descriptor-table slots
  (immediates 0x69A8/0x69B0/0x69B4 do not occur); and no site writes a 56-byte-strided record
  in the 0x80015000–0x8001F000 region. So the loader that turns a TEX pack into descriptors
  lives in an overlay — `overlays/gameeng.bin` (386 KB, the only unclassified code overlay) is
  the place to look next. Until that is read, mapping a UV to a texel relies on assumption.
* **Header 0x14** — multiple of 4 in 400/400, range 120..228,744, 272 distinct values. Not a
  pointer, not any pixel/palette/record total tested, and it differs between structurally
  identical packs.
* **Header 0x18** — 0 in 314/400; the 86 non-zero values match no landmark tested.
* **Record +0x04..+0x07** — 321 distinct patterns. +0x04 is zero in 15,160/15,160 and +0x05 in
  13,332. The other two are bounded by the record's own dimensions — `+0x06 <= vram_width*2`
  and `+0x07 <= height`, both 15,160/15,160, with equality in 11,890 and 10,186 — which reads
  as the used sub-rectangle of a padded block, but no code site was found that consumes them.
* **Record +0x0E** (0 or 3) and **+0x10** (19 distinct values) — no reader traced.
* **The pack tail.** Walking the palettes and then the texture records leaves a residual
  before EOF: 8 bytes in 263 packs, 12 in 51, 32 in 12, and up to 32,904 in a few. Something
  else is stored there.

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

* Nothing in this document places one mesh relative to another. Object placement is a separate
  key track outside the MDL (§9.7) — the transform is written into the runtime instance, not
  read from the file — so a 33-mesh model still has no documented joint or parent
  relationship, and where that key track is stored was not traced.

**Container**

* The ~40-byte fudge in the DAT builder: an extra sector is reserved when `size mod 2048`
  lands in roughly [2012, 2047]. The boundary is pinned only to within 6 bytes (max
  non-padded remainder 2006, min padded remainder 2012).
* Whether the eight groups with interior padding sectors are safe at runtime.
  `group.bytes` sizes both the buffer and the CdRead, but the splitter at 0x800126F4 places
  entry *i* at `(sector[i] − sector[first]) * 2048`, which for group 0 reaches sector 6519
  while the buffer holds 6516.
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
