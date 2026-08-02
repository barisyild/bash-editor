"""OpenGL 3.3 core model viewport with an orbit camera."""

from __future__ import annotations

import ctypes
import math
from dataclasses import dataclass, field

import numpy as np
from PySide6.QtCore import QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QSurfaceFormat, QVector3D
from PySide6.QtOpenGL import QOpenGLShader, QOpenGLShaderProgram
from PySide6.QtOpenGLWidgets import QOpenGLWidget
from OpenGL import GL

from crashbash.formats.anim import Animation
from crashbash.formats.mdl import Mesh, Model
from crashbash.formats.tex import TICKS_PER_SECOND
from crashbash.scene import Scene, rotation_matrix

from .atlas import Atlas, build as build_atlas

VERTEX_SHADER = """
#version 330 core
layout(location = 0) in vec3 in_position;
layout(location = 1) in vec3 in_normal;
layout(location = 2) in vec3 in_color;
layout(location = 3) in vec2 in_uv;

uniform mat4 mvp;
uniform mat4 model;

out vec3 v_normal;
out vec3 v_color;
out vec3 v_world;
out vec2 v_uv;

void main() {
    v_normal = mat3(model) * in_normal;
    v_color = in_color;
    v_uv = in_uv;
    v_world = (model * vec4(in_position, 1.0)).xyz;
    gl_Position = mvp * vec4(in_position, 1.0);
}
"""

FRAGMENT_SHADER = """
#version 330 core
in vec3 v_normal;
in vec3 v_color;
in vec3 v_world;
in vec2 v_uv;

uniform vec3 view_pos;
uniform vec3 override_color;
uniform float use_override;
uniform float unlit;
uniform float shade;
uniform float use_texture;
uniform sampler2D atlas;

out vec4 frag;

void main() {
    vec3 base = mix(v_color, override_color, use_override);
    if (use_texture > 0.5) {
        vec4 texel = texture(atlas, v_uv);
        if (texel.a < 0.5) discard;
        // PS1 modulation: a colour of 128 leaves the texel unchanged. A third of
        // the triangles sit above that, so clamping here would blow them out
        // before the lighting below ever gets to scale them back down.
        base = texel.rgb * base * 2.0;
    }
    if (unlit > 0.5) {
        frag = vec4(clamp(base, 0.0, 1.0), 1.0);
        return;
    }
    vec3 n = normalize(v_normal);
    vec3 view = normalize(view_pos - v_world);
    // Two-sided: PS1 strips have inconsistent winding across seams, so lighting
    // a back-facing normal as if it were front-facing avoids black patches.
    if (dot(n, view) < 0.0) n = -n;

    vec3 key = normalize(vec3(0.45, 0.75, 0.55));
    vec3 fill = normalize(vec3(-0.6, 0.15, -0.4));
    float lighting = 0.30
        + 0.65 * max(dot(n, key), 0.0)
        + 0.22 * max(dot(n, fill), 0.0);
    float rim = pow(1.0 - max(dot(n, view), 0.0), 3.0) * 0.25;
    vec3 lit = base * lighting + rim;
    frag = vec4(clamp(mix(base, lit, shade), 0.0, 1.0), 1.0);
}
"""

# Distinct hues so neighbouring meshes stay tellable apart at a glance.
MESH_PALETTE = [
    (0.85, 0.72, 0.55), (0.55, 0.75, 0.85), (0.80, 0.60, 0.72),
    (0.65, 0.82, 0.62), (0.88, 0.78, 0.48), (0.70, 0.66, 0.88),
    (0.86, 0.62, 0.52), (0.58, 0.80, 0.76), (0.78, 0.80, 0.60),
    (0.72, 0.60, 0.80),
]


def configure_default_format() -> None:
    """Request a 3.3 core context. macOS only offers core profiles above 2.1."""
    fmt = QSurfaceFormat()
    fmt.setVersion(3, 3)
    fmt.setProfile(QSurfaceFormat.CoreProfile)
    fmt.setDepthBufferSize(24)
    fmt.setSamples(4)
    QSurfaceFormat.setDefaultFormat(fmt)


VERTEX_FLOATS = 11  # position 3, normal 3, colour 3, uv 2

# How closely an unowned mesh has to match an on-stage one before it is read as
# a spare take of the same character rather than scenery. A tenth separates the
# archive's 3 spare takes from its 132 unowned backdrops with room to spare.
STAND_IN_TOLERANCE = 0.12
VERTEX_BYTES = VERTEX_FLOATS * 4

# PS1 is Y-down / Z-into-screen; flip both to get a right-handed frame.
AXIS_FLIP = np.array([1.0, -1.0, -1.0], dtype=np.float32)


@dataclass
class MeshDraw:
    mesh_index: int
    first: int
    count: int
    line_first: int
    line_count: int
    visible: bool = True
    # Triangle corners as (n, 3) indices into the mesh's vertex pool. Kept so
    # an animation frame can refill these rows without going back through
    # `_shade`, which is a Python loop per triangle and the bulk of a rebuild.
    indices: np.ndarray | None = None
    # Vertices from `first` that the console draws opaque. The rest are its
    # blended ones, held back to a later pass -- see `_blend_group`.
    opaque_count: int = 0
    # ABR mode -> (offset from `first`, vertex count) for the blended runs.
    blend_spans: dict[int, tuple[int, int]] = field(default_factory=dict)


def _blend_group(model: Model, mesh: Mesh, face: int) -> int:
    """0 for an opaque triangle, else 1 + the ABR mode the console blends it in.

    Both come out of the triangle's colour index (§6.4): bit 15 turns the
    primitive's semi-transparency bit on, and bits 13-14 land in the tpage's ABR
    field, choosing which of the four blends the GPU runs. The polygon writer
    forces ABR 1 for the effect path and otherwise passes the field through:

        800178C8  andi  $v0, $v0, 0x8000  ; the triangle's translucent bit
        800178DC  or    $v1, $v1, $v0     ; code |= 0x02: semi-transparent
        800178E0  andi  $a0, $a0, 0xff9f  ; clear the tpage's ABR field
        800178E8  ori   $a0, $a0, 0x20    ;   ABR 1 -- B + F
        8001791C  or    $a0, $a0, $a3     ; or the triangle's own, from bits 13-14
    """
    if not model.face_is_translucent(mesh, face):
        return 0
    return 1 + model.face_blend_mode(mesh, face)


