"""Byte coverage of the MDL corpus: what does the documented format account for?

Marks every byte a structure in `docs/FORMAT.md` claims, then reports what is
left. An unclaimed span is either padding or a structure nobody has found yet --
which is how the object table of §8.3 and the hub block of §8.6 were both
missed. Run it after changing the spec, and again after changing the readers:

    .venv/bin/python tools/coverage.py game/SCUS_945.70

The number it prints is the honest state of §14. It is not a test: a byte being
claimed says the format names it, not that the naming is right.
"""
from __future__ import annotations

import struct
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from crashbash import scene as scene_module  # noqa: E402
from crashbash.archive import BashArchive, find_exe  # noqa: E402
from crashbash.formats import anim, mdl  # noqa: E402


def i32(data: bytes, at: int) -> int:
    return struct.unpack_from("<i", data, at)[0]


def u16(data: bytes, at: int) -> int:
    return struct.unpack_from("<H", data, at)[0]


def target(data: bytes, field: int) -> int:
    """`T(x)` -- the self-relative resolve every MDL pointer uses."""
    return field + i32(data, field)


def cover(data: bytes, model, clips) -> tuple[bytearray, Counter]:
    marks = bytearray(len(data))
    owners: Counter = Counter()

    def claim(start: int, end: int, label: str) -> None:
        for i in range(max(0, start), min(len(marks), end)):
            if not marks[i]:
                marks[i] = 1
                owners[label] += 1

    claim(0, 0x58, "file header")
    numbered = i32(data, 0x54)
    claim(0x58, 0x58 + mdl.MESH_HEADER_SIZE * numbered, "mesh headers")

    for mesh in model.drawn_meshes:
        if mesh.header_offset >= 0x58 + mdl.MESH_HEADER_SIZE * numbered:
            claim(mesh.header_offset, mesh.header_offset + mdl.MESH_HEADER_SIZE,
                  "object mesh headers")
        if not mesh.ptr_bounds:
            continue
        claim(mesh.ptr_bounds, mesh.ptr_bounds + mdl.BOUNDS_SIZE, "bounds block")
        claim(mesh.ptr_bounds + mdl.BOUNDS_SIZE,
              mesh.ptr_normals or mesh.ptr_uv_index, "vertex pool")
        if mesh.ptr_normals:
            claim(mesh.ptr_normals, mesh.ptr_uv_index, "normals")
        claim(mesh.ptr_strips, mesh.ptr_bounds, "strip list")
        claim(mesh.ptr_uv_index, mesh.ptr_texture, "uv index array")
        claim(mesh.ptr_texture, mesh.ptr_colour_index, "texture runs")
        claim(mesh.ptr_colour_index, mesh.ptr_end, "colour index array")
        if mesh.ptr_attachment:
            count = u16(data, mesh.ptr_attachment + mdl.ATTACHMENT_COUNT)
            claim(mesh.ptr_attachment,
                  mesh.ptr_attachment + mdl.ATTACHMENT_FIRST
                  + mdl.ATTACHMENT_STRIDE * count, "attachment block")
        claim(mesh.ptr_end, mesh.ptr_end + 4, "mesh end terminator")

    pool_alias, vectors = target(data, 0x08), target(data, 0x28)
    claim(target(data, 0x20), target(data, 0x24), "colour table")
    if vectors != pool_alias:
        claim(target(data, 0x24), vectors, "uv table")
        claim(vectors, pool_alias, "vector pool")
    else:
        claim(target(data, 0x24), pool_alias, "uv table")
    claim(pool_alias, pool_alias + 16, "the 16 zero bytes at T(0x08)")

    chunks = target(data, 0x3C)
    claim(chunks, chunks + 4 + 16 * (i32(data, 0x38) + 1), "chunk descriptors")

    objects, roots_at, subobjects = (target(data, 0x1C), target(data, 0x4C),
                                     target(data, 0x18))
    claim(objects, objects + mdl.OBJECT_STRIDE * len(model.objects),
          "object records")
    claim(roots_at, roots_at + 4 * i32(data, 0x48), "scene root array")
    claim(subobjects, subobjects + 4 + 4 * i32(data, 0x14), "sub-object array")

    for slot in range(i32(data, 0x14)):
        at = subobjects + 4 + 4 * slot
        sub = at + i32(data, at)
        claim(sub, sub + mdl.SUBOBJECT_HEADER_SIZE, "sub-object header")
        records = sub + mdl.SUBOBJECT_RECORDS + i32(data, sub + mdl.SUBOBJECT_RECORDS)
        claim(records,
              records + mdl.PLACEMENT_STRIDE * i32(data, sub + mdl.SUBOBJECT_COUNT),
              "placement records")
        listed = sub + 0x0C + i32(data, sub + 0x0C)
        entries = i32(data, listed)
        claim(listed, listed + 4 + 4 * entries, "the +0x0C list")
        for k in range(max(entries, 0)):
            entry_at = listed + 4 + 4 * k
            claim(entry_at + i32(data, entry_at),
                  entry_at + i32(data, entry_at) + 104, "the +0x0C list entries")
        # The +0x10 block (§8.5): a count, then 16-byte records, then the
        # structures their +0x0C points at -- all inside the block's own span.
        block = sub + 0x10 + i32(data, sub + 0x10)
        claim(block, block + 4, "the +0x10 block count")
        records = i32(data, block) if 0 <= block < len(data) - 4 else 0
        if 0 < records <= 4096:
            claim(block + 4, block + 4 + 16 * records, "the +0x10 block records")
        claim(block, sub + 0x14 + i32(data, sub + 0x14),
              "the +0x10 block payloads")

    # The object graph, walked the way the spawner walks it (§9.11): a root
    # names its children and a node runs to wherever the next one starts. That
    # accounts for a node whether or not its type is decoded.
    nodes: list[int] = []
    for index in range(max(min(i32(data, 0x48), 64), 0)):
        at = roots_at + 4 * index
        if not 0 <= at < len(data) - 4:
            continue
        root = at + i32(data, at)
        claim(root, root + 0x1C, "root records")
        try:
            children = scene_module.spawn_order(data, index)
        except Exception:
            children = []
        claim(root + 0x1C, root + 0x1C + 4 * len(children), "root child arrays")
        nodes.extend(children)
    nodes = sorted({n for n in nodes if objects <= n < roots_at})
    for i, node in enumerate(nodes):
        end = nodes[i + 1] if i + 1 < len(nodes) else roots_at
        kind = i32(data, node) if node < len(data) - 4 else -1
        claim(node, min(end, roots_at),
              "scene nodes" if kind in (0, 1, 2, 3, 5)
              else f"scene nodes, type {kind}")

    clip_table = target(data, 0x44)
    claim(clip_table, clip_table + 24 * i32(data, 0x40), "clip table")
    if i32(data, 0x40) == 0 and clip_table + 0x34 < len(data):
        claim(clip_table, len(data), "the hub's appended block (§8.6)")

    for clip in clips:
        claim(clip.start, clip.end, "animation blobs")
        if not clip.pool_is_shared and clip.pool_count:
            claim(clip.pool_offset, clip.pool_offset + 6 * clip.pool_count,
                  "clip position pools")

    # Past the clip table the rest is zero padding to the next 0x800 (§2.1).
    run = None
    for i in range(clip_table + 24 * i32(data, 0x40), len(marks) + 1):
        blank = i < len(marks) and not marks[i] and data[i] == 0
        if blank and run is None:
            run = i
        elif not blank and run is not None:
            claim(run, i, "blob alignment padding")
            run = None

    return marks, owners


