"""The add-on's user-facing surface: preferences, import, export, a panel.

Nothing here decides anything about the format. The operators locate the shared
library, hand it bytes, and report what it says -- including, and especially,
when it refuses. An import that would draw wrong is stopped here with the
reason, because the alternative is finding out on the console.
"""

from __future__ import annotations

from pathlib import Path

import bpy
from bpy.props import BoolProperty, EnumProperty, StringProperty
from bpy_extras.io_utils import ExportHelper, ImportHelper

from . import library, naming as N

ARCHIVE_SUFFIXES = {".70", ".dat", ".exe", ""}
_ENTRY_CACHE: dict[str, list[tuple[str, str, str]]] = {}


def _preferences(context) -> str:
    try:
        return context.preferences.addons[__package__].preferences.library_path
    except (KeyError, AttributeError):
        return ""


def _require_library(operator, context) -> bool:
    if library.ensure(_preferences(context)) is not None:
        return True
    operator.report({"ERROR"}, (
        "the shared crashbash library was not found -- set its path in the "
        "add-on preferences, or install the packaged add-on which bundles it"))
    return False


def _archive(path: str):
    from crashbash.archive import BashArchive

    return BashArchive(path)


def _entry_items(self, context):
    """Every model in the archive at the chosen path, cheaply and once."""
    path = self.filepath
    if not path or Path(path).suffix.lower() == ".mdl":
        return [("", "", "")]
    if path not in _ENTRY_CACHE:
        try:
            archive = _archive(path)
            _ENTRY_CACHE[path] = [
                (entry.name, entry.name.replace("models/", ""),
                 f"{entry.size:,} bytes")
                for entry in archive if entry.name.endswith(".mdl")
            ] or [("", "no models found", "")]
        except Exception as exc:  # noqa: BLE001
            _ENTRY_CACHE[path] = [("", f"cannot read: {exc}", "")]
    return _ENTRY_CACHE[path]


class CRASHBASH_AddonPreferences(bpy.types.AddonPreferences):
    bl_idname = __package__

    library_path: StringProperty(
        name="crash-bash-editor",
        description=("Path to a checkout of crash-bash-editor. Only needed when "
                     "the add-on was not packaged with the library inside it"),
        subtype="DIR_PATH",
    )

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "library_path")
        layout.label(text=library.describe(self.library_path))


class CRASHBASH_OT_import(bpy.types.Operator, ImportHelper):
    """Bring a Crash Bash model into the scene, geometry, textures and clips"""

    bl_idname = "crashbash.import_model"
    bl_label = "Import Crash Bash Model"
    bl_options = {"REGISTER", "UNDO"}

    filename_ext = ""
    filter_glob: StringProperty(default="*.70;*.dat;*.mdl;*", options={"HIDDEN"})
    entry: EnumProperty(name="Model", items=_entry_items,
                        description="Which model in the archive to open")
    # The enum's items come from whichever file is selected, so it holds no
    # valid value until one is -- which makes it unusable from a script, where
    # the path and the choice arrive in the same call and Blender validates the
    # assignment against an empty list. A plain name has no such order to it.
    entry_name: StringProperty(
        name="Model name", options={"HIDDEN"},
        description="Entry to open, by name; overrides the list when set")

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        if Path(self.filepath).suffix.lower() == ".mdl":
            layout.label(text="Loose model; its sibling .tex is used if present",
                         icon="INFO")
        else:
            layout.prop(self, "entry")

    def execute(self, context):
        if not _require_library(self, context):
            return {"CANCELLED"}
        from . import build_scene

        path = Path(self.filepath)
        try:
            if path.suffix.lower() == ".mdl":
                model_data = path.read_bytes()
                sibling = path.with_suffix(".tex")
                pack_data = sibling.read_bytes() if sibling.is_file() else None
                entry, source, pack_entry = path.name, str(path), str(sibling)
            else:
                wanted = self.entry_name or self.entry
                if not wanted:
                    self.report({"ERROR"}, "choose a model from the archive")
                    return {"CANCELLED"}
                archive = _archive(self.filepath)
                by_name = {e.name: e for e in archive}
                if wanted not in by_name:
                    self.report({"ERROR"}, f"'{wanted}' is not in this archive")
                    return {"CANCELLED"}
                model_data = archive.read(by_name[wanted])
                pack_entry = wanted[:-4] + ".tex"
                pack_data = (archive.read(by_name[pack_entry])
                             if pack_entry in by_name else None)
                entry, source = wanted, self.filepath
            collection, notes = build_scene.build_model(
                entry, model_data, pack_data, source, pack_entry)
        except Exception as exc:  # noqa: BLE001
            self.report({"ERROR"}, f"{type(exc).__name__}: {exc}")
            return {"CANCELLED"}

        for note in notes[:6]:
            self.report({"WARNING"}, note)
        self.report({"INFO"}, f"{entry}: {len(collection.all_objects)} objects"
                              + (f", {len(notes)} notes" if notes else ""))
        return {"FINISHED"}


