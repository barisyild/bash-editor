"""Rewrite a level's placement records (§8.5) -- what it draws, and where.

A level draws what its placement list names, and nothing else: `warp_room1` has
81 placements and not one names any of the 42 meshes in `model.meshes`, so the
room the player walks through is object-pool meshes standing where these records
put them. Editing a record is therefore the one edit that changes a level, and
the only one that has been made to work on hardware.

Every field of a record answers to the file, each confirmed by its own probe:

* the **count** at `sub-object +0x1C` -- taking `warp_room1`'s 81 to 80, one
  byte, draws the room with its last object gone
* the **array base** at `sub-object +0x20` -- pointed at a copy of four records
  inside the resident region, the room comes back drawing those four
* the **id** at `record +0x88` and the **translation** at `+0x04` -- spending
  the second of two `0x5047` records on the green panel put a second panel in
  the room, between the POLAR PANIC and POGO PAINTER doors, while the first
  stayed at its own door

What cannot be done is make the list longer. Its array is boxed in -- the
sub-object's `+0x0C`, `+0x10`, `+0x14` and `+0x18` all point at arrays that
begin where it ends -- the resident region holds no run of zeros big enough to
relocate 81 records into (the largest is 1325 bytes against 12,960), and nothing
past the shipped resident size is there at run time: the same array copied
beyond the old end of file with `0x50` grown to cover it drew nothing at all. So
a room's spare capacity is whatever records it can afford to lose, and five of
`warp_room1`'s objects are placed twice.
"""

from __future__ import annotations

import struct

from .mdl import (
    GTE_ONE,
    OBJECT_NAMESPACE,
    PLACEMENT_ID,
    PLACEMENT_MATRIX,
    PLACEMENT_STRIDE,
    PLACEMENT_TRANSLATION,
    Instance,
    Model,
)
from ..binreader import GTE_SCALE_SMALL


def object_id(slot: int) -> int:
    """The id that names object `slot`, one-based as the resolver reads it."""
    return OBJECT_NAMESPACE | (slot + 1)


def write_placement(data: bytes, instance: Instance, *,
                    identifier: int | None = None,
                    translation: tuple[float, float, float] | None = None,
                    rotation: tuple[float, ...] | None = None) -> bytes:
    """One record rewritten in place; every other byte of the file is untouched.

    `translation` is in the same units `Instance.translation` reports, and
    `rotation` is the row-major nine already divided by 4096 -- so a value read
    off a model can be handed straight back.
    """
    out = bytearray(data)
    at = instance.record
    if at + PLACEMENT_STRIDE > len(out):
        raise ValueError(f"placement {instance.index} runs past the end of the file")

    if identifier is not None:
        if not 0 <= identifier <= 0xFFFF:
            raise ValueError(f"{identifier:#x} is not a 16-bit id")
        struct.pack_into("<H", out, at + PLACEMENT_ID, identifier)

    if translation is not None:
        packed = [int(round(v / GTE_SCALE_SMALL)) for v in translation]
        for value in packed:
            if not -(2 ** 31) <= value < 2 ** 31:
                raise ValueError(f"translation {translation} does not fit i32")
        struct.pack_into("<3i", out, at + PLACEMENT_TRANSLATION, *packed)

    if rotation is not None:
        if len(rotation) != 9:
            raise ValueError("a rotation is nine values, row-major")
        packed = [max(-32768, min(32767, int(round(v * GTE_ONE))))
                  for v in rotation]
        struct.pack_into("<9h", out, at + PLACEMENT_MATRIX, *packed)

    return bytes(out)


def spare_records(model: Model) -> list[int]:
    """Placements whose object another record already places.

    The list cannot grow, so these are the only room a level has for something
    new -- spending one costs a duplicate of whatever it draws.
    """
    seen: dict[int, int] = {}
    spare = []
    for instance in model.instances:
        if instance.id in seen:
            spare.append(instance.index)
        else:
            seen[instance.id] = instance.index
    return spare
