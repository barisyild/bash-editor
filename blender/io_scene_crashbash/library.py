"""Find the shared `crashbash` package and put it on the import path.

The add-on holds no knowledge of the file format. Everything it does goes
through the same library the editor uses -- the readers, the writers and the
import core -- so a rule learned on one side is in force on the other. What
lives here is only the search for it.

Three places, in order:

1. already importable, which is the case when Blender was started from a
   checkout or the path was added by hand;
2. `vendor/` beside this file, which `build_addon.py` fills when it packages a
   release, so an artist installs one zip and nothing else;
3. the checkout named in the add-on preferences.

The library needs numpy and nothing else -- Blender ships numpy. PIL is used by
two functions on the glTF side and by the palette requantiser, which falls back
to the pack's existing colours when it is absent; the add-on never reaches the
glTF side at all.
"""

from __future__ import annotations

import sys
from pathlib import Path

MINIMUM = ("crashbash.formats.modelimport", "crashbash.formats.mdlwrite")


def _importable() -> tuple[bool, str]:
    """Whether the library imports, and what went wrong when it does not.

    A failed attempt is undone. Python caches a package the moment its
    `__init__` runs, so a copy that gets as far as `crashbash` and then trips
    over a missing submodule leaves a broken entry behind, and every later
    attempt fails against the cached one rather than the copy it was given.
    That is how a bundled library that was present and complete reported
    itself missing with nothing to say about why.
    """
    before = set(sys.modules)
    try:
        for module in MINIMUM:
            __import__(module)
    except Exception as exc:  # noqa: BLE001
        for name in set(sys.modules) - before:
            if name == "crashbash" or name.startswith("crashbash."):
                del sys.modules[name]
        return False, f"{type(exc).__name__}: {exc}"
    return True, ""


def _candidates(preference: str = "") -> list[Path]:
    here = Path(__file__).resolve().parent
    found = [here / "vendor"]
    if preference:
        root = Path(preference).expanduser()
        # Point it at the checkout or at the package inside it; both are the
        # sort of thing that gets pasted into a text field.
        found += [root, root.parent]
    # A checkout this file is sitting inside, for development: the add-on lives
    # at <repo>/blender/io_scene_crashbash.
    found.append(here.parent.parent)
    return found


def locate(preference: str = "") -> tuple[Path | None, str]:
    """Make `crashbash` importable; say where it came from, or what stopped it."""
    ok, why = _importable()
    if ok:
        import crashbash  # noqa: PLC0415

        return Path(crashbash.__file__).resolve().parent.parent, ""
    trouble = []
    for root in _candidates(preference):
        if not (root / "crashbash" / "formats" / "modelimport.py").is_file():
            continue
        entry = str(root)
        if entry not in sys.path:
            sys.path.insert(0, entry)
        ok, why = _importable()
        if ok:
            return root, ""
        sys.path.remove(entry)
        trouble.append(f"{root}: {why}")
    return None, "; ".join(trouble)


def ensure(preference: str = "") -> Path | None:
    return locate(preference)[0]


def describe(preference: str = "") -> str:
    """One line for the preferences panel: what was found, or what is missing."""
    root, why = locate(preference)
    if root is None:
        return ("crashbash not found -- point this at a checkout of "
                "crash-bash-editor, or install the packaged add-on"
                + (f" ({why})" if why else ""))
    try:
        import numpy  # noqa: PLC0415

        extra = f", numpy {numpy.__version__}"
    except ImportError:  # pragma: no cover - Blender always ships numpy
        extra = ", numpy MISSING"
    return f"crashbash from {root}{extra}"
