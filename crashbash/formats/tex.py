"""Crash Bash TEX packs: a palette table followed by paletted texture blobs.

A pack stores its palettes first, then the textures. Each texture names a palette
by index and is either 4- or 8-bit; `data` is sized in VRAM units (16 bits per
entry), so a 4-bit texture is four pixels wide per unit and an 8-bit one is two.

Colours are PS1 BGR555. Following the hardware, a zero entry means "transparent"
rather than "black", which is why the decoder emits RGBA.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ..binreader import Reader

HEADER_SIZE = 0x24


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
class TexturePack:
    magic: int = 0
    size: int = 0
    palettes: list[np.ndarray] = field(default_factory=list)
    textures: list[Texture] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def image(self, index: int) -> np.ndarray:
        return self.textures[index].to_rgba(self.palettes)


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

    return pack
