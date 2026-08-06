"""Re-emit every model from its own regions and check nothing was lost.

This is the first half of the from-scratch writer, checked on its own: the
layout arithmetic with no content rebuilt. `modelwrite.relayout` copies each
region and recomputes every pointer from where it lands, so a model that comes
back saying the same thing means the writer knows where everything goes -- and
one that does not says exactly where the map in `tools/layout.py` is wrong.

    .venv/bin/python tools/relayout.py game/SCUS_945.70

Byte identity is not the test and would be the wrong one: the shipped files
carry 38,540 bytes of padding between regions, in runs of 4, 8, 20, 24 and 28,
so a writer that aligns to 4 emits a shorter file saying the same thing. What
is compared is what the reader reads back -- every mesh canonically, through
`payload_bag`, which covers positions, colours, UVs, the texture entry and the
corner order that the console culls on.

**Count the meshes before comparing them.** A mesh the reader could not resolve
comes back as `None`, and a comparison that skips those calls a model clean
while its whole object pool has gone missing -- which is exactly what a stale
object record does, and it hid the pool-offset bug through a run that reported
15 failures when every level in the archive was losing meshes.

The second pass is the one the goal turns on: both shared tables grown by 64
entries with every shipped entry left at its own index. That is what an edit
needs and what `mdlwrite` cannot do -- it appends fresh tables and strands the
old, 30,528 bytes on `boss_oxide/arena`, or pins them and pays in colour. Here
the table is simply longer where it stands and everything after it moves, for
**372 bytes**.
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from crashbash.archive import BashArchive
from crashbash.formats import modelimport as MI
from crashbash.formats import modelwrite as MW
from crashbash.formats.anim import read_animations
from crashbash.formats.mdl import read_model
from crashbash.formats.tex import read_pack


GROW_BY = 64  # entries added to each shared table by the growth pass


def compare(data: bytes, model, out: bytes, back, pack) -> str | None:
    """What the relaid model lost, if anything."""
    pooled = [o.mesh.index for o in model.objects if o.mesh is not None]
    repooled = [o.mesh.index for o in back.objects if o.mesh is not None]
    if len(pooled) != len(repooled):
        return f"{len(pooled)} pool meshes became {len(repooled)}"
    if len(model.meshes) != len(back.meshes):
        return f"{len(model.meshes)} meshes became {len(back.meshes)}"
    if len(model.instances) != len(back.instances):
        return f"{len(model.instances)} placements became {len(back.instances)}"
    # §8.4's attachment block, before the geometry: it is the collision volume,
    # nothing in a payload carries it, and a rebuilt mesh that comes back with a
    # null `+0x2C` reads on screen as an object spinning on the spot. Two pool
    # meshes lost theirs to a `landed.get(..., 0)` while every geometry
    # comparison passed.
    volumes = {m.header_offset: len(m.volumes) for m in model.meshes}
    volumes.update({o.mesh.header_offset: len(o.mesh.volumes)
                    for o in model.objects if o.mesh is not None})
    after = {m.header_offset: len(m.volumes) for m in back.meshes}
    after.update({o.mesh.header_offset: len(o.mesh.volumes)
                  for o in back.objects if o.mesh is not None})
    if sorted(volumes.values()) != sorted(after.values()):
        return (f"{sum(1 for v in volumes.values() if v)} meshes carried an "
                f"attachment block and {sum(1 for v in after.values() if v)} do")
    for index in [m.index for m in model.meshes] + pooled:
        want = MI.payload_from_model(data, model, pack, index, {})
        got = MI.payload_from_model(out, back, pack, index, {})
        if (want is None) != (got is None):
            return f"mesh {index} {'vanished' if got is None else 'appeared'}"
        if want is not None and MI.payload_bag(want) != MI.payload_bag(got):
            return f"mesh {index} does not match"
    try:
        if len(read_animations(data, model)) != len(read_animations(out, back)):
            return "the clip count changed"
    except Exception:  # noqa: BLE001
        pass
    return None


def check(data: bytes, pack, model) -> str | None:
    """What a straight relayout of this model lost, if anything."""
    out = MW.relayout(data, model)
    back = read_model(out)
    if back.warnings:
        return f"the reader complained: {back.warnings[0]}"
    return compare(data, model, out, back, pack)


def check_grown(data: bytes, pack, model) -> tuple[str | None, int]:
    """The same with both shared tables `GROW_BY` entries longer.

    The added entries are recognisable filler rather than plausible values: the
    point is that every *shipped* entry still answers to its own index, so what
    the meshes read back has to be unchanged.
    """
    colours, uvs, pool = MW.table_bounds(data)
    if not 0 < colours < uvs < pool <= len(data):
        return None, 0
    out = MW.relayout(data, model, {
        colours: data[colours:uvs] + b"\x11\x22\x33\x00" * GROW_BY,
        uvs: data[uvs:pool] + b"\x07\x09" * GROW_BY,
    })
    return compare(data, model, out, read_model(out), pack), len(out) - len(data)


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    archive = BashArchive(argv[1])
    by_name = {entry.name: entry for entry in archive}
    clean = grown = 0
    failures: Counter[str] = Counter()
    growth: list[int] = []
    cramped: list[str] = []
    before = after = 0
    for entry in archive:
        if not entry.name.endswith(".mdl"):
            continue
        data = archive.read(entry)
        if len(data) < 0x60:
            continue
        tex = by_name.get(entry.name[:-4] + ".tex")
        try:
            model = read_model(data)
            pack = read_pack(archive.read(tex)) if tex else None
            lost = check(data, pack, model)
            before += len(data)
            after += len(MW.relayout(data, model))
        except Exception as exc:  # noqa: BLE001
            failures[f"{entry.name}: {type(exc).__name__} {exc}"] += 1
            continue
        if lost:
            failures[f"{entry.name}: {lost}"] += 1
        else:
            clean += 1
        try:
            lost, added = check_grown(data, pack, model)
        except MW.Unmapped as exc:
            # A §8.6 carrier's block keeps its file offset, so the room below it
            # is finite and a table can only grow into what is left. Refusing is
            # the correct answer, not a failure -- what would be a failure is
            # writing it anyway.
            cramped.append(f"{entry.name}: {exc}")
            continue
        except Exception as exc:  # noqa: BLE001
            failures[f"{entry.name}: grown, {type(exc).__name__} {exc}"] += 1
            continue
        if not added:
            continue
        if lost:
            failures[f"{entry.name}: grown, {lost}"] += 1
        else:
            grown += 1
            growth.append(added)
    print(f"models that survive a relayout intact: {clean}")
    if growth:
        growth.sort()
        print(f"models whose tables grow {GROW_BY} entries each with every "
              f"shipped entry in place: {grown}")
        print(f"   cost: min {growth[0]}, median {growth[len(growth) // 2]}, "
              f"max {growth[-1]} bytes")
    if cramped:
        print(f"§8.6 carriers with no room left below their block: "
              f"{len(cramped)}")
        for line in cramped[:3]:
            print(f"   {line}")
    print(f"failures: {sum(failures.values())}")
    print(f"total size {before} -> {after} ({after - before:+d})")
    for reason, _ in failures.most_common(8):
        print(f"   {reason}")
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