def spans(marks: bytearray) -> list[tuple[int, int]]:
    out, run = [], None
    for i, mark in enumerate(marks):
        if not mark and run is None:
            run = i
        elif mark and run is not None:
            out.append((run, i))
            run = None
    if run is not None:
        out.append((run, len(marks)))
    return out


def main(exe: str) -> int:
    archive = BashArchive(exe)
    total = claimed = 0
    by_owner: Counter = Counter()
    worst: list[tuple[int, str, int]] = []
    for entry in archive:
        if entry.kind not in ("mdl", "mdl2") or not entry.size:
            continue
        data = archive.read(entry)
        model = mdl.read_model(data)
        try:
            clips = anim.read_animations(data, model)
        except Exception:
            clips = []
        marks, owners = cover(data, model, clips)
        total += len(data)
        claimed += sum(marks)
        by_owner.update(owners)
        left = len(data) - sum(marks)
        if left:
            worst.append((left, entry.name, len(data)))

    print(f"{total} bytes over the MDL corpus, {claimed} claimed "
          f"({100.0 * claimed / total:.2f}%), {total - claimed} unclaimed\n")
    for label, count in by_owner.most_common():
        print(f"  {label:34s} {count:10d}")
    print("\nmodels with the most unclaimed:")
    for left, name, size in sorted(worst, reverse=True)[:10]:
        print(f"  {name:46s} {left:7d} of {size:7d}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else str(find_exe("game"))))
