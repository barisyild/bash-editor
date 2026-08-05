"""Crash Bash model support for Blender, on the editor's own library.

The add-on reads and writes `CRASHBSH.DAT`'s models directly -- no interchange
format in the middle. Everything about the format lives in the `crashbash`
package the desktop editor uses; this is the Blender end of it, and the two
enforce the same rules because they run the same code.

Install the packaged zip, or point the add-on preferences at a checkout of
crash-bash-editor.
"""

bl_info = {
    "name": "Crash Bash Model (.mdl)",
    "author": "crash-bash-editor",
    "version": (1, 0, 0),
    "blender": (4, 2, 0),
    "location": "File > Import/Export > Crash Bash Model",
    "description": "Import and export Crash Bash models, textures and clips",
    "category": "Import-Export",
}

from . import operators  # noqa: E402


def register() -> None:
    operators.register()


def unregister() -> None:
    operators.unregister()