# Each ABR mode as (source factor, destination factor, constant, subtract).
# The console's B is the framebuffer -- GL's destination -- and its F the
# incoming pixel, so `B + F/4` is a quarter of the source and all of the
# destination. ABR 2 subtracts the source, which is an equation, not a factor.
_ABR_BLEND = {
    0: (GL.GL_CONSTANT_ALPHA, GL.GL_CONSTANT_ALPHA, 0.5, False),   # B/2 + F/2
    1: (GL.GL_ONE, GL.GL_ONE, 1.0, False),                          # B + F
    2: (GL.GL_ONE, GL.GL_ONE, 1.0, True),                           # B - F
    3: (GL.GL_CONSTANT_ALPHA, GL.GL_ONE, 0.25, False),              # B + F/4
}


def _face_normals(corners: np.ndarray) -> np.ndarray:
    """Unit normals for (n, 3, 3) triangle corners."""
    edge1 = corners[:, 1] - corners[:, 0]
    edge2 = corners[:, 2] - corners[:, 0]
    normals = np.cross(edge1, edge2)
    lengths = np.linalg.norm(normals, axis=1, keepdims=True)
    return np.divide(normals, np.maximum(lengths, 1e-9))


def _edge_pairs(corners: np.ndarray) -> np.ndarray:
    """Wireframe line endpoints for (n, 3, 3) triangle corners, as (6n, 3)."""
    return np.stack(
        [corners[:, [0, 1]], corners[:, [1, 2]], corners[:, [2, 0]]], axis=1
    ).reshape(-1, 3)


def _every_triangle(mesh: Mesh) -> list[tuple[int, int, int, int]]:
    """`Mesh.indexed_triangles` without the degenerate-triangle cull.

    That cull tests the static positions, but which corners coincide depends on
    the pose: over the 1036 resolvable clips the collapsed set differs from the
    static one in 133 clips and 13,864 of 49,151 frames. Most of that is
    harmless -- a triangle that collapses only in some pose rasterises to
    nothing on those frames -- but the other direction leaves a hole, and 31
    clips do drop a triangle that a later frame pulls apart again, up to 12 in
    one frame. Keeping every triangle makes the row layout pose-independent,
    which is what lets `_repose` write into a fixed span; the statically
    degenerate ones cost 316 rows in 275,275 across every animated mesh.
    """
    starts = [i for strip in mesh.strips for i in range(strip.start, strip.end - 2)]
    # `face` counts the whole walk in both functions, so the per-triangle
    # arrays stay addressable by the same index either way.
    return [(i, i + 1, i + 2, face) for face, i in enumerate(starts)]


def _drain_gl_errors() -> None:
    """Clear the GL error flag before issuing our own calls.

    Qt makes GL calls of its own (shader compilation, the widget's backing FBO)
    and does not always clear the error it leaves behind. PyOpenGL checks the
    flag after every call, so a stale flag surfaces as a bogus failure on
    whichever call of ours happens to run next.
    """
    for _ in range(16):
        if GL.glGetError() == GL.GL_NO_ERROR:
            return


def _identity() -> np.ndarray:
    return np.identity(4, dtype=np.float32)


def _perspective(fovy_deg: float, aspect: float, near: float, far: float) -> np.ndarray:
    f = 1.0 / math.tan(math.radians(fovy_deg) / 2.0)
    m = np.zeros((4, 4), dtype=np.float32)
    m[0, 0] = f / max(aspect, 1e-6)
    m[1, 1] = f
    m[2, 2] = (far + near) / (near - far)
    m[2, 3] = (2 * far * near) / (near - far)
    m[3, 2] = -1.0
    return m


def _look_at(eye: np.ndarray, target: np.ndarray, up: np.ndarray) -> np.ndarray:
    forward = target - eye
    forward /= max(np.linalg.norm(forward), 1e-9)
    side = np.cross(forward, up)
    side /= max(np.linalg.norm(side), 1e-9)
    true_up = np.cross(side, forward)
    m = _identity()
    m[0, :3], m[1, :3], m[2, :3] = side, true_up, -forward
    m[:3, 3] = -m[:3, :3] @ eye
    return m


