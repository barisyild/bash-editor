"""Lay a model out from scratch, region by region.

`mdlwrite` patches: it takes the shipped layout as given and squeezes between
its parts, which is why it strands the old tables, pins them when it cannot,
and holds an object-pool mesh to the span it already owns. Every one of those
is a consequence of not owning the layout.

This owns it. The map `tools/layout.py` measures is the plan -- header, mesh
headers, each mesh's blocks, the shared tables, the vector pool, the object
pool, the tables past the boundary, the clip directory and its blobs -- written
in that order with every pointer computed from where a region lands rather than
adjusted from where it was.

`relayout` re-emits a model region by region, copying each one and recomputing
every pointer from where it lands. Hand it `replace` and a region is written
from new bytes instead -- which is how a shared table *grows where it already
stands* rather than being appended in a fresh copy, and that one difference is
what ends the stranding: `boss_oxide/arena` costs 2044 bytes for a one-mesh
edit where appending cost 32,764, of which 30,528 were the tables left behind.
`mdlwrite._install_relaid` is the caller.

Byte identity is *not* the test and would be the wrong one. The shipped files
carry 38,540 bytes of padding between regions in runs of 4, 8, 20, 24 and 28 --
deliberate, not a single alignment rule -- so a writer that aligns to 4 emits a
shorter file that says exactly the same thing. What has to match is what the
reader reads back, and `tools/relayout.py` checks it over the archive.

**What the reader reads back is not all a file holds.** Ten gaps across four
models carry bytes nothing in this project resolves -- `gamelogo_text`'s 7520
of strip words for a mesh its header does not declare, most of all -- so a plan
built only from what the header names would drop them and every round trip
would still pass. Any gap that is not entirely zero is therefore carried
verbatim, and 400 of 400 shipped models keep every non-zero byte they have.
"""

from __future__ import annotations

import struct

from .mdl import MESH_HEADER_SIZE, OBJECT_STRIDE, Model, read_model

MESH_HEADER_START = 0x58
RESIDENT_SIZE = 0x50  # base-relative, not self-relative

# Every self-relative field of the model header. A relayout rewrites each one
# by mapping its old target through where that byte ended up, so a field whose
# target is not inside a region this knows about is a refusal rather than a
# guess.
HEADER_FIELDS = (0x08, 0x10, 0x18, 0x1C, 0x20, 0x24, 0x28, 0x2C, 0x3C, 0x44,
                 0x4C)

# The same for a mesh header: bounds, strips, UVs, textures, colours, the end,
# the normals block and the attachment. `0x24` is one past the mesh's last
# byte, which is why `moved` answers for an offset equal to a region's end.
MESH_POINTERS = (0x10, 0x14, 0x18, 0x1C, 0x20, 0x24, 0x28, 0x2C)

# §8.1's descriptor rows: `[count]` then `count + 1` records of 16 bytes, each
# stating a sub-block's start and end as plain file offsets. `0x800163E0`
# streams them off the disc, so they are read a sector at a time -- and the
# measurement agrees: all seven §8.6 carriers put every row on a 0x800
# boundary, four of them at exactly that granularity rather than 0x1000. A
# block that moves has to land on the same grid or every row inside it slips.
CHUNK_STRIDE = 16
CHUNK_ALIGN = 0x800


class Unmapped(ValueError):
    """A pointer names a byte no planned region covers."""


def _align(out: bytearray, to: int = 4) -> None:
    if len(out) % to:
        out.extend(b"\x00" * (to - len(out) % to))


def table_bounds(data: bytes) -> tuple[int, int, int]:
    """`(colours, uvs, pool)` -- where each shared table starts, in file order.

    The colour table runs to `uvs`, the UV table to `pool`, and the vector pool
    to the layout boundary at `T(0x08)`. These are the offsets a caller names a
    region by when it wants one written from new bytes, so they are stated here
    rather than derived again at each call site.
    """
    resolve = lambda at: at + struct.unpack_from("<i", data, at)[0]  # noqa: E731
    return resolve(0x20), resolve(0x24), resolve(0x28)


