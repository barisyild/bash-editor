"""Cutscene playback: who is on stage, playing what, standing where.

A model file holds more than meshes and clips. Its object graph (§8.3) carries
**nodes**, and a node puts something on stage for a stretch of scene time. Two
kinds, told apart by the type word at node+0x00:

    type 3   an actor: a clip, the frames to play (§9.7), and a placement track
             at node+0x30, stride 0x4C, whose keys hold position at +0x08, a
             quaternion at +0x20 and a scale at +0x3C. Its id is the one at
             node+0x14 *plus* its first played frame, and that sum lands in the
             0x4000 vertex animation namespace.
    type 0   a prop: one mesh, named by the 0x2000 id **each of its keys**
             carries at +0x08, with a transform track at node+0x24, stride
             0x50, position at +0x0C, quaternion at +0x24 and scale at +0x40.
             node+0x14 states the same id and is not what draws.
    type 5   a trigger: fires once when the clock reaches its window start and
             spawns another root -- a scene of its own, with its own clock,
             placed at the trigger's transform

**A model holds several scenes, not one.** `model+0x48` counts roots and
`model+0x4C` points at them, and each is a separate timeline: `root+0x0C` is
its first tick, `root+0x08` its last, and its children's windows are ticks on
*that* clock. Root 0 is the shot; the extras are effects a type-5 node fires.
Flattening them all onto one list dates a 20-tick effect to ticks 0..19 of a
shot that starts at 149.

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

**And a prop reads that id out of its key, not out of its node.** The per-tick
handler finds the key covering the tick and copies the key's own +0x08 into the
entity slot the draw dispatches on, every tick:

    8001EDAC  lhu   $v0, 8($s5)      ; s5 = the key covering this tick
    8001EDB4  sh    $v0, 0x74($s6)   ;   -> entity+0x7C, what the draw names
    80019F44  lhu   $a2, 0x74($s0)   ; and the draw asks for exactly that

The two agree in **11382 of 11382** shipped prop keys and no prop changes mesh
across its own track, so nothing in the archive distinguishes them -- but an
edit does. Writing node+0x14 alone moves nothing on screen, which is what
`out/crashbash-eurocom-control2.bin` showed, and a node copied with its keys
draws the mesh it was copied from.

An actor is the other way round: its id is node+0x14 + node+0x18 (above), read
once, and its key has position at +0x08 where a prop's has the id. The two key
records are different shapes, not one shape at two strides.

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
# A prop key's own copy of the id its node names. The handler reloads it every
# tick from the key covering that tick, so this -- not node+0x14 -- is what the
# shot draws. An actor key has its position here instead; the two key layouts
# are different records.
PROP_KEY_ID = 0x08

NODE_TYPE = 0x00
NODE_TYPE_ACTOR = 3
NODE_TYPE_PROP = 0
# A one-shot trigger: at its window start it spawns another root as a scene of
# its own, placed at its own transform. See _sub_scene.
NODE_TYPE_SUBSCENE = 5
MESH_NAMESPACE = 0x2000

ROOT_END_TICK = 0x08
ROOT_START_TICK = 0x0C
ROOT_CHILDREN = 0x1C  # where the child pointer array starts; count is at +0x00

SUB_POSITION = 0x2C
SUB_ANGLES = 0x38  # three of them, stride 4, only the low halfword read
SUB_SCALE = 0x60
ANGLE_TURN = 4096.0  # the game's full circle

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

# The projection distance the camera is initialised with (0x800143A8, 0x140 =
# 320) and the GTE reads straight from camera+0x18:
#
#     80018E8C  lw   $t3, 0x18($s7)   ; the camera's projection distance
#     80018E94  ctc2 $t3 -> H         ;   into the GTE, unscaled
#
# and the vertical screen offset it is measured against is half the viewport:
#
#     80018E5C  lw   $v0, 0x14($s1)   ; the viewport's height
#     80018E78  sra  $v0, $v0, 1      ;   halved
#     80018E80  sll  $t5, $v0, 0x10   ;   into 16.16
#     80018E88  ctc2 $t5 -> OFY
#
# so the vertical field of view is `2 * atan(OFY / H)`. The half-height is 240,
# not the 120 a 240-line display would suggest: at 120 the cast overflows the
# frame in 181 of 198 camera samples across the cutscenes, and at 240 the median
# subject fills 0.94 of the frame height -- which is what a shot composed around
# a character looks like. `level_intro_crashplain` settles it on screen: at 240
# its opening frame is the whole plain with Crash small in it, and at 120 it is
# a close-up of his head.
SCREEN_DISTANCE = 400.0
SCREEN_HALF_HEIGHT = 240.0

NODE_TYPE_CAMERA = 2
# A particle emitter. Its draw walks a linked list of live particles it owns at
# entity+0x0C, each with its own draw at +0x54 and the next at +0x5C
# (0x80021330), and its per-tick handler integrates each one's position from a
# velocity (0x8001F990..0x8001F9EC). What every particle draws is one mesh, and
# the node names it in the 0x2000 namespace like any prop.
NODE_TYPE_EMITTER = 1
EMITTER_POSITION = 0x18
EMITTER_BUDGET = 0x24  # the whole spray, and the size of the record array
EMITTER_PER_TICK = 0x28  # how many of it leave each tick
EMITTER_LIFETIME = 0x2C  # ticks a particle lives before its bit 15 is cleared
EMITTER_LAST_TICK = 0x30
EMITTER_FADE_IN = 0x6C
EMITTER_FADE_OUT = 0x70
# A second ramp, on the particle's scale rather than its colour:
# it writes the same value to three consecutive words at 0x8001FC5C.
EMITTER_GROW_END = 0x74
EMITTER_SHRINK_START = 0x78
EMITTER_SPEED_MIN = 0x34
EMITTER_SPEED_MAX = 0x38
EMITTER_MESH_ID = 0x3C
EMITTER_YAW = 0x44
EMITTER_YAW_SPREAD = 0x48
EMITTER_PITCH = 0x4C
EMITTER_PITCH_SPREAD = 0x50
EMITTER_ACCEL = (0x54, 0x5C, 0x64)
EMITTER_DAMP = (0x58, 0x60, 0x68)
EMITTER_SPIN = 0x7C

ANGLE_ONE = 4096  # a full turn, and the length of the game's sine table
DAMP_ONE = 256  # the value the handler reads as "leave the velocity alone"
# The spawner scales speed by a sine table entry and shifts down by four
# (0x8001F738, 0x8001F74C); the tick loop shifts the velocity down by eight
# again to move the position (0x8001F99C).
SPEED_SHIFT = 4
VELOCITY_SHIFT = 8
CAMERA_KEYS = 0x1C
CAMERA_STRIDE = 0x28
CAMERA_TARGET = 0x08
CAMERA_EYE = 0x14
NODE_SCREEN_DISTANCE = 0x18

NODE_WINDOW_START = 0x04
NODE_WINDOW_END = 0x08

# The fade (§9.11.10): a window and the two levels it ramps between, 4096 being
# opaque. Over the corpus the pair is only ever (4096, 0), (0, 4096) or held.
NODE_TYPE_FADE = 4
FADE_FROM = 0x14
FADE_TO = 0x18
FADE_ONE = 4096.0
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


def _u16(data: bytes, at: int) -> int:
    return struct.unpack_from("<H", data, at)[0]


def _multiply(first: np.ndarray, second: np.ndarray) -> np.ndarray:
    """One rotation after the other, both x, y, z, w."""
    x1, y1, z1, w1 = first
    x2, y2, z2, w2 = second
    return np.array([
        w1 * x2 + x1 * w2 + y1 * z2 - z1 * y2,
        w1 * y2 - x1 * z2 + y1 * w2 + z1 * x2,
        w1 * z2 + x1 * y2 - y1 * x2 + z1 * w2,
        w1 * w2 - x1 * x2 - y1 * y2 - z1 * z2,
    ])


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
    # What `_onto_parent` did to the file's own values to get these. A sub-scene
    # runs on its own clock and in its parent's frame, so its keys are shifted
    # by `shift` ticks and, when `parented`, moved as well -- and anything
    # writing a key back to `node` has to undo both or it corrupts the record.
    shift: int = 0
    parented: bool = False
    # The placement `parented` was applied with, kept so a writer can invert it.
    parent: "Placement | None" = None

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


class _Random:
    """The game's generator, reimplemented from 0x80015590.

        800155A4  lw   $v1, 0x17b8($a0)   ; the seed
        800155A8  lw   $a1, 0x17bc($a2)   ;   and its increment
        800155B0  addu $v1, $v1, $a1      ; seed += increment
        800155BC  divu $zero, $v1, $a3    ; the result is seed % n
        800155D0  addu $v1, $v1, $a0      ; then the increment moves too
        800155D4  addu $a1, $a1, $v1
        800155DC  sw   $a1, 0x17bc($a2)

    The state lives at 0x800517B8, which is not in the file -- it is whatever
    the console had reached by the time the shot ran. So a spray can be
    reproduced in distribution and never frame for frame, and this starts from
    a fixed seed to at least make playback repeatable.
    """

    MASK = 0xFFFFFFFF
    CARRY = 0x80050000  # the register the generator folds back into its state

    def __init__(self, seed: int = 1, increment: int = 1):
        self.seed = seed & self.MASK
        self.increment = increment & self.MASK

    def below(self, bound: int) -> int:
        if bound < 2:
            return 0
        self.seed = (self.seed + self.increment) & self.MASK
        result = self.seed % bound
        self.increment = (self.increment + self.seed + self.CARRY) & self.MASK
        return result


def _sin(angle: int) -> float:
    return math.sin(2.0 * math.pi * (angle % ANGLE_ONE) / ANGLE_ONE)


def _cos(angle: int) -> float:
    return math.cos(2.0 * math.pi * (angle % ANGLE_ONE) / ANGLE_ONE)


@dataclass(frozen=True)
class Particle:
    position: np.ndarray
    spin: float
    scale: float


@dataclass(frozen=True)
class Emitter:
    """A node that sprays copies of one mesh and lets them fly.

    Type 1 does not place a mesh, it spawns them. Its constructor takes a block
    of `max_live` 40-byte records (0x800216D4), the per-tick handler integrates
    each live one, and the spawner fills a fresh record from the node:

        8001F650  jal  0x80015590      ; a speed between the two bounds
        8001F6B8  jal  0x80015590      ;   a yaw around its centre
        8001F700  jal  0x80015590      ;   and a pitch around its
        8001F738  mult $s1, $v0        ; speed x the sine table at 0x80068BD4
        8001F9A8  addu $v0, $v0, $v1   ; each tick, position += velocity >> 8
        8001FA08  addu $v0, $v0, $v1   ;   and velocity += the acceleration
        8001FA68  mult $v0, $v1        ;   then damped, 256 being no damping

    In `intro_eurocom` the eight emitters sit at the eight letters, each opening
    six ticks after its letter lands, spraying an omnidirectional burst that
    falls: 360 degrees of spread and an acceleration of 7 along the console's
    down axis.
    """

    node: int
    start: int
    end: int
    position: np.ndarray
    mesh_index: int
    budget: int
    per_tick: int
    lifetime: int
    last_tick: int
    speed: tuple[int, int]
    yaw: tuple[int, int]
    pitch: tuple[int, int]
    accel: np.ndarray
    damp: np.ndarray
    spin: int
    fade: tuple[int, int]
    grow: tuple[int, int]
    # The placement the position above was moved into, when the emitter belongs
    # to a sub-scene. Kept so a writer can undo it: `position` is in the
    # parent's frame and the node's own three words are not.
    parent: "Placement | None" = None
    # Ticks the sub-scene's clock was shifted by to get `start`, `end` and
    # `last_tick` onto the shot's own. A writer takes it off again, the same
    # way a track's keys have their `shift` taken off.
    shift: int = 0

    def particles(self, tick: int) -> list[Particle]:
        """Every live particle at `tick`, simulated from the emitter's start.

        They do go out rather than merely leave the frame: once a particle's
        age passes the lifetime the handler clears the bit the loop tests for
        life, which is the same bit the draw path reads.

            8001FAE0  lw    $v1, 0x18($s2)   ; node+0x2C, the lifetime
            8001FAEC  slt   $v1, $v1, $a0    ;   against the age
            8001FB00  ori   $v1, $v1, 0x7fff
            8001FB04  and   $v0, $v0, $v1    ; clear bit 15: dead
        """
        if tick < self.start:
            return []
        random = _Random()
        live: list[list] = []       # [position, velocity, spin, age]
        remaining = self.budget
        for step in range(self.start, min(tick, self.end) + 1):
            for particle in live:
                particle[0] = particle[0] + particle[1] / (1 << VELOCITY_SHIFT)
                particle[1] = (particle[1] + self.accel) * self.damp
                particle[2] += self.spin
                particle[3] += 1
            live = [p for p in live if p[3] <= self.lifetime]
            if step <= self.last_tick and remaining > 0:
                for _ in range(min(self.per_tick, remaining)):
                    live.append(self._spawn(random))
                    remaining -= 1
        return [Particle(self.position + p[0] * GTE_SCALE_SMALL,
                         (p[2] % ANGLE_ONE) / ANGLE_ONE * 2.0 * math.pi,
                         self._scale_at(p[3]))
                for p in live]

    def _scale_at(self, age: int) -> float:
        """The particle's size at `age`: it grows in, holds, then shrinks away.

            8001FC08  slt $v0, $a3, $a2    ; age < node+0x74 -> ramp up from 0
            8001FC24  slt $v0, $v1, $a3    ; node+0x78 < age -> ramp down to 0
            8001FC40  subu $a2, $t0, $v1   ;   over the rest of the lifetime
            8001FC5C  sw  $v1, 0x28($s0)   ; and the result is the scale, three
            8001FC60  sw  $v1, 0x24($s0)   ;   words of it, 4096 being full size
        """
        grow, shrink = self.grow
        if not grow and not shrink:
            return 1.0
        if age < grow:
            return age / grow if grow else 1.0
        if age > shrink:
            span = self.lifetime - shrink
            if span <= 0:
                return 0.0
            return max(0.0, 1.0 - (age - shrink) / span)
        return 1.0

    def _spawn(self, random: _Random) -> list:
        low, high = self.speed
        speed = low + random.below(max(high - low, 0))
        yaw = (self.yaw[0] + random.below(self.yaw[1])) % ANGLE_ONE
        pitch = (self.pitch[0] + random.below(self.pitch[1])) % ANGLE_ONE
        # speed * direction, at the shift the spawner applies
        scale = speed * ANGLE_ONE / (1 << SPEED_SHIFT)
        velocity = np.array([
            _cos(pitch) * _sin(yaw), _sin(pitch), _cos(pitch) * _cos(yaw),
        ]) * scale
        return [np.zeros(3), velocity, float(random.below(ANGLE_ONE)), 0]


@dataclass(frozen=True)
class CameraKey:
    tick: int
    duration: int
    eye: np.ndarray
    target: np.ndarray


@dataclass(frozen=True)
class Camera:
    """The viewpoint a shot is filmed through, over one stretch of its clock.

    A node of type 2 is the camera, and it writes the struct the frame renderer
    reads. Its keys carry two points, one stride of 0x28 apart: the handler
    interpolates both, hands them to the look-at helper to get the angles, and
    then stores the second as the eye:

        8001F074  addiu $a0, $sp, 0x10   ; key+0x14: the eye
        8001F078  addiu $a1, $sp, 0x20   ; key+0x08: what it looks at
        8001F07C  jal   0x800153b4       ;   -> Euler angles from the difference
        8001F080  addiu $a2, $s6, 0x54   ;      into camera+0x54
        8001F090  sw    $t0, 0xc($s6)    ; and the eye into camera+0x0C..0x14
        8001F09C  sw    $zero, 8($s6)    ; the offset is cleared
        8001EF6C  lw    $v0, 0x18($a2)   ; node+0x18, the projection distance,
        8001EF74  sw    $v0, 0x18($s6)   ;   -> camera+0x18, which the GTE reads

    `s6` is 0x80051640, and 0x8002AF78 hands that same address to 0x80014540 --
    the routine that turns the angles at +0x54 into the MATRIX at +0x74. So this
    is the shot's viewpoint, not a guess: eye, target, and a field of view the
    node names for itself.

    A file may carry more than one, with windows that do not overlap:
    `level_ending_good_shot3` cuts at tick 259 from a 303 distance to 609.
    """

    node: int
    start: int
    end: int
    screen_distance: float
    keys: tuple[CameraKey, ...]
    # What `_read_camera_keys` already did to these keys, so a writer can undo
    # it -- the same fields `Track` carries, and for the same reason.
    shift: int = 0
    parented: bool = False
    parent: "Placement | None" = None

    def at(self, tick: int) -> tuple[np.ndarray, np.ndarray]:
        """Eye and target at `tick`, interpolated within the key holding it."""
        if not self.keys:
            return np.zeros(3), np.array([0.0, 0.0, 1.0])
        for index, key in enumerate(self.keys):
            if key.tick <= tick < key.tick + key.duration:
                if index + 1 < len(self.keys) and key.duration:
                    weight = (tick - key.tick) / key.duration
                    nxt = self.keys[index + 1]
                    return (key.eye * (1.0 - weight) + nxt.eye * weight,
                            key.target * (1.0 - weight) + nxt.target * weight)
                return key.eye, key.target
        last = self.keys[-1] if tick >= self.keys[-1].tick else self.keys[0]
        return last.eye, last.target

    @property
    def field_of_view(self) -> float:
        """Vertical field of view in degrees, over the 240-line display."""
        return 2.0 * math.degrees(
            math.atan(SCREEN_HALF_HEIGHT / max(self.screen_distance, 1.0)))


@dataclass(frozen=True)
class Fade:
    """A type-4 node: the shot dipping to or out of a colour (§9.11.10).

    `start`/`end` are the window and `level_at` ramps from `first` to `last`
    across it, 1.0 being fully faded. The render pass scales the colour at the
    context's +0x08 by `1 - level`, so 1.0 is that colour filling the screen.
    """

    node: int
    start: int
    end: int
    first: float
    last: float

    def level_at(self, tick: int) -> float:
        if tick <= self.start:
            return self.first
        if tick >= self.end or self.end == self.start:
            return self.last
        weight = (tick - self.start) / (self.end - self.start)
        return self.first * (1.0 - weight) + self.last * weight


@dataclass
class Scene:
    actors: list[Actor] = field(default_factory=list)
    props: list[Prop] = field(default_factory=list)
    cameras: list[Camera] = field(default_factory=list)
    emitters: list[Emitter] = field(default_factory=list)
    fades: list[Fade] = field(default_factory=list)
    # The root's own clock range, which is what the shot runs on. A node window
    # may fall outside it -- `level_shot8` has one opening at tick 63 in a shot
    # that runs 295..372 -- and such a node simply never opens.
    window: tuple[int, int] | None = None

    @property
    def start(self) -> int:
        if self.window:
            return self.window[0]
        spans = [t.track.start for t in self.actors + self.props]
        return min(spans) if spans else 0

    @property
    def end(self) -> int:
        if self.window:
            return self.window[1]
        spans = [t.track.end for t in self.actors + self.props]
        return max(spans) if spans else 0

    @property
    def mesh_indices(self) -> set[int]:
        """Meshes a node owns: drawn only while one of their windows is open."""
        return ({a.mesh_index for a in self.actors}
                | {p.mesh_index for p in self.props}
                | {e.mesh_index for e in self.emitters})

    def actors_at(self, tick: int) -> list[Actor]:
        return [a for a in self.actors if a.track.start <= tick <= a.track.end]

    def props_at(self, tick: int) -> list[Prop]:
        return [p for p in self.props if p.track.start <= tick <= p.track.end]

    def camera_at(self, tick: int) -> Camera | None:
        """The camera filming `tick`, or the nearest one if none covers it."""
        if not self.cameras:
            return None
        for camera in self.cameras:
            if camera.start <= tick <= camera.end:
                return camera
        return min(self.cameras,
                   key=lambda c: min(abs(tick - c.start), abs(tick - c.end)))


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


def _root_offset(data: bytes, index: int) -> int | None:
    """Where root `index` sits, or None if the model names no such root.

    The spawner takes a root index, not a model: `a1` selects one entry from
    the self-relative array at `model+0x4C`, whose length is at `model+0x48`.

        8001FF78  lw    $v0, 0x4c($v1)   ; the root array, self-relative
        8001FF80  addiu $v0, $v0, 0x4c
        8001FF84  addu  $v0, $v1, $v0
        8001FF88  sll   $v1, $a1, 2      ; a1 = the root INDEX
        8001FF8C  addu  $v0, $v0, $v1
        8001FF98  addu  $s3, $v0, $v1    ; -> the root
    """
    limit = len(data) - 0x40
    try:
        count = _i32(data, 0x48)
        base = 0x4C + _i32(data, 0x4C)
    except (struct.error, IndexError):
        return None
    # Bound each read by what it actually needs, not by a slack off the end.
    # The array sits close to EOF -- `text_intromovie` puts it 8 bytes short --
    # so a blanket `len - 0x40` rejected 75 models outright and their scenes
    # were never read at all.
    if not (0 < count < 4096 and 0 <= base and base + 4 * count <= len(data)):
        return None
    if not 0 <= index < count:
        return None
    slot = base + 4 * index
    root = slot + _i32(data, slot)
    # A root is only usable if its own header and child count are readable.
    return root if 0 <= root and root + ROOT_CHILDREN <= len(data) else None


def root_span(data: bytes, index: int = 0) -> tuple[int, int] | None:
    """The first and last tick a root's clock runs, as the root declares them.

    A fresh context starts at the root's own `+0x0C` and ends at its `+0x08`:

        80020D38  lhu   $v0, 0xc($s3)    ; root+0x0C, the starting tick
        80020D40  sh    $v0, 0xe($s2)    ;   -> context+0x0E
        80020D44  lw    $v0, 8($s3)      ; root+0x08, the last
        80020D50  sw    $v0, 0x10($s2)   ;   -> context+0x10
    """
    root = _root_offset(data, index)
    if root is None:
        return None
    start = struct.unpack_from("<H", data, root + ROOT_START_TICK)[0]
    end = _i32(data, root + ROOT_END_TICK)
    return (start, end) if 0 <= start <= end < MAX_TICK else None


def spawn_order(data: bytes, index: int = 0) -> list[int]:
    """The nodes of one root, in the order the spawner constructs them.

    Not a search: this is the walk performed at 0x8001FE80. A root states how
    many children it has at `+0x00` and where their self-relative pointers
    start at `+0x1C`:

        8001FFD4  addiu $s2, $s3, 0x1c    ; the child array
        8001FFD8  lw    $v0, ($s3)        ; the child count
        8001FFF0  lw    $v0, ($s2)        ; a self-relative pointer
        8001FFF8  addu  $a0, $s2, $v0     ;   -> the child
        8001FFFC  lw    $v0, ($a0)        ; the child's own +0x00 is its type,
        80020004  sll   $v0, $v0, 4       ;   indexing the 16-byte table that
        8002001C  jalr  $v0               ;   holds its constructor

    Reading the graph by walking bytes and matching shapes finds most of this
    and misses the rest -- six actors in `level_ending_good_shot3`, where the
    shape scan saw four. Walking every root at once is a different mistake:
    see `read_scene`.
    """
    limit = len(data) - 0x40
    root = _root_offset(data, index)
    if root is None:
        return []
    children = _i32(data, root)
    array = root + ROOT_CHILDREN
    if not (0 <= children < 4096 and array + 4 * children <= len(data)):
        return []

    nodes: list[int] = []
    for child in range(children):
        slot = array + 4 * child
        node = slot + _i32(data, slot)
        # A node has to have room for the type word the spawner dispatches on.
        if 0 <= node <= limit:
            nodes.append(node)
    return nodes


@dataclass(frozen=True)
class Placement:
    """Where a sub-scene sits in its parent."""

    position: np.ndarray
    rotation: np.ndarray
    scale: np.ndarray

    def applied(self, key: Key) -> Key:
        return Key(
            key.tick, key.duration,
            self.position + rotation_matrix(self.rotation) @ (self.scale * key.position),
            _multiply(self.rotation, key.rotation),
            self.scale * key.scale,
        )


def _sub_scene(data: bytes, node: int, window_start: int) -> tuple[int, Placement]:
    """The root a trigger spawns, and where it puts it.

    Type 5 is a one-shot: it fires the first time the clock reaches its window
    start, guarded by a flag bit so it never fires twice, and what it fires is
    another root of the same model -- its id is a root index, handed straight to
    the spawner that 0x8001FE80 shares:

        8001FDA8  lw    $v0, 4($a3)     ; the node's window start, 16.16
        8001FDB4  slt   $v1, $v1, $v0   ;   not yet -> nothing
        8001FDC8  andi  $v0, $v0, 0x8000; already fired -> nothing
        8001FDD4  lw    $v0, 0x2c($a3)  ; node+0x2C..0x34 -> the entity's position
        8001FDF8  lhu   $v0, 0x38($a3)  ; node+0x38, 0x3C, 0x40 -> three angles
        8001FE1C  lw    $v0, 0x60($a3)  ; node+0x60..0x68 -> its scale
        8001FE54  lw    $a1, 0x14($a3)  ; node+0x14, the ROOT INDEX
        8001FE58  jal   0x80020cc4      ;   -> spawn that root, clock from zero
        8001FE68  ori   $v0, $v0, 0x8000; and mark it fired

    All fourteen in the game carry a unit scale and one non-zero angle, the
    middle one -- a yaw, 4096 to the turn.
    """
    index = _i32(data, node + NODE_COMMAND_ID)
    position = np.array(
        [_i32(data, node + SUB_POSITION + 4 * i) for i in range(3)],
        dtype=np.float64) * GTE_SCALE_SMALL
    yaw = _u16(data, node + SUB_ANGLES + 4) / ANGLE_TURN * 2.0 * math.pi
    rotation = np.array([0.0, math.sin(yaw / 2.0), 0.0, math.cos(yaw / 2.0)])
    scale = np.array(
        [_i32(data, node + SUB_SCALE + 4 * i) for i in range(3)],
        dtype=np.float64) / QUATERNION_ONE
    return index, Placement(position, rotation, scale)


def read_scene(data: bytes, model, clips) -> Scene | None:
    """The scene a model plays, or None if it spawns nothing playable.

    **Only root 0 is the timeline.** The model's other roots are separately
    spawnable scenes of their own, entered by a type-5 trigger with their clocks
    restarting -- so their nodes must be shifted onto the parent's clock, not
    read as if their ticks were already scene ticks.

    Every extra root in a cutscene is one of these, and it is always the same
    thing: a 20-tick effect that a shot fires once, nine props over ticks 0..19.
    `level_intro_cortexlab` fires its at tick 272, exactly where Cortex shrinks
    away, and reading its ticks literally puts the vanishing effect over the
    opening of the shot instead of over its end.
    """
    scene = Scene(window=root_span(data, 0))
    _read_root(data, model, clips, 0, 0, None, scene, set())
    if scene.actors or scene.props or scene.emitters:
        return scene

    # Root 0 empty: four arena models put their nodes in later roots that
    # gameplay code spawns directly, with no trigger naming them. Showing the
    # first that holds anything is a convenience of this editor, not a claim
    # about the format -- those roots are separate scenes and share no clock.
    count = _i32(data, 0x48) if len(data) > 0x50 else 0
    for index in range(1, min(max(count, 0), 64)):
        scene = Scene(window=root_span(data, index))
        _read_root(data, model, clips, index, 0, None, scene, set())
        if scene.actors or scene.props or scene.emitters:
            return scene
    return None


def _read_root(data: bytes, model, clips, index: int, offset: int,
               parent: Placement | None, scene: Scene, seen: set[int]) -> None:
    if index in seen:
        return
    seen.add(index)

    for node in spawn_order(data, index):
        kind = _i32(data, node + NODE_TYPE)
        if kind not in (NODE_TYPE_ACTOR, NODE_TYPE_PROP, NODE_TYPE_SUBSCENE,
                        NODE_TYPE_CAMERA, NODE_TYPE_EMITTER, NODE_TYPE_FADE):
            continue
        window_start = _i32(data, node + NODE_WINDOW_START)
        window_end = _i32(data, node + NODE_WINDOW_END)
        if not (0 <= window_start < window_end < MAX_TICK):
            continue

        if kind == NODE_TYPE_FADE:
            scene.fades.append(Fade(
                node=node,
                start=window_start + offset,
                end=window_end + offset,
                first=_i32(data, node + FADE_FROM) / FADE_ONE,
                last=_i32(data, node + FADE_TO) / FADE_ONE,
            ))
            continue
        command = _i32(data, node + NODE_COMMAND_ID)

        if kind == NODE_TYPE_EMITTER:
            ident = _i32(data, node + EMITTER_MESH_ID)
            mesh_index = (ident & 0xFFF) - 1
            if (ident & NAMESPACE_MASK) != MESH_NAMESPACE:
                continue
            if not 0 <= mesh_index < len(model.meshes):
                continue
            position = np.array(
                [_i32(data, node + EMITTER_POSITION + 4 * i) for i in range(3)],
                dtype=np.float64) * GTE_SCALE_SMALL
            if parent is not None:
                position = (parent.position + rotation_matrix(parent.rotation)
                            @ (parent.scale * position))
            scene.emitters.append(Emitter(
                node=node,
                start=window_start + offset,
                end=window_end + offset,
                position=position,
                mesh_index=mesh_index,
                budget=_i32(data, node + EMITTER_BUDGET),
                per_tick=max(_i32(data, node + EMITTER_PER_TICK), 1),
                lifetime=_i32(data, node + EMITTER_LIFETIME),
                last_tick=_i32(data, node + EMITTER_LAST_TICK) + offset,
                speed=(_i32(data, node + EMITTER_SPEED_MIN),
                       _i32(data, node + EMITTER_SPEED_MAX)),
                yaw=(_i32(data, node + EMITTER_YAW),
                     _i32(data, node + EMITTER_YAW_SPREAD)),
                pitch=(_i32(data, node + EMITTER_PITCH),
                       _i32(data, node + EMITTER_PITCH_SPREAD)),
                accel=np.array([_i32(data, node + o) for o in EMITTER_ACCEL],
                               dtype=np.float64),
                damp=np.array([_i32(data, node + o) for o in EMITTER_DAMP],
                              dtype=np.float64) / DAMP_ONE,
                spin=_i32(data, node + EMITTER_SPIN),
                fade=(_i32(data, node + EMITTER_FADE_IN),
                      _i32(data, node + EMITTER_FADE_OUT)),
                grow=(_i32(data, node + EMITTER_GROW_END),
                      _i32(data, node + EMITTER_SHRINK_START)),
                parent=parent,
                shift=offset,
            ))
            continue

        if kind == NODE_TYPE_CAMERA:
            keys = _read_camera_keys(data, node, offset, parent)
            if not keys:
                continue
            scene.cameras.append(Camera(
                node=node,
                start=window_start + offset,
                end=window_end + offset,
                screen_distance=float(_i32(data, node + NODE_SCREEN_DISTANCE)),
                keys=tuple(keys),
                shift=offset,
                parented=parent is not None,
                parent=parent,
            ))
            continue

        if kind == NODE_TYPE_SUBSCENE:
            child, placement = _sub_scene(data, node, window_start)
            span = root_span(data, child)
            if span is None:
                continue
            if parent is not None:
                placement = Placement(
                    parent.position + rotation_matrix(parent.rotation)
                    @ (parent.scale * placement.position),
                    _multiply(parent.rotation, placement.rotation),
                    parent.scale * placement.scale,
                )
            _read_root(data, model, clips, child,
                       offset + window_start - span[0], placement, scene, seen)
            continue

        if kind == NODE_TYPE_PROP:
            # The id the game draws is the KEY's, not the node's (§9.11.11).
            drawn = _i32(data, node + PROP_KEYS + PROP_KEY_ID)
            mesh_index = (drawn & 0xFFF) - 1
            if (drawn & 0x7000) != MESH_NAMESPACE:
                continue
            if not 0 <= mesh_index < len(model.meshes):
                continue
            keys = _read_keys(data, node, PROP_KEYS, PROP_STRIDE,
                              0x0C, 0x24, 0x40)
            if not keys:
                continue
            scene.props.append(Prop(
                track=_onto_parent(node, window_start, window_end, keys,
                                   offset, parent),
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
            track=_onto_parent(node, window_start, window_end, keys,
                               offset, parent),
            clip_index=index,
            mesh_index=clip.mesh_index,
            play_start=play_start,
            play_end=play_end,
            delay=delay,
            mode=mode,
        ))


def _read_camera_keys(data: bytes, node: int, offset: int,
                      parent: Placement | None) -> list[CameraKey]:
    """A camera node's keys, on the parent's clock and in its frame."""
    keys: list[CameraKey] = []
    at = node + CAMERA_KEYS
    while 0 <= at <= len(data) - CAMERA_STRIDE and len(keys) < MAX_KEYS:
        tick, duration = _i32(data, at), _i32(data, at + 4)
        target = np.array(
            [_i32(data, at + CAMERA_TARGET + 4 * i) for i in range(3)],
            dtype=np.float64) * GTE_SCALE_SMALL
        eye = np.array(
            [_i32(data, at + CAMERA_EYE + 4 * i) for i in range(3)],
            dtype=np.float64) * GTE_SCALE_SMALL
        if parent is not None:
            rotation = rotation_matrix(parent.rotation)
            eye = parent.position + rotation @ (parent.scale * eye)
            target = parent.position + rotation @ (parent.scale * target)
        keys.append(CameraKey(tick + offset, duration, eye, target))
        if duration == 0:
            break
        at += CAMERA_STRIDE
    return keys


def _onto_parent(node: int, start: int, end: int, keys: list[Key],
                 offset: int, parent: Placement | None) -> Track:
    """A track moved onto the parent's clock and into the parent's frame."""
    if parent is not None:
        keys = [parent.applied(key) for key in keys]
    if offset:
        keys = [Key(key.tick + offset, key.duration,
                    key.position, key.rotation, key.scale) for key in keys]
    return Track(node, start + offset, end + offset, tuple(keys),
                 shift=offset, parented=parent is not None, parent=parent)


def field_of_view() -> float:
    """Vertical field of view for a camera that names no distance of its own."""
    return 2.0 * math.degrees(math.atan(SCREEN_HALF_HEIGHT / SCREEN_DISTANCE))


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
