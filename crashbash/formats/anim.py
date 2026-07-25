"""Crash Bash MDL vertex animation.

Animation is the bulk of the model data -- 17.7 MB of the 31.8 MB of MDL bytes in
the NTSC-U set -- and it is stored as whole poses, not as a skeleton. 225 of the
400 models carry 1037 clips totalling 49,167 frames.

The header field `i32@0x40` gives a clip count and `0x44` points (self-relatively,
like every other pointer in an MDL) at that many 24-byte descriptors. Each names a
byte range inside the same DAT entry that the loader pulls into RAM as one
resident buffer -- the *blob*. Everything inside a blob is self-relative to the
field holding it, so the buffer relocates freely::

    80019B34  lw    $v1, 0x44($s1)   ; descriptor[(id & 0xF80) >> 7], stride 24
    80019B70  lw    $v0, 8($s0)      ; +0x08 frame count, bounds-checks the frame
    80019B8C  lw    $v0, 0xc($s0)    ; +0x0C self-relative -> the driven mesh
    80019BA4  addu  $t1, $a3, $v0    ; record = blob + 4 + 16*frame
    80019BF0  lw    $a2, ($a3)       ; blob + 0x00 = the vector pool, 0 -> model+0x28

A blob is tiled exactly, with no gaps, in this order (1037/1037 over the corpus)::

    [u32 pool pointer][16-byte frame records][auxiliary blocks][keyframes][pool]

A *frame record* is one displayed frame, not one authored key: the timeline is
pre-baked. The game's clock advances by exactly 1.0 frame per tick (the 16.16
constant at 0x80058B64 has one writer, `lui $v0,1`), so the stored 12-bit weight
is the whole of the interpolation. Frames with weight 0 sit on a keyframe and go
through the copy decoder at 0x8001C1E0; the rest blend two keyframes through GTE
INTPL at 0x8001C0F0.

A *keyframe* is the same 0x14-byte bounds block a mesh header carries, followed by
one u16 per vertex: `u16 >> 2` indexes the 6-byte position pool and `u16 & 3` is
the flag word the static vertex record stores as its fourth int16. Keyframes are
laid out at a fixed stride of `round4(0x14 + 2*V)`, where V is the vertex count the
decoders obtain by walking the *mesh's own* strip list -- so a decoded pose is in
exactly the same vertex order as `mdl.Mesh.positions`, and can be substituted for
it directly.

Frame 0 is NOT a bind pose. It is a plain copy of a keyframe, and that keyframe
reproduces the mesh's static positions in only 39 of 1037 clips; the static pool
at `mesh+0x10` is a separate pose that the animation replaces wholesale. The game
never writes the decoded vertices back into the mesh either -- it fills a side
buffer at 0x80056AC8 and hands that to the rasteriser as an argument -- so callers
should draw from `pose()` and leave `Mesh.positions` alone.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

import numpy as np

from ..binreader import GTE_SCALE_SMALL
from .mdl import Bounds, Mesh, Model

DESCRIPTOR_SIZE = 0x18
FRAME_RECORD_SIZE = 0x10
KEYFRAME_HEADER = 0x14  # bounds block; the decoders skip it without reading it
POOL_STRIDE = 6  # i16 x, y, z -- no padding, unlike the static vertex record
WEIGHT_ONE = 0x1000  # the game's 1.0, loaded into GTE IR0

PTR_MODEL_END = 0x08  # file header: end of the geometry, i.e. end of the 0x28 pool
PTR_MODEL_POOL = 0x28  # shared pool used when a blob supplies none
PTR_ANIM_COUNT = 0x40
PTR_ANIM_TABLE = 0x44

Vec3 = tuple[float, float, float]


def name_hash(name: str) -> int:
    """The game's name hash, `sum(name[i] * (i + 1))` with i 1-based (0x8001534C).

    It is a sum, not a mix, so it collides freely -- HIT and HOP both hash to 470.
    Treat a match as a hint and the hash itself as the identity.
    """
    return sum(ord(c) * (i + 1) for i, c in enumerate(name))


# Words tried against the 1037 stored hashes. 564 of them match one of these, and
# the distribution reads like an animation set (BREATHE 114, WIN 108, RUN 40...),
# which is the only reason to believe any of it: no name table survives in the
# data, so a "resolved" name is a guess that reproduces the hash, never a fact.
# The words that matched nothing have been dropped rather than left as decoration.
_CANDIDATE_NAMES = (
    "ATTACK", "BARGE", "BOUNCE", "BREATHE", "DIE", "FALL", "FLY", "HIT", "HOP",
    "JUMP", "LAUGH", "LOSE", "LOSE_BREATHE", "OPEN", "PUSH", "RUN", "SINK",
    "SLEEP", "SLIDE", "START", "STOP", "SWIM", "SWING", "TAUNT", "TURN", "WALK",
    "WIN", "WIN_BREATHE",
)

NAME_HASHES: dict[int, tuple[str, ...]] = {}
for _word in _CANDIDATE_NAMES:
    _h = name_hash(_word)
    NAME_HASHES[_h] = NAME_HASHES.get(_h, ()) + (_word,)


def _i32(data: bytes, at: int) -> int:
    return struct.unpack_from("<i", data, at)[0]


def _u32(data: bytes, at: int) -> int:
    return struct.unpack_from("<I", data, at)[0]


def _target(data: bytes, at: int) -> int:
    """Resolve a self-relative pointer stored at `at`, as the game does."""
    return at + _i32(data, at)


def _read_bounds(data: bytes, at: int) -> Bounds:
    """The same interleaved 0x14-byte block `mdl` reads in front of a vertex pool.

    Duplicated here rather than shared because a keyframe's copy is not part of any
    mesh: the animation path adds 0x14 and never looks at it (0x80019BFC), so it is
    metadata for the culler, not something the decoder needs.
    """
    min_x, max_y, min_z, max_x, min_y, max_z = struct.unpack_from("<6h", data, at)
    cx, cy, cz, radius = struct.unpack_from("<4h", data, at + 12)
    s = GTE_SCALE_SMALL
    return Bounds(
        (min_x * s, min_y * s, min_z * s),
        (max_x * s, max_y * s, max_z * s),
        (cx * s, cy * s, cz * s),
        radius * s,
    )


def keyframe_stride(vertex_count: int) -> int:
    """Bytes from one keyframe to the next: the u16 array is padded to 4 bytes.

    Holds for every measurable gap in every blob (1036/1036 clips with more than
    one keyframe), and the pad bytes are zero in all 441 clips with an odd V.
    """
    return (KEYFRAME_HEADER + 2 * vertex_count + 3) & ~3


@dataclass
class Frame:
    """One 16-byte record at `blob + 4 + 16*index`: the pose shown on one tick.

    `weight` is 0 exactly when `key_b` is absent (13,652 records each way, with no
    record having one without the other), which is how the game picks between its
    copy decoder and its blend decoder. No record anywhere stores a weight of
    exactly 0x1000; the largest in the corpus is 0xFE2.
    """

    index: int
    record_offset: int
    key_a: int  # file offset of a keyframe
    key_b: int  # 0 when the frame sits exactly on key_a
    weight: int  # 0 .. 0x1000, where 0x1000 would be "all B"
    aux: int  # 0 when absent; see Animation.aux_block

    @property
    def interpolated(self) -> bool:
        return self.weight != 0


@dataclass
class Animation:
    """One clip: a descriptor, its baked frame timeline and the pool they index."""

    index: int
    descriptor_offset: int
    start: int  # blob range, absolute from the entry base, 0x800-aligned
    end: int
    frame_count: int
    name_hash: int
    mesh_index: int | None  # None when +0x0C does not land on a mesh header
    mesh_header_offset: int
    vertex_count: int
    pool_offset: int
    pool_count: int
    pool_is_shared: bool  # True when blob+0x00 was 0 and model+0x28 was used
    frames: list[Frame] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    _data: bytes = field(default=b"", repr=False)
    _pool: np.ndarray | None = field(default=None, repr=False, compare=False)

    # -- identity --------------------------------------------------------

    @property
    def name_candidates(self) -> tuple[str, ...]:
        """Every guessed word that reproduces `name_hash`, possibly several."""
        return NAME_HASHES.get(self.name_hash, ())

    @property
    def name(self) -> str | None:
        """The guessed name when exactly one candidate fits, else None.

        None does not mean the clip is nameless -- 473 of 1037 hashes match nothing
        that was tried, and two hashes match two words each.
        """
        candidates = self.name_candidates
        return candidates[0] if len(candidates) == 1 else None

    @property
    def label(self) -> str:
        return self.name or f"anim_{self.index:02d}_{self.name_hash:04X}"

    # -- timeline --------------------------------------------------------

    @property
    def loops(self) -> bool:
        """Whether the clip was authored to wrap back to frame 0.

        A clip that ends mid-blend has that blend aimed at frame 0's keyframe --
        true in 486/486 such clips -- so playing ...N-1, 0, 1... is continuous. The
        other 551 clips end sitting on a keyframe, where wrapping would cut, and
        read as one-shots. The runtime can loop either kind (0x8001F288 takes the
        time modulo the clip length), so this is authoring intent, not a flag.
        """
        return bool(self.frames) and self.frames[-1].interpolated

    def keyframes(self) -> list[int]:
        """Distinct keyframe offsets, in ascending address order.

        Ascending order is also chronological: the frames with weight 0 are exactly
        and bijectively the keyframes, in every one of the 1037 clips.
        """
        seen = {f.key_a for f in self.frames} | {f.key_b for f in self.frames if f.key_b}
        return sorted(seen)

    def keyframe_bounds(self, offset: int) -> Bounds:
        """The keyframe's own bounding box, in `mdl.Bounds` units.

        It is the true axis-aligned box of that pose: exact in 11,876 of 13,652
        keyframes and containing the decoded points to within 2/256 of a unit in
        13,651 of them.
        """
        return _read_bounds(self._data, offset)

    def aux_block(self, frame_index: int) -> bytes:
        """The frame's auxiliary block, or b"" -- contents undecoded.

        5354 of 49,167 records carry one, in 192 clips. Its size is
        `4 + 8*u16@+0 + 16*u16@+2` (4734/4734 blocks measured against the gap to the
        next one) and the blocks tile the space between the record array and the
        first keyframe. The draw path never reads the field; the only reader is the
        0x4000 id namespace at 0x800156DC, which returns the block to script code.
        What it means is unknown and deliberately not guessed at here.
        """
        frame = self.frames[frame_index]
        if not frame.aux:
            return b""
        n0, n1 = struct.unpack_from("<2H", self._data, frame.aux)
        size = 4 + 8 * n0 + 16 * n1
        return self._data[frame.aux : frame.aux + size]

    # -- decoding --------------------------------------------------------

    def pool(self) -> np.ndarray:
        """The (N, 3) int32 pool of positions every keyframe indexes.

        Held as int32 rather than int16 because the blend arithmetic below must not
        wrap: the differences are computed at full width and only the result is
        int16-sized in practice (no clamp fires anywhere in the corpus).
        """
        if self._pool is None:
            count = max(0, self.pool_count)
            raw = np.frombuffer(
                self._data, dtype="<i2", count=count * 3, offset=self.pool_offset
            )
            self._pool = raw.reshape(count, 3).astype(np.int32)
        return self._pool

    def _entries(self, keyframe: int) -> np.ndarray:
        return np.frombuffer(
            self._data, dtype="<u2", count=self.vertex_count, offset=keyframe + KEYFRAME_HEADER
        )

    def _slots(self, keyframe: int) -> np.ndarray:
        """Pool indices for one keyframe, clamped to the pool that actually exists.

        Clamping is not cosmetic: `models/arena/boss_oxide/chaselevel.mdl` -- the
        one model carrying the odd 0x09160026 stamp -- has a blob whose declared
        range stops 1284 bytes short of the vectors its keyframes reference, and
        those bytes fall outside the DAT entry. It is the only such clip of 829,
        and clamping keeps a viewer drawing rather than raising.
        """
        slots = (self._entries(keyframe) >> 2).astype(np.int32)
        limit = self.pool().shape[0] - 1
        return np.clip(slots, 0, limit) if limit >= 0 else slots

    def keyframe_pose(self, keyframe: int) -> np.ndarray:
        """(V, 3) float32 positions of one keyframe, addressed by its offset.

        A frame blends two of these; anything that wants the poses themselves --
        a glTF morph target, say -- wants this rather than a frame index, since
        the keyframes are shared between frames and outnumbered by them roughly
        four to one.
        """
        return self.pool()[self._slots(keyframe)].astype(np.float32) * GTE_SCALE_SMALL

    def pose_raw(self, frame_index: int) -> np.ndarray:
        """(V, 3) int32 positions in model units, bit-exact with the game.

        The blend is GTE INTPL with sf=1, which evaluates
        `MAC = (clamp16(FC - IR) * IR0 + (IR << 12)) >> 12` with an *arithmetic*
        shift, i.e. `A + floor((B - A) * w / 4096)`. The flooring matters: it
        differs from round-to-nearest on 26% of the 38.5M interpolated coordinates
        in the corpus. Neither GTE clamp ever fires on retail data, so they are not
        reproduced here.
        """
        frame = self.frames[frame_index]
        pool = self.pool()
        a = pool[self._slots(frame.key_a)]
        if not frame.interpolated:
            return a
        b = pool[self._slots(frame.key_b)]
        return a + (((b - a) * frame.weight) >> 12)

    def pose_array(self, frame_index: int) -> np.ndarray:
        """(V, 3) float32 positions, in `mdl.Mesh.positions` units and orientation."""
        return self.pose_raw(frame_index).astype(np.float32) * GTE_SCALE_SMALL

    def pose(self, frame_index: int) -> list[Vec3]:
        """Positions for one frame, drop-in for `mdl.Mesh.positions`.

        Same order, same 1/256 scale, same axes -- both decoders walk the mesh's own
        strip list to size their output, so vertex i here is vertex i there.

        Going through `tolist()` rather than iterating the array is worth the
        detour: unboxing numpy scalars per component costs fifteen times as much as
        the decode itself.
        """
        return [tuple(v) for v in self.pose_array(frame_index).tolist()]

    def flags(self, frame_index: int) -> list[int]:
        """Per-vertex flag word for one frame: the low 2 bits of each keyframe entry.

        These are the same bits `mdl.Mesh.vertex_flags` holds -- bit 0 is the strip
        winding, bit 1 disables the backface test -- and they match the static mesh
        slot for slot in every keyframe of every clip. They are copied, never
        blended; the blend decoder takes them from keyframe B, which is what is done
        here, and A and B agree in all 12.8M pairs anyway.
        """
        frame = self.frames[frame_index]
        source = frame.key_b if frame.interpolated else frame.key_a
        return [int(e) & 3 for e in self._entries(source)]


def _mesh_vertex_count(mesh: Mesh) -> int:
    """How many vertices the decoders emit: the span the strip list walks.

    Both decoders size their output by walking `mesh+0x14` exactly as the geometry
    reader does -- `count + 2` vertices per strip, 0xFF ends the list -- and that
    walk covers the whole static pool in all 1036 resolvable clips.
    """
    return mesh.strips[-1].end if mesh.strips else len(mesh.positions)


def _vertex_count_from_stride(offsets: list[int]) -> int | None:
    """Infer V from the gap between two keyframes, for a clip with no mesh.

    Ambiguous by one -- `round4` maps V and V-1 to the same stride when V is odd --
    so this is only used for the single clip whose descriptor does not name a mesh.
    """
    if len(offsets) < 2:
        return None
    stride = offsets[1] - offsets[0]
    return (stride - KEYFRAME_HEADER) // 2 if stride > KEYFRAME_HEADER else None


def read_animations(data: bytes, model: Model) -> list[Animation]:
    """Read every clip of one DAT entry. `model` supplies the meshes, already parsed.

    Returns an empty list for a model with no animation table, which is most of
    them: 225 of the 400 models in the NTSC-U set carry one.
    """
    if len(data) < PTR_ANIM_TABLE + 4:
        return []
    try:
        count = _i32(data, PTR_ANIM_COUNT)
    except struct.error:
        return []
    if count <= 0:
        return []

    table = _target(data, PTR_ANIM_TABLE)
    if not 0 < table <= len(data) - DESCRIPTOR_SIZE * count:
        return []

    by_header = {mesh.header_offset: mesh for mesh in model.meshes}
    out: list[Animation] = []
    for i in range(count):
        try:
            out.append(_read_animation(data, model, by_header, table, i))
        except (struct.error, IndexError, ValueError) as exc:
            out.append(
                Animation(
                    index=i,
                    descriptor_offset=table + DESCRIPTOR_SIZE * i,
                    start=0,
                    end=0,
                    frame_count=0,
                    name_hash=0,
                    mesh_index=None,
                    mesh_header_offset=0,
                    vertex_count=0,
                    pool_offset=0,
                    pool_count=0,
                    pool_is_shared=False,
                    warnings=[f"descriptor {i}: {exc}"],
                    _data=data,
                )
            )
    return out


def _read_animation(
    data: bytes, model: Model, by_header: dict[int, Mesh], table: int, index: int
) -> Animation:
    at = table + DESCRIPTOR_SIZE * index
    start = _u32(data, at)
    end = _u32(data, at + 4)
    frame_count = _u32(data, at + 8)
    mesh_header = _target(data, at + 0x0C)
    hash_value = _u32(data, at + 0x10)

    warnings: list[str] = []
    if not 0 < start < end <= len(data):
        raise ValueError(f"blob range 0x{start:X}..0x{end:X} outside the entry")
    if frame_count == 0 or start + 4 + FRAME_RECORD_SIZE * frame_count > end:
        raise ValueError(f"{frame_count} frames do not fit in the blob")

    frames: list[Frame] = []
    for f in range(frame_count):
        r = start + 4 + FRAME_RECORD_SIZE * f
        a, b, weight, aux = (_i32(data, r + k) for k in (0, 4, 8, 0x0C))
        frames.append(
            Frame(
                index=f,
                record_offset=r,
                key_a=r + a,
                key_b=(r + 4 + b) if b else 0,
                weight=weight,
                aux=(r + 0x0C + aux) if aux else 0,
            )
        )

    mesh = by_header.get(mesh_header)
    if mesh is not None:
        vertex_count = _mesh_vertex_count(mesh)
        mesh_index: int | None = mesh.index
    else:
        # One clip of 1037 (models/arena/medieval_mallet/arena.mdl #0) points +0x0C
        # at the model's 0x2C field instead of a mesh header. Its blob is otherwise
        # perfectly regular, so fall back to the stride and say so.
        keys = sorted({f.key_a for f in frames} | {f.key_b for f in frames if f.key_b})
        inferred = _vertex_count_from_stride(keys)
        mesh_index = None
        vertex_count = inferred or 0
        warnings.append(
            f"+0x0C resolves to 0x{mesh_header:X}, not a mesh header; "
            f"vertex count {vertex_count} inferred from the keyframe stride"
        )

    # blob+0x00 selects the pool. Zero means the clip shares the model-wide pool at
    # 0x28, which 208 of the 1037 blobs do (0x80019C08). A blob-local pool starts
    # where the last keyframe ends and runs to the end of the blob.
    local = _i32(data, start)
    if local:
        pool_offset = start + local
        pool_end = end
        pool_is_shared = False
    else:
        pool_offset = _target(data, PTR_MODEL_POOL)
        pool_end = _target(data, PTR_MODEL_END)
        pool_is_shared = True
    pool_count = max(0, (min(pool_end, len(data)) - pool_offset) // POOL_STRIDE)

    animation = Animation(
        index=index,
        descriptor_offset=at,
        start=start,
        end=end,
        frame_count=frame_count,
        name_hash=hash_value,
        mesh_index=mesh_index,
        mesh_header_offset=mesh_header,
        vertex_count=vertex_count,
        pool_offset=pool_offset,
        pool_count=pool_count,
        pool_is_shared=pool_is_shared,
        frames=frames,
        warnings=warnings,
        _data=data,
    )

    if vertex_count <= 0:
        animation.warnings.append("no vertex count could be established")
        return animation

    stride = keyframe_stride(vertex_count)
    for frame in frames:
        for key in (frame.key_a, frame.key_b):
            if key and not start <= key <= len(data) - stride:
                animation.warnings.append(
                    f"frame {frame.index}: keyframe 0x{key:X} outside the blob"
                )
                break
        else:
            continue
        break

    highest = 0
    for key in animation.keyframes():
        if start <= key <= len(data) - stride:
            highest = max(highest, int(animation._entries(key).max(initial=0)) >> 2)
    if highest >= pool_count:
        animation.warnings.append(
            f"keyframes reach pool vector {highest} but only {pool_count} are present; "
            "indices are clamped"
        )
    return animation


def animations_for_mesh(animations: list[Animation], mesh_index: int) -> list[Animation]:
    """The clips that drive one mesh. Most animated models animate a single mesh."""
    return [a for a in animations if a.mesh_index == mesh_index]
