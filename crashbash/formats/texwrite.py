"""Overwrite a texture's pixels or a palette's colours, in place.

Only in place. A pack's size, its record layout and the order of its textures
all stay exactly as they were, because how a pack is placed in VRAM is still
unknown -- pack header `+0x14` and texture record `+0x04..+0x07`, `+0x0E`,
`+0x10` are unidentified (docs/FORMAT.md §10). Changing the pixels inside a slot
touches none of that; adding a texture or resizing one would.

A smaller image can be dropped into a corner of a larger slot, which is how a
32x16 texture reaches a pack that only has a 32x32 slot free. The caller then
shifts the UVs that sample it by the same offset.
"""

from __future__ import annotations

import struct

import numpy as np

from .tex import HEADER_SIZE, Texture, read_pack

TEXTURE_RECORD_SIZE = 0x14
PALETTE_HEADER_SIZE = 4


def palette_offsets(data: bytes) -> list[tuple[int, int]]:
    """(offset of the first colour, colour count) for each palette."""
    count = struct.unpack_from("<h", data, 0x0A)[0]
    out: list[tuple[int, int]] = []
    at = 0x20
    for _ in range(count):
        colours = struct.unpack_from("<i", data, at)[0]
        out.append((at + PALETTE_HEADER_SIZE, colours))
        at += PALETTE_HEADER_SIZE + colours * 2
    return out


def texture_offsets(data: bytes) -> list[tuple[int, int, int]]:
    """(record offset, pixel offset, pixel byte length) for each texture."""
    palettes = palette_offsets(data)
    at = palettes[-1][0] + palettes[-1][1] * 2 if palettes else 0x20
    out: list[tuple[int, int, int]] = []
    for _ in range(struct.unpack_from("<h", data, 0x08)[0]):
        width, height = struct.unpack_from("<2h", data, at)
        length = width * height * 2
        out.append((at, at + TEXTURE_RECORD_SIZE, length))
        at += TEXTURE_RECORD_SIZE + length
    return out


def replace_palette(data: bytes, index: int, colours: list[int] | np.ndarray) -> bytes:
    """Write BGR555 values over a palette. The colour count may not change."""
    at, count = palette_offsets(data)[index]
    values = list(colours)
    if len(values) != count:
        raise ValueError(f"palette {index} holds {count} colours, not {len(values)}")
    out = bytearray(data)
    struct.pack_into(f"<{count}H", out, at, *(int(v) & 0xFFFF for v in values))
    return bytes(out)


def read_palette(data: bytes, index: int) -> list[int]:
    at, count = palette_offsets(data)[index]
    return list(struct.unpack_from(f"<{count}H", data, at))


def replace_pixels(data: bytes, index: int, pixels: bytes) -> bytes:
    """Write raw pixel bytes over a texture. The byte length may not change."""
    _, at, length = texture_offsets(data)[index]
    if len(pixels) != length:
        raise ValueError(f"texture {index} holds {length} bytes, not {len(pixels)}")
    out = bytearray(data)
    out[at : at + length] = pixels
    return bytes(out)


def _pack_indices(indices: np.ndarray, bit_depth: int) -> bytes:
    if bit_depth == 8:
        return indices.astype(np.uint8).tobytes()
    flat = indices.astype(np.uint8).reshape(indices.shape[0], -1, 2)
    return ((flat[:, :, 1] << 4) | (flat[:, :, 0] & 0x0F)).astype(np.uint8).tobytes()


def place(data: bytes, index: int, source: Texture, at: tuple[int, int] = (0, 0)) -> bytes:
    """Draw `source`'s pixels into a slot at a texel offset, leaving the rest.

    Both must be the same bit depth, and at 4bpp the horizontal offset must be
    even, since two pixels share a byte.
    """
    pack = read_pack(data)
    slot = pack.textures[index]
    if slot.bit_depth != source.bit_depth:
        raise ValueError(
            f"slot {index} is {slot.bit_depth}bpp and the source is "
            f"{source.bit_depth}bpp"
        )
    x, y = at
    if slot.bit_depth == 4 and x % 2:
        raise ValueError("a 4bpp image can only be placed on an even column")
    if x + source.width > slot.width or y + source.height > slot.height:
        raise ValueError(
            f"{source.width}x{source.height} at {at} does not fit a "
            f"{slot.width}x{slot.height} slot"
        )
    canvas = slot.indices().copy()
    canvas[y : y + source.height, x : x + source.width] = source.indices()
    return replace_pixels(data, index, _pack_indices(canvas, slot.bit_depth))
