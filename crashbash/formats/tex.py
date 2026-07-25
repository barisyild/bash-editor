"""Crash Bash TEX packs: a palette table followed by paletted texture blobs.

A pack stores its palettes first, then the textures. Each texture names a palette
by index and is either 4- or 8-bit; `data` is sized in VRAM units (16 bits per
entry), so a 4-bit texture is four pixels wide per unit and an 8-bit one is two.

Colours are PS1 BGR555. Following the hardware, a zero entry means "transparent"
rather than "black", which is why the decoder emits RGBA.

After the textures some packs carry an animation block, holding the two ways a
surface moves without the model moving: a flipbook swaps a texture's pixels for
one of a run of stored frames, and a scroller slides a texture under its own UVs.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field, replace

import numpy as np

from ..binreader import Reader

HEADER_SIZE = 0x24

# Header fields. The first two are self-relative in the usual way of this format
# -- `target = field_offset + value` -- but the animation block's is a plain
# offset from the start of the file. Measured across all 400 packs: the first two
# hold for every one of them, and the third lands exactly on the end of the
# palette and texture walk in all 86 packs that have a block.
PTR_TEXTURES = 0x0C
PTR_PALETTES = 0x10
PTR_ANIMATION = 0x18

FLIPBOOK_RECORD = 0x14
SCROLLER_RECORD = 0x10

# Both cursors advance by their record's delta once per tick, in 24.8 fixed
# point, and the game runs at 30 Hz.
FIXED_ONE = 256
TICKS_PER_SECOND = 30


def convert16(color: int) -> tuple[int, int, int, int]:
    """PS1 BGR555 -> RGBA8, using Duckstation's exact 5->8 bit expansion."""

    def to8(c: int) -> int:
        return ((c * 527) + 23) >> 6

    r = to8(color & 0x1F)
    g = to8((color >> 5) & 0x1F)
    b = to8((color >> 10) & 0x1F)
    stp = color >> 15
    # Fully black with no semi-transparency bit is the hardware's "skip this
    # pixel" encoding, not a colour.
    alpha = 0 if (color & 0x7FFF) == 0 and not stp else 255
    return (r, g, b, alpha)


def palette_to_rgba(entries: list[int]) -> np.ndarray:
    out = np.zeros((len(entries), 4), dtype=np.uint8)
    for i, c in enumerate(entries):
        out[i] = convert16(c)
    return out


@dataclass
class Texture:
    index: int
    vram_width: int  # width in 16-bit VRAM units
    height: int
    unk01: int
    unk02: int
    unk03: int
    unk04: int
    palette_field: int  # bit 0 selects bit depth, the rest is the palette index
    unk22: int
    flags: int
    data: bytes = b""
    palette_ok: bool = True

    @property
    def bit_depth(self) -> int:
        return 8 if (self.palette_field & 1) else 4

    # The loader treats 0x7FFF as "this texture brings no palette of its own".
    NO_PALETTE = 0x7FFF

    @property
    def palette_index(self) -> int:
        return ((self.palette_field & 0xFFFF) >> 1) & 0x7FFF

    @property
    def is_swatch(self) -> bool:
        """A colour-swatch texture: the palette comes from the model, per triangle.

        The last texture of a character pack is one of these. It carries no
        palette of its own, and each triangle that samples it names the palette
        to use in the low 9 bits of its texture entry.
        """
        return self.palette_index == self.NO_PALETTE

    @property
    def width(self) -> int:
        return self.vram_width * (2 if self.bit_depth == 8 else 4)

    @property
    def name(self) -> str:
        suffix = "_swatch" if self.is_swatch else ""
        return f"tex_{self.index:03d}_{self.width}x{self.height}_{self.bit_depth}bpp{suffix}"

    def indices(self) -> np.ndarray:
        """Unpack the pixel data into one palette index per pixel."""
        raw = np.frombuffer(self.data, dtype=np.uint8)
        expected = self.vram_width * self.height * 2
        if raw.size < expected:
            raw = np.pad(raw, (0, expected - raw.size))
        raw = raw[:expected]
        if self.bit_depth == 8:
            return raw.reshape(self.height, self.width)
        # 4-bit: the low nibble is the left pixel of each byte.
        low = raw & 0x0F
        high = raw >> 4
        return np.stack([low, high], axis=1).reshape(self.height, self.width)

    def to_rgba(
        self, palettes: list[np.ndarray], palette_override: int | None = None
    ) -> np.ndarray:
        """Decode to RGBA. Swatch textures need `palette_override` from the model.

        Without one they have no colours of their own, so the result is filled
        with magenta rather than silently borrowing another texture's palette --
        which is what made flat-shaded triangles come out with wrong colours.
        """
        idx = self.indices()
        chosen = self.palette_index if palette_override is None else palette_override
        if 0 <= chosen < len(palettes):
            pal = palettes[chosen]
        else:
            pal = np.tile(np.array([255, 0, 255, 255], dtype=np.uint8), (1, 1))
        needed = int(idx.max()) + 1 if idx.size else 1
        if pal.shape[0] < needed:
            # Palettes are shared and sometimes shorter than the indices used;
            # pad with magenta so the gap is obvious instead of silently black.
            pad = np.tile(np.array([255, 0, 255, 255], dtype=np.uint8), (needed - pal.shape[0], 1))
            pal = np.concatenate([pal, pad], axis=0)
        return pal[idx]


