"""Take every model out through `modelimport` and back, and measure the loss.

`tools/roundtrip.py` checks the glTF path. This checks the path underneath it:
`payload_from_model` restates a shipped mesh as an incoming one and
`import_payload` builds it back, which is exactly what the Blender add-on does
either side of an artist's edit. Nothing here parses a file the project wrote,
so a reader and writer that agreed with each other and with nothing else would
not pass -- the comparison is against the shipped mesh.

A triangle counts as identical only when its positions, its three corner
colours, its UVs and its texture entry all come back, **and its corners come
back in the same cyclic order**. That last one is the point: `face_key` is
canonical under rotation and not under reversal, because reversing a triangle
turns it inside out (§11.3) and the console culls it rather than drawing it.
Comparing sorted corners instead -- which is what a positions-only check does --
scored 45,300 of 45,300 on a corpus where 62 of `chars/crate/coco`'s 511
triangles were being handed to the writer backwards.

Two things are measured that glTF cannot check at all, because that exporter
folds them into the vertex colour: the **swatch palette** each untextured face
names, which a mesh may vary face by face, and the **cell** it reads.

Run:  .venv/bin/python tools/native_roundtrip.py game/SCUS_945.70 [--limit N]
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from crashbash.archive import BashArchive  # noqa: E402
from crashbash.formats import modelimport as MI  # noqa: E402
from crashbash.formats.anim import read_animations  # noqa: E402
from crashbash.formats.mdl import read_model  # noqa: E402
from crashbash.formats.tex import read_pack  # noqa: E402

GROUPS = ("level", "cutscene", "character", "models")


def group_of(name: str) -> str:
    if "/arena/" in name or name.endswith("/level.mdl"):
        return "level"
    if "/cutscene" in name:
        return "cutscene"
    if "/chars/" in name:
        return "character"
    return "models"


def strip_flags(payload) -> Counter:
    """§5.1's flag per face -- which primitive the strip this triangle is in draws as.

    Measured nowhere until a disc showed it: `payload_bag` covers positions,
    colours, UVs, the texture entry and corner order, the swatch palette and
    cell are counted separately, and the blend mode separately again, so a file
    carrying the flag wrong scored clean everywhere. It is not derivable from
    the texture entry -- 33,097 faces of the archive carry the swatch bit
    inside a strip flagged *textured* -- and a strip rebuilt with the wrong
    flag draws flat shaded with no texture.
    """
    if payload.untextured is None:
        return Counter({None: payload.positions.shape[0]})
    return Counter(bool(v) for v in payload.untextured)


def facing_bag(payload) -> Counter:
    """Each face, canonically, with bit 1 of the vertex flag it ends on.

    Set, the draw skips the backface test altogether and the triangle is
    double-sided (§4.2, `0x80019498`). 15,623 of the archive's pool entries
    carry it and the writer used to drop every one: on a console that is a flat
    card visible from the front and culled from behind -- Aku Aku's feathers --
    and it is invisible to everything else here, because positions, colours,
    UVs, texture entries, blend and strip flags are all still identical.

    Keyed per face rather than counted, because re-striping permutes face order
    and a bare count cannot tell a permutation from a fix.
    """
    bag: Counter = Counter()
    if payload.double_sided is None:
        return Counter({None: payload.positions.shape[0]})
    points = np.clip(np.round(payload.positions), -32768, 32767).astype(np.int64)
    for face in range(points.shape[0]):
        corners = [tuple(int(v) for v in points[face, k]) for k in range(3)]
        start = min(range(3), key=lambda k: corners[k])
        bag[(tuple(corners[(start + k) % 3] for k in range(3)),
             bool(payload.double_sided[face]))] += 1
    return bag


def swatch_bag(payload) -> Counter:
    """(palette, cell) per face that reads the swatch image -- what §6.2 paints."""
    bag: Counter = Counter()
    for row in range(payload.positions.shape[0]):
        entry = int(payload.textures[row])
        if entry >= -1:
            continue
        bag[((-entry) & 0x1FF, tuple(int(v) for v in payload.uvs[row, 0]))] += 1
    return bag


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("exe")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--name", default="")
    args = parser.parse_args()

    archive = BashArchive(args.exe)
    by_name = {e.name: e for e in archive}
    models = [e for e in archive if e.name.endswith(".mdl")]
    if args.name:
        models = [e for e in models if args.name in e.name]
    if args.limit:
        models = models[: args.limit]

    tally: dict[str, list[int]] = defaultdict(lambda: [0] * 10)
    failures: list[str] = []
    for entry in models:
        group = group_of(entry.name)
        data = archive.read(entry)
        pack_entry = by_name.get(entry.name[:-4] + ".tex")
        pack_data = archive.read(pack_entry) if pack_entry else None
        try:
            model = read_model(data)
            pack = read_pack(pack_data) if pack_data else None
            clips = read_animations(data, model)
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{entry.name}: unreadable, {exc}")
            continue

        request = MI.ImportRequest()
        for mesh in model.meshes:
            payload = MI.payload_from_model(data, model, pack, mesh.index, clips)
            if payload is not None:
                request.meshes[mesh.index] = payload
        if not request.meshes:
            continue
        try:
            # Rebuilding every mesh of a model grows its shared tables far past
            # anything a real edit does, so 233 of the 400 would be refused for
            # putting them past the resident end. That is a shippability rule
            # and this sweep measures geometry, so it is turned off here on
            # purpose -- see `_refuse_if_past_resident`.
            report = MI.import_payload(data, pack_data, request, rebuild_all=True,
                                       check_resident=False)
            rebuilt = read_model(report.model)
            new_clips = read_animations(report.model, rebuilt)
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{entry.name}: {exc}")
            continue

        row = tally[group]
        row[0] += 1
        for index, before in request.meshes.items():
            after = MI.payload_from_model(report.model, rebuilt, pack, index,
                                          new_clips)
            if after is None:
                row[5] += 1
                continue
            want, got = MI.payload_bag(before), MI.payload_bag(after)
            row[1] += sum(want.values())
            row[2] += sum((want & got).values())
            wsw, gsw = swatch_bag(before), swatch_bag(after)
            row[3] += sum(wsw.values())
            row[4] += sum((wsw & gsw).values())
            wfl, gfl = strip_flags(before), strip_flags(after)
            row[6] += sum(wfl.values())
            row[7] += sum((wfl & gfl).values())
            wfa, gfa = facing_bag(before), facing_bag(after)
            row[8] += sum(wfa.values())
            row[9] += sum((wfa & gfa).values())
            if want != got or wfl != gfl or wfa != gfa:
                row[5] += 1

    print(f"{'group':<10}{'files':>6}{'triangles':>11}{'same':>11}"
          f"{'swatch':>9}{'same':>9}{'strip flags':>12}{'same':>10}"
          f"{'two-sided':>11}{'same':>9}{'meshes off':>12}")
    for group in GROUPS:
        if group not in tally:
            continue
        files, tris, same, sw, sw_same, off, fl, fl_same, fa, fa_same = tally[group]
        print(f"{group:<10}{files:>6}{tris:>11}{same:>11}{sw:>9}{sw_same:>9}"
              f"{fl:>12}{fl_same:>10}{fa:>11}{fa_same:>9}{off:>12}")

    if failures:
        print(f"\n{len(failures)} refused or unreadable:")
        for line in failures[:8]:
            print(f"    {line}")
        if len(failures) > 8:
            print(f"    ... {len(failures)} in all")


if __name__ == "__main__":
    main()
