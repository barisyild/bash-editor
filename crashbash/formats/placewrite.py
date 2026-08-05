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

Making the list *longer* was called impossible here for a long time, and the
reasoning was sound but aimed at the wrong move. Relocating the array is what
has no room: the resident region's largest run of zeros is 1325 bytes against
the 12,960 an 81-record array needs, and nothing past the shipped resident size
is there at run time -- the same array copied beyond the old end of file with
`0x50` grown to cover it drew nothing at all.

`append_placement` does not relocate it. It **slides the four blocks that follow
it** one record further on, into the zeros the resident region already ends
with. In `warp_room1` those blocks run from the array's end at `0x268E0` to
`0x29000`, which is `T(0x44)` and `i32@0x50` both, and the last 160 bytes of
that span are zero -- because the `+0x14` block's own count is 0, so it uses 8
of its 544 bytes and the other 536 are padding. Slide the span onto them and the
array has one more record's worth of room, the file is exactly the size it was,
and the resident region ends where it did.

Both invariants the corpus states survive the slide, which is why it is done
this way and not by moving the two small blocks out to the padding:

* `+0x0C`'s target is the **end of the record array**, 73/73 -- and it still is,
  because the array grew by one record and the block moved by one record.
* `+0x14`'s target is the **last block in the file**, running to `T(0x44)` in
  73/73 -- and it still is.

Every self-relative pointer *inside* the span keeps its value, source and target
having moved together; only the sub-object's own four reach further. What is not
established is whether anything outside the span points into it. Nothing in the
format documents such a pointer and the sub-object is reached only through
`model+0x18`, but a scan of the file cannot tell a pointer from a vertex that
resolves there by chance, so this is a hardware question, not a static one.
"""

from __future__ import annotations

import struct

from .mdl import (
    COUNT_SUBOBJECTS,
    GTE_ONE,
    OBJECT_NAMESPACE,
    PLACEMENT_ID,
    PLACEMENT_MATRIX,
    PLACEMENT_MATRIX_T,
    PLACEMENT_STRIDE,
    PLACEMENT_TRANSLATION,
    PTR_SUBOBJECTS,
    SUBOBJECT_COUNT,
    SUBOBJECT_RECORDS,
    Instance,
    Model,
)
from ..binreader import GTE_SCALE_SMALL

# The sub-object's own pointers at the blocks laid after the record array. All
# four are self-relative and all four have to reach one record further once the
# span they name has slid; `+0x14` and `+0x18` hold the same value in 73/73 and
# are stretched separately rather than assumed equal.
TRAILING_POINTERS = (0x0C, 0x10, 0x14, 0x18)
# `i32@0x50`, a plain length from the model's base rather than self-relative.
# Nothing past it is there at run time, so it is the ceiling the slide works
# under and never something to grow.
RESIDENT_SIZE = 0x50


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
        # The MATRIX's own `t[3]` says the same thing, and the archive keeps the
        # two in step in 2689/2689 records. Nothing is known to read it -- the
        # loader takes the position from +0x04, and a disc built with the two
        # disagreeing drew its object in the right place -- but a 2689/2689
        # agreement is not something to break for free.
        struct.pack_into("<3i", out, at + PLACEMENT_MATRIX_T, *packed)

    if rotation is not None:
        if len(rotation) != 9:
            raise ValueError("a rotation is nine values, row-major")
        packed = [max(-32768, min(32767, int(round(v * GTE_ONE))))
                  for v in rotation]
        struct.pack_into("<9h", out, at + PLACEMENT_MATRIX, *packed)

    return bytes(out)


def _subobject(data: bytes, model: Model) -> int:
    """Where the one sub-object's header starts, resolved as the game does."""
    if not model.instances:
        raise ValueError("this model has no placement list")
    i32 = lambda at: struct.unpack_from("<i", data, at)[0]  # noqa: E731
    table = PTR_SUBOBJECTS + i32(PTR_SUBOBJECTS)
    if i32(COUNT_SUBOBJECTS) != 1:
        raise ValueError(
            f"expected one sub-object, this model states {i32(COUNT_SUBOBJECTS)}")
    entry = table + 4
    return entry + i32(entry)


