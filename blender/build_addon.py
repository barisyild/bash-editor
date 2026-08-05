"""Package the add-on with the shared library inside it.

The add-on and the desktop editor are the same code: `crashbash/` is the one
place the format lives, and both drive it. For development that is enough --
`library.py` finds the checkout the add-on is sitting in. For an artist who
only wants a zip, the library has to travel with it, so this copies it into
`vendor/` and zips the result.

    .venv/bin/python blender/build_addon.py [--out DIR]

What comes out installs through Blender's Extensions panel (Install from Disk)
and needs nothing else: numpy is the only dependency and Blender ships it.
"""

from __future__ import annotations

import argparse
import shutil
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ADDON = ROOT / "blender" / "io_scene_crashbash"
PACKAGE = "io_scene_crashbash"

# The modules the add-on reaches. Everything glTF and everything GUI stays out:
# the add-on never writes a `.glb`, and PIL -- which the glTF image code wants
# and Blender does not ship -- is never imported along the way.
LIBRARY = [
    "crashbash/__init__.py",
    "crashbash/archive.py",
    "crashbash/binreader.py",
    "crashbash/data",
    # `scenewrite` reaches back into `scene` for the placement-record layout,
    # so the reader travels with it. Leaving it out imported far enough to
    # cache a broken `crashbash` package and then failed with nothing to say.
    "crashbash/scene.py",
    "crashbash/formats/__init__.py",
    "crashbash/formats/anim.py",
    "crashbash/formats/animwrite.py",
    "crashbash/formats/mdl.py",
    "crashbash/formats/mdlwrite.py",
    "crashbash/formats/modelimport.py",
    "crashbash/formats/scenewrite.py",
    "crashbash/formats/tex.py",
    "crashbash/formats/texwrite.py",
]


def vendor(target: Path) -> int:
    """Copy the library into the add-on, and say how many files that was."""
    if target.exists():
        shutil.rmtree(target)
    copied = 0
    for name in LIBRARY:
        source = ROOT / name
        if not source.exists():
            raise SystemExit(f"missing from the checkout: {name}")
        destination = target / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            shutil.copytree(source, destination,
                            ignore=shutil.ignore_patterns("__pycache__"))
            copied += sum(1 for _ in destination.rglob("*") if _.is_file())
        else:
            shutil.copy2(source, destination)
            copied += 1
    return copied


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(ROOT / "out"))
    parser.add_argument("--keep-vendor", action="store_true",
                        help="leave vendor/ in place for testing the zip layout")
    args = parser.parse_args()

    target = ADDON / "vendor"
    copied = vendor(target)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    archive = out / f"{PACKAGE}.zip"

    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(ADDON.rglob("*")):
            if path.is_dir() or "__pycache__" in path.parts:
                continue
            zf.write(path, Path(PACKAGE) / path.relative_to(ADDON))

    if not args.keep_vendor:
        shutil.rmtree(target)
    size = archive.stat().st_size
    print(f"{archive}: {size:,} bytes, library vendored from {copied} files")
    print("install it with Blender's Edit > Preferences > Get Extensions > "
          "Install from Disk")


if __name__ == "__main__":
    main()
