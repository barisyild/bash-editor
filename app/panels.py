"""Side panels and the non-3D editors: file tree, textures, audio, hex."""

from __future__ import annotations

import functools
import traceback

import numpy as np
from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from crashbash.archive import BashArchive, Entry
from crashbash.formats import anim, sfx, tex
from crashbash.formats import placewrite as PW
from crashbash.formats.mdl import IDENTITY


def guarded(fn):
    """Print a traceback instead of letting Qt swallow one.

    An exception raised inside a slot never reaches the caller: Qt logs it at
    best and the event loop carries on, so a playback timer that throws simply
    stops advancing and the viewport keeps showing the frame it last drew.
    """

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception:
            traceback.print_exc()

    return wrapper

GROUP_ICON = {
    "model": "◆",
    "texture": "▩",
    "audio": "♪",
    "image": "▣",
    "map": "▤",
    "code": "⚙",
    "binary": "·",
}


# The archive interleaves each model with its textures, so browsing by name
# alone makes it easy to open a .tex expecting the 3D view.
KIND_TABS = [
    ("Models", "model"),
    ("Textures", "texture"),
    ("Audio", "audio"),
    ("All", None),
]


class FileTree(QWidget):
    """Archive contents as a folder tree, filtered by kind and by name."""

    entry_selected = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._archive: BashArchive | None = None
        self._items: dict[int, QTreeWidgetItem] = {}
        self._group: str | None = "model"

        self.kind_buttons: list[QPushButton] = []
        kind_row = QHBoxLayout()
        kind_row.setSpacing(2)
        for label, group in KIND_TABS:
            button = QPushButton(label, checkable=True, checked=group == self._group)
            button.setToolTip(
                "Only 3D models open in the viewport; textures and sounds have "
                "their own panels."
            )
            button.clicked.connect(lambda _=False, g=group: self._set_group(g))
            self.kind_buttons.append(button)
            kind_row.addWidget(button)

        self.filter_box = QLineEdit(placeholderText="Filter by name or extension…")
        self.filter_box.textChanged.connect(self._apply_filter)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Name", "Size"])
        self.tree.setColumnWidth(0, 260)
        self.tree.setSelectionMode(QAbstractItemView.SingleSelection)
        self.tree.currentItemChanged.connect(self._on_current_changed)

        self.summary = QLabel("No archive loaded")
        self.summary.setWordWrap(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.addLayout(kind_row)
        layout.addWidget(self.filter_box)
        layout.addWidget(self.tree, 1)
        layout.addWidget(self.summary)

    def set_replaced(self, index: int, replaced: bool) -> None:
        """Mark an entry as having a staged replacement.

        Worth showing in the tree rather than only in the menu: the staged set
        outlives the selection, and a build silently writing a swap the user has
        forgotten about is the thing to avoid.
        """
        item = self._items.get(index)
        if item is None:
            return
        name = item.text(0).lstrip("• ")
        item.setText(0, f"• {name}" if replaced else name)
        font = item.font(0)
        font.setBold(replaced)
        item.setFont(0, font)

    def _set_group(self, group: str | None) -> None:
        self._group = group
        for button, (_, value) in zip(self.kind_buttons, KIND_TABS):
            button.setChecked(value == group)
        self._apply_filter(self.filter_box.text())

    def set_archive(self, archive: BashArchive | None) -> None:
        self._archive = archive
        self.tree.clear()
        self._items.clear()
        if archive is None:
            self.summary.setText("No archive loaded")
            return

        folders: dict[str, QTreeWidgetItem] = {}

        def folder_for(path: str) -> QTreeWidgetItem | None:
            if not path:
                return None
            if path in folders:
                return folders[path]
            parent_path, _, name = path.rpartition("/")
            parent = folder_for(parent_path)
            item = QTreeWidgetItem([name, ""])
            item.setData(0, Qt.UserRole, None)
            if parent is None:
                self.tree.addTopLevelItem(item)
            else:
                parent.addChild(item)
            folders[path] = item
            return item

        for entry in archive:
            directory, _, filename = entry.name.rpartition("/")
            parent = folder_for(directory)
            icon = GROUP_ICON.get(entry.group, "·")
            item = QTreeWidgetItem([f"{icon}  {filename}", _human(entry.size)])
            item.setData(0, Qt.UserRole, entry)
            self._items[entry.index] = item
            item.setToolTip(
                0,
                f"#{entry.index}  offset 0x{entry.offset:X}  "
                f"magic 0x{entry.magic:08X}  kind {entry.kind}",
            )
            if parent is None:
                self.tree.addTopLevelItem(item)
            else:
                parent.addChild(item)

        counts: dict[str, int] = {}
        for entry in archive:
            counts[entry.group] = counts.get(entry.group, 0) + 1
        breakdown = ", ".join(f"{k} {v}" for k, v in sorted(counts.items()))
        self.summary.setText(
            f"{archive.version.name} — {len(archive)} files\n{breakdown}"
        )
        self.tree.expandToDepth(0)
        self._apply_filter(self.filter_box.text())

    def _apply_filter(self, text: str) -> None:
        needle = text.strip().lower()

        def visit(item: QTreeWidgetItem) -> bool:
            entry = item.data(0, Qt.UserRole)
            if entry is None:
                match = False  # a folder is shown only for the sake of its children
            else:
                match = (not needle or needle in entry.name.lower()) and (
                    self._group is None or entry.group == self._group
                )
            child_match = False
            for i in range(item.childCount()):
                child_match |= visit(item.child(i))
            visible = match or child_match
            item.setHidden(not visible)
            if needle and child_match:
                item.setExpanded(True)
            return visible

        for i in range(self.tree.topLevelItemCount()):
            visit(self.tree.topLevelItem(i))

    def _on_current_changed(self, current: QTreeWidgetItem | None, _previous) -> None:
        if current is None:
            return
        entry = current.data(0, Qt.UserRole)
        if isinstance(entry, Entry):
            self.entry_selected.emit(entry)


class MeshPanel(QWidget):
    """Per-mesh checkboxes plus the parse report for the selected model."""

    visibility_changed = Signal(int, bool)
    all_visibility_changed = Signal(bool)
    hide_unplaced_changed = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.list = QListWidget()
        self.list.itemChanged.connect(self._on_item_changed)

        # On by default: a level's unreachable meshes are drawn here only
        # because `draw_list` stands one at the origin rather than dropping it,
        # and showing them makes a room look like something the game never puts
        # on screen.
        self.hide_unplaced = QCheckBox("Hide meshes no placement reaches")
        self.hide_unplaced.setChecked(True)
        self.hide_unplaced.setToolTip(
            "A level draws what its placement list names. Meshes nothing names "
            "are never on screen, however correct they are on disc — untick to "
            "look at them."
        )
        self.hide_unplaced.toggled.connect(self.hide_unplaced_changed.emit)

        self.show_all = QPushButton("Show all")
        self.hide_all = QPushButton("Hide all")
        self.show_all.clicked.connect(lambda: self.all_visibility_changed.emit(True))
        self.hide_all.clicked.connect(lambda: self.all_visibility_changed.emit(False))

        buttons = QHBoxLayout()
        buttons.addWidget(self.show_all)
        buttons.addWidget(self.hide_all)

        self.report = QPlainTextEdit(readOnly=True)
        self.report.setMaximumHeight(150)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.addWidget(QLabel("Meshes"))
        layout.addWidget(self.list, 1)
        layout.addWidget(self.hide_unplaced)
        layout.addLayout(buttons)
        layout.addWidget(QLabel("Parse report"))
        layout.addWidget(self.report)

        self._loading = False

    def set_model(self, model, title: str) -> None:
        self._loading = True
        self.list.clear()
        lines = [title]
        if model is None or not model.meshes:
            self.report.setPlainText("\n".join(lines + ["No meshes parsed."]))
            self._loading = False
            return

        # An object's mesh is listed under the id the game reaches it by, since
        # that is what names it everywhere else -- it has no place in the
        # numbered array the header counts.
        names = {mesh.index: f"mesh {mesh.index:02d}" for mesh in model.meshes}
        names.update({
            obj.mesh.index: f"object {obj.id:04X}"
            for obj in model.objects if obj.mesh is not None
        })

        matched = 0
        drawn = model.drawn_meshes
        unplaced = model.unplaced_meshes()
        for mesh in drawn:
            flag = "✓" if mesh.faces_match_header else "!"
            item = QListWidgetItem(
                f"{flag} {names[mesh.index]} — {mesh.vertex_count} verts, "
                f"{mesh.face_count} tris, {len(mesh.strips)} strips"
                + (f", {len(mesh.volumes)} volume"
                   + ("s" if len(mesh.volumes) != 1 else "") if mesh.volumes else "")
                + (" — no placement reaches it" if mesh.index in unplaced else "")
            )
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked)
            item.setData(Qt.UserRole, mesh.index)
            self.list.addItem(item)
            matched += mesh.faces_match_header

        total_v = sum(m.vertex_count for m in drawn)
        total_f = sum(m.face_count for m in drawn)
        objects = len(drawn) - len(model.meshes)
        lines.append(
            f"{len(model.meshes)} meshes"
            + (f" and {objects} objects" if objects else "")
            + f", {total_v} vertices, {total_f} triangles"
        )
        lines.append(
            f"{matched}/{len(drawn)} meshes match the triangle count in "
            "their header"
        )
        unresolved = [o for o in model.objects if o.mesh is None]
        if unresolved:
            lines.append(
                f"{len(unresolved)} objects live in a model this level loads "
                "alongside its own and cannot be shown from this file"
            )
        if model.instances:
            moved = sum(
                1 for i in model.instances
                if i.rotation != IDENTITY or any(i.translation)
            )
            unplaced = len([
                o for o in model.objects
                if o.mesh is not None
                and not any(i.mesh is o.mesh for i in model.instances)
            ])
            lines.append(
                f"{len(model.instances)} placements stand the set up, "
                f"{moved} of them away from the origin"
            )
            if unplaced:
                lines.append(
                    f"{unplaced} objects that no placement names are shown "
                    "where their own vertices sit"
                )
        for warning in model.warnings:
            lines.append(f"model: {warning}")
        for mesh in drawn:
            for warning in mesh.warnings:
                lines.append(f"{names[mesh.index]}: {warning}")
        self.report.setPlainText("\n".join(lines))
        self._loading = False

    def _on_item_changed(self, item: QListWidgetItem) -> None:
        if self._loading:
            return
        self.visibility_changed.emit(
            item.data(Qt.UserRole), item.checkState() == Qt.Checked
        )

    def set_all_checked(self, checked: bool) -> None:
        self._loading = True
        for i in range(self.list.count()):
            self.list.item(i).setCheckState(Qt.Checked if checked else Qt.Unchecked)
        self._loading = False


