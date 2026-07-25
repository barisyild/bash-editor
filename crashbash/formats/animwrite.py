"""Write MDL vertex animation: the inverse of `anim.read_animations`.

A blob is entirely self-relative inside itself -- every pointer is an offset from
the field holding it -- so one can be built without knowing where it will land
and dropped anywhere in the file. Only the clip descriptor carries absolute
offsets, and `retarget` is what fills those in.

The layout follows what the reader measured across all 1037 clips::

    [u32 pool pointer][16-byte frame records][auxiliary blocks][keyframes][pool]

Nothing here invents a field. Frame records, keyframe entries and the bounds
block are written exactly as §9 of docs/FORMAT.md describes them, and the one
field whose meaning is still unknown -- the per-frame auxiliary block -- is
copied through rather than generated.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

import numpy as np

from .anim import (
    DESCRIPTOR_SIZE,
    FRAME_RECORD_SIZE,
    KEYFRAME_HEADER,
    POOL_STRIDE,
    PTR_ANIM_COUNT,
    PTR_ANIM_TABLE,
    keyframe_stride,
)

# A keyframe entry packs the pool index into the top 14 bits, so a blob cannot
# address more positions than that.
POOL_INDEX_SHIFT = 2
MAX_POOL_ENTRIES = 1 << 14

# Blobs in the game start on a 0x800 boundary. Nothing requires it -- the
# descriptor gives an absolute offset -- but matching it keeps a rebuilt file
# looking like the ones it sits beside.
BLOB_ALIGNMENT = 0x800


@dataclass
class FrameSpec:
    """One displayed frame: which keyframes it blends and how far between them."""

    key_a: int  # index into the keyframe list
    key_b: int | None = None  # None when the frame sits exactly on key_a
    weight: int = 0  # 0..0x1000
    aux: bytes = b""  # copied through untouched; contents unknown


@dataclass
class ClipSpec:
    """A clip ready to be written: its poses, its timeline and its name.

    `vertex_flags` belongs to the clip rather than the model because a model's
    clips may drive different meshes, and so different vertex counts.

    It must be the driven mesh's own flag words, in pool order. The game draws
    the animated pose, and a keyframe entry's low two bits are that vertex's
    flag word -- winding included -- so these bits are what the renderer sees.
    The game's own keyframes repeat the mesh's flags in 5,148 of 5,148 measured;
    write zeros here and every triangle lands on one winding, which shreds the
    model at draw time while every static-data check still passes.
    """

    poses: list[np.ndarray]  # each (V, 3) int16 in model units
    frames: list[FrameSpec]
    name_hash: int
    mesh_header: int  # absolute offset of the mesh header the clip drives
    vertex_flags: np.ndarray | None = None  # (V,) low two bits per vertex


def _bounds_block(pose: np.ndarray) -> bytes:
    """The 0x14-byte bounds block a keyframe carries in front of its entries.

    The two corners are stored interleaved, `(minX, maxY, minZ)` then
    `(maxX, minY, maxZ)`, followed by the centre and a radius.
    """
    low = pose.min(axis=0).astype(np.int64)
    high = pose.max(axis=0).astype(np.int64)
    centre = (low + high) // 2
    radius = int(np.ceil(np.sqrt(float(((high - low) ** 2).sum())) / 2))
    values = [
        low[0], high[1], low[2],
        high[0], low[1], high[2],
        centre[0], centre[1], centre[2],
        radius,
    ]
    return struct.pack("<10h", *(int(np.clip(v, -32768, 32767)) for v in values))


def build_blob(clip: ClipSpec) -> bytes:
    """Assemble one clip's blob. Position-independent: every pointer is relative."""
    if not clip.poses:
        raise ValueError("a clip needs at least one keyframe")
    vertex_count = clip.poses[0].shape[0]
    for pose in clip.poses:
        if pose.shape != (vertex_count, 3):
            raise ValueError("every pose must have the same shape")
    if clip.vertex_flags is None:
        vertex_flags = np.zeros(vertex_count, dtype=np.uint16)
    else:
        vertex_flags = np.asarray(clip.vertex_flags)
        if vertex_flags.shape[0] != vertex_count:
            raise ValueError(
                f"{vertex_flags.shape[0]} vertex flags for {vertex_count} vertices"
            )

    # One pool shared by every keyframe, deduplicated the way the game's own
    # blobs are -- a pose is the index array, not a run of positions.
    stacked = np.concatenate(clip.poses, axis=0).astype(np.int16)
    pool, inverse = np.unique(stacked, axis=0, return_inverse=True)
    if pool.shape[0] > MAX_POOL_ENTRIES:
        raise ValueError(
            f"{pool.shape[0]} distinct positions exceed the {MAX_POOL_ENTRIES} a "
            "14-bit keyframe entry can address"
        )
    slots = inverse.reshape(len(clip.poses), vertex_count)
    flags = (vertex_flags & 0x3).astype(np.uint16)

    stride = keyframe_stride(vertex_count)
    records = FRAME_RECORD_SIZE * len(clip.frames)

    # Lay the sections out before writing, because the frame records have to name
    # keyframe offsets that only exist once the auxiliary blocks are placed.
    aux_at: list[int] = []
    cursor = 4 + records
    for frame in clip.frames:
        aux_at.append(cursor if frame.aux else 0)
        cursor += len(frame.aux)
    cursor = (cursor + 3) & ~3
    keyframe_at = [cursor + i * stride for i in range(len(clip.poses))]
    pool_at = cursor + stride * len(clip.poses)
    total = pool_at + POOL_STRIDE * pool.shape[0]

    blob = bytearray(total)
    struct.pack_into("<i", blob, 0, pool_at)  # self-relative from blob + 0

    for i, frame in enumerate(clip.frames):
        record = 4 + FRAME_RECORD_SIZE * i
        if not 0 <= frame.key_a < len(keyframe_at):
            raise ValueError(f"frame {i}: keyframe {frame.key_a} does not exist")
        a = keyframe_at[frame.key_a] - record
        if frame.key_b is None:
            b = 0
        else:
            if not 0 <= frame.key_b < len(keyframe_at):
                raise ValueError(f"frame {i}: keyframe {frame.key_b} does not exist")
            b = keyframe_at[frame.key_b] - (record + 4)
        aux = aux_at[i] - (record + 0x0C) if aux_at[i] else 0
        struct.pack_into("<4i", blob, record, a, b, frame.weight, aux)
        if frame.aux:
            blob[aux_at[i] : aux_at[i] + len(frame.aux)] = frame.aux

    for i, pose in enumerate(clip.poses):
        at = keyframe_at[i]
        blob[at : at + KEYFRAME_HEADER] = _bounds_block(pose)
        entries = (slots[i].astype(np.uint16) << POOL_INDEX_SHIFT) | flags
        blob[at + KEYFRAME_HEADER : at + KEYFRAME_HEADER + 2 * vertex_count] = (
            entries.astype("<u2").tobytes()
        )

    blob[pool_at:total] = pool.astype("<i2").tobytes()
    return bytes(blob)


