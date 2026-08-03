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

Two things it will not write, and the reason is the same for both. The scene
reader moves a sub-scene's keys onto the parent's clock and into the parent's
frame (`_onto_parent`), and `extras` records `shift` and `parented` but not the
parent's own placement. Subtracting `shift` undoes the clock; nothing in the
file's own `extras` undoes the frame. So a parented track or camera is reported
as skipped rather than written wrong -- see `Patched.skipped`.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field

from ..binreader import GTE_SCALE_SMALL
from ..scene import (CAMERA_EYE, CAMERA_TARGET, PLACEMENT_STRIDE, PROP_STRIDE,
                     QUATERNION_ONE)
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


@dataclass
class Patched:
    """What the patch actually wrote, and what it declined to."""

    placements: int = 0
    keys: int = 0
    camera_keys: int = 0
    skipped: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return self.placements + self.keys + self.camera_keys


def _fixed(value: float, one: float) -> int:
    """A float back to the fixed-point integer the file stores."""
    return int(round(float(value) * one))


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
    if track.get("parented"):
        report.skipped.append(
            f"{what} at 0x{int(track['node']):X}: its keys are in a parent's "
            f"frame, which `extras` cannot undo")
        return
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
        _put_i32(data, at + KEY_TICK, int(key["tick"]) - shift)
        _put_i32(data, at + KEY_DURATION, int(key["duration"]))
        for i, value in enumerate(key["position"][:3]):
            _put_i32(data, at + position_at + 4 * i,
                     _fixed(value, 1.0 / GTE_SCALE_SMALL))
        for i, value in enumerate(key["rotation"][:4]):
            _put_i32(data, at + rotation_at + 4 * i,
                     _fixed(value, QUATERNION_ONE))
        for i, value in enumerate(key["scale"][:3]):
            _put_i32(data, at + scale_at + 4 * i, _fixed(value, QUATERNION_ONE))
        report.keys += 1


def _patch_camera(data: bytearray, camera: dict, report: Patched) -> None:
    """A camera node's keys: tick, duration, and the two points it looks along."""
    node = int(camera["node"])
    if camera.get("parented"):
        report.skipped.append(
            f"camera at 0x{node:X}: its keys are in a parent's frame, which "
            f"`extras` cannot undo")
        return
    shift = int(camera.get("shift") or 0)
    for key in camera["keys"]:
        at = int(key["at"])
        if not _fits(data, at, CAMERA_EYE + 12):
            report.skipped.append(f"camera key at 0x{at:X} is outside the file")
            continue
        _put_i32(data, at + KEY_TICK, int(key["tick"]) - shift)
        _put_i32(data, at + KEY_DURATION, int(key["duration"]))
        for i, value in enumerate(key["target"][:3]):
            _put_i32(data, at + CAMERA_TARGET + 4 * i,
                     _fixed(value, 1.0 / GTE_SCALE_SMALL))
        for i, value in enumerate(key["eye"][:3]):
            _put_i32(data, at + CAMERA_EYE + 4 * i,
                     _fixed(value, 1.0 / GTE_SCALE_SMALL))
        report.camera_keys += 1


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

    assert len(data) == len(model_data), "a scene patch must not resize the file"
    return bytes(data), report