# Frames per second for playback. The stored timeline is already baked one
# record per tick, so this only sets how fast ticks are consumed; what the
# console's tick actually was has not been established here, and 30 is the rate
# the poses read plausibly at rather than a measured fact.
PLAYBACK_FPS = 30.0


class PlacementPanel(QWidget):
    """The level's placement list (§8.5): what it draws, and where.

    A level draws what these records name and nothing else, so this is the one
    panel that changes what a room looks like. The list cannot be made longer --
    see `crashbash.formats.placewrite` for why, and for the probes behind it --
    so a record is either moved, re-aimed at another object, or spent: a
    placement whose object is placed elsewhere too is marked "spare", and
    rewriting one of those adds something to the room at the cost of a
    duplicate.
    """

    placement_changed = Signal(int, int, tuple)   # record, id, translation

    def __init__(self, parent=None):
        super().__init__(parent)
        self.list = QListWidget()
        self.list.currentRowChanged.connect(self._on_selected)

        self.identifier = QLineEdit()
        self.identifier.setPlaceholderText("0x5021")
        self.axes = []
        for label in ("X", "Y", "Z"):
            box = QDoubleSpinBox()
            box.setRange(-8388608.0, 8388607.0)
            box.setDecimals(3)
            box.setSingleStep(1.0)
            box.setPrefix(f"{label}  ")
            self.axes.append(box)

        form = QFormLayout()
        form.addRow("Object id", self.identifier)
        for box in self.axes:
            form.addRow("", box)

        self.apply = QPushButton("Apply to entry")
        self.apply.clicked.connect(self._on_apply)
        self.apply.setEnabled(False)

        self.report = QPlainTextEdit(readOnly=True)
        self.report.setMaximumHeight(90)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.addWidget(QLabel("Placements"))
        layout.addWidget(self.list, 1)
        layout.addLayout(form)
        layout.addWidget(self.apply)
        layout.addWidget(self.report)

        self._instances = []

    def set_model(self, model) -> None:
        self.list.clear()
        self._instances = list(model.instances) if model is not None else []
        self.apply.setEnabled(False)
        if not self._instances:
            self.report.setPlainText(
                "This model has no placement list. Its meshes are drawn by "
                "something else -- the menu draws its own from code -- so there "
                "is nothing here to edit."
            )
            return

        spare = set(PW.spare_records(model))
        names = {}
        for slot, obj in enumerate(model.objects):
            if obj.mesh is not None:
                names[PW.object_id(slot)] = obj.mesh
        for instance in self._instances:
            mesh = names.get(instance.id)
            where = ", ".join(f"{v:.1f}" for v in instance.translation)
            item = QListWidgetItem(
                f"{instance.index:>3}  id {instance.id:#06x}"
                + (f"  mesh {mesh.index} ({mesh.face_count} tris)" if mesh
                   else "  (drawn from another file)")
                + f"  at {where}"
                + ("  — spare" if instance.index in spare else "")
            )
            self.list.addItem(item)
        self.report.setPlainText(
            f"{len(self._instances)} placements, {len(spare)} of them spare — "
            "their object is placed elsewhere too, so one can be spent on "
            "something new. The list cannot be made longer."
        )

    def _on_selected(self, row: int) -> None:
        if not 0 <= row < len(self._instances):
            self.apply.setEnabled(False)
            return
        instance = self._instances[row]
        self.identifier.setText(f"{instance.id:#06x}")
        for box, value in zip(self.axes, instance.translation):
            box.setValue(float(value))
        self.apply.setEnabled(True)

    def _on_apply(self) -> None:
        row = self.list.currentRow()
        if not 0 <= row < len(self._instances):
            return
        try:
            identifier = int(self.identifier.text(), 0)
        except ValueError:
            self.report.setPlainText(
                f"{self.identifier.text()!r} is not a number — an id looks like "
                "0x5021.")
            return
        translation = tuple(box.value() for box in self.axes)
        self.placement_changed.emit(self._instances[row].index, identifier,
                                    translation)


