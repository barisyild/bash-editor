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
    if tail < len(data):
        spans.append((tail, len(data)))

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
             landed: dict[int, int] | None = None) -> bytes:
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
    out = bytearray()
    moves: list[tuple[int, int, int, int]] = []   # (start, end, new start, new end)
    for start, end in regions:
        _align(out, 4)
        at = len(out)
        out.extend(replace.get(start, data[start:end]))
        moves.append((start, end, at, len(out)))
        if landed is not None:
            landed[start] = at
    _align(out, 4)

    def moved(offset: int) -> int:
        for start, end, at, stop in moves:
            if start <= offset < end:
                # Inside a replaced region only its start is meaningful, and
                # only the header names one, so this is exact where it is used.
                return at if start in replace else at + (offset - start)
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
    struct.pack_into("<i", out, RESIDENT_SIZE,
                     moved(min(struct.unpack_from("<i", data, RESIDENT_SIZE)[0],
                               len(data))))
    return bytes(out)


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