def table_start_of_objects(data: bytes) -> int:
    """`T(0x1C)` -- where the object table begins (§8.3)."""
    return 0x1C + struct.unpack_from("<i", data, 0x1C)[0]


def plan(data: bytes, model: Model) -> list[tuple[int, int]]:
    """The regions to emit, as `(start, end)` in the order they are written.

    File order, because that is the order the shipped models already use and
    the map leaves only zero padding between them -- so it is the one layout
    known to load. Regions that overlap or nest are merged: `gamelogo_text`
    has two mesh headers naming one block set, and several levels have a pool
    mesh whose span covers its neighbour.
    """
    resolve = lambda at: at + struct.unpack_from("<i", data, at)[0]  # noqa: E731
    spans: list[tuple[int, int]] = [(0, MESH_HEADER_START)]

    if model.meshes:
        spans.append((MESH_HEADER_START,
                      MESH_HEADER_START + MESH_HEADER_SIZE * len(model.meshes)))
    pooled = [o.mesh for o in model.objects if o.mesh is not None]
    in_pool = {id(mesh) for mesh in pooled}
    for mesh in list(model.meshes) + pooled:
        low = min(mesh.ptr_bounds, mesh.ptr_strips, mesh.ptr_uv_index,
                  mesh.ptr_texture, mesh.ptr_colour_index)
        # An object-pool mesh's header sits with its own blocks and has to
        # travel with them; a numbered mesh's is in the header table at 0x58
        # and travels separately, so folding it in here would fuse that table
        # into the first mesh's blocks and leave no region to replace on its
        # own. Matched by identity: a `Mesh` carries numpy arrays, so `in` on a
        # list of them is both quadratic and a comparison that can be ambiguous.
        if id(mesh) in in_pool:
            low = min(low, mesh.header_offset)
        spans.append((low, mesh.ptr_end))
        if mesh.ptr_attachment:
            spans.append((mesh.ptr_attachment,
                          mesh.ptr_attachment + 4 + 16 * len(mesh.volumes)))
    spans += [(resolve(0x20), resolve(0x24)), (resolve(0x24), resolve(0x28)),
              (resolve(0x28), resolve(0x08))]

    # Everything past the object pool is copied as one run: the model refs, the
    # object table, the scene nodes, the sub-objects, the clip directory and its
    # blobs. Splitting it would need lengths the header does not state, and
    # nothing is gained -- it moves as a unit and its pointers are internal.
    #
    # `0x2C` is deliberately not among these. It is the pool's first byte, so
    # starting the run there swallowed all 1971 pool meshes into one region and
    # left none of them replaceable.
    tail = min((resolve(field) for field in (0x1C, 0x18, 0x3C, 0x44)
                if 0 < resolve(field) <= len(data)), default=len(data))
    tail = max(tail, resolve(0x08))
    # A mesh whose blocks were appended past everything else -- which is what
    # `append_mesh` does, since a new slot has nowhere else to go -- would
    # otherwise be swallowed by this run and lose the region of its own that
    # `install_meshes` needs to fill it.
    beyond = [min(mesh.ptr_bounds, mesh.ptr_strips, mesh.ptr_uv_index,
                  mesh.ptr_texture, mesh.ptr_colour_index)
              for mesh in model.meshes]
    after = [low for low in beyond if tail < low < len(data)]
    tail_end = min(after) if after else len(data)
    # A §8.6 carrier's door-preview block is streamed from disc a sub-block at
    # a time through §8.1's descriptor rows, which state plain file offsets, so
    # it is emitted as a region of its own: it has to stay sector-aligned and
    # its rows have to move with it. Splitting it out is what lets a carrier be
    # laid out at all instead of pinned.
    block = resolve(0x44) if struct.unpack_from("<i", data, 0x38)[0] else 0
    if tail < tail_end:
        if 0 < tail < block < tail_end:
            spans.append((tail, block))
            spans.append((block, tail_end))
        else:
            spans.append((tail, tail_end))

    # Whatever the pool holds between its meshes is emitted as regions of its
    # own rather than left to alignment. §8.3's run puts the next header exactly
    # four bytes past the previous `ptr_end` in 1802 of 1898 pairs, and a hole
    # in that run black-screened `warp_room1`; carrying the gaps verbatim keeps
    # the spacing the disc shipped, and anything in the pool the reader could
    # not account for survives instead of being dropped.
    pool = resolve(0x2C)
    if 0 < pool < tail and pooled:
        at = pool
        for low, end in sorted((min(m.ptr_bounds, m.ptr_strips, m.ptr_uv_index,
                                    m.ptr_texture, m.ptr_colour_index,
                                    m.header_offset), m.ptr_end)
                               for m in pooled):
            if at < low:
                spans.append((at, low))
            at = max(at, end)
        if at < tail:
            spans.append((at, tail))

    # Merge only what genuinely overlaps. Two regions that merely touch stay
    # apart, because a region's start is its name -- fold an adjacent table
    # into the blocks before it and there is nothing left to replace it by.
    merged: list[tuple[int, int]] = []
    for start, end in sorted(s for s in spans if 0 <= s[0] < s[1] <= len(data)):
        if merged and start < merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))

    # Anything left between regions is carried verbatim unless it is all zeros.
    # A writer may only drop what it can show is padding: 7270 of the 7280 gaps
    # the map leaves are entirely zero and alignment reproduces them, but ten
    # are not, and the largest is `gamelogo_text`'s 7520 bytes of strip words
    # for a mesh its header does not declare. Nothing here reads those, so no
    # round trip through this project's own reader can notice them going
    # missing -- the relaid file simply came back 6768 bytes *below* the one
    # the disc shipped.
    kept: list[tuple[int, int]] = []
    at = 0
    for start, end in merged:
        if at < start and any(data[at:start]):
            kept.append((at, start))
        kept.append((start, end))
        at = max(at, end)
    if at < len(data) and any(data[at:]):
        kept.append((at, len(data)))
    return kept


