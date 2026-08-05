"""Take a model into Blender and back out, and measure what survived.

The add-on's own check, and the only one that exercises what an artist actually
touches: `build_scene` makes the datablocks, `read_scene` reads them back, and
the result is compared against the shipped entry rather than against anything
this project wrote on the way. Nothing is edited in between, so every difference
is a loss.

Four things are measured, and the last two are what the glTF path cannot check
at all because it folds them away:

* the drawn surface -- same triangles over the same corner positions;
* the corner colours, which the console multiplies the texel by;
* the swatch palette and cell each untextured face reads (§6.2);
* every clip, pose by pose and frame by frame.

    Blender --background --python blender/roundtrip.py -- game/SCUS_945.70 [name...]
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

import numpy as np

import bpy

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

from io_scene_crashbash import build_scene, read_scene  # noqa: E402

from crashbash.archive import BashArchive  # noqa: E402
from crashbash.binreader import GTE_SCALE_SMALL  # noqa: E402
from crashbash.formats import modelimport as MI  # noqa: E402
from crashbash.formats.anim import read_animations  # noqa: E402
from crashbash.formats.mdl import read_model  # noqa: E402
from crashbash.formats.tex import read_pack  # noqa: E402

DEFAULT = ["models/chars/crate/coco.mdl", "models/chars/crate/crash.mdl",
           "models/mainmenu/models.mdl", "models/cutscene/level_shot12.mdl"]


def faces(data: bytes, model, pack, index: int, clips) -> Counter:
    """Triangles as a multiset of everything one carries, facing included.

    Through `payload_from_model` on both sides, which is what the add-on itself
    reads a mesh with -- position, the three corner colours, the UVs and the
    texture entry, keyed canonically under rotation and not under reversal.
    """
    payload = MI.payload_from_model(data, model, pack, index, clips)
    return MI.payload_bag(payload) if payload is not None else Counter()


def clip_frames(data: bytes, model) -> dict[str, list[Counter]]:
    """Every clip's drawn triangles, frame by frame.

    Not the pose arrays: a rebuild re-stripes, so the pool is a different length
    and its order is its own -- `chars/crate/coco` mesh 0 goes from 319 pool
    entries to 629 while drawing the same 243 triangles. What the player sees is
    the triangles the strips make out of that pool, so that is what is compared.
    """
    out: dict[str, list[Counter]] = {}
    for clip in read_animations(data, model):
        # A clip whose mesh the reader could not resolve has nothing to draw
        # here; the import copies it through untouched.
        if clip.mesh_index is None or clip.mesh_index >= len(model.meshes):
            continue
        mesh = model.meshes[clip.mesh_index]
        corners = [(a, b, c) for a, b, c, _ in mesh.indexed_triangles()]
        shots = []
        for frame in range(clip.frame_count):
            # Back to raw model units. `pose` hands back 8.8 fixed point, so
            # rounding it as it stands puts a whole model inside a couple of
            # integers and every triangle compares equal to rubbish.
            pose = np.asarray(clip.pose(frame), dtype=np.float64) / GTE_SCALE_SMALL
            bag: Counter = Counter()
            for a, b, c in corners:
                bag[tuple(sorted(tuple(int(round(v)) for v in pose[i])
                                 for i in (a, b, c)))] += 1
            shots.append(bag)
        out[clip.label] = shots
    return out


def check(name: str, data: bytes, pack_data: bytes | None, source: str) -> bool:
    model = read_model(data)
    pack = read_pack(pack_data) if pack_data else None
    clips = read_animations(data, model)

    collection, notes = build_scene.build_model(name, data, pack_data, source,
                                                name[:-4] + ".tex")
    request = read_scene.build_request(collection, model, clips, pack)
    if request.problems:
        print(f"  REFUSED: {'; '.join(request.problems[:3])}")
        return False
    report = MI.import_payload(data, pack_data, request, rebuild_all=True)
    rebuilt = read_model(report.model)

    good = True
    total = same = 0
    new_clips = read_animations(report.model, rebuilt)
    for index in sorted(request.meshes):
        want = faces(data, model, pack, index, clips)
        got = faces(report.model, rebuilt, pack, index, new_clips)
        total += sum(want.values())
        same += sum((want & got).values())
        if want != got:
            good = False
            print(f"  mesh {index}: {sum((want - got).values())} of "
                  f"{sum(want.values())} triangles differ")

    was, now = clip_frames(data, model), clip_frames(report.model, rebuilt)
    shown = matched = 0
    intact = 0
    for label, shots in was.items():
        other = now.get(label)
        if other is None or len(other) != len(shots):
            print(f"  clip {label}: {len(shots)} frames -> "
                  f"{None if other is None else len(other)}")
            good = False
            continue
        loss = 0
        for want, got in zip(shots, other):
            shown += sum(want.values())
            hit = sum((want & got).values())
            matched += hit
            loss += sum(want.values()) - hit
        if loss:
            print(f"  clip {label}: {loss} animated triangles differ")
            good = False
        else:
            intact += 1
    print(f"  {same}/{total} triangles identical (positions, colours, palette, "
          f"cell), {intact}/{len(was)} clips intact, "
          f"{matched}/{shown} animated triangles identical")
    for note in notes:
        print(f"  note: {note}")
    for warning in report.warnings:
        print(f"  warn: {warning}")
    return good


def main() -> None:
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    if not argv:
        print("usage: ... -- <exe> [entry ...]")
        sys.exit(2)
    exe, wanted = argv[0], argv[1:] or DEFAULT
    archive = BashArchive(exe)
    by_name = {e.name: e for e in archive}
    if len(wanted) == 1 and wanted[0].isdigit():
        # A count means "sweep": that many models, spread evenly over the
        # archive rather than taken off the front, so one run covers levels,
        # cutscenes, characters and the menu instead of whichever comes first.
        models = [e.name for e in archive if e.name.endswith(".mdl")]
        step = max(1, len(models) // int(wanted[0]))
        wanted = models[::step][: int(wanted[0])]

    failures = refused = 0
    for name in wanted:
        if name not in by_name:
            print(f"{name}: not in the archive")
            failures += 1
            continue
        print(f"{name}:")
        pack_name = name[:-4] + ".tex"
        data = archive.read(by_name[name])
        pack_data = archive.read(by_name[pack_name]) if pack_name in by_name else None
        try:
            if not check(name, data, pack_data, exe):
                failures += 1
        except ValueError as exc:
            # A refusal is the add-on working. Six of the archive's font models
            # carry no numbered mesh at all -- only object-pool ones, which
            # cannot be installed back (§8.3) -- and the §8.6 carriers keep a
            # pinned UV table a rebuild cannot satisfy. Neither is a loss, and
            # counting them as one would hide a real regression among them.
            print(f"  refused: {exc}")
            refused += 1
        except Exception as exc:  # noqa: BLE001
            import traceback

            traceback.print_exc()
            print(f"  ERROR {type(exc).__name__}: {exc}")
            failures += 1
        # A fresh file per entry, so nothing carries over between them.
        bpy.ops.wm.read_factory_settings(use_empty=True)

    survived = len(wanted) - failures - refused
    print(f"\n{survived}/{len(wanted) - refused} models survived the trip"
          + (f", {refused} refused for a stated reason" if refused else ""))
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