class AnimationPanel(QWidget):
    """Clip list, transport and scrubber for a model's vertex animation.

    Row 0 is always the static pose, so a model with no clips still has a
    meaningful, selectable state and the user can always get back to the
    geometry as the file stores it.
    """

    animation_changed = Signal(object)  # anim.Animation | None
    scene_changed = Signal(object)  # scene.Scene | None
    frame_changed = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._animations: list[anim.Animation] = []
        self._scene = None
        self._playing = False

        self.list = QListWidget()
        self.list.currentRowChanged.connect(self._on_row_changed)

        self.play_button = QPushButton("▶  Play")
        self.play_button.clicked.connect(self._toggle_play)
        # A shot names its own viewpoint (§9.12); showing it by default is the
        # point of scene playback, but the orbit controls have to stay reachable
        # for looking at what the shot keeps off screen.
        self.shot_camera = QCheckBox("Shot camera", checked=True)
        self.shot_camera.setToolTip(
            "Film through the camera the cutscene names, at its own field of view."
        )
        self.shot_camera.setEnabled(False)
        self.slider = QSlider(Qt.Horizontal, minimum=0, maximum=0)
        self.slider.valueChanged.connect(self._on_slider)
        self.frame_label = QLabel("—", alignment=Qt.AlignRight | Qt.AlignVCenter)
        # A counter that reflows the row every time it gains a digit makes the
        # whole transport twitch as a scene plays, so it is sized once for the
        # widest thing it will ever hold.
        self.frame_label.setMinimumWidth(
            self.frame_label.fontMetrics().horizontalAdvance("8888 / 8888")
        )
        self.frame_label.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)
        self.info = QLabel("No model loaded")
        self.info.setWordWrap(True)

        # The slider gets its own row: this panel shares a narrow side column
        # with the mesh list, and beside a button it collapses to just the grip.
        transport = QHBoxLayout()
        transport.addWidget(self.play_button)
        transport.addWidget(self.shot_camera)
        transport.addStretch(1)
        transport.addWidget(self.frame_label)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.addWidget(QLabel("Animations"))
        layout.addWidget(self.list, 1)
        layout.addLayout(transport)
        layout.addWidget(self.slider)
        layout.addWidget(self.info)

        self._timer = QTimer(self)
        self._timer.setInterval(round(1000.0 / PLAYBACK_FPS))
        self._timer.timeout.connect(self._tick)

        self._loading = False
        self._set_enabled(False)

    # -- contents --------------------------------------------------------

    def set_animations(self, animations: list[anim.Animation], scene=None) -> None:
        """Show the clips of a newly opened model, stopping whatever was playing.

        A model that carries a cutscene gets a row of its own above the clips:
        one clip poses one mesh, but a scene poses the whole cast at once, on
        its own clock and through its own camera.
        """
        self.stop()
        self._animations = animations
        self._scene = scene
        self._loading = True
        self.list.clear()
        self.list.addItem(QListWidgetItem("—  static pose"))
        if scene is not None:
            owned = len(scene.mesh_indices)
            item = QListWidgetItem(
                f"▣  scene  —  {scene.end - scene.start + 1} ticks, {owned} meshes"
            )
            item.setToolTip(
                f"The whole cutscene over ticks {scene.start}..{scene.end}: "
                f"{len(scene.actors)} actors playing clips and {len(scene.props)} "
                f"prop tracks, over meshes {sorted(scene.mesh_indices)}.\n"
                "A mesh a node owns is drawn only while one of its windows is "
                "open, as the game does; meshes no node owns are the set and "
                "stay put.\n"
                + (f"{len(scene.cameras)} camera(s) of its own."
                   if scene.cameras else "No camera of its own; orbit it.")
            )
            self.list.addItem(item)
        for clip in animations:
            item = QListWidgetItem(f"{_clip_name(clip)}  —  {clip.frame_count} frames")
            item.setToolTip(self._describe(clip))
            if clip.mesh_index is None or not clip.frame_count:
                # The descriptor never named a mesh, so there is nothing to
                # pose. One clip in the archive is like this.
                item.setFlags(item.flags() & ~Qt.ItemIsEnabled)
            self.list.addItem(item)
        self.list.setCurrentRow(0)
        self._loading = False

        if animations:
            self.info.setText(f"{len(animations)} clips")
        else:
            self.info.setText("This model has no animations.")
        self._sync_transport(None)
        # No animation_changed here. The panel lands on the static pose, and so
        # does the viewport, because loading a model resets it -- emitting would
        # only buy a redundant rebuild of the model being replaced, which costs
        # 28 ms on the heaviest one in the archive.

    def _describe(self, clip: anim.Animation) -> str:
        lines = [
            f"clip {clip.index}, hash {clip.name_hash}",
            f"{clip.frame_count} frames, {clip.vertex_count} vertices",
            f"mesh {clip.mesh_index}" if clip.mesh_index is not None else "no mesh",
            "shared vertex pool" if clip.pool_is_shared else "own vertex pool",
        ]
        if len(clip.name_candidates) > 1:
            lines.append("name is ambiguous: " + ", ".join(clip.name_candidates))
        elif not clip.name_candidates:
            lines.append("no guessed name reproduces this hash")
        lines += clip.warnings
        return "\n".join(lines)

    @property
    def _clip_offset(self) -> int:
        """Row of the first clip: after the static pose, and the scene if any."""
        return 2 if self._scene is not None else 1

    def scene_selected(self) -> bool:
        return self._scene is not None and self.list.currentRow() == 1

    def current(self) -> anim.Animation | None:
        row = self.list.currentRow() - self._clip_offset
        if 0 <= row < len(self._animations):
            return self._animations[row]
        return None

    # -- transport -------------------------------------------------------

    def _set_enabled(self, enabled: bool) -> None:
        self.play_button.setEnabled(enabled)
        self.slider.setEnabled(enabled)

    def _sync_transport(self, clip: anim.Animation | None) -> None:
        scene = self._scene if self.scene_selected() else None
        self._loading = True
        if scene is not None:
            last = max(0, scene.end - scene.start)
        else:
            last = max(0, (clip.frame_count - 1) if clip else 0)
        self.slider.setMaximum(last)
        self.slider.setValue(0)
        self._loading = False
        playable = scene is not None or (clip is not None and clip.frame_count > 1)
        self._set_enabled(playable)
        self.shot_camera.setEnabled(bool(scene is not None and scene.cameras))
        self.frame_label.setText(f"0 / {last}" if playable else "—")

    @guarded
    def _on_row_changed(self, _row: int) -> None:
        if self._loading:
            return
        self.stop()
        clip = self.current()
        self._sync_transport(clip)
        if self.scene_selected():
            scene = self._scene
            self.info.setText(
                f"Cutscene: ticks {scene.start}..{scene.end}, "
                f"{len(scene.actors)} actors, {len(scene.props)} prop tracks"
            )
            self.scene_changed.emit(scene)
            return
        if clip is not None:
            self.info.setText(self._describe(clip).replace("\n", " · "))
        elif self._animations:
            self.info.setText(f"{len(self._animations)} clips")
        if self._scene is not None:
            self.scene_changed.emit(None)
        self.animation_changed.emit(clip)

    @guarded
    def _on_slider(self, value: int) -> None:
        if self._loading:
            return
        self.frame_label.setText(f"{value} / {self.slider.maximum()}")
        self.frame_changed.emit(value)

    @guarded
    def _toggle_play(self) -> None:
        self.stop() if self._playing else self.play()

    def play(self) -> None:
        if self.current() is None and not self.scene_selected():
            return
        self._playing = True
        self.play_button.setText("❚❚  Pause")
        self._timer.start()

    def stop(self) -> None:
        self._playing = False
        self._timer.stop()
        self.play_button.setText("▶  Play")

    @guarded
    def _tick(self) -> None:
        span = self.slider.maximum() + 1
        if span < 2:
            self.stop()
            return
        # Wrapping unconditionally: whether a clip was authored to loop is
        # visible in the data but the runtime loops either kind, and a preview
        # that stops on the last frame is harder to inspect.
        self.slider.setValue((self.slider.value() + 1) % span)


