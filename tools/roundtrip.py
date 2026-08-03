"""Export every model to glTF, import it straight back, and measure the loss.

The house rule is that a writer is checked against the game's own data, never
against this project's reader (`CLAUDE.md`). So this does not compare a parse to
a parse: it exports a shipped entry, imports the file it just wrote, and asks
whether the model that comes out still draws what the shipped one drew.

Byte identity is the wrong bar for geometry and the right one for the scene.
Import rebuilds a mesh from triangles, so the strip list is re-derived and the
bytes legitimately differ; what must survive is the drawn surface -- the same
triangle count, over the same corner positions. The scene is different: it is
patched in place, field by field, so an unedited trip has to reproduce the
shipped bytes exactly, and that is checked separately here.

Run:  .venv/bin/python tools/roundtrip.py game/SCUS_945.70 [--limit N] [--group G]
"""
from __future__ import annotations

import argparse
import os
import struct
import sys
import tempfile
from collections import defaultdict

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from crashbash.archive import BashArchive  # noqa: E402
from crashbash.formats.anim import read_animations  # noqa: E402
from crashbash.formats.gltf import _scene_extras, export_glb  # noqa: E402
from crashbash.formats.gltfimport import import_glb  # noqa: E402
from crashbash.formats.mdl import read_model  # noqa: E402
from crashbash.formats.scenewrite import patch_scene  # noqa: E402
from crashbash.scene import read_scene  # noqa: E402

WARP = ("demo_hub1", "demo_hub2", "warp_room1", "warp_room2", "warp_room3",
        "warp_room4", "warp_room5")


def group_of(name: str) -> str:
    """The four families the corpus actually splits into."""
    head = name.split("/")[1] if name.count("/") > 1 else name.split("/")[0]
    if head in WARP or head == "arena":
        return "level"
    return {"chars": "character", "cutscene": "cutscene"}.get(head, "models")


def corners(model) -> np.ndarray:
    """Every triangle corner a model draws, sorted, as one array of positions.

    Comparing corners rather than the vertex pool is deliberate: a rebuild is
    free to renumber or re-order the pool, and only what the triangles reach is
    the drawn surface.

    Sorting is not cosmetic. `install_mesh` re-strips a mesh, so the triangles
    come back in a different order, and comparing the two lists position by
    position measures that re-ordering rather than any loss -- it reported
    corner errors of 5 to 20 units on models that had in fact lost nothing.
    """
    out = []
    for mesh in model.drawn_meshes:
        if not mesh.positions:
            continue
        positions = np.asarray(mesh.positions, dtype=np.float64)
        for triangle in mesh.triangles(consistent_winding=True):
            if max(triangle) < len(positions):
                out.append(positions[list(triangle)])
    if not out:
        return np.zeros((0, 3))
    corner = np.concatenate(out)
    return corner[np.lexsort((corner[:, 2], corner[:, 1], corner[:, 0]))]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("exe")
    parser.add_argument("--limit", type=int, default=0,
                        help="stop after this many models per group")
    parser.add_argument("--group", default="")
    args = parser.parse_args()

    archive = BashArchive(args.exe)
    seen: dict[str, int] = defaultdict(int)
    stats: dict[str, dict] = defaultdict(
        lambda: {"files": 0, "triangles": 0, "matched": 0, "worst": 0.0,
                 "scenes": 0, "identical": 0, "skipped": 0, "failed": []})

    with tempfile.TemporaryDirectory() as work:
        path = os.path.join(work, "roundtrip.glb")
        for entry in archive:
            if entry.kind not in ("mdl", "mdl2") or not entry.size:
                continue
            group = group_of(entry.name)
            if args.group and group != args.group:
                continue
            if args.limit and seen[group] >= args.limit:
                continue
            seen[group] += 1
            row = stats[group]

            data = archive.read(entry)
            model = read_model(data)
            clips = read_animations(data, model)
            scene = read_scene(data, model, clips)

            # --- the scene, which must come back byte for byte -------------
            if scene is not None or model.instances:
                extras = _scene_extras(scene, model) if scene is not None else {
                    "placements": [
                        {"record": i.record, "id": i.id, "flags": i.flags,
                         "translation": [float(v) for v in i.translation],
                         "rotation": [float(v) for v in i.rotation]}
                        for i in model.instances]}
                patched, report = patch_scene(data, extras)
                row["scenes"] += 1
                row["identical"] += patched == data
                row["skipped"] += len(report.skipped)

            # --- the geometry, which must come back drawing the same -------
            before = corners(model)
            if not len(before):
                row["files"] += 1
                continue
            try:
                with open(path, "wb") as handle:
                    handle.write(export_glb(model, None, clips, "roundtrip",
                                            scene))
                result = import_glb(path, data, None)
                after = corners(read_model(result.model))
            except Exception as error:  # noqa: BLE001 - reported, not raised
                row["failed"].append(f"{entry.name}: {error}")
                row["files"] += 1
                continue

            row["files"] += 1
            row["triangles"] += len(before) // 3
            if len(after) == len(before):
                row["matched"] += len(before) // 3
                row["worst"] = max(row["worst"],
                                   float(np.abs(after - before).max()))
            else:
                row["failed"].append(
                    f"{entry.name}: {len(before)//3} triangles out, "
                    f"{len(after)//3} back")

    print(f'{"group":10s} {"files":>6} {"triangles":>10} {"same count":>11} '
          f'{"worst corner":>13} {"scenes":>7} {"identical":>10} {"skipped":>8}')
    for group in ("level", "cutscene", "character", "models"):
        row = stats.get(group)
        if not row:
            continue
        print(f'{group:10s} {row["files"]:6d} {row["triangles"]:10d} '
              f'{row["matched"]:11d} {row["worst"]:13.4f} {row["scenes"]:7d} '
              f'{row["identical"]:10d} {row["skipped"]:8d}')
        for line in row["failed"][:5]:
            print(f"    {line}")
        if len(row["failed"]) > 5:
            print(f'    ... {len(row["failed"])} failures in all')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
