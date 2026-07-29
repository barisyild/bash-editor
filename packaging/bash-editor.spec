# PyInstaller recipe for a standalone Bash Editor, shared by the release
# workflow and by anyone building one locally:
#
#     pip install pyinstaller && pyinstaller --noconfirm packaging/bash-editor.spec
#
# One directory rather than one file, on purpose. Qt is LGPL, and keeping its
# libraries as separate files beside the executable is what lets someone swap
# them for their own build, which is what the licence asks for. It also starts
# faster, since nothing has to be unpacked to a temporary directory first.
#
# The game's own files are never bundled: they are the user's copy, supplied at
# run time.

import sys
from pathlib import Path

ROOT = Path(SPECPATH).parent  # noqa: F821 -- PyInstaller injects SPECPATH

NAME = "Bash Editor"

analysis = Analysis(  # noqa: F821
    [str(ROOT / "app" / "main.py")],
    pathex=[str(ROOT)],
    # The CTR-tools file list is read through `Path(__file__).with_name("data")`
    # in crashbash/archive.py, so it has to keep that position in the bundle.
    # Without it every entry falls back to a numbered name.
    datas=[(str(ROOT / "crashbash" / "data"), "crashbash/data")],
    hiddenimports=[],
    excludes=[
        "tkinter",
        # Nothing here draws with Qt Quick, and the QML stack is a large part
        # of what PySide6-Essentials installs.
        "PySide6.QtQml",
        "PySide6.QtQuick",
        "PySide6.QtQuickWidgets",
        "PySide6.QtQuick3D",
        "PySide6.QtTest",
    ],
    noarchive=False,
)

pyz = PYZ(analysis.pure)  # noqa: F821

executable = EXE(  # noqa: F821
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name=NAME,
    debug=False,
    strip=False,
    upx=False,
    # A windowed build on Windows and macOS: this is a GUI, and a console
    # window behind it is noise. The launchers in the repository are the
    # route for anyone who wants the traceback.
    console=False,
    disable_windowed_traceback=False,
)

collected = COLLECT(  # noqa: F821
    executable,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    name=NAME,
)

if sys.platform == "darwin":
    # Unsigned, so macOS quarantines it on download; the README says how to
    # clear that. Signing needs a paid certificate this project does not have.
    app = BUNDLE(  # noqa: F821
        collected,
        name=f"{NAME}.app",
        icon=None,
        bundle_identifier="dev.barisyild.bash-editor",
        info_plist={
            "NSHighResolutionCapable": True,
            "CFBundleShortVersionString": "0.1.0",
            "LSMinimumSystemVersion": "11.0",
        },
    )
