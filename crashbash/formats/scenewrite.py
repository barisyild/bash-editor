"""Write a glTF file's `extras.crashbash` back into a model, in place.

The exporter records the byte offset of every record it emits (§9.11), and this
is the other half of that bargain: each field is written back exactly where it
was read from, so no count, no size and no offset changes. The region whose
record kinds are still unread (§8.3) is never rebuilt -- only read past. That is
what makes the return trip possible without a decoded object graph.

Because nothing here resizes anything, this must run on the *original* bytes,
before `mdlwrite` installs a mesh and moves the layout boundary. Patch first,
then rebuild geometry: the offsets in `extras` were recorded against the file as
it was exported.

A sub-scene is the hard case. The reader moves its keys onto the parent's clock
and into the parent's frame (`_onto_parent`), so both have to be run backwards:
`shift` undoes the clock and the parent placement `extras` carries undoes the
frame, through `_Frame`. A file exported before that placement was carried gets
its parented keys reported as skipped rather than written wrong -- see
`Patched.skipped`.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field

import numpy as np

from .. import scene as SC
from ..binreader import GTE_SCALE_SMALL
from ..scene import (CAMERA_EYE, CAMERA_TARGET, PLACEMENT_STRIDE, PROP_STRIDE,
                     QUATERNION_ONE, _multiply, rotation_matrix)
from .mdl import (PLACEMENT_FLAGS, PLACEMENT_ID, PLACEMENT_MATRIX,
                  PLACEMENT_TRANSLATION)

# Where a key keeps its pose, by the stride of the list it belongs to. An
# actor's record and a prop's disagree on all three, which is why the exporter
# writes the stride next to every key (§9.11.2).
KEY_LAYOUT = {
    PLACEMENT_STRIDE: (0x08, 0x20, 0x3C),  # position, rotation, scale
    PROP_STRIDE: (0x0C, 0x24, 0x40),
}
KEY_TICK = 0x00
KEY_DURATION = 0x04
GTE_ONE = 4096.0
# A shot's clock. The game ticks at the display rate and every key time in the
# file is one of those ticks.
TICKS_PER_SECOND = 30.0


@dataclass
class Patched:
    """What the patch actually wrote, and what it declined to."""

    placements: int = 0
    keys: int = 0
    camera_keys: int = 0
    emitters: int = 0
    skipped: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return self.placements + self.keys + self.camera_keys + self.emitters


def _fixed(value: float, one: float) -> int:
    """A float back to the fixed-point integer the file stores."""
    return int(round(float(value) * one))


def placement_of(placement) -> dict | None:
    """A sub-scene's placement, so a writer can invert what it was applied to.

    Without this the shift in a serialised shot undoes a sub-scene's clock but
    nothing undoes its frame, and a parented key can only be skipped on the way
    back.
    """
    if placement is None:
        return None
    return {"position": [float(v) for v in placement.position],
            "rotation": [float(v) for v in placement.rotation],
            "scale": [float(v) for v in placement.scale]}


def scene_extras(scene, model) -> dict:
    """The shot as the file itself holds it, ready for `patch_scene`.

    It lives beside the writer rather than beside any one front end, because it
    is the shape both of them speak: the glTF exporter puts it in `extras` and
    the Blender add-on stores it on the collection, and each hands the same dict
    back here to be written.

    Every entry carries the byte offset of the record it came from. That is what
    makes the trip back possible without understanding the object graph: a
    writer patches those fields where they already are, changing no count, no
    size and no offset, so the region whose record kinds are still unread
    (§8.3) is never rebuilt -- only read past.

    A particle emitter is written out whole (§9.11.7), field for field. No
    interchange format has particles at all, so a summary would be the end of
    them.
    """
    def track_of(track, first: int, stride: int) -> dict:
        """One track, with what it would take to write a key back.

        An actor's keys and a prop's are read from different offsets at
        different strides -- 0x30/0x4C against 0x24/0x50 -- so the writer has to
        be told which, or every prop offset lands in the wrong record. And a
        sub-scene's keys are not the file's: they are shifted onto the parent's
        clock and moved into its frame, so `shift` and `parented` say what to
        undo. 433 of the corpus's 4209 prop keys are in that position.
        """
        return {
            "node": track.node, "first": first, "stride": stride,
            "shift": track.shift, "parented": bool(track.parented),
            "parent": placement_of(track.parent),
            "keys": [{"at": track.node + first + stride * i,
                      "tick": int(k.tick), "duration": int(k.duration),
                      "position": [float(v) for v in k.position],
                      "rotation": [float(v) for v in k.rotation],
                      "scale": [float(v) for v in k.scale]}
                     for i, k in enumerate(track.keys)],
        }

    out: dict = {"window": list(scene.window) if scene.window else None,
                 "ticks_per_second": TICKS_PER_SECOND}
    out["actors"] = [{"mesh": a.mesh_index, "clip": a.clip_index,
                      "play": [a.play_start, a.play_end],
                      "delay": a.delay, "mode": a.mode,
                      "track": track_of(a.track, SC.PLACEMENT_KEYS,
                                        SC.PLACEMENT_STRIDE)}
                     for a in scene.actors]
    out["props"] = [{"mesh": p.mesh_index,
                     "track": track_of(p.track, SC.PROP_KEYS, SC.PROP_STRIDE)}
                    for p in scene.props]
    out["cameras"] = [{"node": c.node, "start": c.start, "end": c.end,
                       "screen_distance": float(c.screen_distance),
                       "shift": c.shift, "parented": bool(c.parented),
                       "parent": placement_of(c.parent),
                       "keys": [{"at": c.node + SC.CAMERA_KEYS
                                 + SC.CAMERA_STRIDE * i,
                                 "tick": int(k.tick), "duration": int(k.duration),
                                 "eye": [float(v) for v in k.eye],
                                 "target": [float(v) for v in k.target]}
                                for i, k in enumerate(c.keys)]}
                      for c in scene.cameras]
    out["emitters"] = [{"node": e.node, "mesh": e.mesh_index,
                        # `mesh` is the resolved index and the node holds an id
                        # in the 0x2000 namespace; `position` has been moved
                        # into the parent's frame. A writer needs both undone,
                        # so it is told how.
                        "namespace": SC.MESH_NAMESPACE,
                        "parent": placement_of(e.parent),
                        "shift": e.shift,
                        "start": e.start, "end": e.end,
                        "position": [float(v) for v in e.position],
                        "budget": e.budget, "per_tick": e.per_tick,
                        "lifetime": e.lifetime, "last_tick": e.last_tick,
                        "speed": list(e.speed), "yaw": list(e.yaw),
                        "pitch": list(e.pitch),
                        "accel": [float(v) for v in e.accel],
                        "damp": [float(v) for v in e.damp],
                        "spin": e.spin, "fade": list(e.fade),
                        "grow": list(e.grow)}
                       for e in scene.emitters]
    out["placements"] = [{"record": i.record, "id": i.id, "flags": i.flags,
                          "translation": [float(v) for v in i.translation],
                          "rotation": [float(v) for v in i.rotation]}
                         for i in model.instances]
    return out


class _Frame:
    """The inverse of `Placement.applied` -- a parent's frame, undone.

    The reader puts a sub-scene's keys into its parent's frame with
    `p + R(q) @ (s * v)` for a point and `q * r` for an orientation. Writing one
    back means running that backwards, so a key lands in the record as the file
    stated it rather than as the shot displays it.
    """

    def __init__(self, placement: dict) -> None:
        self.position = np.asarray(placement["position"], dtype=np.float64)
        self.rotation = np.asarray(placement["rotation"], dtype=np.float64)
        self.scale = np.asarray(placement["scale"], dtype=np.float64)
        self.basis = rotation_matrix(self.rotation).T
        # A zero component would divide by zero; all fourteen sub-scenes in the
        # game carry a unit scale, so this only guards the pathological file.
        self.safe_scale = np.where(np.abs(self.scale) < 1e-9, 1.0, self.scale)

    def point(self, world) -> np.ndarray:
        return (self.basis @ (np.asarray(world, dtype=np.float64)
                              - self.position)) / self.safe_scale

    def orientation(self, world) -> np.ndarray:
        x, y, z, w = self.rotation
        norm = float(np.dot(self.rotation, self.rotation))
        if norm < 1e-9:
            return np.asarray(world, dtype=np.float64)
        inverse = np.array([-x, -y, -z, w], dtype=np.float64) / norm
        return _multiply(inverse, np.asarray(world, dtype=np.float64))

    def factor(self, world) -> np.ndarray:
        return np.asarray(world, dtype=np.float64) / self.safe_scale


def _put_i32(data: bytearray, at: int, value: int) -> None:
    struct.pack_into("<i", data, at, max(-2147483648, min(2147483647, value)))


def _put_u16(data: bytearray, at: int, value: int) -> None:
    struct.pack_into("<H", data, at, value & 0xFFFF)


def _put_i16(data: bytearray, at: int, value: int) -> None:
    struct.pack_into("<h", data, at, max(-32768, min(32767, value)))


def _fits(data: bytearray, at: int, size: int) -> bool:
    return 0 <= at and at + size <= len(data)


def _patch_placement(data: bytearray, entry: dict, report: Patched) -> None:
    """One 160-byte placement record, as 0x8001E0A8 reads it (§8.5)."""
    base = int(entry["record"])
    if not _fits(data, base, PLACEMENT_STRIDE):
        report.skipped.append(f"placement at 0x{base:X} is outside the file")
        return

    _put_u16(data, base + PLACEMENT_FLAGS, int(entry["flags"]))
    _put_u16(data, base + PLACEMENT_ID, int(entry["id"]))
    for i, value in enumerate(entry["translation"][:3]):
        _put_i32(data, base + PLACEMENT_TRANSLATION + 4 * i,
                 _fixed(value, 1.0 / GTE_SCALE_SMALL))
    # The MATRIX's rotation is 3x3 i16 in 4096ths, row-major, the way
    # `_read_instance` reads it back.
    for i, value in enumerate(entry["rotation"][:9]):
        _put_i16(data, base + PLACEMENT_MATRIX + 2 * i, _fixed(value, GTE_ONE))
    report.placements += 1


def _patch_track(data: bytearray, track: dict, what: str,
                 report: Patched) -> None:
    """A node's keys, each written where the reader found it."""
    frame = None
    if track.get("parented"):
        if not track.get("parent"):
            report.skipped.append(
                f"{what} at 0x{int(track['node']):X}: its keys are in a "
                f"parent's frame and `extras` names no parent to undo it with")
            return
        frame = _Frame(track["parent"])
    stride = int(track["stride"])
    layout = KEY_LAYOUT.get(stride)
    if layout is None:
        report.skipped.append(f"{what}: unknown key stride 0x{stride:X}")
        return

    position_at, rotation_at, scale_at = layout
    shift = int(track.get("shift") or 0)
    for key in track["keys"]:
        at = int(key["at"])
        if not _fits(data, at, stride):
            report.skipped.append(f"{what}: key at 0x{at:X} is outside the file")
            continue
        position, rotation = key["position"][:3], key["rotation"][:4]
        scale = key["scale"][:3]
        if frame is not None:
            position = frame.point(position)
            rotation = frame.orientation(rotation)
            scale = frame.factor(scale)

        _put_i32(data, at + KEY_TICK, int(key["tick"]) - shift)
        _put_i32(data, at + KEY_DURATION, int(key["duration"]))
        for i, value in enumerate(position):
            _put_i32(data, at + position_at + 4 * i,
                     _fixed(value, 1.0 / GTE_SCALE_SMALL))
        for i, value in enumerate(rotation):
            _put_i32(data, at + rotation_at + 4 * i,
                     _fixed(value, QUATERNION_ONE))
        for i, value in enumerate(scale):
            _put_i32(data, at + scale_at + 4 * i, _fixed(value, QUATERNION_ONE))
        report.keys += 1


