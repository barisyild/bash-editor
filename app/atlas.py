"""Pack a texture pack's images into one atlas the viewport can sample."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from crashbash.formats import tex

PADDING = 1  # keeps bilinear-free sampling from bleeding between neighbours

# Enough to keep a corner off the exact texel boundary, far too little to reach
# the next one. On the boundary the floor in the shader could fall either way.
EDGE_NUDGE = 1.0 / 512.0


@dataclass
class Atlas:
    image: np.ndarray  # (h, w, 4) uint8, RGBA
    rects: list[tuple[int, int, int, int]]  # per texture: x, y, w, h in pixels
    neutral: tuple[int, int]  # mid-grey texel: leaves an untextured colour alone

    @property
    def size(self) -> tuple[int, int]:
        return self.image.shape[1], self.image.shape[0]

    def uv(self, texture_index: int, u: int, v: int) -> tuple[float, float]:
        """Map a texture-local pixel coordinate to an atlas *texel* coordinate.

        Not a normalised one: the shader floors what it is handed, because that
        is what the console does. It interpolates the coordinate along the span
        and truncates, so a quad whose corners are texel 0 and texel 63 only
        reaches 63 on the very last pixel -- and where two quads meet that pixel
        belongs to the next quad under the fill rule, so the last column never
        appears. Interpolating between normalised centres instead runs half a
        texel past the end and draws it, and every tile of the `intro_eurocom`
        logo carries a black last row and column: they showed as a dark cross
        through the badge.

        The nudge keeps the near corner off the exact cell boundary, where the
        rounding direction is the driver's to choose and the seam came back
        dashed.
        """
        if not 0 <= texture_index < len(self.rects):
            return self.neutral_uv()
        rx, ry, rw, rh = self.rects[texture_index]
        return (rx + min(max(u, 0), rw - 1) + EDGE_NUDGE,
                ry + min(max(v, 0), rh - 1) + EDGE_NUDGE)

    def neutral_uv(self) -> tuple[float, float]:
        x, y = self.neutral
        return (x + 0.5, y + 0.5)

    def flipbook_patches(
        self, pack: tex.TexturePack | None, tick: float
    ) -> list[tuple[tuple[int, int, int, int], np.ndarray]]:
        """Where each animated texture sits, and how it looks at this tick.

        Every frame is the same size as the texture it replaces, so a flipbook
        never disturbs the packing -- the viewport re-uploads the rectangle in
        place rather than rebuilding the atlas.
        """
        if pack is None:
            return []
        patches = []
        for book in pack.flipbooks:
            if not (0 <= book.texture < len(self.rects)) or not book.frames:
                continue
            rect = self.rects[book.texture]
            texture = pack.frame(book, book.frame_at(tick))
            if texture.is_swatch:
                continue
            try:
                patches.append((rect, texture.to_rgba(pack.palettes)))
            except (ValueError, IndexError):
                continue
        return patches


def build(pack: tex.TexturePack | None) -> Atlas:
    """Shelf-pack every texture into a square-ish RGBA atlas.

    A one-texel mid-grey block is reserved so untextured triangles can go through
    the same shader path: the PS1 modulation is `texel * colour * 2`, so a texel
    of 128 reproduces the flat colour exactly. A white texel would double it and
    wash the triangle out.
    """
    images: list[np.ndarray] = []
    if pack is not None:
        for texture in pack.textures:
            if texture.is_swatch:
                # No palette of its own: the triangles that sample it each name
                # one, so it is resolved per triangle rather than baked here.
                images.append(np.zeros((1, 1, 4), dtype=np.uint8))
                continue
            try:
                images.append(texture.to_rgba(pack.palettes))
            except (ValueError, IndexError):
                images.append(np.zeros((1, 1, 4), dtype=np.uint8))

    boxes = [(im.shape[1], im.shape[0]) for im in images]
    boxes.append((1, 1))  # the neutral texel

    total = sum((w + PADDING) * (h + PADDING) for w, h in boxes)
    width = max(64, 1 << max(6, int(np.ceil(np.log2(max(np.sqrt(total), 8))))))

    # Tallest-first shelves keep the packing tight without a real bin packer.
    order = sorted(range(len(boxes)), key=lambda i: -boxes[i][1])
    placed: dict[int, tuple[int, int]] = {}
    x = y = shelf_height = 0
    for i in order:
        w, h = boxes[i]
        if x + w + PADDING > width:
            x = 0
            y += shelf_height + PADDING
            shelf_height = 0
        placed[i] = (x, y)
        x += w + PADDING
        shelf_height = max(shelf_height, h)
    height = max(64, 1 << int(np.ceil(np.log2(max(y + shelf_height + PADDING, 8)))))

    image = np.zeros((height, width, 4), dtype=np.uint8)
    rects: list[tuple[int, int, int, int]] = []
    for i, im in enumerate(images):
        px, py = placed[i]
        h, w = im.shape[0], im.shape[1]
        if py + h <= height and px + w <= width:
            image[py : py + h, px : px + w] = im
        rects.append((px, py, w, h))

    nx, ny = placed[len(images)]
    if ny < height and nx < width:
        image[ny, nx] = (128, 128, 128, 255)

    return Atlas(image=image, rects=rects, neutral=(nx, ny))