def relayout(data: bytes, model: Model | None = None,
             replace: dict[int, bytes] | None = None,
             landed: dict[int, int] | None = None,
             move_block: bool = False) -> bytes:
    """Re-emit a model with every region in place and every pointer recomputed.

    With no `replace`, each region's bytes are copied verbatim and what is
    exercised is the arithmetic alone. With it, a region named by its shipped
    start offset is written from the bytes given instead -- which is how a table
    grows without anything being stranded, because the regions after it simply
    land further on and every pointer is computed from where they land.

    A replacement may be any length. Only a replaced region's *start* can be
    resolved -- its interior is new content whose old offsets mean nothing --
    so a caller replacing a region something points into has to write those
    pointers itself, and `landed` is how it finds them: it is filled with each
    region's shipped start mapped to where that region now begins. A mesh's
    five blocks are the one case, and `install_meshes` uses it.
    """
    model = model or read_model(data)
    replace = replace or {}
    regions = plan(data, model)
    # §8.1's rows name their sub-blocks by plain file offset and the loader
    # streams them from disc, so every shipped one is 0x1000-aligned. Land the
    # block on the same grid and each row inside it keeps its alignment.
    carrier = (0x44 + struct.unpack_from("<i", data, 0x44)[0]
               if struct.unpack_from("<i", data, 0x38)[0] else 0)
    out = bytearray()
    moves: list[tuple[int, int, int, int]] = []   # (start, end, new start, new end)
    # A §8.6 carrier's block keeps its shipped file offset. The reason is not
    # §8.1's descriptor rows -- those repoint fine -- it is that for a carrier
    # `i32@0x50` *is* this block's start (167,936 = 0x29000 in `warp_room1`),
    # the pack's `u32@0x14` mirrors it in 400/400 pairs, and §10.4's
    # 0x8002A62C carries that value into the texture context at +0x24. Move it
    # and the room draws perfectly until a door preview is opened, at which
    # point every textured surface in the level turns to garbage.
    #
    # The slack goes *before* the region that runs up to the block, never
    # between them: §8.5's `+0x14` block ends exactly at `T(0x44)` in 73 of 73
    # levels, and padding into that gap instead crashed the room outright.
    # `move_block` lets it slide instead, on the sector grid, with §8.1's rows
    # following. That is only safe once the preview meshes inside it have been
    # renumbered onto the table being written -- which is the caller's job, and
    # why this is not the default.
    abuts = (next((s for s, e in regions if e == carrier), None)
             if carrier and not move_block else None)

    for start, end in regions:
        if start == abuts:
            want = carrier - (end - start)
            if len(out) > want:
                raise Unmapped(
                    f"the §8.6 block has to stay at {carrier:#x} -- `i32@0x50` "
                    f"names it and the pack's `u32@0x14` mirrors it -- and "
                    f"what goes before it needs {len(out) + (end - start)} "
                    f"bytes against the {carrier} there are")
            out.extend(b"\x00" * (want - len(out)))
        else:
            _align(out, CHUNK_ALIGN if start == carrier else 4)
        at = len(out)
        out.extend(replace.get(start, data[start:end]))
        moves.append((start, end, at, len(out)))
        if landed is not None:
            landed[start] = at
    _align(out, 4)

    def moved(offset: int) -> int:
        # Containment first, everywhere, before any one-past-the-end reading.
        # Two regions meet at one offset -- the end of the first and the start
        # of the second -- and taking whichever came first in the list answered
        # "the end of the first" for the §8.6 block's own start, which put a
        # descriptor row 156 bytes short of the block it names.
        for start, end, at, stop in moves:
            if start <= offset < end:
                # A replacement that is the same length as what it replaces has
                # not moved anything inside itself, so an interior offset maps
                # straight across. §8.6's block is exactly that -- its preview
                # meshes are renumbered in place -- and collapsing its interior
                # onto the region start wrote four of `warp_room1`'s five
                # descriptor rows as zero-length, which on hardware is a room
                # whose door previews simply never appear.
                # The same holds for a region *extended* at its end: adding a
                # scene node after the ones already there moves nothing in
                # front of it. What decides it is whether the replacement still
                # opens with what it replaced, which is exact and cheap.
                if (start in replace
                        and replace[start][:end - start] != data[start:end]):
                    return at
                return at + (offset - start)
        for start, end, at, stop in moves:
            if offset == end:            # a one-past-the-end pointer, and the
                return stop                # tables use them
        raise Unmapped(f"{offset:#x} is in no region")

    for field in HEADER_FIELDS:
        target = field + struct.unpack_from("<i", data, field)[0]
        if not 0 <= target <= len(data):
            continue
        struct.pack_into("<i", out, field, moved(target) - field)

    # A numbered mesh's header lives in its own region and its blocks in
    # another, so when a table between them grows the two move by different
    # amounts and the self-relative pointers stop meeting. An object-pool
    # mesh is not affected -- its header and blocks are one region and travel
    # together -- which is why this only showed on 15 models, all of them
    # losing a numbered mesh that sits after the tables.
    if model.meshes:
        headers = moved(MESH_HEADER_START)
        for slot, mesh in enumerate(model.meshes):
            at = headers + MESH_HEADER_SIZE * slot
            for field in MESH_POINTERS:
                raw = struct.unpack_from("<i", data, mesh.header_offset + field)[0]
                if not raw:
                    continue
                target = mesh.header_offset + field + raw
                if not 0 <= target <= len(data):
                    continue
                struct.pack_into("<i", out, at + field,
                                 moved(target) - (at + field))
    _repoint_objects(data, out, model, moved)
    _repoint_roots(data, out, moved)
    _repoint_chunks(data, out, moved)
    struct.pack_into("<i", out, RESIDENT_SIZE,
                     moved(min(struct.unpack_from("<i", data, RESIDENT_SIZE)[0],
                               len(data))))
    return bytes(out)