class CRASHBASH_OT_export(bpy.types.Operator, ExportHelper):
    """Write the active collection back out as a model entry the game loads"""

    bl_idname = "crashbash.export_model"
    bl_label = "Export Crash Bash Model"
    bl_options = {"REGISTER"}

    filename_ext = ".mdl"
    filter_glob: StringProperty(default="*.mdl", options={"HIDDEN"})
    animation_only: BoolProperty(
        name="Animation only",
        description=("Rebuild the clips and leave every mesh byte-identical. "
                     "Use it whenever the edit is to the timeline rather than "
                     "the shape -- installing geometry that did not change "
                     "costs a copy of the shared tables each time"),
        default=False,
    )

    @classmethod
    def poll(cls, context):
        return _target(context) is not None

    def invoke(self, context, event):
        collection = _target(context)
        if collection is not None:
            entry = collection.get(N.PROP_ENTRY, "model.mdl")
            self.filepath = entry.rsplit("/", 1)[-1]
        return super().invoke(context, event)

    def execute(self, context):
        if not _require_library(self, context):
            return {"CANCELLED"}
        from crashbash.formats import modelimport as MI
        from crashbash.formats.anim import read_animations
        from crashbash.formats.mdl import read_model
        from crashbash.formats.tex import read_pack

        from . import read_scene

        collection = _target(context)
        if collection is None:
            self.report({"ERROR"}, "no imported collection is active")
            return {"CANCELLED"}
        try:
            model_data, pack_data = _source_bytes(collection)
            model = read_model(model_data)
            clips = read_animations(model_data, model)
            pack = read_pack(pack_data) if pack_data else None
            request = read_scene.build_request(collection, model, clips, pack)
            report = MI.import_payload(model_data, pack_data, request,
                                       animation_only=self.animation_only)
        except Exception as exc:  # noqa: BLE001
            self.report({"ERROR"}, f"nothing was written. {exc}")
            return {"CANCELLED"}

        target = Path(self.filepath)
        target.write_bytes(report.model)
        written = [target.name]
        if report.pack:
            beside = target.with_suffix(".tex")
            beside.write_bytes(report.pack)
            written.append(beside.name)
        for note in report.warnings[:6]:
            self.report({"WARNING"}, note)
        self.report({"INFO"}, (
            f"{', '.join(written)}: {len(report.meshes_rebuilt)} meshes rebuilt, "
            f"{len(report.meshes_unchanged)} untouched, "
            f"{len(report.clips_rebuilt)} clips rebuilt, "
            f"{len(report.textures_written)} textures written"))
        return {"FINISHED"}


def _target(context) -> bpy.types.Collection | None:
    """The imported collection the user means: the active object's, or the only one."""
    obj = context.active_object
    if obj is not None:
        for collection in obj.users_collection:
            if collection.get(N.PROP_ENTRY):
                return collection
    found = [c for c in bpy.data.collections if c.get(N.PROP_ENTRY)]
    return found[0] if len(found) == 1 else None


def _source_bytes(collection) -> tuple[bytes, bytes | None]:
    """The shipped entry this collection came from, model and pack."""
    source = collection.get(N.PROP_SOURCE, "")
    entry = collection.get(N.PROP_ENTRY, "")
    pack_entry = collection.get(N.PROP_PACK, "")
    path = Path(source)
    if path.suffix.lower() == ".mdl":
        pack = Path(pack_entry) if pack_entry else path.with_suffix(".tex")
        return path.read_bytes(), pack.read_bytes() if pack.is_file() else None
    archive = _archive(source)
    by_name = {e.name: e for e in archive}
    if entry not in by_name:
        raise ValueError(f"'{entry}' is not in {source}")
    pack_data = (archive.read(by_name[pack_entry])
                 if pack_entry in by_name else None)
    return archive.read(by_name[entry]), pack_data


class VIEW3D_PT_crashbash(bpy.types.Panel):
    bl_label = "Crash Bash"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Crash Bash"

    def draw(self, context):
        layout = self.layout
        collection = _target(context)
        if collection is None:
            layout.label(text="Nothing imported in this scene", icon="INFO")
            layout.operator(CRASHBASH_OT_import.bl_idname, icon="IMPORT")
            return
        box = layout.box()
        box.label(text=collection.get(N.PROP_ENTRY, "?"), icon="MESH_DATA")
        box.label(text=f"{len(collection.all_objects)} objects")

        obj = context.active_object
        if obj is not None and obj.get(N.PROP_MESH) is not None:
            box = layout.box()
            box.label(text=f"mesh {obj[N.PROP_MESH]}", icon="OUTLINER_OB_MESH")
            box.label(text=f"{len(obj.data.polygons)} faces, "
                           f"{len(obj.data.materials)} materials")
            clips = obj.get(N.PROP_CLIPS) or {}
            if clips:
                box.label(text=f"{len(clips)} clips")
                for label in list(clips)[:8]:
                    box.label(text=f"  {label}")
            if obj.get(N.PROP_VOLUMES):
                box.label(text=f"{len(obj[N.PROP_VOLUMES])} collision volumes "
                               f"(carried through, not edited)")
        elif obj is not None and obj.get(N.PROP_OBJECT) is not None:
            layout.box().label(
                text=f"object {obj[N.PROP_OBJECT]:04X}: pool mesh, read only",
                icon="LOCKED")
        layout.operator(CRASHBASH_OT_export.bl_idname, icon="EXPORT")


def _menu_import(self, context):
    self.layout.operator(CRASHBASH_OT_import.bl_idname, text="Crash Bash Model")


def _menu_export(self, context):
    self.layout.operator(CRASHBASH_OT_export.bl_idname, text="Crash Bash Model")


CLASSES = (
    CRASHBASH_AddonPreferences,
    CRASHBASH_OT_import,
    CRASHBASH_OT_export,
    VIEW3D_PT_crashbash,
)


def register() -> None:
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.TOPBAR_MT_file_import.append(_menu_import)
    bpy.types.TOPBAR_MT_file_export.append(_menu_export)


def unregister() -> None:
    bpy.types.TOPBAR_MT_file_export.remove(_menu_export)
    bpy.types.TOPBAR_MT_file_import.remove(_menu_import)
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