def _clip_name(clip: anim.Animation) -> str:
    """List label: the guessed name when there is one, the hash otherwise.

    Both ambiguous candidates are shown rather than one picked, because the
    hash is a plain weighted sum and genuinely does not distinguish them.
    """
    if clip.name:
        return clip.name
    if clip.name_candidates:
        return " / ".join(clip.name_candidates)
    return f"clip {clip.index}  (hash {clip.name_hash})"


def rgba_to_pixmap(image: np.ndarray) -> QPixmap:
    """Composite RGBA onto a checkerboard so transparency is visible."""
    h, w, _ = image.shape
    board = np.empty((h, w, 3), dtype=np.float32)
    ys, xs = np.mgrid[0:h, 0:w]
    checker = ((xs // 8 + ys // 8) % 2).astype(np.float32)
    board[:] = (0.18 + checker * 0.06)[..., None] * 255.0

    alpha = image[..., 3:4].astype(np.float32) / 255.0
    blended = image[..., :3].astype(np.float32) * alpha + board * (1.0 - alpha)
    rgb = np.ascontiguousarray(blended.astype(np.uint8))
    qimage = QImage(rgb.data, w, h, 3 * w, QImage.Format_RGB888)
    return QPixmap.fromImage(qimage.copy())


class TextureView(QWidget):
    """Texture pack browser: thumbnail list on the left, zoomable preview right."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pack: tex.TexturePack | None = None
        self._zoom = 3
        self._frame = 0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._advance)

        self.list = QListWidget()
        self.list.setMaximumWidth(230)
        self.list.currentRowChanged.connect(self._on_row)

        self.canvas = QLabel(alignment=Qt.AlignCenter)
        self.canvas.setMinimumSize(200, 200)
        scroll = QScrollArea()
        scroll.setWidget(self.canvas)
        scroll.setWidgetResizable(True)

        self.zoom_slider = QSlider(Qt.Horizontal, minimum=1, maximum=12, value=3)
        self.zoom_slider.valueChanged.connect(self._on_zoom)
        self.info = QLabel("—")

        self.play_button = QPushButton("Play")
        self.play_button.setCheckable(True)
        self.play_button.toggled.connect(self._on_play)
        self.frame_slider = QSlider(Qt.Horizontal, minimum=0, maximum=0)
        self.frame_slider.valueChanged.connect(self._on_frame)
        self.anim_label = QLabel("—")
        self.anim_row = QWidget()
        anim_layout = QHBoxLayout(self.anim_row)
        anim_layout.setContentsMargins(0, 0, 0, 0)
        anim_layout.addWidget(self.play_button)
        anim_layout.addWidget(self.frame_slider, 1)
        anim_layout.addWidget(self.anim_label)
        self.anim_row.hide()

        right = QVBoxLayout()
        right.addWidget(scroll, 1)
        zoom_row = QHBoxLayout()
        zoom_row.addWidget(QLabel("Zoom"))
        zoom_row.addWidget(self.zoom_slider, 1)
        right.addLayout(zoom_row)
        right.addWidget(self.anim_row)
        right.addWidget(self.info)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.addWidget(self.list)
        layout.addLayout(right, 1)

    def set_pack(self, pack: tex.TexturePack | None) -> None:
        self._pack = pack
        self.play_button.setChecked(False)
        self.list.clear()
        self.canvas.clear()
        if pack is None:
            self.info.setText("—")
            return
        animated = pack.animated()
        scrolling = {s.texture: s for s in pack.scrollers}
        for texture in pack.textures:
            marks = ""
            if texture.index in animated:
                marks += f"  ▶ {len(animated[texture.index].frames)}"
            if texture.index in scrolling:
                marks += "  ⇄"
            if not texture.palette_ok:
                marks += "  (palette?)"
            self.list.addItem(f"{texture.index:03d}  {texture.width}×{texture.height}"
                              f"  {texture.bit_depth}bpp{marks}")
        note = f"{len(pack.textures)} textures, {len(pack.palettes)} palettes"
        if pack.flipbooks or pack.scrollers:
            note += (f", {len(pack.flipbooks)} animated, "
                     f"{len(pack.scrollers)} scrolling")
        if pack.warnings:
            note += f" — {pack.warnings[0]}"
        self.info.setText(note)
        if pack.textures:
            self.list.setCurrentRow(0)

    def current_texture(self) -> tex.Texture | None:
        if self._pack is None:
            return None
        row = self.list.currentRow()
        if 0 <= row < len(self._pack.textures):
            return self._pack.textures[row]
        return None

    def _on_zoom(self, value: int) -> None:
        self._zoom = value
        self._show(self.list.currentRow())

    def _flipbook(self, row: int) -> tex.Flipbook | None:
        if self._pack is None:
            return None
        return self._pack.animated().get(row)

    def _on_row(self, row: int) -> None:
        """A texture with frames of its own gets a transport, the rest do not."""
        book = self._flipbook(row)
        self._frame = 0
        self.anim_row.setVisible(book is not None)
        if book is None:
            self.play_button.setChecked(False)
        else:
            self.frame_slider.blockSignals(True)
            self.frame_slider.setMaximum(max(len(book.frames) - 1, 0))
            self.frame_slider.setValue(0)
            self.frame_slider.blockSignals(False)
            self.anim_label.setText(f"{len(book.frames)} frames, {book.fps:.1f} fps")
            if self.play_button.isChecked():
                self._timer.start(max(int(1000 / max(book.fps, 0.1)), 16))
        self._show(row)

    def _on_play(self, playing: bool) -> None:
        book = self._flipbook(self.list.currentRow())
        self.play_button.setText("Pause" if playing else "Play")
        if playing and book is not None:
            self._timer.start(max(int(1000 / max(book.fps, 0.1)), 16))
        else:
            self._timer.stop()

    def _advance(self) -> None:
        book = self._flipbook(self.list.currentRow())
        if book is None or not book.frames:
            self._timer.stop()
            return
        self.frame_slider.setValue((self._frame + 1) % len(book.frames))

    def _on_frame(self, value: int) -> None:
        self._frame = value
        self._show(self.list.currentRow())

    def _show(self, row: int) -> None:
        if self._pack is None or not (0 <= row < len(self._pack.textures)):
            return
        texture = self._pack.textures[row]
        book = self._flipbook(row)
        if book is not None:
            texture = self._pack.frame(book, self._frame)
        pixmap = rgba_to_pixmap(texture.to_rgba(self._pack.palettes))
        scaled = pixmap.scaled(
            QSize(pixmap.width() * self._zoom, pixmap.height() * self._zoom),
            Qt.KeepAspectRatio,
            Qt.FastTransformation,
        )
        self.canvas.setPixmap(scaled)
        self.canvas.resize(scaled.size())
        note = (
            f"#{texture.index}  {texture.width}×{texture.height}  "
            f"{texture.bit_depth}bpp  palette {texture.palette_index}  "
            f"flags 0x{texture.flags:X}  ({len(self._pack.palettes)} palettes in pack)"
        )
        if book is not None:
            note += f"  —  frame {self._frame + 1}/{len(book.frames)}"
        for scroller in self._pack.scrollers:
            if scroller.texture == row:
                note += f"  —  scrolls {scroller.texels_per_second:+.1f} texels/s"
        self.info.setText(note)


class AudioView(QWidget):
    """SFX bank browser: pick a sample, hear it, or export the lot as WAV."""

    export_requested = Signal()
    export_wav_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._bank: sfx.SoundBank | None = None
        self._sink = None
        self._buffer = None

        self.list = QListWidget()
        self.list.setMaximumWidth(260)
        self.list.currentRowChanged.connect(self._describe)
        self.list.itemActivated.connect(lambda _: self.play())

        self.text = QPlainTextEdit(readOnly=True)
        self.play_button = QPushButton("▶  Play")
        self.play_button.clicked.connect(self.play)
        self.stop_button = QPushButton("■  Stop")
        self.stop_button.clicked.connect(self.stop)
        self.wav_button = QPushButton("Export all as WAV…")
        self.wav_button.clicked.connect(self.export_wav_requested)
        self.raw_button = QPushButton("Export VB / VH / SEQ…")
        self.raw_button.clicked.connect(self.export_requested)

        buttons = QHBoxLayout()
        buttons.addWidget(self.play_button)
        buttons.addWidget(self.stop_button)
        buttons.addStretch(1)
        buttons.addWidget(self.wav_button)
        buttons.addWidget(self.raw_button)

        right = QVBoxLayout()
        right.addWidget(self.text, 1)
        right.addLayout(buttons)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.addWidget(self.list)
        layout.addLayout(right, 1)

    # -- contents --------------------------------------------------------

    def set_bank(self, bank: sfx.SoundBank | None, name: str) -> None:
        self.stop()
        self._bank = bank
        self.list.clear()
        if bank is None:
            self.text.setPlainText("Not a sound bank.")
            for button in (self.play_button, self.wav_button, self.raw_button):
                button.setEnabled(False)
            return

        for sample in bank.samples:
            self.list.addItem(
                f"{sample.index:03d}   {sample.duration:5.2f}s   {sample.rate:5d} Hz"
            )

        lines = [
            name,
            "",
            f"VB (ADPCM samples): {_human(len(bank.vb))}",
            f"VH (VAB header):    {_human(len(bank.vh))}",
            f"Programs:           {len(bank.programs)}",
            f"Samples:            {len(bank.samples)}",
            f"Sequences:          {len(bank.sequences)}",
        ]
        for i, seq in enumerate(bank.sequences):
            lines.append(f"  seq {i}: {_human(len(seq))}")
        if bank.warnings:
            lines += ["", "Warnings:"] + [f"  {w}" for w in bank.warnings]
        if bank.sequences:
            lines += [
                "",
                "The sequences are PS1 SEQp music. Playing them needs a sequencer "
                "driving this bank, which this editor does not have -- export them "
                "for a tool that does.",
            ]
        self.text.setPlainText("\n".join(lines))

        self.raw_button.setEnabled(bool(bank.vb or bank.vh or bank.sequences))
        self.wav_button.setEnabled(bool(bank.samples))
        self.play_button.setEnabled(bool(bank.samples))
        if bank.samples:
            self.list.setCurrentRow(0)

    def _current(self) -> sfx.Sample | None:
        if self._bank is None:
            return None
        row = self.list.currentRow()
        if 0 <= row < len(self._bank.samples):
            return self._bank.samples[row]
        return None

    def _describe(self, _row: int) -> None:
        sample = self._current()
        if sample is None or self._bank is None:
            return
        tones = [
            (program.index, slot, tone)
            for program in self._bank.programs
            for slot, tone in enumerate(program.tones)
            if tone.vag == sample.index
        ]
        lines = [
            f"Sample {sample.index}",
            "",
            f"ADPCM:    {_human(len(sample.data))}  ({len(sample.data) // 16} blocks)",
            f"Decoded:  {sample.frame_count} frames, {sample.duration:.3f} s",
            f"Rate:     {sample.rate} Hz",
            "",
            f"Used by {len(tones)} tone(s):",
        ]
        for program_index, slot, tone in tones[:12]:
            lines.append(
                f"  program {program_index:3d} tone {slot:2d} — centre note "
                f"{tone.centre_note}, vol {tone.volume}, pan {tone.pan}, "
                f"notes {tone.min_note}..{tone.max_note}"
            )
        self.text.setPlainText("\n".join(lines))

    # -- playback --------------------------------------------------------

    def play(self) -> None:
        sample = self._current()
        if sample is None:
            return
        try:
            from PySide6.QtCore import QBuffer, QByteArray, QIODevice
            from PySide6.QtMultimedia import QAudioFormat, QAudioSink
        except ImportError:
            self.text.setPlainText(
                "Playback needs QtMultimedia:\n\n    pip install PySide6-Addons\n\n"
                "Exporting to WAV works without it."
            )
            return

        self.stop()
        fmt = QAudioFormat()
        fmt.setSampleRate(sample.rate)
        fmt.setChannelCount(1)
        fmt.setSampleFormat(QAudioFormat.Int16)

        # Both objects have to outlive this call: the sink pulls from the buffer
        # on its own thread, and a garbage-collected buffer plays silence.
        self._buffer = QBuffer(self)
        self._buffer.setData(QByteArray(sample.decode()))
        self._buffer.open(QIODevice.ReadOnly)
        self._sink = QAudioSink(fmt, self)
        self._sink.start(self._buffer)

    def stop(self) -> None:
        if self._sink is not None:
            self._sink.stop()
            self._sink = None
        if self._buffer is not None:
            self._buffer.close()
            self._buffer = None


class HexView(QWidget):
    """Fallback preview for entries with no dedicated viewer."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.text = QPlainTextEdit(readOnly=True)
        font = self.text.font()
        font.setFamily("Menlo")
        self.text.setFont(font)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.addWidget(self.text)

    def set_data(self, data: bytes, header: str, limit: int = 4096) -> None:
        lines = [header, ""]
        view = data[:limit]
        for offset in range(0, len(view), 16):
            chunk = view[offset : offset + 16]
            hexed = " ".join(f"{b:02x}" for b in chunk).ljust(47)
            ascii_ = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
            lines.append(f"{offset:08X}  {hexed}  {ascii_}")
        if len(data) > limit:
            lines.append(f"... {_human(len(data) - limit)} more")
        self.text.setPlainText("\n".join(lines))


class ViewOptions(QWidget):
    """Render toggles for the 3D viewport."""

    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.solid = QCheckBox("Solid", checked=True)
        self.wireframe = QCheckBox("Wireframe")
        self.points = QCheckBox("Points")
        self.volumes = QCheckBox("Volumes")
        self.volumes.setToolTip(
            "The gameplay volume a mesh carries at +0x2C, drawn as the box its "
            "two horizontal extents describe. For a playable character this is "
            "its collision body -- Crash stands in a 128-unit half-width, and "
            "his spin mesh widens it to 307. Only 812 of the archive's meshes "
            "have one, and on the rest of that family the volume serves some "
            "purpose other than collision. Nothing found so far reads the block, "
            "so the shape is the record's reading, not a proven test volume."
        )
        self.textures = QCheckBox("Textures", checked=True)
        self.vertex_colours = QCheckBox("Vertex colours", checked=True)
        self.vertex_colours.setToolTip(
            "The three colours the file stores per triangle. They multiply into "
            "the texture, so switching them off is the only way to see a texture "
            "as it sits in the pack. They also carry the shading, so the viewport "
            "lights the model itself once they are off."
        )
        self.texture_animation = QCheckBox("Animate textures", checked=True)
        self.texture_animation.setToolTip(
            "Play the flipbooks the texture pack carries. They run on their own "
            "clock, independently of the model's animation, as they do in game."
        )
        self.reset = QPushButton("Reset view (F)")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 2, 6, 2)
        for widget in (
            self.solid,
            self.wireframe,
            self.points,
            self.volumes,
            self.textures,
            self.vertex_colours,
            self.texture_animation,
        ):
            widget.toggled.connect(self.changed)
            layout.addWidget(widget)
        layout.addStretch(1)
        layout.addWidget(self.reset)


def _human(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / (1024 * 1024):.2f} MB"