@dataclass
class Flipbook:
    """A texture whose pixels are swapped for one of a run of stored frames.

    The frames are each exactly as long as the texture's own pixel data, so
    playing one is a straight substitution: the size, the bit depth and the
    palette all stay as they are. That equality holds for all 136 flipbooks in
    the game, which is what identifies these blobs as frames rather than
    anything else stored after the textures.
    """

    texture: int
    delta: int  # frames per tick, 24.8 fixed point
    frames: list[bytes] = field(default_factory=list)

    @property
    def fps(self) -> float:
        return self.delta / FIXED_ONE * TICKS_PER_SECOND

    def frame_at(self, tick: float) -> int:
        if not self.frames:
            return 0
        return int(tick * self.delta / FIXED_ONE) % len(self.frames)


@dataclass
class Scroller:
    """A texture that slides under its own UVs, so the surface appears to flow.

    `delta` is texels per tick in 24.8 fixed point. Which axis it moves along is
    not settled: the model's UVs are untouched and no record in the game sets
    more than one component, so the data alone cannot say. Measuring the images
    argues for the horizontal one -- a scrolling texture's left and right edges
    join up about three times as smoothly as its top and bottom, and more than
    twice as smoothly as an average texture's -- which is also the axis a PS1
    texture window wraps most naturally.
    """

    texture: int
    delta: int

    @property
    def texels_per_second(self) -> float:
        return self.delta / FIXED_ONE * TICKS_PER_SECOND


@dataclass
class TexturePack:
    magic: int = 0
    size: int = 0
    palettes: list[np.ndarray] = field(default_factory=list)
    textures: list[Texture] = field(default_factory=list)
    flipbooks: list[Flipbook] = field(default_factory=list)
    scrollers: list[Scroller] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def image(self, index: int) -> np.ndarray:
        return self.textures[index].to_rgba(self.palettes)

    def animated(self) -> dict[int, Flipbook]:
        """Flipbooks by the texture index they drive."""
        return {book.texture: book for book in self.flipbooks}

    def frame(self, book: Flipbook, index: int) -> Texture:
        """The flipbook's texture as it looks on one of its frames."""
        base = self.textures[book.texture]
        if not book.frames:
            return base
        return replace(base, data=book.frames[index % len(book.frames)])


