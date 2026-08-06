"""Account for every byte of a model, region by region.

A writer that lays a model out from scratch can only be trusted as far as the
map it works from: anything the map does not name is something the writer would
drop. So this is the floor that work stands on -- it walks a model the way the
header states it and reports what is left over.

    .venv/bin/python tools/layout.py game/SCUS_945.70
    .venv/bin/python tools/layout.py game/SCUS_945.70 models/warp_room1/level.mdl

Measured over the shipped archive as this was written: 400 models, **174 with
under 64 bytes left over** -- which is the 4- and 8-byte alignment padding
between regions -- and a median of 284. What still goes unnamed is concentrated
in the seven §8.6 carriers, whose door-preview block runs from `T(0x44)` to EOF
and is walked through the `T(0x3C)` descriptor rows rather than any length in
the header.
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from crashbash.archive import BashArchive
from crashbash.formats.mdl import MESH_HEADER_SIZE, read_model

MESH_HEADER_START = 0x58
PLACEMENT_STRIDE = 160
CLIP_STRIDE = 24
MODEL_REF_STRIDE = 16


def regions(data: bytes, model) -> list[tuple[str, int, int]]:
    """`(name, start, end)` for everything the header accounts for.

    In file order as the shipped models lay it out: the header, the mesh
    headers, each mesh's blocks, the shared tables, the vector pool, the object
    pool, the tables past the boundary, and the clip directory with its blobs.
    """
    i32 = lambda at: struct.unpack_from("<i", data, at)[0]  # noqa: E731
    resolve = lambda at: at + i32(at)  # noqa: E731
    out: list[tuple[str, int, int]] = [("header", 0, MESH_HEADER_START)]

    if model.meshes:
        out.append(("mesh headers", MESH_HEADER_START,
                    MESH_HEADER_START + MESH_HEADER_SIZE * len(model.meshes)))
    for mesh in model.meshes:
        low = min(mesh.ptr_bounds, mesh.ptr_strips, mesh.ptr_uv_index,
                  mesh.ptr_texture, mesh.ptr_colour_index)
        out.append((f"mesh {mesh.index}", low, mesh.ptr_end))
        if mesh.ptr_attachment:
            # §8.4: a count then that many 16-byte records.
            out.append((f"mesh {mesh.index} volumes", mesh.ptr_attachment,
                        mesh.ptr_attachment + 4 + 16 * len(mesh.volumes)))

    out += [("colour table", resolve(0x20), resolve(0x24)),
            ("uv table", resolve(0x24), resolve(0x28)),
            ("vector pool", resolve(0x28), resolve(0x08))]

    seen = set()
    for obj in model.objects:
        if obj.mesh is not None and obj.mesh.header_offset not in seen:
            seen.add(obj.mesh.header_offset)
            out.append((f"pool {obj.id:04X}", obj.mesh.header_offset,
                        obj.mesh.ptr_end))

    refs = i32(0x38)
    if refs:
        out.append(("model refs", resolve(0x3C),
                    resolve(0x3C) + MODEL_REF_STRIDE * refs))

    # §8.3: the object table's leading rows name pool meshes and the scene
    # nodes follow them, with no count in the header for either -- so its
    # extent is "up to whatever the header names next". `0x4C` is the same
    # shape and unidentified; both are mapped by where they end rather than by
    # a length, which is honest about what is known.
    marks = sorted({resolve(field) for field in (0x18, 0x44, 0x4C)
                    if 0 < resolve(field) <= len(data)} | {len(data)})
    for field, name in ((0x1C, "object table and scene nodes"), (0x4C, "0x4C block")):
        start = resolve(field)
        if not 0 < start <= len(data):
            continue
        end = next((mark for mark in marks if mark > start), len(data))
        out.append((name, start, end))

    if i32(0x14):
        table = resolve(0x18)
        out.append(("sub-object table", table, table + 4 + 4 * i32(0x14)))
        for slot in range(i32(0x14)):
            at = table + 4 + 4 * slot
            sub = at + i32(at)
            records = sub + 0x20 + i32(sub + 0x20)
            out.append((f"sub-object {slot}", sub,
                        records + PLACEMENT_STRIDE * i32(sub + 0x1C)))
            # The four blocks laid after the record array (§8.5). `+0x0C` is
            # its end and `+0x14` runs to the clip table, so between them they
            # cover the rest of the sub-object.
            first = min(sub + off + i32(sub + off) for off in (0x0C, 0x10))
            out.append((f"sub-object {slot} blocks", first, resolve(0x44)))

    clips = i32(0x40)
    if clips:
        out.append(("clip directory", resolve(0x44),
                    resolve(0x44) + CLIP_STRIDE * clips))
        out.append(("clip blobs", resolve(0x44) + CLIP_STRIDE * clips, len(data)))
    elif i32(0x38):
        # A §8.6 carrier: the door-preview block runs from the clip table's
        # place to the end and is reached through the `T(0x3C)` rows, not
        # through any length the header states.
        out.append(("§8.6 block", resolve(0x44), len(data)))
    return out


def unaccounted(data: bytes, model) -> int:
    """Bytes no named region covers."""
    spans = sorted((a, b) for _, a, b in regions(data, model)
                   if 0 <= a < b <= len(data))
    covered = end = 0
    for a, b in spans:
        if b <= end:
            continue
        covered += b - max(a, end)
        end = max(end, b)
    return len(data) - covered


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    archive = BashArchive(argv[1])
    by_name = {entry.name: entry for entry in archive}

    if len(argv) > 2:
        data = archive.read(by_name[argv[2]])
        model = read_model(data)
        print(f"{argv[2]}: {len(data)} bytes")
        for name, start, end in sorted(regions(data, model),
                                       key=lambda r: (r[1], r[2])):
            print(f"  {start:#09x}..{end:#09x}  {end - start:8d}  {name}")
        print(f"  unaccounted: {unaccounted(data, model)} bytes")
        return 0

    left = []
    for entry in archive:
        if not entry.name.endswith(".mdl"):
            continue
        data = archive.read(entry)
        if len(data) < 0x60:
            continue
        try:
            model = read_model(data)
        except Exception:  # noqa: BLE001
            continue
        left.append((unaccounted(data, model), entry.name))
    left.sort()
    values = [n for n, _ in left]
    print(f"{len(values)} models")
    print(f"  fully accounted for: {sum(1 for v in values if v == 0)}")
    print(f"  under 64 bytes left: {sum(1 for v in values if v < 64)}")
    print(f"  median {values[len(values) // 2]}, worst {values[-1]}")
    print("  the ones still worst understood:")
    for n, name in left[-6:]:
        print(f"     {name:46s} {n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
