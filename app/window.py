"""Main window: the archive on the left, an editor per file kind on the right."""

from __future__ import annotations

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
)
from crashbash import build, iso
from crashbash.formats import anim, gltf, mdl, sfx, tex

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
        self.anim_panel.frame_changed.connect(self._set_frame)

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
        self.statusBar().showMessage("Open a Crash Bash EXE to begin (⌘O)")
        self.setAcceptDrops(True)

        last = self.settings.value("last_exe", "")
        if last and Path(last).exists():
            self.load_archive(Path(last), quiet=True)

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
        self.anim_panel.set_animations([])
        self.export_obj_action.setEnabled(False)
        self.export_glb_action.setEnabled(False)
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
            self.view3d.set_model(self.model, self._sibling_texture_pack(entry))
            self.mesh_panel.set_model(self.model, header)
            self.anim_panel.set_animations(self.animations)
            self.pages.setCurrentIndex(0)
            self.export_obj_action.setEnabled(bool(self.model.meshes))
            self.export_glb_action.setEnabled(bool(self.model.meshes))
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

    def _sibling_texture_pack(self, entry: Entry) -> tex.TexturePack | None:
        """A model's textures live in the .tex file of the same name."""
        if self.archive is None:
            return None
        wanted = entry.name.rsplit(".", 1)[0] + ".tex"
        for candidate in self.archive:
            if candidate.name == wanted:
                return tex.read_pack(self.archive.read(candidate))
        return None

    @guarded
    def _set_animation(self, animation: anim.Animation | None) -> None:
        self.view3d.set_animation(animation)
        if animation is not None:
            self.statusBar().showMessage(
                f"{animation.label} — {animation.frame_count} frames, mesh "
                f"{animation.mesh_index}, {animation.vertex_count} vertices"
            )

    @guarded
    def _set_frame(self, frame: int) -> None:
        self.view3d.set_frame(frame)

    def _set_all_meshes(self, visible: bool) -> None:
        self.view3d.set_all_meshes_visible(visible)
        self.mesh_panel.set_all_checked(visible)

    def _apply_view_options(self) -> None:
        options = self.view_options
        self.view3d.show_solid = options.solid.isChecked()
        self.view3d.show_wireframe = options.wireframe.isChecked()
        self.view3d.show_points = options.points.isChecked()
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
            gltf.export_glb(self.model, pack, self.animations, name=stem)
        )
        self.settings.setValue("last_export", str(Path(path).parent))
        self.statusBar().showMessage(
            f"Wrote {path} — {len(self.model.meshes)} meshes, "
            f"{len(self.animations)} clips"
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
