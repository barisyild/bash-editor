"""Cutscene playback: who is on stage, playing what, standing where.

A model file holds more than meshes and clips. Its object graph (§8.3) carries
**nodes**, and a node puts something on stage for a stretch of scene time. Two
kinds, told apart by the type word at node+0x00:

    type 3   an actor: a clip, the frames to play (§9.7), and a placement track
             at node+0x30, stride 0x4C, whose keys hold position at +0x08, a
             quaternion at +0x20 and a scale at +0x3C. Its id is the one at
             node+0x14 *plus* its first played frame, and that sum lands in the
             0x4000 vertex animation namespace.
    type 0   a prop: one mesh, named by the 0x2000 id at node+0x14, with a
             transform track at node+0x24, stride 0x50, position at +0x0C,
             quaternion at +0x24 and scale at +0x40

Scale is three components in the same 4096 = 1.0 fixed point as the rotation,
and the handlers interpolate it between keys exactly as they do position: each
field is paired with the same field one stride on -- `key+0x3C` against
`key+0x88` for an actor, `key+0x40` against `key+0x90` for a prop -- and both
go through the same routine. Uka Uka swells and shrinks through his cutscene
on nothing but this track.

**A key list ends at a zero duration, and that key is the last pose, not a
sentinel to discard.** 100 nodes across the game are a single such key -- a node
standing still for its whole window -- and every other track ends in one. Cut
the list short and Cortex never appears in `level_intro_cortexlab`, because the
node holding him on stage is one of those hundred, and the node that carries him
away loses the pose he shrinks into.

The prop id is a mesh id in the 0x2000 namespace, which the dispatcher at
0x80015A48 resolves as `52 * id + 0x24` from the model base -- the mesh header
stride, 1-based -- so id 0x2000 | n addresses mesh n - 1.

**A node's window is its visibility.** Outside it the handler clears bit 15 of
the entity's flag word (0x8001EDC8) or zeroes the word outright (0x8001F4CC),
and the draw path tests that bit (0x80021258) before drawing. So a mesh with a
node is drawn only while one of its windows is open; a mesh with none is
scenery and always drawn. Ignoring that leaves every prop stacked on the origin,
which is what a cutscene full of debris looks like when the tracks are missed.

The scene tick runs at the same 30 Hz as animation: the sequence clock advances
the frame cursor by exactly 1.0 per tick (§9.7).
"""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass, field

import numpy as np

from .binreader import GTE_SCALE_SMALL

PLACEMENT_KEYS = 0x30
PLACEMENT_STRIDE = 0x4C
PROP_KEYS = 0x24
PROP_STRIDE = 0x50

NODE_TYPE = 0x00
NODE_TYPE_ACTOR = 3
NODE_TYPE_PROP = 0
MESH_NAMESPACE = 0x2000

# A node's id is not what it plays. The handler adds the play range's first
# frame to it and stores the sum as the entity's animation id:
#
#     8001F2FC  lhu   $v0, ($t0)      ; t0 = node+0x14, the id, unsigned
#     8001F300  lhu   $v1, 4($t0)     ;   + node+0x18, the play range's start
#     8001F308  addu  $v0, $v0, $v1
#     8001F30C  sh    $v0, 0x74($s6)  ;   -> entity+0x7C
#
# So the ids that look like they name nothing -- 0x3FFF, 0x3FF7, 0x3F32 -- are
# just biased: each carries its own play start, and the sum lands in the vertex
# animation namespace. Every actor node in the game, all 177 of them, resolves
# this way, and 0x3FFF was read as a camera for a while purely because its shape
# invited it.
ANIM_NAMESPACE = 0x4000
NAMESPACE_MASK = 0x7000

# The projection distance the camera is initialised with (0x80014388) and the
# GTE reads from camera+0x18 (0x80018E8C), over a 240-line display.
SCREEN_DISTANCE = 400.0
SCREEN_LINES = 240.0

NODE_WINDOW_START = 0x04
NODE_WINDOW_END = 0x08
NODE_COMMAND_ID = 0x14
NODE_PLAY_START = 0x18
NODE_PLAY_END = 0x1C
NODE_PLAY_DELAY = 0x20
NODE_PLAY_MODE = 0x24

QUATERNION_ONE = 4096.0
MODE_LOOP = 0

# A node's window and its key times are scene ticks; nothing in the corpus runs
# longer than a few thousand, and the bound keeps a misread pointer from
# looking like a plausible track.
MAX_TICK = 100_000

# Only the zero duration ends a key list, so a node whose records are garbage
# would be walked to the end of the file. The longest real track is well under
# this; the cap is a backstop, not a rule of the format.
MAX_KEYS = 4096


