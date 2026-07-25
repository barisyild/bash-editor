"""Headless companion to the editor: list, extract and convert without a GUI.

    python -m crashbash.cli list   game/SCUS_945.70
    python -m crashbash.cli info   game/SCUS_945.70
    python -m crashbash.cli extract game/SCUS_945.70 -o out
    python -m crashbash.cli obj    game/SCUS_945.70 -o out --filter chars/
    python -m crashbash.cli glb    game/SCUS_945.70 -o out --filter chars/
    python -m crashbash.cli png    game/SCUS_945.70 -o out --filter interface
    python -m crashbash.cli audio  game/SCUS_945.70 -o out
    python -m crashbash.cli wav    game/SCUS_945.70 -o out --filter arena
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import build
from .archive import BashArchive, Entry, UnknownGameVersion, find_exe
from .formats import anim, gltf, mdl, sfx, tex


def _open(path: str) -> BashArchive:
    target = Path(path)
    if target.is_dir():
        found = find_exe(target)
        if found is None:
            raise SystemExit(f"No recognised Crash Bash EXE in {target}")
        target = found
    return BashArchive(target)


def _selected(archive: BashArchive, needle: str | None, group: str | None) -> list[Entry]:
    entries = list(archive)
    if group:
        entries = [e for e in entries if e.group == group]
    if needle:
        low = needle.lower()
        entries = [e for e in entries if low in e.name.lower()]
    return entries


def _human(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / (1024 * 1024):.2f} MB"


def cmd_info(archive: BashArchive, _args) -> int:
    counts: dict[str, int] = {}
    for entry in archive:
        counts[entry.kind] = counts.get(entry.kind, 0) + 1
    print(f"EXE      : {archive.exe_path}")
    print(f"DAT      : {archive.dat_path}")
    print(f"MD5      : {archive.md5}")
    print(f"Build    : {archive.version.name}")
    print(f"Table    : 0x{archive.version.table_offset:X}, {len(archive)} entries")
    print(f"Filenames: {'known' if archive.version.has_filelist else 'not known for this build'}")
    print("Contents :")
    for kind, count in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"    {kind:<6} {count}")
    return 0


def cmd_list(archive: BashArchive, args) -> int:
    for entry in _selected(archive, args.filter, args.group):
        print(
            f"{entry.index:5d}  {entry.kind:<5}  {_human(entry.size):>10}  "
            f"0x{entry.offset:08X}  {entry.name}"
        )
    return 0


def cmd_extract(archive: BashArchive, args) -> int:
    out = Path(args.output)
    entries = _selected(archive, args.filter, args.group)
    for entry in entries:
        destination = out / entry.name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(archive.read(entry))
    print(f"Extracted {len(entries)} files to {out}")
    return 0


def cmd_obj(archive: BashArchive, args) -> int:
    out = Path(args.output)
    written = matched = total = 0
    for entry in _selected(archive, args.filter, "model"):
        model = mdl.read_model(archive.read(entry))
        if not model.meshes:
            continue
        destination = out / Path(entry.name).with_suffix(".obj")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(model.to_obj(), encoding="utf-8")
        written += 1
        total += len(model.meshes)
        matched += sum(1 for m in model.meshes if m.faces_match_header)
    print(f"Wrote {written} OBJ files to {out}")
    if total:
        print(f"{matched}/{total} meshes match the triangle count stated in the file")
    return 0


def cmd_glb(archive: BashArchive, args) -> int:
    """Export models to glTF 2.0, carrying textures and animation with them."""
    out = Path(args.output)
    written = clips = 0
    for entry in _selected(archive, args.filter, "model"):
        data = archive.read(entry)
        model = mdl.read_model(data)
        if not model.meshes:
            continue
        pack = _sibling_pack(archive, entry)
        animations = anim.read_animations(data, model)
        destination = out / Path(entry.name).with_suffix(".glb")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(
            gltf.export_glb(model, pack, animations, name=destination.stem)
        )
        written += 1
        clips += len(animations)
    print(f"Wrote {written} glTF files to {out}, carrying {clips} animation clips")
    return 0


def _sibling_pack(archive: BashArchive, entry: Entry):
    """A model's textures live in the .tex file of the same name."""
    wanted = entry.name.rsplit(".", 1)[0] + ".tex"
    for candidate in archive:
        if candidate.name == wanted:
            return tex.read_pack(archive.read(candidate))
    return None