def spare_capacity(data: bytes, model: Model) -> int:
    """How many more records this level's list can be grown by.

    The padding at the end of the resident region is the whole of the room, so
    this is that padding divided by a record. Across the game it comes to 53
    records in 8 models -- all five warp rooms, both demo hubs and Oxide's chase
    level, which has 1746 spare bytes. The other 65 levels get nothing: an arena
    typically ends its resident region with 6 or 18 bytes of alignment and no
    padding at all.

    Zero is the answer for any level laid out differently from the corpus, so a
    caller can treat this as "may `append_placement` be called" without
    repeating its checks.
    """
    try:
        sub = _subobject(data, model)
    except ValueError:
        return 0
    i32 = lambda at: struct.unpack_from("<i", data, at)[0]  # noqa: E731
    resident = i32(RESIDENT_SIZE)
    if not 0 < resident <= len(data):
        return 0
    count = i32(sub + SUBOBJECT_COUNT)
    records = sub + SUBOBJECT_RECORDS + i32(sub + SUBOBJECT_RECORDS)
    end = records + PLACEMENT_STRIDE * count
    targets = [sub + off + i32(sub + off) for off in TRAILING_POINTERS]
    if min(targets) != end or max(targets) >= resident:
        return 0
    padding = 0
    while padding < resident - end and data[resident - padding - 1] == 0:
        padding += 1
    return padding // PLACEMENT_STRIDE


def append_placement(data: bytes, model: Model, source: Instance, *,
                     identifier: int | None = None,
                     translation: tuple[float, float, float] | None = None,
                     rotation: tuple[float, ...] | None = None) -> bytes:
    """One more record on the end of the list, copied from `source`.

    The four blocks that follow the array slide one record further on, into the
    padding the resident region ends with, and the sub-object's four pointers
    are stretched to match. The file keeps its length and its resident size, and
    every byte before the array's old end is untouched.

    It refuses rather than build a level that cannot load: the padding has to be
    there, it has to be zero, and the blocks have to be laid out the way the
    corpus says they are.
    """
    out = bytearray(data)
    i32 = lambda at: struct.unpack_from("<i", out, at)[0]  # noqa: E731

    sub = _subobject(out, model)
    count = i32(sub + SUBOBJECT_COUNT)
    records = sub + SUBOBJECT_RECORDS + i32(sub + SUBOBJECT_RECORDS)
    array_end = records + PLACEMENT_STRIDE * count
    resident = i32(RESIDENT_SIZE)

    # The blocks that follow, in the order the header names them. `+0x0C` has to
    # be the first of them: it is the array's own end, and the slide is only
    # correct if nothing sits between the array and the span being moved.
    trailing = [sub + off for off in TRAILING_POINTERS]
    targets = [at + i32(at) for at in trailing]
    if min(targets) != array_end:
        raise ValueError(
            f"the first block after the array starts at {min(targets):#x}, not at "
            f"the array's end {array_end:#x}; this level is not laid out the way "
            f"the slide assumes")
    if resident > len(out):
        raise ValueError(
            f"the resident size {resident:#x} runs past the file's {len(out):#x}")
    if max(targets) >= resident:
        raise ValueError("a trailing block starts outside the resident region")

    padding = out[resident - PLACEMENT_STRIDE:resident]
    if any(padding):
        raise ValueError(
            f"the {PLACEMENT_STRIDE} bytes before the resident end {resident:#x} "
            f"are not padding, so there is nowhere for the blocks to slide; this "
            f"level cannot take another record")

    # Slide, then stretch the four pointers over the same distance. Pointers
    # inside the span keep their values: source and target moved together.
    out[array_end + PLACEMENT_STRIDE:resident] = data[array_end:resident - PLACEMENT_STRIDE]
    for at in trailing:
        struct.pack_into("<i", out, at, i32(at) + PLACEMENT_STRIDE)

    # The new record is a copy of one that already works, so every field this
    # project does not understand arrives already set to something the game ran.
    out[array_end:array_end + PLACEMENT_STRIDE] = data[
        source.record:source.record + PLACEMENT_STRIDE]
    struct.pack_into("<i", out, sub + SUBOBJECT_COUNT, count + 1)

    if len(out) != len(data):
        raise AssertionError("the slide changed the file's length")
    fresh = Instance(index=count, record=array_end, id=source.id,
                     flags=source.flags, rotation=source.rotation,
                     translation=source.translation)
    return write_placement(bytes(out), fresh, identifier=identifier,
                           translation=translation, rotation=rotation)


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
