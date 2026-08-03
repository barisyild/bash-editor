"""Main window: the archive on the left, an editor per file kind on the right."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import Qt, QSettings
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QFileDialog,
    QMainWindow,
    QMessageBox,
    QProgressDialog,
    QSplitter,
    QStackedWidget,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from crashbash.archive import (
    BashArchive,
    Entry,
    UnknownGameVersion,
    find_dat,
    find_exe,
    find_exe_near,
)
from crashbash import build, iso, scene
from crashbash.formats import anim, gltf, gltfimport, mdl, sfx, tex

from .glview import ModelView
from .panels import (
    AnimationPanel,
    AudioView,
    FileTree,
    HexView,
    MeshPanel,
    TextureView,
    ViewOptions,
    guarded,
)

APP_NAME = "Bash Editor"

# The folder a packaged build reads its game from, beside the application.
GAME_DIR_NAME = "game"


def program_dir() -> Path:
    """The folder the editor itself lives in, as a user would see it.

    For a frozen build that is the folder holding the executable — and on
    macOS the folder holding the `.app`, not the `Contents/MacOS` inside it,
    since that is where a user would drop a game next to the application.
    Running from a checkout it is the repository root.
    """
    if getattr(sys, "frozen", False):
        executable = Path(sys.executable).resolve()
        for parent in executable.parents:
            if parent.suffix == ".app":
                return parent.parent
        return executable.parent
    return Path(__file__).resolve().parent.parent


class MainWindow(QMainWindow):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(APP_NAME)
        self.resize(1360, 860)
        # Deliberately unchanged across the rename: these keys hold the last
        # opened EXE, and rewriting them would make everyone re-pick it once.
        self.settings = QSettings("crash-bash-editor", "viewer")

        self.archive: BashArchive | None = None
        self.entry: Entry | None = None
        # Staged file replacements, by entry index. A build rewrites both index
        # tables in the EXE, so it has to see every change at once -- which is
        # why these are held here rather than written as they are made.
        self.replacements: dict[int, bytes] = {}
        self.model: mdl.Model | None = None
        self.pack: tex.TexturePack | None = None
        self.bank: sfx.SoundBank | None = None
        self.animations: list[anim.Animation] = []
        self.scene: scene.Scene | None = None

        self.tree = FileTree()
        self.tree.entry_selected.connect(self.open_entry)

        self.view3d = ModelView()
        self.view_options = ViewOptions()
        self.view_options.changed.connect(self._apply_view_options)
        self.view_options.reset.clicked.connect(self.view3d.reset_view)

        model_page = QWidget()
        model_layout = QVBoxLayout(model_page)
        model_layout.setContentsMargins(0, 0, 0, 0)
        model_layout.setSpacing(2)
        model_layout.addWidget(self.view3d, 1)
        model_layout.addWidget(self.view_options)

        self.texture_view = TextureView()
        self.audio_view = AudioView()
        self.audio_view.export_requested.connect(self.export_current_audio)
        self.audio_view.export_wav_requested.connect(self.export_current_wavs)
        self.hex_view = HexView()

        self.pages = QStackedWidget()
        for page in (model_page, self.texture_view, self.audio_view, self.hex_view):
            self.pages.addWidget(page)

        self.mesh_panel = MeshPanel()
        self.mesh_panel.visibility_changed.connect(self.view3d.set_mesh_visible)
        self.mesh_panel.all_visibility_changed.connect(self._set_all_meshes)

        self.anim_panel = AnimationPanel()
        self.anim_panel.animation_changed.connect(self._set_animation)
        self.anim_panel.scene_changed.connect(self._set_scene)
        self.anim_panel.frame_changed.connect(self._set_frame)
        self.anim_panel.shot_camera.toggled.connect(self.view3d.set_use_shot_camera)

        model_side = QSplitter(Qt.Vertical)
        model_side.addWidget(self.mesh_panel)
        model_side.addWidget(self.anim_panel)
        model_side.setStretchFactor(0, 1)
        model_side.setStretchFactor(1, 1)
        model_side.setSizes([430, 430])

        self.side = QTabWidget()
        self.side.addTab(model_side, "Model")

        right = QSplitter(Qt.Horizontal)
        right.addWidget(self.pages)
        right.addWidget(self.side)
        right.setStretchFactor(0, 4)
        right.setStretchFactor(1, 1)

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(self.tree)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 4)
        splitter.setSizes([330, 1030])
        self.setCentralWidget(splitter)

        self._build_menu()
        self.statusBar().showMessage(self._waiting_message())
        self.setAcceptDrops(True)

        startup = self._startup_exe()
        if startup is not None:
            self.load_archive(startup, quiet=True)
        elif getattr(sys, "frozen", False):
            # The folder is the packaged build's whole configuration, so name
            # it rather than leaving an empty window with no explanation. It
            # exists by now, empty, which is half the instruction already.
            QMessageBox.information(
                self,
                APP_NAME,
                "No game found.\n\n"
                "Put your Crash Bash files in the 'game' folder next to the "
                "application, then start it again:\n\n"
                f"{self._game_dir()}\n\n"
                "It needs the game EXE and CRASHBSH.DAT — an extracted disc "
                "as it comes.",
            )

    # -- menu -----------------------------------------------------------

    def _build_menu(self) -> None:
        file_menu = self.menuBar().addMenu("&File")

        open_action = QAction("&Open game EXE…", self)
        open_action.setShortcut(QKeySequence.Open)
        open_action.triggered.connect(self.choose_archive)
        file_menu.addAction(open_action)

        open_folder = QAction("Open game &folder…", self)
        open_folder.triggered.connect(self.choose_folder)
        file_menu.addAction(open_folder)

        file_menu.addSeparator()

        self.export_obj_action = QAction("Export model as &OBJ…", self)
        self.export_obj_action.setShortcut("Ctrl+E")
        self.export_obj_action.triggered.connect(self.export_obj)
        self.export_obj_action.setEnabled(False)
        file_menu.addAction(self.export_obj_action)

        self.export_glb_action = QAction("Export model as &glTF…", self)
        self.export_glb_action.setShortcut("Ctrl+G")
        self.export_glb_action.setToolTip(
            "Geometry, textures and animation in one .glb, ready for Blender"
        )
        self.export_glb_action.triggered.connect(self.export_glb)
        self.export_glb_action.setEnabled(False)
        file_menu.addAction(self.export_glb_action)

        self.import_glb_action = QAction("&Import model from glTF…", self)
        self.import_glb_action.setShortcut("Ctrl+Shift+G")
        self.import_glb_action.setToolTip(
            "The return trip: rebuild this entry's meshes, clips and repainted "
            "textures from a .glb that was exported here and edited elsewhere. "
            "In Blender, set the scene to 30 fps before importing the export, "
            "or the clips come back resampled onto the wrong tick grid."
        )
        self.import_glb_action.triggered.connect(self.import_glb)
        self.import_glb_action.setEnabled(False)
        file_menu.addAction(self.import_glb_action)

        self.export_png_action = QAction("Export textures as &PNG…", self)
        self.export_png_action.triggered.connect(self.export_textures)
        self.export_png_action.setEnabled(False)
        file_menu.addAction(self.export_png_action)

        self.export_raw_action = QAction("Export selected file…", self)
        self.export_raw_action.triggered.connect(self.export_raw)
        self.export_raw_action.setEnabled(False)
        file_menu.addAction(self.export_raw_action)

        extract_all = QAction("Extract &whole archive…", self)
        extract_all.triggered.connect(self.extract_all)
        file_menu.addAction(extract_all)

        file_menu.addSeparator()

        self.replace_action = QAction("&Replace selected file…", self)
        self.replace_action.setShortcut("Ctrl+R")
        self.replace_action.setToolTip(
            "Stage a file to take this entry's place in the next build"
        )
        self.replace_action.triggered.connect(self.replace_entry)
        self.replace_action.setEnabled(False)
        file_menu.addAction(self.replace_action)

        self.revert_action = QAction("Re&vert selected file", self)
        self.revert_action.triggered.connect(self.revert_entry)
        self.revert_action.setEnabled(False)
        file_menu.addAction(self.revert_action)

        self.build_action = QAction("&Build disc…", self)
        self.build_action.setShortcut("Ctrl+B")
        self.build_action.setToolTip(
            "Repack CRASHBSH.DAT with the staged replacements and patch the EXE"
        )
        self.build_action.triggered.connect(self.build_disc)
        file_menu.addAction(self.build_action)

        view_menu = self.menuBar().addMenu("&View")
        for label, attr in (
            ("Solid", "solid"),
            ("Wireframe", "wireframe"),
            ("Points", "points"),
            ("Volumes", "volumes"),
        ):
            checkbox = getattr(self.view_options, attr)
            action = QAction(label, self, checkable=True, checked=checkbox.isChecked())
            action.toggled.connect(checkbox.setChecked)
            checkbox.toggled.connect(action.setChecked)
            view_menu.addAction(action)
        view_menu.addSeparator()
        reset = QAction("Reset view", self)
        reset.setShortcut("F")
        reset.triggered.connect(self.view3d.reset_view)
        view_menu.addAction(reset)

    # -- archive --------------------------------------------------------

    def choose_archive(self) -> None:
        start = self.settings.value("last_dir", str(Path.home()))
        path, _ = QFileDialog.getOpenFileName(self, "Select the Crash Bash EXE", start)
        if path:
            self.load_archive(Path(path))

    def _waiting_message(self) -> str:
        """The status bar before anything is loaded."""
        if getattr(sys, "frozen", False):
            return f"Waiting for a game in {program_dir() / GAME_DIR_NAME}"
        # Native text so the shortcut reads as the platform writes it.
        shortcut = QKeySequence(QKeySequence.Open).toString(QKeySequence.NativeText)
        return f"Open a Crash Bash EXE to begin ({shortcut})"

    def _game_dir(self) -> Path:
        """The `game` folder beside the program, created when it is missing.

        Creating it is the point: an empty folder named `game` next to the
        application says where the disc goes better than any message can, and
        it is there before the message that names it. A location that cannot
        be written to is not worth interrupting anyone over — the message
        still gives the path, and the user can make the folder themselves.
        """
        folder = program_dir() / GAME_DIR_NAME
        try:
            folder.mkdir(exist_ok=True)
        except OSError:
            pass
        return folder

    def _startup_exe(self) -> Path | None:
        """What to open on launch.

        A packaged build reads one place and one place only: the `game` folder
        beside the application. Nothing is remembered between runs and nothing
        else on the machine is looked at, so what a packaged copy edits is
        whatever was put in its own folder — visible from the outside, and the
        same on every launch.

        From a checkout the last-opened game wins instead, when it is still
        there, falling back to the same `game` folder. Opening a second disc
        during development should not be undone by the next launch.
        """
        if getattr(sys, "frozen", False):
            return find_exe_near(self._game_dir())

        last = self.settings.value("last_exe", "")
        if last and Path(last).is_file():
            return Path(last)
        return find_exe_near(self._game_dir())

    def choose_folder(self) -> None:
        start = self.settings.value("last_dir", str(Path.home()))
        folder = QFileDialog.getExistingDirectory(self, "Select the game folder", start)
        if not folder:
            return
        exe = find_exe(Path(folder))
        if exe is None:
            QMessageBox.warning(
                self,
                APP_NAME,
                "No recognised Crash Bash EXE in that folder.\n"
                "Pick the EXE directly if it is named unusually.",
            )
            return
        self.load_archive(exe)

    def load_archive(self, exe_path: Path, quiet: bool = False) -> None:
        try:
            archive = BashArchive(exe_path)
        except UnknownGameVersion as exc:
            if not quiet:
                QMessageBox.warning(
                    self,
                    APP_NAME,
                    f"{exc}\n\nThe file table lives inside the EXE, so an "
                    "unrecognised build cannot be unpacked.",
                )
            return
        except (OSError, ValueError, FileNotFoundError) as exc:
            if not quiet:
                QMessageBox.critical(self, APP_NAME, str(exc))
            return

        self.archive = archive
        # Indices belong to the archive that was open, so staged edits cannot
        # follow it to another one.
        self.replacements.clear()
        self.entry = None
        self._sync_edit_actions()
        self.settings.setValue("last_exe", str(exe_path))
        self.settings.setValue("last_dir", str(exe_path.parent))
        self.tree.set_archive(archive)
        self.setWindowTitle(f"{APP_NAME} — {archive.version.name}")
        self.statusBar().showMessage(
            f"{len(archive)} files from {archive.dat_path.name} "
            f"({archive.version.name}, md5 {archive.md5[:8]}…)"
        )

    # -- entries --------------------------------------------------------

    def open_entry(self, entry: Entry) -> None:
        if self.archive is None:
            return
        self.entry = entry
        self.model = self.pack = self.bank = None
        # Stops playback and drops the previous model's clips, whatever the new
        # entry turns out to be.
        self.animations = []
        self.scene = None
        self.anim_panel.set_animations([])
        self.export_obj_action.setEnabled(False)
        self.export_glb_action.setEnabled(False)
        self.import_glb_action.setEnabled(False)
        self.export_png_action.setEnabled(False)
        self.export_raw_action.setEnabled(True)
        self._sync_edit_actions()

        # A staged replacement is what the next build will write, so it is also
        # what the panels should show -- otherwise a swap cannot be checked
        # until after the disc is built.
        staged = entry.index in self.replacements
        data = self.replacements[entry.index] if staged else self.archive.read(entry)
        header = (
            f"{entry.name}  —  #{entry.index}, {len(data)} bytes, "
            f"{'STAGED REPLACEMENT, ' if staged else ''}"
            f"kind {entry.kind}, magic 0x{entry.magic:08X}"
        )

        if entry.group == "model":
            self.model = mdl.read_model(data)
            self.animations = anim.read_animations(data, self.model)
            self.scene = scene.read_scene(data, self.model, self.animations)
            self.view3d.set_model(self.model, self._sibling_texture_pack(entry))
            self.mesh_panel.set_model(self.model, header)
            self.anim_panel.set_animations(self.animations, self.scene)
            self.pages.setCurrentIndex(0)
            self.export_obj_action.setEnabled(bool(self.model.meshes))
            self.export_glb_action.setEnabled(bool(self.model.meshes))
            self.import_glb_action.setEnabled(bool(self.model.meshes))
        elif entry.group == "texture":
            self.mesh_panel.set_model(None, header)
            self.pack = tex.read_pack(data)
            self.texture_view.set_pack(self.pack)
            self.pages.setCurrentIndex(1)
            self.export_png_action.setEnabled(bool(self.pack.textures))
        elif entry.group == "audio":
            self.mesh_panel.set_model(None, header)
            self.bank = sfx.read_bank(data)
            self.audio_view.set_bank(self.bank, header)
            self.pages.setCurrentIndex(2)
        else:
            self.mesh_panel.set_model(None, header)
            self.hex_view.set_data(data, header)
            self.pages.setCurrentIndex(3)

        self.statusBar().showMessage(header)

    def _sibling_texture_entry(self, entry: Entry) -> Entry | None:
        if self.archive is None:
            return None
        wanted = entry.name.rsplit(".", 1)[0] + ".tex"
        return next((c for c in self.archive if c.name == wanted), None)

    def _effective_bytes(self, entry: Entry) -> bytes:
        """What the entry holds right now: the staged replacement, else the disc."""
        staged = self.replacements.get(entry.index)
        return staged if staged is not None else self.archive.read(entry)

    def _sibling_texture_pack(self, entry: Entry) -> tex.TexturePack | None:
        """A model's textures live in the .tex file of the same name."""
        sibling = self._sibling_texture_entry(entry)
        if sibling is None:
            return None
        return tex.read_pack(self._effective_bytes(sibling))

    @guarded
    def _set_animation(self, animation: anim.Animation | None) -> None:
        self.view3d.set_animation(animation)
        if animation is not None:
            self.statusBar().showMessage(
                f"{animation.label} — {animation.frame_count} frames, mesh "
                f"{animation.mesh_index}, {animation.vertex_count} vertices"
            )

    @guarded
    def _set_scene(self, playing) -> None:
        clips = self.animations if playing is not None else []
        self.view3d.set_scene(playing, clips)
        if playing is not None:
            self.statusBar().showMessage(
                f"Cutscene: ticks {playing.start}..{playing.end}, "
                f"{len(playing.actors)} actors and {len(playing.props)} prop "
                f"tracks over {len(playing.mesh_indices)} meshes — each drawn "
                "only inside its own window"
            )

    @guarded
    def _set_frame(self, frame: int) -> None:
        if self.anim_panel.scene_selected() and self.scene is not None:
            self.view3d.set_scene_tick(self.scene.start + frame)
            return
        self.view3d.set_frame(frame)

    def _set_all_meshes(self, visible: bool) -> None:
        self.view3d.set_all_meshes_visible(visible)
        self.mesh_panel.set_all_checked(visible)

    def _apply_view_options(self) -> None:
        options = self.view_options
        self.view3d.show_solid = options.solid.isChecked()
        self.view3d.show_wireframe = options.wireframe.isChecked()
        self.view3d.show_points = options.points.isChecked()
        self.view3d.show_volumes = options.volumes.isChecked()
        self.view3d.set_vertex_colours(options.vertex_colours.isChecked())
        self.view3d.set_textured(options.textures.isChecked())
        self.view3d.set_texture_animation(options.texture_animation.isChecked())
        self.view3d.update()

    # -- export ---------------------------------------------------------

    def export_obj(self) -> None:
        if self.model is None or self.entry is None:
            return
        default = Path(self.settings.value("last_export", str(Path.home())))
        stem = Path(self.entry.name).stem
        path, _ = QFileDialog.getSaveFileName(
            self, "Export OBJ", str(default / f"{stem}.obj"), "Wavefront OBJ (*.obj)"
        )
        if not path:
            return
        Path(path).write_text(self.model.to_obj(), encoding="utf-8")
        self.settings.setValue("last_export", str(Path(path).parent))
        self.statusBar().showMessage(f"Wrote {path}")

    # -- editing ---------------------------------------------------------

    def replace_entry(self) -> None:
        """Stage a file from disk to take the selected entry's place.

        Replacements are held in memory rather than written straight away: a
        build rewrites both index tables in the EXE, so it has to see every
        change at once.
        """
        if self.entry is None:
            return
        path, _ = QFileDialog.getOpenFileName(
            self,
            f"Replace {Path(self.entry.name).name}",
            self.settings.value("last_export", str(Path.home())),
        )
        if not path:
            return
        payload = Path(path).read_bytes()
        self.replacements[self.entry.index] = payload
        self.tree.set_replaced(self.entry.index, True)
        self._sync_edit_actions()
        self.statusBar().showMessage(
            f"Staged {Path(path).name} ({len(payload):,} bytes) for {self.entry.name} — "
            f"{len(self.replacements)} pending"
        )
        self.open_entry(self.entry)

    def import_glb(self) -> None:
        """Rebuild the selected model from a .glb and stage the result."""
        if self.entry is None or self.model is None:
            return
        path, _ = QFileDialog.getOpenFileName(
            self,
            f"Import {Path(self.entry.name).name} from glTF",
            self.settings.value("last_export", str(Path.home())),
            "glTF binary (*.glb)",
        )
        if not path:
            return
        self.settings.setValue("last_export", str(Path(path).parent))
        self._import_glb_path(Path(path))

    def _import_glb_path(self, path: Path) -> None:
        entry = self.entry
        sibling = self._sibling_texture_entry(entry)
        try:
            report = gltfimport.import_glb(
                path,
                self._effective_bytes(entry),
                self._effective_bytes(sibling) if sibling else None,
            )
        except (ValueError, KeyError, OSError) as exc:
            QMessageBox.warning(
                self,
                APP_NAME,
                f"Nothing was staged.\n\n{exc}\n\n"
                "The file must come from this editor's own glTF export — mesh "
                "names carry the index the importer matches on. Every mesh "
                "present is rebuilt and its clips with it, so a file holding "
                "the whole model replaces the whole model; deleting meshes "
                "before exporting only narrows what is touched.",
            )
            return

        self.replacements[entry.index] = report.model
        self.tree.set_replaced(entry.index, True)
        lines = [
            f"Meshes rebuilt: {', '.join(map(str, report.meshes_rebuilt))}",
            f"Clips rebuilt from the file: {len(report.clips_rebuilt)}"
            + (f" ({', '.join(report.clips_rebuilt)})" if report.clips_rebuilt else ""),
        ]
        if report.clips_static:
            lines.append(
                f"Clips with no animation in the file, frozen at rest: "
                f"{', '.join(report.clips_static)}"
            )
        if report.clips_copied:
            lines.append(f"Clips of untouched meshes, kept exactly: "
                         f"{len(report.clips_copied)}")
        if report.pack is not None and sibling is not None:
            self.replacements[sibling.index] = report.pack
            self.tree.set_replaced(sibling.index, True)
            lines.append(
                f"Repainted textures written into their slots: "
                f"{', '.join(map(str, report.textures_written))}"
            )
            if report.palettes_shared:
                lines.append(
                    "Slots matched to an existing shared palette rather than "
                    f"repainting it: {', '.join(map(str, report.palettes_shared))}"
                )
        elif report.textures_unchanged:
            lines.append("Textures unchanged; the pack is not touched.")
        lines.append("")
        lines.append("Staged, not written: preview it here, then File → Build disc…")

        self._sync_edit_actions()
        self.open_entry(entry)
        QMessageBox.information(self, APP_NAME, "\n".join(lines))

    def revert_entry(self) -> None:
        if self.entry is None or self.entry.index not in self.replacements:
            return
        del self.replacements[self.entry.index]
        self.tree.set_replaced(self.entry.index, False)
        self._sync_edit_actions()
        self.statusBar().showMessage(
            f"Reverted {self.entry.name} — {len(self.replacements)} pending"
        )
        self.open_entry(self.entry)

    def _sync_edit_actions(self) -> None:
        staged = self.entry is not None and self.entry.index in self.replacements
        self.replace_action.setEnabled(self.entry is not None)
        self.revert_action.setEnabled(staged)
        pending = len(self.replacements)
        self.build_action.setText(
            f"&Build disc… ({pending} staged)" if pending else "&Build disc…"
        )

    def build_disc(self) -> None:
        """Repack the archive with whatever is staged, read it back, write a disc."""
        if self.archive is None:
            QMessageBox.information(self, APP_NAME, "Open a game EXE first.")
            return
        default = Path(self.settings.value("last_build", str(Path.home())))
        chosen, _ = QFileDialog.getSaveFileName(
            self, "Build a disc image as…", str(default / "crashbash.bin"),
            "PS1 disc image (*.bin)"
        )
        if not chosen:
            return
        image = Path(chosen)
        self.settings.setValue("last_build", str(image.parent))

        # Patching the original keeps what an extracted folder cannot hold: the
        # licence area, and the Spyro demo's interleaved XA speech.
        original = None
        answer = QMessageBox.question(
            self,
            APP_NAME,
            "Patch your original disc image?\n\n"
            "The disc holds things an extracted folder does not — the licence "
            "area that lets it boot on a console, and the demo's interleaved XA "
            "speech. Patching your own .bin keeps them; mastering the folder "
            "loses them.",
            QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel,
            QMessageBox.Yes,
        )
        if answer == QMessageBox.Cancel:
            return
        if answer == QMessageBox.Yes:
            picked, _ = QFileDialog.getOpenFileName(
                self, "Original Crash Bash disc image", str(image.parent),
                "PS1 disc image (*.bin *.img *.iso)"
            )
            if not picked:
                return
            original = Path(picked)

        tree = image.with_suffix(".tree")
        progress = QProgressDialog("Repacking…", "Cancel", 0, len(self.archive), self)
        progress.setWindowModality(Qt.WindowModal)

        def tick(done: int, total: int, entry: Entry) -> None:
            progress.setValue(done)
            if done % 32 == 0:
                progress.setLabelText(entry.name)
            if progress.wasCanceled():
                raise KeyboardInterrupt

        try:
            report = build.build(
                self.archive, tree, replacements=self.replacements, progress=tick
            )
        except KeyboardInterrupt:
            self.statusBar().showMessage("Build cancelled")
            return
        finally:
            progress.close()

        matched, problems = build.verify(
            self.archive,
            tree / self.archive.exe_path.name,
            replacements=self.replacements,
        )

        lines = [
            f"{report.entries} entries in {report.groups} groups",
            f"{len(report.replaced)} replaced",
            f"CRASHBSH.DAT {report.original_dat_size:,} → {report.dat_size:,} bytes",
            "",
            f"Verified {matched} of {report.entries} entries byte-identical to what "
            "went in.",
        ]
        if problems:
            lines += ["", "Problems:"] + [f"  {p}" for p in problems[:6]]
        else:
            lines += [""] + self._write_image(image, tree, original)

        box = QMessageBox.warning if problems else QMessageBox.information
        box(self, APP_NAME, "\n".join(lines))
        self.statusBar().showMessage(
            f"Built {image.name} — {matched}/{report.entries} verified"
        )

    def _write_image(self, image: Path, tree: Path, original: Path | None) -> list[str]:
        """Write the .bin, either by patching an original or mastering the tree."""
        progress = QProgressDialog("Writing sectors…", "Cancel", 0, 100, self)
        progress.setWindowModality(Qt.WindowModal)

        def tick(done: int, total: int) -> None:
            progress.setValue(int(done * 100 / max(total, 1)))
            if progress.wasCanceled():
                raise KeyboardInterrupt

        try:
            if original is not None:
                dat = self.archive.dat_path
                result = iso.patch_image(
                    original,
                    image,
                    {
                        f"{dat.parent.name}/{dat.name}":
                            (tree / dat.parent.name / dat.name).read_bytes(),
                        self.archive.exe_path.name:
                            (tree / self.archive.exe_path.name).read_bytes(),
                    },
                    progress=tick,
                )
                lines = [
                    f"Patched {original.name} into {image.name}, "
                    f"{result['sectors']:,} sectors rewritten and nothing else touched."
                ]
            else:
                result = iso.build_iso(tree, image, progress=tick)
                lines = [
                    f"Mastered {image.name}: {result['sectors']:,} sectors, "
                    f"{result['bytes'] / 2**20:.1f} MiB."
                ]
                lines += [f"  {w}" for w in result["warnings"]]
        except KeyboardInterrupt:
            return ["Image writing cancelled; the disc tree is still there."]
        except (OSError, ValueError) as exc:
            return [f"The disc tree is built, but the image failed: {exc}"]
        finally:
            progress.close()

        return lines + [f"Its cue sheet is beside it at {result['cue'].name}."]

    def export_glb(self) -> None:
        """Everything about the model in one file: geometry, textures, clips."""
        if self.model is None or self.entry is None:
            return
        default = Path(self.settings.value("last_export", str(Path.home())))
        stem = Path(self.entry.name).stem
        path, _ = QFileDialog.getSaveFileName(
            self, "Export glTF", str(default / f"{stem}.glb"), "glTF binary (*.glb)"
        )
        if not path:
            return
        pack = self._sibling_texture_pack(self.entry)
        Path(path).write_bytes(
            gltf.export_glb(self.model, pack, self.animations, name=stem,
                            scene=self.scene)
        )
        self.settings.setValue("last_export", str(Path(path).parent))
        shot = ""
        if self.scene is not None:
            shot = (f", the shot with {len(self.scene.actors)} actors, "
                    f"{len(self.scene.props)} props and "
                    f"{len(self.scene.cameras)} cameras")
        self.statusBar().showMessage(
            f"Wrote {path} — {len(self.model.meshes)} meshes, "
            f"{len(self.animations)} clips{shot}"
        )

    def export_textures(self) -> None:
        if self.pack is None or self.entry is None:
            return
        folder = QFileDialog.getExistingDirectory(self, "Export textures to…")
        if not folder:
            return
        out = Path(folder) / Path(self.entry.name).stem
        out.mkdir(parents=True, exist_ok=True)
        from .panels import rgba_to_pixmap

        written = 0
        for texture in self.pack.textures:
            pixmap = rgba_to_pixmap(texture.to_rgba(self.pack.palettes))
            if pixmap.save(str(out / f"{texture.name}.png"), "PNG"):
                written += 1
        self.statusBar().showMessage(f"Wrote {written} PNGs to {out}")

    def export_raw(self) -> None:
        if self.archive is None or self.entry is None:
            return
        suggested = Path(self.entry.name).name
        path, _ = QFileDialog.getSaveFileName(self, "Export file", suggested)
        if not path:
            return
        Path(path).write_bytes(self.archive.read(self.entry))
        self.statusBar().showMessage(f"Wrote {path}")

    def export_current_audio(self) -> None:
        if self.bank is None or self.entry is None:
            return
        folder = QFileDialog.getExistingDirectory(self, "Export sound bank to…")
        if not folder:
            return
        out = Path(folder) / Path(self.entry.name).stem
        out.mkdir(parents=True, exist_ok=True)
        for name, blob in self.bank.files():
            (out / name).write_bytes(blob)
        self.statusBar().showMessage(f"Wrote sound bank to {out}")

    def export_current_wavs(self) -> None:
        if self.bank is None or self.entry is None or not self.bank.samples:
            return
        folder = QFileDialog.getExistingDirectory(self, "Export samples as WAV to…")
        if not folder:
            return
        out = Path(folder) / Path(self.entry.name).with_suffix("")
        out.mkdir(parents=True, exist_ok=True)
        for name, blob in self.bank.wav_files():
            (out / name).write_bytes(blob)
        self.statusBar().showMessage(
            f"Wrote {len(self.bank.samples)} WAV files to {out}"
        )

    def extract_all(self) -> None:
        if self.archive is None:
            QMessageBox.information(self, APP_NAME, "Open a game EXE first.")
            return
        folder = QFileDialog.getExistingDirectory(self, "Extract archive to…")
        if not folder:
            return

        progress = QProgressDialog("Extracting…", "Cancel", 0, len(self.archive), self)
        progress.setWindowModality(Qt.WindowModal)
        cancelled = False

        def tick(done: int, total: int, entry: Entry) -> None:
            nonlocal cancelled
            progress.setValue(done)
            progress.setLabelText(entry.name)
            if progress.wasCanceled():
                cancelled = True
                raise KeyboardInterrupt

        try:
            count = self.archive.extract_all(folder, progress=tick)
        except KeyboardInterrupt:
            self.statusBar().showMessage("Extraction cancelled")
            return
        finally:
            progress.close()

        if not cancelled:
            self.statusBar().showMessage(f"Extracted {count} files to {folder}")

    # -- drag and drop --------------------------------------------------

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        for url in event.mimeData().urls():
            path = Path(url.toLocalFile())
            if path.is_dir():
                exe = find_exe(path)
                if exe:
                    self.load_archive(exe)
                    return
            elif path.is_file():
                if find_dat(path.parent) is not None:
                    self.load_archive(path)
                    return
        QMessageBox.information(
            self, APP_NAME, "Drop the game EXE (with CRASHBSH.DAT nearby) or its folder."
        )