def cmd_build(archive: BashArchive, args) -> int:
    """Rebuild the disc tree: repacked DAT, patched EXE, everything else copied."""
    out = Path(args.output)
    report = build.build(archive, out)
    print(f"Wrote a disc tree to {out}")
    print(f"  {report.entries} entries in {report.groups} groups")
    print(
        f"  CRASHBSH.DAT {report.original_dat_size:,} -> {report.dat_size:,} bytes "
        f"({report.saved:,} saved by packing tight)"
    )
    for warning in report.warnings:
        print(f"  warning: {warning}")

    matched, problems = build.verify(archive, out / archive.exe_path.name)
    print(f"  verified {matched}/{report.entries} entries byte-identical")
    for problem in problems[:5]:
        print(f"  PROBLEM: {problem}")

    config = build.write_iso_config(out, out.parent / f"{out.name}.xml", out.name)
    print(f"  mkpsxiso project written to {config}")
    print(f"  master it with: mkpsxiso {config}")
    return 1 if problems else 0


def cmd_png(archive: BashArchive, args) -> int:
    try:
        from PIL import Image  # noqa: PLC0415
    except ImportError:
        print(
            "PNG export needs Pillow (pip install pillow), or use the GUI's "
            "texture export which goes through Qt.",
            file=sys.stderr,
        )
        return 2

    out = Path(args.output)
    written = 0
    for entry in _selected(archive, args.filter, "texture"):
        pack = tex.read_pack(archive.read(entry))
        if not pack.textures:
            continue
        folder = out / Path(entry.name).with_suffix("")
        folder.mkdir(parents=True, exist_ok=True)
        for texture in pack.textures:
            image = Image.fromarray(texture.to_rgba(pack.palettes), mode="RGBA")
            image.save(folder / f"{texture.name}.png")
            written += 1
    print(f"Wrote {written} PNG files to {out}")
    return 0


def cmd_audio(archive: BashArchive, args) -> int:
    out = Path(args.output)
    written = 0
    for entry in _selected(archive, args.filter, "audio"):
        bank = sfx.read_bank(archive.read(entry))
        files = bank.files()
        if not files:
            continue
        folder = out / Path(entry.name).with_suffix("")
        folder.mkdir(parents=True, exist_ok=True)
        for name, blob in files:
            (folder / name).write_bytes(blob)
            written += 1
    print(f"Wrote {written} audio streams to {out}")
    return 0


def cmd_wav(archive: BashArchive, args) -> int:
    """Decode every SPU-ADPCM sample in the selected banks to WAV."""
    out = Path(args.output)
    written = 0
    for entry in _selected(archive, args.filter, "audio"):
        bank = sfx.read_bank(archive.read(entry))
        if not bank.samples:
            continue
        folder = out / Path(entry.name).with_suffix("")
        folder.mkdir(parents=True, exist_ok=True)
        for name, blob in bank.wav_files():
            (folder / name).write_bytes(blob)
            written += 1
    print(f"Wrote {written} WAV files to {out}")
    return 0


COMMANDS = {
    "info": cmd_info,
    "list": cmd_list,
    "extract": cmd_extract,
    "obj": cmd_obj,
    "glb": cmd_glb,
    "build": cmd_build,
    "png": cmd_png,
    "audio": cmd_audio,
    "wav": cmd_wav,
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="crashbash", description="Crash Bash CRASHBSH.DAT tools"
    )
    parser.add_argument("command", choices=sorted(COMMANDS))
    parser.add_argument("exe", help="game EXE, or a folder containing it")
    parser.add_argument("-o", "--output", default="out", help="output folder")
    parser.add_argument("-f", "--filter", help="only entries whose path contains this")
    parser.add_argument(
        "-g",
        "--group",
        choices=["model", "texture", "audio", "image", "map", "code", "binary"],
        help="only entries of this kind",
    )
    args = parser.parse_args(argv)

    try:
        archive = _open(args.exe)
    except UnknownGameVersion as exc:
        print(exc, file=sys.stderr)
        return 1
    except (OSError, ValueError) as exc:
        print(exc, file=sys.stderr)
        return 1

    return COMMANDS[args.command](archive, args)


if __name__ == "__main__":
    raise SystemExit(main())
