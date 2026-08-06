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


def check(data: bytes, pack, model) -> list[str]:
    """What a relayout of this model lost, if anything."""
    out = MW.relayout(data, model)
    back = read_model(out)
    lost: list[str] = []
    if back.warnings:
        lost.append(f"the reader complained: {back.warnings[0]}")
    indices = [m.index for m in model.meshes]
    indices += [o.mesh.index for o in model.objects if o.mesh is not None]
    for index in indices:
        want = MI.payload_from_model(data, model, pack, index, {})
        got = MI.payload_from_model(out, back, pack, index, {})
        if want is None or got is None:
            continue
        if MI.payload_bag(want) != MI.payload_bag(got):
            lost.append(f"mesh {index} does not match")
            break
    if len(model.instances) != len(back.instances):
        lost.append(f"{len(model.instances)} placements became "
                    f"{len(back.instances)}")
    try:
        if len(read_animations(data, model)) != len(read_animations(out, back)):
            lost.append("the clip count changed")
    except Exception:  # noqa: BLE001
        pass
    return lost


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    archive = BashArchive(argv[1])
    by_name = {entry.name: entry for entry in archive}
    clean = 0
    failures = Counter()
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
        except Exception as exc:  # noqa: BLE001
            failures[f"{entry.name}: {type(exc).__name__} {exc}"] += 1
            continue
        before += len(data)
        after += len(MW.relayout(data, model))
        if lost:
            failures[f"{entry.name}: {lost[0]}"] += 1
        else:
            clean += 1
    print(f"models that survive a relayout intact: {clean}")
    print(f"models that do not: {sum(failures.values())}")
    print(f"total size {before} -> {after} ({after - before:+d})")
    for reason, _ in failures.most_common(8):
        print(f"   {reason}")
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
