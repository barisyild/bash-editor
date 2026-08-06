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

**Two stages, and this is the first.** `relayout` re-emits a model from its own
bytes, rebuilding nothing: each region is copied and every pointer recomputed.
A layout writer that cannot reproduce a file it was handed has no business
rebuilding one, and this is how that gets checked before any content depends on
it. `tools/relayout.py` runs it over the archive.

Byte identity is *not* the test and would be the wrong one. The shipped files
carry 38,540 bytes of padding between regions in runs of 4, 8, 20, 24 and 28 --
deliberate, not a single alignment rule -- so a writer that aligns to 4 emits a
shorter file that says exactly the same thing. What has to match is what the
reader reads back.
"""

from __future__ import annotations

import struct

from .mdl import MESH_HEADER_SIZE, Model, read_model

MESH_HEADER_START = 0x58
RESIDENT_SIZE = 0x50  # base-relative, not self-relative

# Every self-relative field of the model header. A relayout rewrites each one
# by mapping its old target through where that byte ended up, so a field whose
# target is not inside a region this knows about is a refusal rather than a
# guess.
HEADER_FIELDS = (0x08, 0x10, 0x18, 0x1C, 0x20, 0x24, 0x28, 0x2C, 0x3C, 0x44,
                 0x4C)


class Unmapped(ValueError):
    """A pointer names a byte no planned region covers."""


def _align(out: bytearray, to: int = 4) -> None:
    if len(out) % to:
        out.extend(b"\x00" * (to - len(out) % to))


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
    for mesh in list(model.meshes) + [o.mesh for o in model.objects
                                      if o.mesh is not None]:
        low = min(mesh.ptr_bounds, mesh.ptr_strips, mesh.ptr_uv_index,
                  mesh.ptr_texture, mesh.ptr_colour_index,
                  mesh.header_offset or mesh.ptr_bounds)
        spans.append((low, mesh.ptr_end))
        if mesh.ptr_attachment:
            spans.append((mesh.ptr_attachment,
                          mesh.ptr_attachment + 4 + 16 * len(mesh.volumes)))
    spans += [(resolve(0x20), resolve(0x24)), (resolve(0x24), resolve(0x28)),
              (resolve(0x28), resolve(0x08))]

    # Everything past the boundary is copied as one run: the object table, the
    # scene nodes, the sub-objects, the model refs, the clip directory and its
    # blobs. Splitting it would need lengths the header does not state, and
    # nothing is gained -- it moves as a unit and its pointers are internal.
    tail = min((resolve(field) for field in (0x1C, 0x18, 0x2C, 0x3C, 0x44)
                if 0 < resolve(field) <= len(data)), default=len(data))
    tail = max(tail, resolve(0x08))
    if tail < len(data):
        spans.append((tail, len(data)))

    merged: list[tuple[int, int]] = []
    for start, end in sorted(s for s in spans if 0 <= s[0] < s[1] <= len(data)):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def relayout(data: bytes, model: Model | None = None) -> bytes:
    """Re-emit a model with every region in place and every pointer recomputed.

    Nothing is rebuilt -- each region's bytes are copied verbatim. What is
    exercised is the arithmetic, so a model that reads back the same says the
    writer knows where everything goes, and one that does not says where the
    map is wrong.
    """
    model = model or read_model(data)
    regions = plan(data, model)
    out = bytearray()
    moves: list[tuple[int, int, int]] = []      # (start, end, new start)
    for start, end in regions:
        _align(out, 4)
        moves.append((start, end, len(out)))
        out.extend(data[start:end])
    _align(out, 4)

    def moved(offset: int) -> int:
        for start, end, at in moves:
            if start <= offset < end:
                return at + (offset - start)
            if offset == end:            # a one-past-the-end pointer, and the
                return at + (end - start)  # tables use them
        raise Unmapped(f"{offset:#x} is in no region")

    for field in HEADER_FIELDS:
        target = field + struct.unpack_from("<i", data, field)[0]
        if not 0 <= target <= len(data):
            continue
        struct.pack_into("<i", out, field, moved(target) - field)
    struct.pack_into("<i", out, RESIDENT_SIZE,
                     moved(min(struct.unpack_from("<i", data, RESIDENT_SIZE)[0],
                               len(data))))
    return bytes(out)