def _patch_camera(data: bytearray, camera: dict, report: Patched) -> None:
    """A camera node's keys: tick, duration, and the two points it looks along."""
    node = int(camera["node"])
    frame = None
    if camera.get("parented"):
        if not camera.get("parent"):
            report.skipped.append(
                f"camera at 0x{node:X}: its keys are in a parent's frame and "
                f"`extras` names no parent to undo it with")
            return
        frame = _Frame(camera["parent"])
    shift = int(camera.get("shift") or 0)
    for key in camera["keys"]:
        at = int(key["at"])
        if not _fits(data, at, CAMERA_EYE + 12):
            report.skipped.append(f"camera key at 0x{at:X} is outside the file")
            continue
        target, eye = key["target"][:3], key["eye"][:3]
        if frame is not None:
            target, eye = frame.point(target), frame.point(eye)

        _put_i32(data, at + KEY_TICK, int(key["tick"]) - shift)
        _put_i32(data, at + KEY_DURATION, int(key["duration"]))
        for i, value in enumerate(target):
            _put_i32(data, at + CAMERA_TARGET + 4 * i,
                     _fixed(value, 1.0 / GTE_SCALE_SMALL))
        for i, value in enumerate(eye):
            _put_i32(data, at + CAMERA_EYE + 4 * i,
                     _fixed(value, 1.0 / GTE_SCALE_SMALL))
        report.camera_keys += 1