def _i32(data: bytes, at: int) -> int:
    return struct.unpack_from("<i", data, at)[0]


def _slerp(first: np.ndarray, second: np.ndarray, weight: float) -> np.ndarray:
    """Turn from one orientation to the other along the shorter arc."""
    a = first / max(float(np.linalg.norm(first)), 1e-9)
    b = second / max(float(np.linalg.norm(second)), 1e-9)
    dot = float(np.dot(a, b))
    if dot < 0.0:                      # same rotation, opposite sign: go short
        b, dot = -b, -dot
    if dot > 0.9995:                   # too close to divide by the sine
        return a * (1.0 - weight) + b * weight
    angle = math.acos(max(-1.0, min(1.0, dot)))
    sine = math.sin(angle)
    return (a * math.sin((1.0 - weight) * angle) / sine
            + b * math.sin(weight * angle) / sine)


def _table(data: bytes, field_offset: int) -> int:
    return field_offset + _i32(data, field_offset)


@dataclass(frozen=True)
class Key:
    """One segment of a track: hold from `tick` for `duration`."""

    tick: int
    duration: int
    position: np.ndarray
    rotation: np.ndarray
    scale: np.ndarray


@dataclass(frozen=True)
class Track:
    node: int
    start: int
    end: int
    keys: tuple[Key, ...]

    def at(self, tick: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Position, rotation and scale at `tick`, interpolated within its key.

        Every field is blended with the same field one stride on, which is what
        the handler does -- but not all through the same routine. Position and
        scale go component by component through the scalar interpolator at
        0x80015304; rotation goes whole, through a quaternion routine of its
        own:

            8001F324  addiu $a0, $s4, 0x20    ; this key's quaternion
            8001F328  addiu $a1, $s4, 0x6c    ;   and the next key's
            8001F32C  addiu $a2, $s6, 0x10    ; into entity+0x18
            8001F350  jal   0x80020b44        ;   -> 0x80020680, which opens on
                                              ;      the two quaternions' dot
                                              ;      product: a slerp

        Holding the rotation instead makes a character snap from one facing to
        another instead of turning through it.
        """
        if not self.keys:
            return np.zeros(3), np.array([0.0, 0.0, 0.0, 1.0]), np.ones(3)
        for index, key in enumerate(self.keys):
            if key.tick <= tick < key.tick + key.duration:
                if index + 1 < len(self.keys) and key.duration:
                    weight = (tick - key.tick) / key.duration
                    nxt = self.keys[index + 1]
                    return (key.position * (1.0 - weight) + nxt.position * weight,
                            _slerp(key.rotation, nxt.rotation, weight),
                            key.scale * (1.0 - weight) + nxt.scale * weight)
                return key.position, key.rotation, key.scale
        last = self.keys[-1] if tick >= self.keys[-1].tick else self.keys[0]
        return last.position, last.rotation, last.scale


@dataclass(frozen=True)
class Actor:
    """A node that plays a clip on a mesh while its window is open."""

    track: Track
    clip_index: int
    mesh_index: int
    play_start: int
    play_end: int
    delay: int
    mode: int

    def frame(self, tick: int, frame_count: int) -> int:
        """The clip frame shown at `tick`.

        The handler counts from the node's window, not from the play range, and
        subtracts the range's start again before storing the frame -- so the
        frame is always zero-based, whatever the range is numbered from:

            8001F218  lw    $a0, 0xc($t1)   ; the scene clock, 16.16
            8001F224  subu  $a2, $a0, $v0   ;   - the window's start
            8001F22C  subu  $a2, $a2, $v1   ;   - node+0x20, a start delay
            8001F230  bgez  $a2, 0x8001f244 ; before that -> frame 0
            8001F244  lw    $v0, 0x10($t0)  ; node+0x24: zero loops, else once
            8001F2A4  div   $zero, $a2, $v0 ;   looping is a modulo of span+1
            8001F2E8  move  $a1, $a0        ;   and both clamp at span-1
            8001F2EC  lw    $v0, 4($t0)
            8001F2F4  subu  $a1, $a1, $v0   ; back to zero-based
            8001F2F8  sw    $a1, 0x70($s6)  ;   -> entity+0x78

        That settles what a play range starting at 206 means in a file whose
        only clip is 218 frames long: the range is numbered across the whole
        cutscene, which is cut over several files, and the frames it selects
        still start at zero.
        """
        elapsed = tick - self.track.start - self.delay
        if elapsed < 0:
            return 0
        span = self.play_end - self.play_start
        if span <= 0:
            return 0
        if self.mode == MODE_LOOP:
            frame = elapsed % (span + 1)
        else:
            frame = elapsed
        return max(0, min(frame, span - 1, frame_count - 1))


@dataclass(frozen=True)
class Prop:
    """A node that carries one mesh through the shot without animating it."""

    track: Track
    mesh_index: int


@dataclass
class Scene:
    actors: list[Actor] = field(default_factory=list)
    props: list[Prop] = field(default_factory=list)

    @property
    def start(self) -> int:
        spans = [t.track.start for t in self.actors + self.props]
        return min(spans) if spans else 0

    @property
    def end(self) -> int:
        spans = [t.track.end for t in self.actors + self.props]
        return max(spans) if spans else 0

    @property
    def mesh_indices(self) -> set[int]:
        """Meshes a node owns: drawn only while one of their windows is open."""
        return ({a.mesh_index for a in self.actors}
                | {p.mesh_index for p in self.props})

    def actors_at(self, tick: int) -> list[Actor]:
        return [a for a in self.actors if a.track.start <= tick <= a.track.end]

    def props_at(self, tick: int) -> list[Prop]:
        return [p for p in self.props if p.track.start <= tick <= p.track.end]


def _read_keys(data: bytes, node: int, first: int, stride: int,
               position_at: int, rotation_at: int, scale_at: int) -> list[Key]:
    """A node's placement keys, ending where the handler stops reading them.

    A duration of zero ends the list, and the record carrying it is still a key
    -- the endpoint the previous segment interpolates towards, and the pose the
    node holds once the tick passes it. Both handlers say so in the same shape,
    the actor's at 0x8001F1BC and the prop's at 0x8001EB98:

        8001F1BC  lw    $v0, 4($a2)          ; the first key's duration
        8001F1C4  beqz  $v0, 0x8001f210      ;   zero -> no search: this key, held
        8001F1FC  addiu $a1, $a1, 0x4c       ; otherwise walk the list, and
        8001F200  lw    $v0, ($a1)           ;   stop when the next duration
        8001F208  bnez  $v0, 0x8001f1d0      ;   is zero

    Nothing bounds the list but that zero -- not the node's window, not the
    keys running consecutively. Requiring either one truncates real tracks:
    `level_intro_cortexlab` loses Cortex outright, because the node that holds
    him on stage for ticks 149..271 is a single key with a zero duration, and
    the node that lifts him away loses the pose he shrinks into.
    """
    keys: list[Key] = []
    at = node + first
    while 0 <= at <= len(data) - stride and len(keys) < MAX_KEYS:
        tick, duration = _i32(data, at), _i32(data, at + 4)
        position = np.array(
            [_i32(data, at + position_at + 4 * i) for i in range(3)],
            dtype=np.float64) * GTE_SCALE_SMALL
        rotation = np.array(
            [_i32(data, at + rotation_at + 4 * i) for i in range(4)],
            dtype=np.float64) / QUATERNION_ONE
        scale = np.array(
            [_i32(data, at + scale_at + 4 * i) for i in range(3)],
            dtype=np.float64) / QUATERNION_ONE
        keys.append(Key(tick, duration, position, rotation, scale))
        if duration == 0:
            break
        at += stride
    return keys


def _clip_index(command_id: int, play_start: int, clips) -> int | None:
    """The clip a node plays, read the way the game reads it.

    The id the handler stores is the node's own plus its play start, and the
    decoder splits that sum: the namespace picks the resource kind, and bits
    11..7 index the descriptor table at `model+0x44`, bounds-checked against
    the count at `model+0x40`:

        80019B00  addiu $v0, $zero, 0x4000  ; the vertex animation namespace
        80019B04  beq   $v1, $v0, 0x80019b1c
        80019B1C  andi  $v1, $s2, 0xf80     ; the id's clip field
        80019B20  lw    $v0, 0x40($s1)      ;   against the clip count
        80019B24  sra   $a1, $v1, 7
        80019B2C  beqz  $v0, 0x80019ef8     ;   out of range -> draw nothing
        80019B34  lw    $v1, 0x44($s1)      ; descriptor[clip], stride 24

    This is exact, and it replaces two guesses that stood here before -- one
    matching the play range's length against clip frame counts, the other
    treating an id outside the namespace as naming nothing. Every actor node in
    the game, 177 of 177, lands in the namespace and inside the count.
    """
    if not clips:
        return None
    ident = (command_id + play_start) & 0xFFFF
    if (ident & NAMESPACE_MASK) != ANIM_NAMESPACE:
        return None
    index = (ident & 0xF80) >> 7
    return index if index < len(clips) else None


def spawn_order(data: bytes) -> list[int]:
    """Every node the game spawns, in the order it spawns them.

    Not a search: this is the walk the spawner performs at 0x8001FE80. The
    model names its roots -- a count at `model+0x48` and self-relative pointers
    at `model+0x4C` -- and each root states how many children it has at `+0x00`
    and where their self-relative pointers start at `+0x1C`:

        8001FFD4  addiu $s2, $s3, 0x1c    ; the child array
        8001FFD8  lw    $v0, ($s3)        ; the child count
        8001FFF0  lw    $v0, ($s2)        ; a self-relative pointer
        8001FFF8  addu  $a0, $s2, $v0     ;   -> the child
        8001FFFC  lw    $v0, ($a0)        ; the child's own +0x00 is its type,
        80020004  sll   $v0, $v0, 4       ;   indexing the 16-byte table that
        8002001C  jalr  $v0               ;   holds its constructor

    Reading the graph by walking bytes and matching shapes finds most of this
    and misses the rest -- six actors in `level_ending_good_shot3`, where the
    shape scan saw four.
    """
    limit = len(data) - 0x40
    try:
        count = _i32(data, 0x48)
        base = 0x4C + _i32(data, 0x4C)
    except (struct.error, IndexError):
        return []
    if not (0 < count < 4096 and 0 <= base <= limit):
        return []

    nodes: list[int] = []
    for index in range(count):
        at = base + 4 * index
        if not 0 <= at <= limit:
            break
        root = at + _i32(data, at)
        if not 0 <= root <= limit:
            continue
        children = _i32(data, root)
        array = root + 0x1C
        if not (0 <= children < 4096 and 0 <= array <= limit):
            continue
        for child in range(children):
            slot = array + 4 * child
            if not 0 <= slot <= limit:
                break
            node = slot + _i32(data, slot)
            if 0 <= node <= limit:
                nodes.append(node)
    return nodes


def read_scene(data: bytes, model, clips) -> Scene | None:
    """The scene a model plays, or None if it spawns nothing playable."""
    scene = Scene()

    for node in spawn_order(data):
        kind = _i32(data, node + NODE_TYPE)
        if kind not in (NODE_TYPE_ACTOR, NODE_TYPE_PROP):
            continue
        window_start = _i32(data, node + NODE_WINDOW_START)
        window_end = _i32(data, node + NODE_WINDOW_END)
        if not (0 <= window_start < window_end < MAX_TICK):
            continue
        command = _i32(data, node + NODE_COMMAND_ID)

        if kind == NODE_TYPE_PROP:
            mesh_index = (command & 0xFFF) - 1
            if (command & 0x7000) != MESH_NAMESPACE:
                continue
            if not 0 <= mesh_index < len(model.meshes):
                continue
            keys = _read_keys(data, node, PROP_KEYS, PROP_STRIDE,
                              0x0C, 0x24, 0x40)
            if not keys:
                continue
            scene.props.append(Prop(
                track=Track(node, window_start, window_end, tuple(keys)),
                mesh_index=mesh_index,
            ))
            continue

        play_start = _i32(data, node + NODE_PLAY_START)
        play_end = _i32(data, node + NODE_PLAY_END)
        delay = _i32(data, node + NODE_PLAY_DELAY)
        mode = _i32(data, node + NODE_PLAY_MODE)
        if not (0 <= play_start <= play_end < MAX_TICK and play_end > 0):
            continue
        keys = _read_keys(data, node, PLACEMENT_KEYS, PLACEMENT_STRIDE,
                          0x08, 0x20, 0x3C)
        if not keys:
            continue
        index = _clip_index(command, play_start, clips)
        if index is None:
            continue
        clip = clips[index]
        if clip.mesh_index is None or clip.mesh_index >= len(model.meshes):
            continue
        scene.actors.append(Actor(
            track=Track(node, window_start, window_end, tuple(keys)),
            clip_index=index,
            mesh_index=clip.mesh_index,
            play_start=play_start,
            play_end=play_end,
            delay=delay,
            mode=mode,
        ))

    return scene if (scene.actors or scene.props) else None


def field_of_view() -> float:
    """The camera's vertical field of view, in degrees.

    `2 * atan(120 / 400)` -- half the display's 240 lines over the projection
    distance the GTE is given.
    """
    return 2.0 * math.degrees(math.atan((SCREEN_LINES / 2.0) / SCREEN_DISTANCE))


def rotation_matrix(quaternion: np.ndarray) -> np.ndarray:
    """3x3 rotation from an (x, y, z, w) quaternion."""
    norm = float(np.linalg.norm(quaternion))
    if norm < 1e-9:
        return np.eye(3)
    x, y, z, w = quaternion / norm
    return np.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
    ])