def read_pack(data: bytes | Reader) -> TexturePack:
    reader = data if isinstance(data, Reader) else Reader(data)
    pack = TexturePack()

    if len(reader) < HEADER_SIZE:
        pack.warnings.append("file too small to be a TEX pack")
        return pack

    pack.magic = reader.u32()
    pack.size = reader.u32()
    num_tex = reader.i16()
    num_pals = reader.i16()
    reader.skip(4 * 5)  # skipToTex, skipToPal, skipToUnk, ptrNext, zero

    if not (0 <= num_tex < 4096 and 0 <= num_pals < 4096):
        pack.warnings.append(f"implausible counts: {num_tex} textures, {num_pals} palettes")
        return pack

    for i in range(num_pals):
        try:
            count = reader.i32()
            if not 0 < count <= 256:
                pack.warnings.append(f"palette {i}: bad colour count {count}")
                break
            pack.palettes.append(palette_to_rgba(list(reader.array_u16(count))))
        except EOFError:
            pack.warnings.append(f"truncated in palette {i}")
            break

    for i in range(num_tex):
        try:
            vram_width, height = reader.array_i16(2)
            unk01, unk02, unk03, unk04 = reader.bytes(4)
            reader.skip(4)
            palette_field, unk22 = reader.array_i16(2)
            flags = reader.i32()
            if vram_width <= 0 or height <= 0 or vram_width * height * 2 > reader.remaining:
                pack.warnings.append(f"texture {i}: bad size {vram_width}x{height}")
                break
            blob = reader.bytes(vram_width * height * 2)
        except EOFError:
            pack.warnings.append(f"truncated in texture {i}")
            break

        tex = Texture(
            index=i,
            vram_width=vram_width,
            height=height,
            unk01=unk01,
            unk02=unk02,
            unk03=unk03,
            unk04=unk04,
            palette_field=palette_field,
            unk22=unk22,
            flags=flags,
            data=blob,
        )
        tex.palette_ok = tex.is_swatch or 0 <= tex.palette_index < len(pack.palettes)
        if not tex.palette_ok:
            pack.warnings.append(f"texture {i}: palette {tex.palette_index} out of range")
        pack.textures.append(tex)

    _read_animation(reader.data, pack)
    return pack


def _read_animation(data: bytes, pack: TexturePack) -> None:
    """Read the block the pack header points at, when it has one.

    The block is two tables, each a self-relative pointer in the header followed
    by a count and then its records. Either may be absent, and 314 of the game's
    400 packs have no block at all.
    """
    if len(data) < PTR_ANIMATION + 4:
        return
    base = struct.unpack_from("<I", data, PTR_ANIMATION)[0]
    if not base or base >= len(data):
        return
    block = data[base:]

    try:
        flip_ptr, scroll_ptr = struct.unpack_from("<2I", block, 0)
        if flip_ptr:
            pack.flipbooks = _read_flipbooks(block, flip_ptr, pack)
        if scroll_ptr:
            pack.scrollers = _read_scrollers(block, 4 + scroll_ptr, pack)
    except struct.error:
        pack.warnings.append("animation block is truncated")


def _read_flipbooks(block: bytes, at: int, pack: TexturePack) -> list[Flipbook]:
    count = struct.unpack_from("<I", block, at)[0]
    books: list[Flipbook] = []
    for i in range(count):
        record = at + 4 + i * FLIPBOOK_RECORD
        # The fifth field is the runtime cursor, stored zeroed in every record.
        frames_ptr, index, frames, delta, _cursor = struct.unpack_from(
            "<I4i", block, record
        )
        if not 0 <= index < len(pack.textures):
            pack.warnings.append(f"flipbook {i}: texture {index} out of range")
            continue
        texture = pack.textures[index]
        length = texture.vram_width * texture.height * 2
        table = record + frames_ptr
        book = Flipbook(texture=index, delta=delta)
        for frame in range(frames):
            slot = table + frame * 4
            start = slot + struct.unpack_from("<i", block, slot)[0]
            if start + length > len(block):
                pack.warnings.append(f"flipbook {i}: frame {frame} runs past the block")
                break
            book.frames.append(block[start : start + length])
        books.append(book)
    return books


def _read_scrollers(block: bytes, at: int, pack: TexturePack) -> list[Scroller]:
    count = struct.unpack_from("<I", block, at)[0]
    scrollers: list[Scroller] = []
    for i in range(count):
        # The two trailing fields are the runtime cursor and a spare, and both
        # are zero in all 108 records the game ships.
        index, delta, _cursor, _spare = struct.unpack_from(
            "<4i", block, at + 4 + i * SCROLLER_RECORD
        )
        if not 0 <= index < len(pack.textures):
            pack.warnings.append(f"scroller {i}: texture {index} out of range")
            continue
        scrollers.append(Scroller(texture=index, delta=delta))
    return scrollers
