"""Overwrite a texture's pixels or a palette's colours -- or add one.

Most of this writes in place: a pack's size and record layout stay as they were,
and changing the pixels inside a slot touches nothing structural. A smaller
image can be dropped into a corner of a larger slot, which is how a 32x16
texture reaches a pack that only has a 32x32 slot free; the caller then shifts
the UVs that sample it by the same offset.

`append_texture` is the exception, and it is only possible because **VRAM
placement turned out not to be in the file at all** (§10.4). The pack states no
position and the loader allocates one: `0x80029560` walks the records, picks a
free rect off the list its size class names (`0x80028994` buckets, `0x800282F8`
pops), computes the tpage inline and the CLUT id through `0x800364FC`, which is
`GetClut` letter for letter. So a pack may hold one more texture than it
shipped with, and nothing has to be reproduced -- what a replacement must not
change is a texture's **size class**, since that is what picks the bucket, and
an appended texture brings its own.

That was written down as unknown for a long time because three scans looked for
`GetClut` *inlined* rather than called. The negative result was real; the
conclusion drawn from it was not.
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


def append_texture(data: bytes, source: Texture,
                   colours: list[int] | np.ndarray) -> tuple[bytes, int, int]:
    """Add a texture and its palette to a pack, replacing nothing.

    Returns the new pack, the slot the texture took and the palette index it
    was given. Every slot and palette the pack already had keeps its number,
    **except the swatch**, which moves on by one -- see below.

    Two placements are forced and neither is a choice:

    * The palette goes on the **end of the palette table**, so the indices a
      model already writes still mean what they meant.
    * The texture record goes **before the last one**, because the last is where
      bit 15 of a texture entry sends a face (§6.2, `0x80017FC4`): the swatch is
      not found by index but by being last, and appending after it would send
      every untextured triangle in the model to the new picture instead. The
      swatch's own slot number therefore rises by one, and a caller that has
      faces naming it by index -- 23,413 faces across 225 models do -- has to
      renumber them.

    The tail is rebuilt to §10.6: a length that is a multiple of 8, with a
    further eight zero bytes when the pack has no animation block, which is the
    empty block itself and holds in 400/400.
    """
    out = bytearray(data)
    texture_count = struct.unpack_from("<h", out, 0x08)[0]
    palette_count = struct.unpack_from("<h", out, 0x0A)[0]
    if texture_count < 1:
        raise ValueError("the pack holds no textures to insert before")

    values = [int(v) & 0xFFFF for v in colours]
    if len(values) not in (16, 256):
        raise ValueError(
            f"a palette holds 16 or 256 colours, not {len(values)} (§10.2)")
    if source.bit_depth == 4 and source.width % 4:
        raise ValueError(
            f"a 4bpp texture is {source.vram_width} VRAM units wide, so its "
            f"pixel width is a multiple of 4; {source.width} is not")

    palettes = palette_offsets(out)
    records = texture_offsets(out)
    palette_end = (palettes[-1][0] + palettes[-1][1] * 2) if palettes else 0x20
    last_record = records[-1][0]

    # The record, with every field the corpus says is constant set to what it
    # says. `+0x06`/`+0x07` are the used sub-rectangle, equal to the full size
    # here; `+0x0E`/`+0x10` are the variant gate, which an added texture has no
    # siblings for.
    palette_field = ((palette_count << 1) & 0xFFFE) | (1 if source.bit_depth == 8 else 0)
    record = struct.pack(
        "<hhBBBBIhhI",
        source.vram_width, source.height,
        0, 0, min(source.vram_width * 2, 255), min(source.height, 255),
        0, palette_field, 0, 0)
    pixels = _pack_indices(source.indices(), source.bit_depth)
    if len(pixels) != source.vram_width * source.height * 2:
        raise ValueError(
            f"{source.width}x{source.height} at {source.bit_depth}bpp packs to "
            f"{len(pixels)} bytes, not the {source.vram_width * source.height * 2} "
            f"the record states")

    palette_bytes = struct.pack(f"<i{len(values)}H", len(values), *values)
    # Insert the texture first, so the palette insertion does not move the
    # offset it was measured at.
    out[last_record:last_record] = record + pixels
    out[palette_end:palette_end] = palette_bytes

    struct.pack_into("<h", out, 0x08, texture_count + 1)
    struct.pack_into("<h", out, 0x0A, palette_count + 1)
    # `0x0C + value` is the first texture record, which the palette pushed on.
    struct.pack_into("<I", out, 0x0C,
                     struct.unpack_from("<I", out, 0x0C)[0] + len(palette_bytes))
    animation = struct.unpack_from("<I", out, 0x18)[0]
    if animation:
        struct.pack_into("<I", out, 0x18,
                         animation + len(palette_bytes) + len(record) + len(pixels))

    # Everything past the texture walk -- §10.5's animation block and §10.6's
    # tail -- is left exactly as it was and simply moved on by what was
    # inserted. Rebuilding it instead cut 32,864 bytes off `crate_jungle/arena`:
    # its walk ends where `u32@0x18` points, and a flipbook's stored frames live
    # after that, so "the last structure" is not the last texture.
    pad = (-len(out)) % 8
    out.extend(b"\0" * pad)
    struct.pack_into("<I", out, 0x04, len(out))
    return bytes(out), texture_count - 1, palette_count


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