def _repoint_chunks(data: bytes, out: bytearray, moved) -> None:
    """Move §8.1's descriptor rows to where their sub-blocks landed.

    A row states its sub-block's start and end as **plain file offsets**, not
    self-relative ones, and `0x800163E0` hands them straight to the disc read.
    They are the reason a §8.6 carrier's door-preview block was said to be
    unmovable: the block may move, its rows just have to move with it.

    Row 0 is the model itself and is all zeros on disc, so only rows 1..count
    are touched, and only when they resolve inside this file.
    """
    count = struct.unpack_from("<i", data, 0x38)[0]
    if count <= 0:
        return
    rows = 0x3C + struct.unpack_from("<i", data, 0x3C)[0] + 4
    at = moved(rows - 4) + 4
    for index in range(count + 1):
        row = rows + CHUNK_STRIDE * index
        start, end = struct.unpack_from("<2I", data, row)
        if not start or not (start < end <= len(data)):
            continue
        struct.pack_into("<2I", out, at + CHUNK_STRIDE * index,
                         moved(start), moved(end))


def _repoint_objects(data: bytes, out: bytearray, model: Model, moved) -> None:
    """Move each object record's pool offset to where its mesh landed.

    An object record's `+4` is the one field in this format that is *not*
    self-relative: 0x8001DD20 adds it to the load-time base address named
    through `+8`, so it is a plain offset from the start of whichever file that
    base belongs to. Reference 0 is this model, and those are the pool meshes.

    Nothing warns when this is missed. The pool is a packed run of similar
    headers, so a stale offset still reads *a* mesh -- it lands on a neighbour,
    or short of the pool entirely, and then `_read_objects` silently drops the
    entry. That is what a table grown ahead of the pool did to every level here
    while only 15 models reported a mesh changing.
    """
    resolve = lambda at: at + struct.unpack_from("<i", data, at)[0]  # noqa: E731
    table, refs, pool = (resolve(0x1C), resolve(0x3C), resolve(0x2C))
    if not (0 < pool <= refs <= len(data) and 0 < table <= len(data)):
        return
    at = moved(table)
    for index, obj in enumerate(model.objects):
        if obj.reference != 0 or not pool <= obj.offset < refs:
            continue
        struct.pack_into("<i", out, at + OBJECT_STRIDE * index + 4,
                         moved(obj.offset))