class ModelView(QOpenGLWidget):
    """Draws a parsed MDL. Left-drag orbits, right/middle-drag pans, wheel zooms."""

    status_changed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(320, 240)
        self.setFocusPolicy(Qt.StrongFocus)

        self._model: Model | None = None
        self._program: QOpenGLShaderProgram | None = None
        # Raw GL names rather than QOpenGLVertexArrayObject/QOpenGLBuffer: the Qt
        # wrappers do not bind into the context PyOpenGL draws through, so the
        # core-profile draw call fails with no VAO bound.
        self._vao = 0
        self._vbo = 0
        self._draws: list[MeshDraw] = []
        # Kept apart from the draw list, which is thrown away and rebuilt
        # whenever the colour source changes -- visibility must survive that.
        self._hidden: set[int] = set()
        self._vertex_data = np.zeros((0, VERTEX_FLOATS), dtype=np.float32)
        self._dirty = False
        self._animation: Animation | None = None
        self._frame = 0
        # Scene playback: every actor posed and placed at one scene tick, seen
        # through the shot's own camera. Mutually exclusive with a single clip.
        self._scene = None
        self._scene_clips: list = []
        self._scene_tick = 0
        self._stand_ins: set[int] = set()
        # Row spans whose position/normal columns have been rewritten in place
        # and still have to reach the VBO. Drained in paintGL rather than at the
        # call site so the widget's context is guaranteed current.
        self._pose_pending: list[tuple[int, int]] = []
        self._pack = None
        self._atlas: Atlas = build_atlas(None)
        self._atlas_texture = 0
        self._atlas_dirty = True
        # Texture animation runs on its own clock: a flipbook keeps going while
        # the model is paused, because on the console the two are unrelated.
        self._texture_tick = 0.0
        self._texture_patches_pending = False
        self.show_texture_animation = True
        self._texture_timer = QTimer(self)
        self._texture_timer.setInterval(round(1000.0 / TICKS_PER_SECOND))
        self._texture_timer.timeout.connect(self._advance_textures)

        self._center = np.zeros(3, dtype=np.float32)
        self._radius = 1.0
        self.yaw = math.radians(35.0)
        self.pitch = math.radians(18.0)
        self.distance = 3.0
        self._pan = np.zeros(3, dtype=np.float32)
        self._last_pos: QPoint | None = None
        self._last_button = Qt.NoButton

        self.show_wireframe = False
        self.show_solid = True
        self.show_points = False
        # The file's own per-vertex colours. Switching them off leaves the
        # neutral 0.5 the PS1 blend ignores, which is what shows a texture raw.
        self.show_vertex_colours = True
        self.show_textures = True

    # -- model ----------------------------------------------------------

    def set_model(self, model: Model | None, pack=None) -> None:
        """Show `model`, textured with `pack` (its sibling .tex) when given."""
        self._model = model
        self._pack = pack
        self._hidden.clear()
        self._animation = None
        self._frame = 0
        self._scene = None
        self._scene_clips = []
        self._scene_tick = 0
        self._stand_ins = set()
        self._atlas = build_atlas(pack)
        self._atlas_dirty = True
        self._texture_tick = 0.0
        self._texture_patches_pending = False
        self._sync_texture_timer()
        self._rebuild()
        self.reset_view()
        self.update()

    def has_texture_animation(self) -> bool:
        return bool(self._pack is not None and self._pack.flipbooks)

    def set_texture_tick(self, tick: float) -> None:
        """Advance the flipbooks to `tick`, in game ticks at 30 Hz."""
        if not self.has_texture_animation() or tick == self._texture_tick:
            return
        self._texture_tick = tick
        self._texture_patches_pending = True
        self.update()

    def set_texture_animation(self, enabled: bool) -> None:
        self.show_texture_animation = enabled
        self._sync_texture_timer()
        if not enabled:
            self.set_texture_tick(0.0)

    def _sync_texture_timer(self) -> None:
        if self.show_texture_animation and self.has_texture_animation():
            self._texture_timer.start()
        else:
            self._texture_timer.stop()

    def _advance_textures(self) -> None:
        self.set_texture_tick(self._texture_tick + 1.0)

    def mesh_visibility(self) -> list[bool]:
        return [d.visible for d in self._draws]

    # -- animation ------------------------------------------------------

    def set_animation(self, animation: Animation | None, frame: int = 0) -> None:
        """Pose the model with `animation`, or restore the static pose with None.

        Changing clip rebuilds, because a different clip may drive a different
        mesh -- the one it stops driving has to go back to its static pose, and
        the one it takes over needs its degenerate triangles reinstated. That is
        a click, not a tick, so the cost does not matter; `set_frame` is the hot
        path and only refills positions.
        """
        self._animation = animation
        self._frame = max(0, frame) if animation is not None else 0
        self._rebuild()
        # The clip's own extent has just gone into the framing radius, so pull
        # back to it. Orbit angles survive, so switching clips does not throw
        # away the angle the user chose.
        self.frame_model()

    def set_scene(self, scene: Scene | None, clips=None, tick: int | None = None) -> None:
        """Play the model's whole cutscene rather than one clip.

        A full rebuild, because every actor's mesh changes at once and the
        placement moves them outside the framing radius the single-clip path
        assumes.
        """
        self._scene = scene
        self._scene_clips = list(clips or [])
        self._stand_ins = self._find_stand_ins(scene)
        if scene is not None:
            self._animation = None
            self._scene_tick = scene.start if tick is None else tick
        self._rebuild()
        self.frame_model()

    def _find_stand_ins(self, scene: Scene | None) -> set[int]:
        """Meshes that are another take of someone already on stage.

        A shot's node graph does not account for everything drawn: the backdrop
        domes carry no node and the cutscene player puts them up anyway, so an
        unowned mesh cannot simply be treated as absent. But a file also carries
        spare versions of its cast -- `level_intro_crashplain` holds three Crash
        meshes and spawns one, asleep at 0.6 scale, and drawing the other two
        stood a full-size Crash beside him.

        What tells them apart is that a spare take is the same size as the
        character that is on stage, to within a tenth, while a backdrop is
        nothing like anything. Over the archive that separates the 3 spare takes
        from all 132 unowned backdrops and set pieces without a single
        misplacement -- but it is a measurement, not something the file states.
        """
        model = self._model
        if scene is None or model is None or not scene.actors:
            return set()
        sizes = []
        for index in sorted(scene.mesh_indices):
            if index < len(model.meshes) and model.meshes[index].positions:
                points = np.asarray(model.meshes[index].positions, dtype=np.float64)
                sizes.append(points.max(axis=0) - points.min(axis=0))
        if not sizes:
            return set()
        stand_ins = set()
        for mesh in model.meshes:
            if mesh.index in scene.mesh_indices or not mesh.positions:
                continue
            points = np.asarray(mesh.positions, dtype=np.float64)
            extent = points.max(axis=0) - points.min(axis=0)
            if any(np.all(np.abs(extent - other)
                          <= STAND_IN_TOLERANCE * np.maximum(extent, other) + 1e-6)
                   for other in sizes):
                stand_ins.add(mesh.index)
        return stand_ins

    def set_scene_tick(self, tick: int) -> None:
        """Scrub the scene clock. Every actor reposes; the camera follows."""
        if self._scene is None or tick == self._scene_tick:
            return
        self._scene_tick = int(tick)
        self._rebuild()
        self.update()

    @property
    def scene_tick(self) -> int:
        return self._scene_tick

    def set_frame(self, frame: int) -> None:
        """Scrub to a frame of the current clip. Cheap enough to call per tick."""
        animation = self._animation
        if animation is None or not animation.frame_count:
            return
        frame = max(0, min(int(frame), animation.frame_count - 1))
        if frame == self._frame:
            return
        self._frame = frame
        self._repose()
        self.update()

    @property
    def frame(self) -> int:
        return self._frame

    def _pose_positions(self, mesh: Mesh) -> np.ndarray:
        """Vertex positions for one mesh in viewport axes, animated or static.

        The clip's decoded pose replaces the static pool outright -- the game
        never writes it back into the mesh -- so the animated mesh is the only
        one that differs from the file.

        Under scene playback the mesh is posed by whichever actor is on stage
        and then moved into place by that actor's track, because a scene keeps
        its cast at the origin and positions them from the object graph.

        The length check is belt and braces: a clip's vertex count matches its
        mesh in every clip that names one, so the fallback should never fire,
        but a short pose would otherwise index out of the array.
        """
        if self._scene is not None:
            placed = self._scene_positions(mesh)
            if placed is not None:
                return placed
        animation = self._animation
        if animation is not None and animation.mesh_index == mesh.index:
            pose = animation.pose_array(min(self._frame, animation.frame_count - 1))
            if pose.shape[0] >= len(mesh.positions):
                return pose[: len(mesh.positions)] * AXIS_FLIP
        return np.asarray(mesh.positions, dtype=np.float32) * AXIS_FLIP

    def _scene_positions(self, mesh: Mesh) -> np.ndarray | None:
        """This mesh posed and placed for the current scene tick, or None.

        A mesh with a node is visible only while one of its windows is open,
        the way the game clears the entity's draw bit outside it; off stage it
        collapses to a point rather than standing at the origin in the middle
        of the set.

        A mesh with no node is scenery, and stays where the file put it. The
        node graph is not the whole picture -- a shot's backdrop domes carry no
        node and the cutscene player raises them anyway -- so the only unowned
        meshes held back are spare takes of the cast; see `_find_stand_ins`.
        """
        scene = self._scene
        if mesh.index not in scene.mesh_indices:
            if mesh.index in self._stand_ins:
                return np.zeros((len(mesh.positions), 3), dtype=np.float32)
            return None
        tick = self._scene_tick
        for actor in scene.actors_at(tick):
            if actor.mesh_index != mesh.index:
                continue
            clip = self._scene_clips[actor.clip_index]
            pose = clip.pose_array(actor.frame(tick, clip.frame_count))
            if pose.shape[0] < len(mesh.positions):
                break
            position, rotation, scale = actor.track.at(tick)
            placed = (pose[: len(mesh.positions)] * scale) \
                @ rotation_matrix(rotation).T + position
            return (placed * AXIS_FLIP).astype(np.float32)
        for prop in scene.props_at(tick):
            if prop.mesh_index != mesh.index:
                continue
            position, rotation, scale = prop.track.at(tick)
            rest = np.asarray(mesh.positions, dtype=np.float64)
            placed = (rest * scale) @ rotation_matrix(rotation).T + position
            return (placed * AXIS_FLIP).astype(np.float32)
        return np.zeros((len(mesh.positions), 3), dtype=np.float32)

    def _repose(self) -> None:
        """Refill the animated mesh's position and normal columns for `_frame`.

        Only that mesh's rows are touched, and the colour and UV columns are
        left alone: they are per-triangle and a pose change cannot alter them.
        A full rebuild costs 28 ms on the heaviest animated model in the
        archive, which no amount of frame rate hides.
        """
        animation = self._animation
        if animation is None or animation.mesh_index is None or self._model is None:
            return
        draw = next(
            (d for d in self._draws if d.mesh_index == animation.mesh_index), None
        )
        if draw is None or draw.indices is None or not draw.count:
            return
        mesh = self._model.meshes[animation.mesh_index]

        corners = self._pose_positions(mesh)[draw.indices]
        normals = _face_normals(corners)
        data = self._vertex_data

        rows = slice(draw.first, draw.first + draw.count)
        data[rows, 0:3] = corners.reshape(-1, 3)
        data[rows, 3:6] = np.repeat(normals, 3, axis=0)
        self._pose_pending.append((draw.first, draw.count))

        if draw.line_count:
            rows = slice(draw.line_first, draw.line_first + draw.line_count)
            data[rows, 0:3] = _edge_pairs(corners)
            data[rows, 3:6] = np.repeat(normals, 6, axis=0)
            self._pose_pending.append((draw.line_first, draw.line_count))

    def set_textured(self, enabled: bool) -> None:
        """Costs a rebuild: the swatch texel is folded into the vertex buffer."""
        if enabled == self.show_textures:
            return
        self.show_textures = enabled
        self._rebuild()
        self.update()

    def set_vertex_colours(self, enabled: bool) -> None:
        """Colours live in the vertex buffer, so this costs a rebuild too."""
        if enabled == self.show_vertex_colours:
            return
        self.show_vertex_colours = enabled
        self._rebuild()
        self.update()

    def set_mesh_visible(self, mesh_index: int, visible: bool) -> None:
        if visible:
            self._hidden.discard(mesh_index)
        else:
            self._hidden.add(mesh_index)
        for draw in self._draws:
            if draw.mesh_index == mesh_index:
                draw.visible = visible
        self.update()

    def set_all_meshes_visible(self, visible: bool) -> None:
        if visible:
            self._hidden.clear()
        else:
            self._hidden = {draw.mesh_index for draw in self._draws}
        for draw in self._draws:
            draw.visible = visible
        self.update()

    def _shade(self, mesh, triangles) -> tuple[np.ndarray, np.ndarray]:
        """Per-vertex colours and atlas UVs for one mesh's triangles.

        Three cases, in the order the game decides them:

        * untextured strip -- a plain gouraud triangle, so the three vertex
          colours are the whole answer and the UV points at the neutral texel.
        * ordinary texture -- the atlas supplies the texel and the shader applies
          the PS1 blend, texel * colour / 128.
        * swatch -- the triangle samples the pack's palette-less texture at a
          single texel, with the palette chosen per triangle. There is no atlas
          entry for that combination, so the texel is resolved here and folded
          into the vertex colours, again against the neutral texel.

        With `show_vertex_colours` off, every colour becomes the neutral 0.5 that
        the PS1 blend leaves alone, so a textured triangle shows its raw texels
        and an untextured one shows plain grey. That is the only way to see a
        texture as it sits in the pack: the vertex colour multiplies into it
        whatever else is switched on.

        The swatch texel is a texture sample like any other, even though it is
        resolved here rather than in the shader, so `show_textures` governs it
        too -- otherwise the flat-coloured triangles, which are most of a
        character, keep their palette colour with both switches off.
        """
        model = self._model
        count = len(triangles)
        colours = np.empty((count * 3, 3), dtype=np.float32)
        uvs = np.empty((count * 3, 2), dtype=np.float32)
        neutral = self._atlas.neutral_uv()
        # 0.5 is the blend's identity: the shader doubles it back to 1.0.
        NEUTRAL_COLOUR = 0.5

        swatch = None
        if self._pack is not None:
            swatch = next((t for t in self._pack.textures if t.is_swatch), None)
        swatch_cells = swatch.indices() if swatch is not None else None

        tinted = self.show_vertex_colours and model is not None and model.colours

        for row, (*_, face) in enumerate(triangles):
            span = slice(row * 3, row * 3 + 3)

            if tinted:
                triple = model.face_colours(mesh, face)
                base = (
                    np.array(triple, dtype=np.float32) / 255.0
                    if triple
                    else np.full((3, 3), 0.75, dtype=np.float32)
                )
            else:
                base = np.full((3, 3), NEUTRAL_COLOUR, dtype=np.float32)

            sampling = model.face_sampling(mesh, face) if model else None
            kind, index = sampling if sampling else ("none", 0)
            texel_uvs = model.face_uvs(mesh, face) if model else None

            if kind == "texture" and texel_uvs is not None:
                for k, (u, v) in enumerate(texel_uvs):
                    uvs[row * 3 + k] = self._atlas.uv(index, u, v)
                colours[span] = base
                continue

            uvs[span] = neutral
            if (
                kind == "swatch"
                and self.show_textures
                and swatch_cells is not None
                and texel_uvs is not None
                and index < len(self._pack.palettes)
            ):
                u, v = texel_uvs[0]
                cell = int(swatch_cells[min(v, swatch.height - 1), min(u, swatch.width - 1)])
                palette = self._pack.palettes[index]
                if cell < palette.shape[0]:
                    texel = palette[cell][:3].astype(np.float32) / 255.0
                    # The neutral texel is 0.5 and the shader doubles it, so the
                    # blend has to be pre-applied here to survive that round trip.
                    base = base * texel * 2.0
            colours[span] = base

        return colours, uvs

    def _rebuild(self) -> None:
        self._draws = []
        self._pose_pending = []
        if self._model is None or not self._model.meshes:
            self._vertex_data = np.zeros((0, VERTEX_FLOATS), dtype=np.float32)
            self._dirty = True
            return

        animated = self._animation.mesh_index if self._animation else None

        tri_chunks: list[np.ndarray] = []
        line_chunks: list[np.ndarray] = []
        pending: list[tuple[int, int, int, np.ndarray | None]] = []

        for mesh in self._model.meshes:
            triangles_indexed = (
                _every_triangle(mesh)
                if mesh.index == animated
                else mesh.indexed_triangles()
            )
            if not triangles_indexed:
                pending.append((mesh.index, 0, 0, None, 0, {}))
                continue

            # Sorted opaque first, then one contiguous run per blend mode, so a
            # mesh stays a single span and each later pass is a slice of it.
            groups: dict[int, list] = {}
            for t in triangles_indexed:
                groups.setdefault(_blend_group(self._model, mesh, t[3]), []).append(t)
            triangles_indexed = []
            spans: dict[int, tuple[int, int]] = {}
            for group in sorted(groups):
                start = 3 * len(triangles_indexed)
                triangles_indexed.extend(groups[group])
                if group:
                    spans[group - 1] = (start, 3 * len(groups[group]))
            opaque_count = 3 * len(groups.get(0, ()))

            positions = self._pose_positions(mesh)
            idx = np.asarray([t[:3] for t in triangles_indexed], dtype=np.int32)

            corners = positions[idx]  # (n, 3, 3)
            normals = _face_normals(corners)

            # Wireframe keeps a per-mesh hue so the mesh split stays readable
            # even when the surface itself is drawn from the file's colours.
            hue = np.array(
                MESH_PALETTE[mesh.index % len(MESH_PALETTE)], dtype=np.float32
            )
            color_rows, uv_rows = self._shade(mesh, triangles_indexed)

            flat = corners.reshape(-1, 3)
            normal_rows = np.repeat(normals, 3, axis=0)
            tri_chunks.append(np.hstack([flat, normal_rows, color_rows, uv_rows]))

            # Wireframe as explicit line pairs; GL core has no polygon-mode edges
            # worth relying on across drivers.
            pairs = _edge_pairs(corners)
            line_normals = np.repeat(normals, 6, axis=0)
            line_colors = np.tile(hue * 0.35 + 0.25, (pairs.shape[0], 1))
            line_uvs = np.tile(
                np.array(self._atlas.neutral_uv(), dtype=np.float32), (pairs.shape[0], 1)
            )
            line_chunks.append(
                np.hstack([pairs, line_normals, line_colors, line_uvs])
            )

            pending.append((mesh.index, flat.shape[0], pairs.shape[0], idx,
                            opaque_count, spans))

        empty = np.zeros((0, VERTEX_FLOATS), dtype=np.float32)
        tri_data = np.vstack(tri_chunks) if tri_chunks else empty
        line_data = np.vstack(line_chunks) if line_chunks else empty

        tri_cursor = 0
        line_cursor = tri_data.shape[0]
        for mesh_index, tri_count, line_count, idx, opaque_count, spans in pending:
            self._draws.append(
                MeshDraw(
                    mesh_index,
                    tri_cursor,
                    tri_count,
                    line_cursor,
                    line_count,
                    visible=mesh_index not in self._hidden,
                    indices=idx,
                    opaque_count=opaque_count,
                    blend_spans=spans,
                )
            )
            tri_cursor += tri_count
            line_cursor += line_count

        self._vertex_data = np.ascontiguousarray(
            np.vstack([tri_data, line_data]), dtype=np.float32
        )
        self._dirty = True

        bounds = self._model.bounds
        cast = self._cast_extent()
        if cast is not None:
            # Watching a scene, frame the cast rather than the set. A cutscene
            # carries its sky as a mesh 50 units across around actors barely two
            # tall, so the file's own bounds pull the view back until nobody on
            # stage is bigger than a speck.
            lo, hi = cast
            self._center = ((lo + hi) / 2).astype(np.float32)
            self._radius = max(float(np.linalg.norm(hi - lo) / 2), 1e-3)
        elif bounds is not None:
            cx, cy, cz = bounds.center
            self._center = np.array([cx, -cy, -cz], dtype=np.float32)
            self._radius = max(float(bounds.radius), 1e-3)
        elif tri_data.shape[0]:
            lo = tri_data[:, :3].min(axis=0)
            hi = tri_data[:, :3].max(axis=0)
            self._center = ((lo + hi) / 2).astype(np.float32)
            self._radius = max(float(np.linalg.norm(hi - lo) / 2), 1e-3)

        extent = None if cast is not None else self._clip_extent()
        if extent is not None:
            lo = np.minimum(extent[0], self._center - self._radius)
            hi = np.maximum(extent[1], self._center + self._radius)
            self._center = ((lo + hi) / 2).astype(np.float32)
            self._radius = max(float(np.linalg.norm(hi - lo) / 2), 1e-3)

    def _cast_extent(self) -> tuple[np.ndarray, np.ndarray] | None:
        """Viewport-space box around everything a node puts on stage, over the
        whole scene, or None when no scene is playing.

        The whole scene rather than the current tick, so the view holds still
        while it plays instead of breathing with whoever is on screen.
        """
        scene, model = self._scene, self._model
        if scene is None or model is None:
            return None
        low: list[np.ndarray] = []
        high: list[np.ndarray] = []
        for actor in scene.actors:
            # Actors are the subject. Props are included only when a scene has
            # no actors at all: `level_ending_evil_shot4` throws debris twenty
            # units wide, and framing that pulls the view outside the sky dome,
            # which then fills the screen with its own back face.
            mesh = model.meshes[actor.mesh_index]
            clip = self._scene_clips[actor.clip_index]
            rest = np.asarray(mesh.positions, dtype=np.float64)
            for key in actor.track.keys:
                placed = (rest * key.scale) @ rotation_matrix(key.rotation).T \
                    + key.position
                low.append(placed.min(axis=0))
                high.append(placed.max(axis=0))
            # A clip can carry a limb well outside the rest pose, so let the
            # extremes of the animation widen the box too.
            span = clip.pose_array(0)
            if span.shape[0] >= len(mesh.positions):
                low.append(span[: len(mesh.positions)].min(axis=0))
                high.append(span[: len(mesh.positions)].max(axis=0))
        if not low:
            for prop in scene.props:
                rest = np.asarray(model.meshes[prop.mesh_index].positions,
                                  dtype=np.float64)
                if not len(rest):
                    continue
                for key in prop.track.keys:
                    placed = (rest * key.scale) @ rotation_matrix(key.rotation).T \
                        + key.position
                    low.append(placed.min(axis=0))
                    high.append(placed.max(axis=0))
        if not low:
            return None
        return (np.minimum.reduce(low) * AXIS_FLIP,
                np.maximum.reduce(high) * AXIS_FLIP)

    def _clip_extent(self) -> tuple[np.ndarray, np.ndarray] | None:
        """Viewport-space box covering every keyframe of the current clip.

        Framing on the model's own bounds loses a fifth of the library: 230 of
        the 1036 resolvable clips reach further from the model centre than its
        bounds radius, and the FALL clips drop the character up to 8.75 radii
        out of shot. Keyframe boxes are cheap to read and accurate enough to
        aim a camera with -- they are the pose's exact AABB in 11,876 of 13,652
        keyframes and within 2/256 of a unit in all but one.
        """
        animation = self._animation
        if animation is None or animation.mesh_index is None:
            return None
        keyframes = animation.keyframes()
        if not keyframes:
            return None
        corners = []
        for offset in keyframes:
            box = animation.keyframe_bounds(offset)
            corners.append(np.array(box.min, dtype=np.float32) * AXIS_FLIP)
            corners.append(np.array(box.max, dtype=np.float32) * AXIS_FLIP)
        stacked = np.vstack(corners)
        return stacked.min(axis=0), stacked.max(axis=0)

    # -- camera ---------------------------------------------------------

    def frame_model(self) -> None:
        """Pull back far enough to see everything, leaving the orbit angles be."""
        self._pan = np.zeros(3, dtype=np.float32)
        self.distance = self._radius * 2.8
        # A scene's backdrop is a mesh wrapped around the whole set, so backing
        # out past it turns the view into a solid ball of its own outside face.
        # Stay inside whatever encloses the shot.
        enclosing = self._enclosing_radius()
        if enclosing is not None:
            self.distance = min(self.distance, enclosing * 0.85)
        self.update()

    def _enclosing_radius(self) -> float | None:
        """Distance from the framed centre to the nearest mesh that wraps it.

        Only meaningful while a scene is playing; scenery a node never places
        stays where the file put it, so the test is against its own extent.
        """
        if self._scene is None or self._model is None:
            return None
        owned = self._scene.mesh_indices
        radii = []
        for mesh in self._model.meshes:
            if mesh.index in owned or not mesh.positions:
                continue
            points = np.asarray(mesh.positions, dtype=np.float64) * AXIS_FLIP
            lo, hi = points.min(axis=0), points.max(axis=0)
            if not ((lo < self._center) & (self._center < hi)).all():
                continue  # does not wrap the shot -- it is set dressing
            radii.append(float(np.abs(np.concatenate(
                [hi - self._center, self._center - lo])).min()))
        return min(radii) if radii else None

    def reset_view(self) -> None:
        self.yaw = math.radians(35.0)
        self.pitch = math.radians(18.0)
        self.frame_model()

    def _eye(self) -> np.ndarray:
        cp = math.cos(self.pitch)
        offset = np.array(
            [
                math.sin(self.yaw) * cp,
                math.sin(self.pitch),
                math.cos(self.yaw) * cp,
            ],
            dtype=np.float32,
        )
        return self._center + self._pan + offset * self.distance

    # -- GL -------------------------------------------------------------

    def initializeGL(self) -> None:
        _drain_gl_errors()
        GL.glClearColor(0.09, 0.10, 0.12, 1.0)
        GL.glEnable(GL.GL_DEPTH_TEST)
        GL.glEnable(GL.GL_MULTISAMPLE)

        program = QOpenGLShaderProgram(self)
        program.addShaderFromSourceCode(QOpenGLShader.Vertex, VERTEX_SHADER)
        program.addShaderFromSourceCode(QOpenGLShader.Fragment, FRAGMENT_SHADER)
        if not program.link():
            self.status_changed.emit(f"Shader link failed: {program.log()}")
            return
        self._program = program
        _drain_gl_errors()

        self._vao = int(GL.glGenVertexArrays(1))
        self._vbo = int(GL.glGenBuffers(1))
        self._atlas_texture = int(GL.glGenTextures(1))
        self._upload_atlas()
        self._upload()

    def _upload(self) -> None:
        if not self._vao or not self._vbo:
            return
        _drain_gl_errors()
        GL.glBindVertexArray(self._vao)
        GL.glBindBuffer(GL.GL_ARRAY_BUFFER, self._vbo)
        payload = self._vertex_data.tobytes()
        GL.glBufferData(
            GL.GL_ARRAY_BUFFER, len(payload), payload or None, GL.GL_STATIC_DRAW
        )
        stride = VERTEX_FLOATS * 4
        for location, size, offset in (
            (0, 3, 0),
            (1, 3, 3 * 4),
            (2, 3, 6 * 4),
            (3, 2, 9 * 4),
        ):
            GL.glEnableVertexAttribArray(location)
            GL.glVertexAttribPointer(
                location, size, GL.GL_FLOAT, GL.GL_FALSE, stride, ctypes.c_void_p(offset)
            )
        GL.glBindBuffer(GL.GL_ARRAY_BUFFER, 0)
        GL.glBindVertexArray(0)
        self._dirty = False
        self._pose_pending = []

    def _upload_pose(self) -> None:
        """Push the rows `_repose` rewrote, leaving the rest of the VBO alone.

        Whole rows rather than just their first six floats: the colour and UV
        columns in them are still correct, and one contiguous transfer beats a
        strided one per vertex.
        """
        if not self._vbo or not self._pose_pending:
            return
        GL.glBindBuffer(GL.GL_ARRAY_BUFFER, self._vbo)
        for first, count in self._pose_pending:
            chunk = np.ascontiguousarray(self._vertex_data[first : first + count])
            payload = chunk.tobytes()
            GL.glBufferSubData(
                GL.GL_ARRAY_BUFFER, first * VERTEX_BYTES, len(payload), payload
            )
        GL.glBindBuffer(GL.GL_ARRAY_BUFFER, 0)
        self._pose_pending = []

    def _upload_atlas(self) -> None:
        if not self._atlas_texture:
            return
        image = np.ascontiguousarray(self._atlas.image)
        height, width = image.shape[0], image.shape[1]
        GL.glBindTexture(GL.GL_TEXTURE_2D, self._atlas_texture)
        GL.glPixelStorei(GL.GL_UNPACK_ALIGNMENT, 1)
        GL.glTexImage2D(
            GL.GL_TEXTURE_2D, 0, GL.GL_RGBA, width, height, 0,
            GL.GL_RGBA, GL.GL_UNSIGNED_BYTE, image.tobytes(),
        )
        # Nearest sampling and clamping: PS1 textures are tiny and unfiltered,
        # and neighbouring atlas entries must never bleed in.
        for name, value in (
            (GL.GL_TEXTURE_MIN_FILTER, GL.GL_NEAREST),
            (GL.GL_TEXTURE_MAG_FILTER, GL.GL_NEAREST),
            (GL.GL_TEXTURE_WRAP_S, GL.GL_CLAMP_TO_EDGE),
            (GL.GL_TEXTURE_WRAP_T, GL.GL_CLAMP_TO_EDGE),
        ):
            GL.glTexParameteri(GL.GL_TEXTURE_2D, name, value)
        GL.glBindTexture(GL.GL_TEXTURE_2D, 0)
        self._atlas_dirty = False
        self._texture_patches_pending = self.has_texture_animation()

    def _upload_texture_patches(self) -> None:
        """Re-upload just the rectangles the flipbooks own."""
        patches = self._atlas.flipbook_patches(self._pack, self._texture_tick)
        if patches:
            GL.glBindTexture(GL.GL_TEXTURE_2D, self._atlas_texture)
            GL.glPixelStorei(GL.GL_UNPACK_ALIGNMENT, 1)
            for (x, y, w, h), image in patches:
                if image.shape[0] != h or image.shape[1] != w:
                    continue
                GL.glTexSubImage2D(
                    GL.GL_TEXTURE_2D, 0, x, y, w, h,
                    GL.GL_RGBA, GL.GL_UNSIGNED_BYTE,
                    np.ascontiguousarray(image).tobytes(),
                )
            GL.glBindTexture(GL.GL_TEXTURE_2D, 0)
        self._texture_patches_pending = False

    def paintGL(self) -> None:
        _drain_gl_errors()
        GL.glClear(GL.GL_COLOR_BUFFER_BIT | GL.GL_DEPTH_BUFFER_BIT)
        if self._program is None or not self._vao:
            return
        if self._atlas_dirty:
            self._upload_atlas()
        if self._texture_patches_pending:
            self._upload_texture_patches()
        if self._dirty:
            self._upload()
        elif self._pose_pending:
            self._upload_pose()
        if not self._vertex_data.size:
            return

        aspect = self.width() / max(self.height(), 1)
        eye = self._eye()
        target = self._center + self._pan
        up = np.array([0.0, 1.0, 0.0], dtype=np.float32)
        near = max(self._radius * 0.01, 1e-3)
        far = max(self.distance + self._radius * 6.0, near * 10)
        view = _look_at(eye, target, up)
        mvp = _perspective(45.0, aspect, near, far) @ view

        self._program.bind()
        self._program.setUniformValue("mvp", _to_qmatrix(mvp))
        self._program.setUniformValue("model", _to_qmatrix(_identity()))
        self._program.setUniformValue("view_pos", QVector3D(*eye.tolist()))
        self._program.setUniformValue("override_color", QVector3D(0.82, 0.80, 0.76))
        self._program.setUniformValue1f("use_override", 0.0)
        self._program.setUniformValue1i("atlas", 0)
        GL.glActiveTexture(GL.GL_TEXTURE0)
        GL.glBindTexture(GL.GL_TEXTURE_2D, self._atlas_texture)

        GL.glBindVertexArray(self._vao)
        if self.show_solid:
            # The file's colours already carry baked per-vertex shading -- they
            # range from near black to over 1.0 on the same limb -- so lighting
            # them again crushes the dark side to black. With them switched off
            # nothing carries that shading any more, and the surface needs the
            # viewport's own light to read as anything but a flat silhouette.
            self._program.setUniformValue1f("unlit", 0.0)
            self._program.setUniformValue1f(
                "shade", 0.0 if self.show_vertex_colours else 1.0
            )
            self._program.setUniformValue1f(
                "use_texture", 1.0 if self.show_textures else 0.0
            )
            # Qt hands paintGL a context with GL_BLEND already enabled, and
            # whatever function was last set stays set -- so the opaque pass has
            # to turn it off rather than assume it is off. Leaving that to the
            # caller drew every surface with the blend the effects had asked
            # for: a red character over grass came out yellow.
            GL.glDisable(GL.GL_BLEND)
            for draw in self._draws:
                if draw.visible and draw.opaque_count:
                    GL.glDrawArrays(GL.GL_TRIANGLES, draw.first, draw.opaque_count)

            # The console's blended triangles, after every opaque surface and
            # without writing depth, so a glow lights what is behind it and
            # never hides the one beside it. One pass per ABR mode, since each
            # is a different blend equation.
            blended = [d for d in self._draws if d.visible and d.blend_spans]
            if blended:
                GL.glEnable(GL.GL_BLEND)
                GL.glDepthMask(GL.GL_FALSE)
                for abr in sorted({m for d in blended for m in d.blend_spans}):
                    source, dest, weight, subtract = _ABR_BLEND[abr]
                    GL.glBlendColor(weight, weight, weight, weight)
                    GL.glBlendEquation(
                        GL.GL_FUNC_REVERSE_SUBTRACT if subtract else GL.GL_FUNC_ADD
                    )
                    GL.glBlendFunc(source, dest)
                    for draw in blended:
                        span = draw.blend_spans.get(abr)
                        if span:
                            GL.glDrawArrays(GL.GL_TRIANGLES,
                                            draw.first + span[0], span[1])
                GL.glBlendEquation(GL.GL_FUNC_ADD)
                GL.glBlendFunc(GL.GL_SRC_ALPHA, GL.GL_ONE_MINUS_SRC_ALPHA)
                GL.glDepthMask(GL.GL_TRUE)
                GL.glDisable(GL.GL_BLEND)

        if self.show_wireframe or self.show_points:
            self._program.setUniformValue1f("unlit", 1.0)
            self._program.setUniformValue1f("shade", 1.0)
            self._program.setUniformValue1f("use_texture", 0.0)
            self._program.setUniformValue1f("use_override", 1.0)
            self._program.setUniformValue("override_color", QVector3D(0.35, 0.85, 0.95))
            if self.show_solid:
                # Nudge the overlay toward the camera so edges don't z-fight the
                # faces they belong to.
                GL.glEnable(GL.GL_POLYGON_OFFSET_LINE)
                GL.glPolygonOffset(-1.0, -1.0)
            else:
                GL.glDisable(GL.GL_DEPTH_TEST)
            for draw in self._draws:
                if not draw.visible or not draw.line_count:
                    continue
                if self.show_wireframe:
                    GL.glDrawArrays(GL.GL_LINES, draw.line_first, draw.line_count)
                if self.show_points:
                    GL.glDrawArrays(GL.GL_POINTS, draw.first, draw.count)
            GL.glDisable(GL.GL_POLYGON_OFFSET_LINE)
            GL.glEnable(GL.GL_DEPTH_TEST)

        GL.glBindVertexArray(0)
        self._program.release()

    def resizeGL(self, w: int, h: int) -> None:
        GL.glViewport(0, 0, max(w, 1), max(h, 1))

    # -- input ----------------------------------------------------------

    def mousePressEvent(self, event) -> None:
        self._last_pos = event.position().toPoint()
        self._last_button = event.button()

    def mouseMoveEvent(self, event) -> None:
        if self._last_pos is None:
            return
        pos = event.position().toPoint()
        dx = pos.x() - self._last_pos.x()
        dy = pos.y() - self._last_pos.y()
        self._last_pos = pos

        panning = self._last_button in (Qt.MiddleButton, Qt.RightButton) or (
            event.modifiers() & Qt.ShiftModifier
        )
        if panning:
            scale = self.distance * 0.0022
            right = np.array(
                [math.cos(self.yaw), 0.0, -math.sin(self.yaw)], dtype=np.float32
            )
            self._pan += right * (-dx * scale)
            self._pan += np.array([0.0, dy * scale, 0.0], dtype=np.float32)
        else:
            self.yaw -= dx * 0.01
            self.pitch = max(
                min(self.pitch + dy * 0.01, math.radians(89.0)), math.radians(-89.0)
            )
        self.update()

    def mouseReleaseEvent(self, event) -> None:
        self._last_pos = None
        self._last_button = Qt.NoButton

    # Closest and furthest the camera may sit, as multiples of the model radius.
    ZOOM_NEAR = 0.05
    ZOOM_FAR = 20.0

    def zoom_by(self, factor: float) -> None:
        """Scale the orbit distance, clamped so the model cannot be lost."""
        low = self._radius * self.ZOOM_NEAR
        high = self._radius * self.ZOOM_FAR
        self.distance = max(low, min(high, self.distance * factor))
        self.update()

    def wheelEvent(self, event) -> None:
        # A mouse wheel reports 120 units per notch, but a trackpad reports a
        # pixel delta and leaves angleDelta tiny -- dividing that by 120 gives
        # a factor of about 1.0, which is why trackpad zoom did nothing.
        pixels = event.pixelDelta()
        if not pixels.isNull():
            self.zoom_by(math.exp(-pixels.y() * 0.004))
        else:
            steps = event.angleDelta().y() / 120.0
            self.zoom_by(0.88**steps)
        event.accept()

    def event(self, event) -> bool:
        """Handle the macOS pinch gesture, which never arrives as a wheel event."""
        from PySide6.QtCore import QEvent

        if event.type() == QEvent.NativeGesture:
            if event.gestureType() == Qt.ZoomNativeGesture:
                self.zoom_by(1.0 - event.value())
                return True
        return super().event(event)

    def keyPressEvent(self, event) -> None:
        key = event.key()
        if key == Qt.Key_F:
            self.reset_view()
        elif key in (Qt.Key_Plus, Qt.Key_Equal):
            self.zoom_by(0.8)
        elif key in (Qt.Key_Minus, Qt.Key_Underscore):
            self.zoom_by(1.25)
        elif key == Qt.Key_0:
            self.frame_model()
        else:
            super().keyPressEvent(event)


def _to_qmatrix(m: np.ndarray):
    from PySide6.QtGui import QMatrix4x4

    return QMatrix4x4(*[float(v) for v in m.flatten()])