def _patch_emitter(data: bytearray, emitter: dict, report: Patched) -> None:
    """One particle emitter's node, field by field (§9.11.7).

    Every field is a whole word at a fixed offset in the node, so this resizes
    nothing and touches only what the caller named -- the window it opens in,
    when it stops spawning, how many particles it has and how fast they leave,
    the cone, the acceleration and damping, the spin, and the two ramps.
    """
    node = emitter.get("node")
    if node is None:
        return
    # The window and the spawn cutoff are ticks of the shot's clock, and a
    # sub-scene's were shifted onto it; that comes off again before they are
    # written, exactly as a track's key times do.
    shift = int(emitter.get("shift") or 0)
    fields = {
        SC.NODE_WINDOW_START: None if emitter.get("start") is None
        else int(emitter["start"]) - shift,
        SC.NODE_WINDOW_END: None if emitter.get("end") is None
        else int(emitter["end"]) - shift,
        SC.EMITTER_LAST_TICK: None if emitter.get("last_tick") is None
        else int(emitter["last_tick"]) - shift,
        SC.EMITTER_BUDGET: emitter.get("budget"),
        SC.EMITTER_PER_TICK: emitter.get("per_tick"),
        SC.EMITTER_LIFETIME: emitter.get("lifetime"),
        # The node names its mesh by *id*, in the 0x2000 namespace, one-based:
        # the reader turns that into an index and writing the index straight
        # back aims every emitter at the wrong mesh -- which is how eight
        # emitters vanished from a shot that had been patched with nothing
        # changed at all.
        SC.EMITTER_MESH_ID: None if emitter.get("mesh") is None else
        (SC.MESH_NAMESPACE | (int(emitter["mesh"]) + 1)),
        SC.EMITTER_SPEED_MIN: (emitter.get("speed") or [None, None])[0],
        SC.EMITTER_SPEED_MAX: (emitter.get("speed") or [None, None])[1],
        SC.EMITTER_YAW: (emitter.get("yaw") or [None, None])[0],
        SC.EMITTER_YAW_SPREAD: (emitter.get("yaw") or [None, None])[1],
        SC.EMITTER_PITCH: (emitter.get("pitch") or [None, None])[0],
        SC.EMITTER_PITCH_SPREAD: (emitter.get("pitch") or [None, None])[1],
        SC.EMITTER_SPIN: emitter.get("spin"),
        SC.EMITTER_FADE_IN: (emitter.get("fade") or [None, None])[0],
        SC.EMITTER_FADE_OUT: (emitter.get("fade") or [None, None])[1],
        SC.EMITTER_GROW_END: (emitter.get("grow") or [None, None])[0],
        SC.EMITTER_SHRINK_START: (emitter.get("grow") or [None, None])[1],
    }
    for axis, offset in enumerate(SC.EMITTER_ACCEL):
        fields[offset] = (emitter.get("accel") or [None] * 3)[axis]
    for axis, offset in enumerate(SC.EMITTER_DAMP):
        value = (emitter.get("damp") or [None] * 3)[axis]
        fields[offset] = None if value is None else _fixed(value, SC.DAMP_ONE)

    written = 0
    for offset, value in fields.items():
        if value is None or not _fits(data, node + offset, 4):
            continue
        _put_i32(data, node + offset, int(round(float(value))))
        written += 1
    position = emitter.get("position")
    if position is not None and _fits(data, node + SC.EMITTER_POSITION, 12):
        # The reader moves a sub-scene's emitter into its parent's frame, the
        # same way it moves a track's keys, so that has to come off again
        # before the node's own three words are written.
        if emitter.get("parent"):
            position = _Frame(emitter["parent"]).point(position)
        for axis in range(3):
            _put_i32(data, node + SC.EMITTER_POSITION + 4 * axis,
                     _fixed(position[axis], 1.0 / GTE_SCALE_SMALL))
        written += 1
    if written:
        report.emitters += 1
    else:
        report.skipped.append(f"emitter at node {node:#x}: nothing to write")


def patch_scene(model_data: bytes, extras: dict) -> tuple[bytes, Patched]:
    """Write `extras` back into `model_data` without changing its size."""
    data = bytearray(model_data)
    report = Patched()

    for entry in extras.get("placements") or []:
        _patch_placement(data, entry, report)
    for actor in extras.get("actors") or []:
        _patch_track(data, actor["track"], "actor", report)
    for prop in extras.get("props") or []:
        _patch_track(data, prop["track"], "prop", report)
    for camera in extras.get("cameras") or []:
        _patch_camera(data, camera, report)
    for emitter in extras.get("emitters") or []:
        _patch_emitter(data, emitter, report)

    assert len(data) == len(model_data), "a scene patch must not resize the file"
    return bytes(data), report