def _repoint_roots(data: bytes, out: bytearray, moved) -> None:
    """Move each scene root's entry to where that root landed.

    `HEADER_FIELDS` carries `0x4C` -- where the array *is* -- and that is only
    half of it: every entry in the array is itself self-relative, from its own
    slot to a root (§9.11, 0x8001FF78). The two only stay in step while the
    array and the roots move by the same amount, which is exactly what stops
    being true once a region between them changes length.

    A cutscene given a new mesh is where that happens, and what it looks like
    when it goes wrong is a root entry that still resolves to a plausible
    offset -- nothing outside the file, nothing unmapped -- with something that
    is not a root at the end of it. `mdlwrite._shift_roots` is the same repair
    on the appending path, where it was measured: `intro_logo` came back with a
    child count of 2,691,088 and no shot at all.
    """
    try:
        count = struct.unpack_from("<i", data, 0x48)[0]
        base = 0x4C + struct.unpack_from("<i", data, 0x4C)[0]
    except struct.error:
        return
    if not (0 < count < 4096 and 0 <= base and base + 4 * count <= len(data)):
        return
    for index in range(count):
        slot = base + 4 * index
        root = slot + struct.unpack_from("<i", data, slot)[0]
        if not 0 <= root <= len(data):
            continue
        at = moved(slot)
        struct.pack_into("<i", out, at, moved(root) - at)