# Every animated model in the game ends exactly 4 bytes past its last blob
# (223/223), so a longer tail means something else is out there.
BLOB_TAIL_SLACK = 4


def _reclaimable(data: bytes, blobs: list[tuple[int, int]]) -> int | None:
    """Where the old blobs begin, if the space they occupy is theirs alone.

    Every clip is being replaced, so the old blobs are dead. Reusing their space
    keeps the file from doubling, but only when nothing else lives out there: the
    geometry must end before the first blob, and the last blob must run to the
    end of the file.
    """
    from .anim import PTR_MODEL_END, _target  # noqa: PLC0415

    if not blobs:
        return None
    try:
        geometry_end = _target(data, PTR_MODEL_END)
    except (struct.error, IndexError):
        return None
    first = min(start for start, _ in blobs)
    last = max(end for _, end in blobs)
    if len(data) - last > BLOB_TAIL_SLACK or first < geometry_end:
        return None
    return first


def write_clips(data: bytes, clips: list[ClipSpec], reclaim: bool = True) -> bytes:
    """Replace a model's clips with `clips`, writing their blobs after the geometry.

    The descriptor table is rewritten in place, so the new clip count must match
    the old one -- which is the case that matters here, one model's timeline
    moved onto another's geometry.

    With `reclaim` the old blobs' space is reused, since every one of them is
    being replaced. It is given up whenever that cannot be shown to be safe, and
    then the new blobs simply go on the end.
    """
    out = bytearray(data)
    count = struct.unpack_from("<i", out, PTR_ANIM_COUNT)[0]
    if count != len(clips):
        raise ValueError(
            f"the model declares {count} clips and {len(clips)} were given; "
            "rewriting the table's length is not supported"
        )
    table = PTR_ANIM_TABLE + struct.unpack_from("<i", out, PTR_ANIM_TABLE)[0]

    if reclaim:
        old = [
            struct.unpack_from("<2I", out, table + DESCRIPTOR_SIZE * i)
            for i in range(count)
        ]
        floor = _reclaimable(data, old)
        if floor is not None and floor >= table + DESCRIPTOR_SIZE * count:
            del out[floor:]

    for index, clip in enumerate(clips):
        blob = build_blob(clip)
        start = (len(out) + BLOB_ALIGNMENT - 1) & ~(BLOB_ALIGNMENT - 1)
        out.extend(b"\x00" * (start - len(out)))
        out.extend(blob)

        at = table + DESCRIPTOR_SIZE * index
        struct.pack_into("<3I", out, at, start, start + len(blob), len(clip.frames))
        struct.pack_into("<i", out, at + 0x0C, clip.mesh_header - (at + 0x0C))
        struct.pack_into("<I", out, at + 0x10, clip.name_hash)
        struct.pack_into("<I", out, at + 0x14, 0)  # zero in 1037/1037 of the game's
    return bytes(out)