MESH_COUNT = 0x54


def append_mesh(data: bytes, model: Model | None = None,
                template: int = 0) -> bytes:
    """Add a mesh slot to the model, copied from one it already has.

    A cutscene has no spare slots to borrow into -- `intro_eurocom` looks like
    it has two and both are its backdrop -- so putting a model *into* one takes
    something away. This adds a slot instead.

    The header table at 0x58 grows like any other region, which is the whole of
    why this is possible now: everything after it simply lands further on and
    every pointer is recomputed. The new header **aims at `template`'s own
    blocks** rather than at a copy of them, so no other region changes length
    and nothing new has to be found a home inside the layout boundary;
    `install_meshes` gives the slot blocks of its own afterwards.

    Two headers over one block set is what the shipped files already do --
    `balls_crash/crystalarena` packs five pool meshes onto one, five headers
    over the same strips, bounds, UVs and colours. It is also the only
    placement the layout writer will own. Copying the blocks meant finding a
    region for them, and the one "ending at the layout boundary" is the vector
    pool *only when the pool is not empty*: `intro_logo`'s is, so the copy went
    into the **UV table's** region instead, and `_install_relaid` refuses a
    region it is already rewriting. Every added mesh in the archive fell back to
    the appending path that way, and that path leaves the scene root behind --
    which is a cutscene losing its shot, silently, on every model with a clip.
    """
    model = model or read_model(data)
    if not model.meshes:
        raise ValueError("this model has no mesh to copy a slot from")
    if not 0 <= template < len(model.meshes):
        raise ValueError(f"mesh {template} is not one of the "
                         f"{len(model.meshes)} this model holds")
    source = model.meshes[template]

    regions = plan(data, model)
    headers = MESH_HEADER_START
    table = next((r for r in regions if r[0] == headers), None)
    if table is None:
        raise ValueError("this model's header table is not a region of its own")

    grown = bytearray(data[table[0]:table[1]])
    grown.extend(b"\x00" * MESH_HEADER_SIZE)

    landed: dict[int, int] = {}
    out = bytearray(relayout(data, model, {table[0]: bytes(grown)}, landed))
    struct.pack_into("<i", out, MESH_COUNT, len(model.meshes) + 1)

    # The new header, written where the grown table now sits and aimed at the
    # template's blocks wherever the relayout put them -- which the rebuilt
    # model states outright, so nothing has to be mapped by hand.
    rebuilt = read_model(bytes(out))
    moved_source = rebuilt.meshes[template]
    header = landed[headers] + MESH_HEADER_SIZE * len(model.meshes)
    struct.pack_into("<2h", out, header + 0x08,
                     source.face_count_header, source.format)
    struct.pack_into("<2h", out, header + 0x0C, source.unk13, source.unk14)
    for field, target in ((0x10, moved_source.ptr_bounds),
                          (0x14, moved_source.ptr_strips),
                          (0x18, moved_source.ptr_uv_index),
                          (0x1C, moved_source.ptr_texture),
                          (0x20, moved_source.ptr_colour_index),
                          (0x24, moved_source.ptr_end),
                          (0x28, moved_source.ptr_normals)):
        if field == 0x28 and not source.ptr_normals:
            continue
        at = header + field
        struct.pack_into("<i", out, at, target - at)

    # The slot has to be inside the image the game loads, and for a model with
    # no clips `i32@0x50` and `T(0x44)` state that end together (§2.1).
    clips_at = 0x44 + struct.unpack_from("<i", out, 0x44)[0]
    resident = struct.unpack_from("<i", out, RESIDENT_SIZE)[0]
    if clips_at == resident == len(out):
        return bytes(out)
    if resident < len(out):
        struct.pack_into("<i", out, RESIDENT_SIZE, len(out))
        if clips_at == resident:
            struct.pack_into("<i", out, 0x44, len(out) - 0x44)
    return bytes(out)
