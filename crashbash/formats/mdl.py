"""Crash Bash MDL models.

An MDL holds a mesh count at 0x54 followed by that many 0x34-byte mesh headers.
Every pointer, in the file header and in the mesh headers alike, is relative to
its own position -- the game resolves them as

    lw v0, 0x10(mesh); addiu v0, v0, 0x10; addu ptr, mesh, v0

The field meanings below were read out of the NTSC-U executable's triangle loop
at 0x80017EE8 rather than guessed; `tools/psxdis.py` reproduces the disassembly.

File header:

    0x20  colour table, 4-byte R G B 00 records
    0x24  UV table, 2-byte (u, v) records
    0x54  mesh count, followed by the mesh headers at 0x58

Mesh header:

    0x08  triangle count (int16)   0x0A  attribute format (4/6/7, rarely 5 or 2)
    0x10  bounds (0x14 bytes), then the vertex pool, 8 bytes per vertex
    0x14  strip list: high byte = triangle count, low byte = flags, 0xFF ends it
    0x18  one u16 per triangle: index of the first of its three UVs
    0x1C  run-length texture list: (run << 9) | texture, covering run + 1 triangles
    0x20  one u16 per triangle: colour index, 13 bits, bit 15 = semi-transparent
    0x24  end of the mesh's data
    0x28  per-vertex normals, appended after the positions (300 of 5990 meshes)
    0x2C  attachment records, addressed by the game's 0x2000 id namespace

Every strip spans `triangle count + 2` vertices, taken from the pool in order,
so the geometry is fully determined by the file -- this accounts for every
triangle in the NTSC-U set. CTR-tools reads the same list with the bytes the
other way round, taking the low byte as the count and the high byte as flags,
which is what scrambles its exports.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field

from ..binreader import GTE_SCALE_SMALL, Reader

MESH_HEADER_SIZE = 0x34
MESH_COUNT_OFFSET = 0x54
VERTEX_STRIDE = 8
BOUNDS_SIZE = 0x14  # bounds block in front of the vertex pool

PTR_COLOUR_TABLE = 0x20  # file header field holding the colour table pointer
PTR_UV_TABLE = 0x24  # file header field holding the UV table pointer

# The object table (§8.3): a second mesh array, one entry per level object,
# reached through the game's 0x5000 id namespace rather than the 0x54 count.
PTR_OBJECT_TABLE = 0x1C
PTR_POOL = 0x2C  # first byte an object's mesh header may occupy
PTR_MODEL_REFS = 0x3C  # the load-time base addresses, and the end of the pool
COUNT_MODEL_REFS = 0x38
OBJECT_NAMESPACE = 0x5000
OBJECT_STRIDE = 12
MODEL_REF_STRIDE = 16
MODEL_REF_SLOT = 4  # the field an object's reference pointer lands on; the
#                     loaded base address is the word after it

# The placement list (§8.5): a level does not draw its objects where their
# vertices sit, it draws them once per record of this list, each under the
# record's own transform.
PTR_SUBOBJECTS = 0x18
COUNT_SUBOBJECTS = 0x14
SUBOBJECT_HEADER_SIZE = 0x34
SUBOBJECT_COUNT = 0x1C  # placement records, read raw at 0x8001E0B0
SUBOBJECT_RECORDS = 0x20  # self-relative pointer to the first one
PLACEMENT_STRIDE = 160
PLACEMENT_FLAGS = 0x00
PLACEMENT_TRANSLATION = 0x04
PLACEMENT_MATRIX = 0x28  # a libgte MATRIX: i16 m[3][3], pad, i32 t[3]
PLACEMENT_MATRIX_T = 0x3C  # that MATRIX's t[3], equal to +0x04 in 2689/2689
PLACEMENT_ID = 0x88
PLACEMENT_FLAG_DRAWN = 0x8000  # tested at 0x8001DD6C before anything is drawn
GTE_ONE = 4096  # 1.0 in the 3.12 fixed point a MATRIX rotation holds

# A placement id names a clip and a frame rather than an object when it lands in
# the 0x4000 namespace; the fields are split at 0x80019B1C.
CLIP_NAMESPACE = 0x4000
CLIP_INDEX_MASK = 0xF80
CLIP_INDEX_SHIFT = 7
CLIP_FRAME_MASK = 0x7F
COUNT_CLIPS = 0x40
PTR_CLIPS = 0x44
CLIP_STRIDE = 24
CLIP_DRIVES = 0x0C  # self-relative pointer to the mesh header the clip drives

# The game masks the colour index with 0x1FFF and tests bit 15 separately.
COLOUR_INDEX_MASK = 0x1FFF
COLOUR_FLAG_TRANSLUCENT = 0x8000

# Bits 13-14 of a colour index carry the GPU's semi-transparency mode (ABR).
COLOUR_BLEND_SHIFT = 13
COLOUR_BLEND_MASK = 0x3

# Texture run entries: (run << 9) | index. Bit 15 means the triangle samples the
# pack's swatch texture, and the low 9 bits then name a PALETTE, not a texture.
TEXTURE_INDEX_MASK = 0x1FF
TEXTURE_FLAG_SWATCH = 0x8000
TEXTURE_FLAG_ALTERNATE = TEXTURE_FLAG_SWATCH  # old name

# Low byte of a strip's list entry.
STRIP_FLAG_UNTEXTURED = 0x01

# The attachment block at mesh+0x2C (§8.4): a u16 of flags, a u16 record count,
# then that many 16-byte records read as eight i16.
ATTACHMENT_COUNT = 0x02
ATTACHMENT_FIRST = 0x04
ATTACHMENT_STRIDE = 16
ATTACHMENT_FIELDS = 8

Vec3 = tuple[float, float, float]


@dataclass
class Bounds:
    min: Vec3
    max: Vec3
    center: Vec3
    radius: float


@dataclass
class Volume:
    """One record of a mesh's attachment block (§8.4).

    For a playable character this is the collision body, and that reading is
    behavioural in both directions: a replacement whose block was zeroed walked
    through the crates, and carrying the original's block through the same swap
    brought the collision back. For the rest of the 1717 records in the archive
    it is a volume of some other purpose -- `height` matches the mesh's own
    standing height in only 677 of them -- so a reader should show it, not
    trust it.

    It has **two** horizontal extents, not one. `depth` is set in 349 records
    and differs from `half_width` in 25 of those, which is what rules out a
    circular cross-section; where it is zero the two are the same. Everything is
    in world units like a vertex, and `height` is negative in 1708/1717, running
    from the offset toward the model's -Y.
    """

    offset: Vec3
    half_width: float  # field 3
    height: float  # field 4
    depth: float  # field 5, zero meaning "the same as half_width"
    unknown: int  # field 6; 64 for Crash standing, 1360 for his spin body
    flags: int  # field 7; carries 0x4000 in 1356 of 1717

    @property
    def half_depth(self) -> float:
        return self.depth or self.half_width

    @property
    def size(self) -> Vec3:
        return tuple(hi - lo for lo, hi in zip(self.min, self.max))  # type: ignore[return-value]


@dataclass
class Strip:
    """A triangle strip: a half-open slice of the mesh's vertex pool."""

    start: int
    end: int

    @property
    def length(self) -> int:
        return self.end - self.start

    @property
    def face_count(self) -> int:
        return max(0, self.length - 2)


@dataclass
class Mesh:
    index: int
    header_offset: int

    face_count_header: int  # u11: triangle count as stated by the file
    format: int  # mesh 0x0A: an attribute-format selector; 4, 6 or 7, rarely 5 or 2
    # 0x0C and 0x0E. MEASURED, no reader found in the executable yet: value 100
    # at 0x0C sits on exactly the backdrop domes every cutscene raises without
    # a node (138 meshes), with 0x0E ordering their layers -- opaque 0, tint 1.
    # Other small families (4, 5, 10, 20..33, 101..103) appear only in arenas,
    # and no mesh carrying any of them is ever owned by a scene node.
    unk13: int
    unk14: int

    # Field offsets 0x10..0x24, each self-relative to its own position.
    ptr_bounds: int  # 0x10: bounds block, then the vertex pool
    ptr_strips: int  # 0x14: one u16 of flags per strip
    ptr_uv_index: int  # 0x18: one u16 per triangle, into the model's UV table
    ptr_texture: int  # 0x1C: texture references
    ptr_colour_index: int  # 0x20: one u16 per triangle, into the colour table
    ptr_end: int  # 0x24: end of this mesh's data
    ptr_normals: int = 0  # 0x28: per-vertex normals, appended after the positions
    ptr_attachment: int = 0  # 0x2C: attachment records, addressed by id namespace 0x2000

    bounds: Bounds | None = None
    positions: list[Vec3] = field(default_factory=list)
    winding: list[int] = field(default_factory=list)  # bit 0 of vertex_flags
    vertex_flags: list[int] = field(default_factory=list)  # raw 4th int16
    normals: list[Vec3] = field(default_factory=list)
    strips: list[Strip] = field(default_factory=list)
    strips_exact: bool = False
    strip_flags: list[int] = field(default_factory=list)  # low byte, bit 0 = untextured
    # One entry per triangle. The UV entry indexes the model's shared UV table
    # and covers three consecutive UVs; it is a plain index, with no flag bits
    # (the largest seen over 363251 triangles is 2942).
    face_uv_index: list[int] = field(default_factory=list)
    face_colour_index: list[int] = field(default_factory=list)
    # Run-length list expanded to one entry per triangle: (run << 9) | index.
    face_texture: list[int] = field(default_factory=list)
    # The owning strip's flag byte, repeated for each of its triangles.
    face_strip_flags: list[int] = field(default_factory=list)
    # The records of the attachment block at +0x2C, when it has one.
    volumes: list["Volume"] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def vertex_count(self) -> int:
        return len(self.positions)

    @property
    def face_count(self) -> int:
        return sum(s.face_count for s in self.strips)

    @property
    def faces_match_header(self) -> bool:
        return self.face_count == self.face_count_header

    def indexed_triangles(self) -> list[tuple[int, int, int, int]]:
        """Expand every strip into (v0, v1, v2, face) tuples, in strip order.

        The order is not rotated to give every triangle the same facing: the
        game builds its GPU packet by writing vertex i, i+1, i+2 and UV 0, 1, 2
        in step, so the correspondence is positional. Reordering the corners
        without reordering the UVs alongside lands the texture on the triangle
        rotated or mirrored.

        `face` counts triangles in file order, including the degenerate ones
        skipped here, so it stays aligned with the per-triangle arrays.
        """
        out: list[tuple[int, int, int, int]] = []
        face = 0
        for strip in self.strips:
            for i in range(strip.start, strip.end - 2):
                current, face = face, face + 1
                a, b, c = i, i + 1, i + 2
                pa, pb, pc = self.positions[a], self.positions[b], self.positions[c]
                # A repeated position is how a strip stitches to the next run;
                # it must not become visible geometry.
                if pa == pb or pb == pc or pa == pc:
                    continue
                out.append((a, b, c, current))
        return out

    def triangles(self, consistent_winding: bool = False) -> list[tuple[int, int, int]]:
        """Triangles in strip order, or with every other one flipped.

        Consecutive triangles in a strip wind opposite ways. Flipping the odd
        ones gives a consistently oriented surface, which is what mesh tools
        expect -- but it breaks the positional UV correspondence, so only ask
        for it when exporting geometry without UVs.
        """
        if not consistent_winding:
            return [(a, b, c) for a, b, c, _ in self.indexed_triangles()]
        out = []
        for strip in self.strips:
            for n, i in enumerate(range(strip.start, strip.end - 2)):
                a, b, c = i, i + 1, i + 2
                pa, pb, pc = self.positions[a], self.positions[b], self.positions[c]
                if pa == pb or pb == pc or pa == pc:
                    continue
                out.append((a, c, b) if n % 2 else (a, b, c))
        return out

    def to_obj(self, name: str = "mesh") -> str:
        """Emit the mesh as Wavefront OBJ, sharing the vertex pool between faces."""
        lines = [f"o {name}"]
        for x, y, z in self.positions:
            # PS1 is Y-down / Z-forward; flip both for OBJ's right-handed frame.
            lines.append(f"v {x:.6f} {-y:.6f} {-z:.6f}")
        for a, b, c in self.triangles(consistent_winding=True):
            lines.append(f"f {a + 1} {b + 1} {c + 1}")
        return "\n".join(lines) + "\n"


@dataclass
class Object:
    """One entry of the object table: a mesh reached by a 0x5000 id.

    `offset` is a byte offset into the model named by `reference`, not into this
    file, so an object whose reference is anything but 0 belongs to a file the
    level loads alongside this one and cannot be resolved from here; its `mesh`
    stays None.
    """

    id: int
    offset: int
    reference: int
    mesh: Mesh | None = None


@dataclass
class Instance:
    """One record of the level's placement list (§8.5).

    `id` names what is drawn -- an object in the 0x5000 namespace, or a clip and
    a frame in the 0x4000 one -- and the transform stands it where it belongs.
    `rotation` is row-major and already divided by the 4096 the file stores, so
    a placed vertex is `rotation @ position + translation`.

    An object authored at the origin is placed by its record; one authored in
    room coordinates carries identity here. Both compose the same way, so a
    reader never has to tell them apart.
    """

    index: int
    id: int
    flags: int
    record: int  # byte offset of the 160-byte record, for an in-place rewrite
    translation: Vec3
    rotation: tuple[float, float, float, float, float, float, float, float, float]
    mesh: Mesh | None = None
    clip: int | None = None
    frame: int = 0

    @property
    def is_drawn(self) -> bool:
        """The draw handler at 0x8001DD50 returns early without this bit."""
        return bool(self.flags & PLACEMENT_FLAG_DRAWN)


IDENTITY = (1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0)


def transform(point: Vec3, rotation, translation: Vec3) -> Vec3:
    """Stand one point where its placement puts it: `rotation @ point + t`.

    `rotation` is the row-major 3x3 a placement record holds, which is how
    libgte stores a MATRIX and how it applies one -- row *r* of the matrix
    against the whole point gives component *r* of the result.
    """
    return tuple(  # type: ignore[return-value]
        rotation[3 * r] * point[0]
        + rotation[3 * r + 1] * point[1]
        + rotation[3 * r + 2] * point[2]
        + translation[r]
        for r in range(3)
    )


@dataclass
class Model:
    meshes: list[Mesh] = field(default_factory=list)
    # The level's static set: a second mesh array the 0x54 count says nothing
    # about. Kept apart from `meshes` because the two are addressed differently
    # and a writer that confused them would move the wrong block.
    objects: list[Object] = field(default_factory=list)
    # How the level stands its set up: one record per placed piece. Empty for
    # everything that is not a level.
    instances: list[Instance] = field(default_factory=list)
    # Shared tables that live after the last mesh, referenced per triangle.
    colours: list[tuple[int, int, int]] = field(default_factory=list)
    uvs: list[tuple[int, int]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def drawn_meshes(self) -> list[Mesh]:
        """Every mesh the game can draw out of this file, in index order.

        The object meshes carry indices continuing past the last numbered mesh,
        so a viewer can key visibility and palettes off `mesh.index` across both
        arrays without the two colliding.
        """
        return self.meshes + [o.mesh for o in self.objects if o.mesh is not None]

    def draw_list(self) -> list[tuple[Mesh, tuple[float, ...], Vec3]]:
        """Every mesh to draw, each with the transform to draw it under.

        A level draws its set through the placement list, so an object several
        records name is drawn once per record, each somewhere else -- reading
        only `drawn_meshes` stacks those copies on one another at the origin.

        No record names the 0x2000 namespace, but a numbered mesh can still be
        placed: the 45 records that name a clip instead of an object all resolve
        to one, and five arenas draw the same numbered mesh 1, 6 or 16 times
        that way. Whatever a record places is left out of the identity pass, so
        it is drawn where the records put it and not once more at the origin.

        Objects no record names are drawn at identity rather than dropped; 96 of
        the corpus's 1971 are in that position and nothing traced so far says
        what, if anything, draws them.
        """
        drawn = [i for i in self.instances if i.mesh is not None and i.is_drawn]
        placed = {id(i.mesh) for i in drawn}
        origin: Vec3 = (0.0, 0.0, 0.0)
        out: list[tuple[Mesh, tuple[float, ...], Vec3]] = [
            (mesh, IDENTITY, origin) for mesh in self.meshes if id(mesh) not in placed
        ]
        out += [(i.mesh, i.rotation, i.translation) for i in drawn]  # type: ignore[misc]
        out += [
            (obj.mesh, IDENTITY, origin) for obj in self.objects
            if obj.mesh is not None and id(obj.mesh) not in placed
        ]
        return out

    def unplaced_meshes(self) -> set[int]:
        """Meshes a level carries that no placement reaches, by index.

        `draw_list` stands these at the origin because nothing traced so far
        says what draws them, and for a level the honest reading is that the
        game does not: `warp_room1` has 81 placements and not one of them names
        any of its 42 numbered meshes, so geometry written into those is
        correct on disc and never appears. The set is empty for a model with no
        placement list at all -- the menu draws its meshes from code, and
        nothing here would say so.
        """
        if not self.instances:
            return set()
        placed = {id(i.mesh) for i in self.instances
                  if i.mesh is not None and i.is_drawn}
        return {mesh.index for mesh in self.drawn_meshes
                if id(mesh) not in placed}

    def face_colours(
        self, mesh: Mesh, face: int
    ) -> tuple[tuple[int, int, int], tuple[int, int, int], tuple[int, int, int]] | None:
        """The triangle's three vertex colours -- the shading is gouraud.

        The stored index names the first of three consecutive table entries, one
        per corner, exactly as the game loads them:
            sw colours[i], 0xC(packet); sw colours[i+1], 0x14; sw colours[i+2], 0x1C
        Because a strip shares vertices, consecutive triangles overlap here the
        same way they overlap in the vertex pool.
        """
        if face >= len(mesh.face_colour_index):
            return None
        i = mesh.face_colour_index[face] & COLOUR_INDEX_MASK
        if i + 3 > len(self.colours):
            return None
        return (self.colours[i], self.colours[i + 1], self.colours[i + 2])

    def face_colour(self, mesh: Mesh, face: int) -> tuple[int, int, int] | None:
        """The first of the triangle's three vertex colours."""
        triple = self.face_colours(mesh, face)
        return triple[0] if triple else None

    def face_is_untextured(self, mesh: Mesh, face: int) -> bool:
        """Bit 0 of the owning strip's flags: drawn as a plain gouraud triangle.

        The game tests this before it looks at anything texture-related, so these
        triangles never sample a texture even though the run-length cursor still
        advances over them.
        """
        if face >= len(mesh.face_strip_flags):
            return False
        return bool(mesh.face_strip_flags[face] & STRIP_FLAG_UNTEXTURED)

    def face_blend_mode(self, mesh: Mesh, face: int) -> int:
        """Bits 13-14 of the colour index: the GPU's semi-transparency mode."""
        if face >= len(mesh.face_colour_index):
            return 0
        return (mesh.face_colour_index[face] >> COLOUR_BLEND_SHIFT) & COLOUR_BLEND_MASK

    def flipbooks(self, minimum: int = 3) -> list["Flipbook"]:
        """Texture animations built out of duplicated meshes; see `Flipbook`."""
        return find_texture_flipbooks(self, minimum)

    def face_sampling(self, mesh: Mesh, face: int) -> tuple[str, int] | None:
        """What the triangle samples: ("none" | "texture" | "swatch", index).

        - "none"    the strip is flagged untextured; the index means nothing
        - "texture" index selects a texture in the sibling .tex pack
        - "swatch"  index selects a PALETTE, applied to the pack's swatch texture

        Bit 15 of the entry chooses between the last two. It is not an
        unresolvable case: the swatch is the pack's palette-less texture, and the
        entry names the palette to colour it with. That is how one mesh paints
        itself in several colour schemes out of a single 16x16 image.

        The untextured test comes first because the game tests it first, before
        it looks at anything texture-related -- though the run-length cursor still
        advances over those triangles, so their entry is read and discarded.
        """
        if face >= len(mesh.face_texture):
            return None
        if self.face_is_untextured(mesh, face):
            return ("none", 0)
        entry = mesh.face_texture[face]
        index = entry & TEXTURE_INDEX_MASK
        return ("swatch" if entry & TEXTURE_FLAG_SWATCH else "texture", index)

    def face_texture_index(self, mesh: Mesh, face: int) -> int | None:
        """Texture-pack index, or None when the triangle samples no texture."""
        kind = self.face_sampling(mesh, face)
        return kind[1] if kind and kind[0] == "texture" else None

    def face_is_translucent(self, mesh: Mesh, face: int) -> bool:
        """Bit 15 of the colour index turns on the GPU's semi-transparency."""
        if face >= len(mesh.face_colour_index):
            return False
        return bool(mesh.face_colour_index[face] & COLOUR_FLAG_TRANSLUCENT)

    def face_uvs(
        self, mesh: Mesh, face: int
    ) -> tuple[tuple[int, int], tuple[int, int], tuple[int, int]] | None:
        """The triangle's three UV pairs.

        A triple of three identical UVs is not "untextured" -- the game still
        samples the texture, just at one texel, which paints the triangle a flat
        colour taken from the palette. Models lean on this heavily: the last
        texture of a pack is usually a swatch strip used exactly this way.
        """
        if face >= len(mesh.face_uv_index):
            return None
        base = mesh.face_uv_index[face]
        if base + 3 > len(self.uvs):
            return None
        return (self.uvs[base], self.uvs[base + 1], self.uvs[base + 2])

    @property
    def bounds(self) -> Bounds | None:
        """The box around everything drawn, each mesh in its placed position.

        A placed box is the box around its eight transformed corners, not the
        transformed box: a rotated one no longer has axis-aligned sides.
        """
        boxes = []
        for mesh, rotation, translation in self.draw_list():
            if mesh.bounds is None:
                continue
            corners = [
                transform(corner, rotation, translation)
                for corner in itertools.product(*zip(mesh.bounds.min, mesh.bounds.max))
            ]
            boxes.append(Bounds(
                tuple(min(c[i] for c in corners) for i in range(3)),  # type: ignore[arg-type]
                tuple(max(c[i] for c in corners) for i in range(3)),  # type: ignore[arg-type]
                (0.0, 0.0, 0.0), 0.0,
            ))
        if not boxes:
            return None
        lo = tuple(min(b.min[i] for b in boxes) for i in range(3))
        hi = tuple(max(b.max[i] for b in boxes) for i in range(3))
        center = tuple((lo[i] + hi[i]) / 2 for i in range(3))
        radius = max((sum((hi[i] - lo[i]) ** 2 for i in range(3))) ** 0.5 / 2, 1e-6)
        return Bounds(lo, hi, center, radius)  # type: ignore[arg-type]

    def to_obj(self) -> str:
        """Emit every mesh into one OBJ, keeping per-mesh objects separate.

        A level's set is written out placed, so an object several records name
        appears once per record. The name carries a copy number in that case,
        since the mesh itself is the same one each time.
        """
        names = {mesh.index: f"mesh_{mesh.index:02d}" for mesh in self.meshes}
        names.update({
            obj.mesh.index: f"object_{obj.id:04X}"
            for obj in self.objects if obj.mesh is not None
        })
        chunks = []
        offset = 0
        seen: dict[int, int] = {}
        for mesh, rotation, translation in self.draw_list():
            copy = seen.get(mesh.index, 0)
            seen[mesh.index] = copy + 1
            chunks.append(f"o {names[mesh.index]}" + (f"_{copy}" if copy else ""))
            for position in mesh.positions:
                x, y, z = transform(position, rotation, translation)
                chunks.append(f"v {x:.6f} {-y:.6f} {-z:.6f}")
            for a, b, c in mesh.triangles(consistent_winding=True):
                chunks.append(f"f {a + 1 + offset} {b + 1 + offset} {c + 1 + offset}")
            offset += mesh.vertex_count
        return "\n".join(chunks) + "\n"


def segment_strips(winding: list[int]) -> list[Strip]:
    """Recover strip boundaries from the per-vertex winding flags.

    Inside a strip the flag flips on every triangle, so two equal flags in a row
    mean a new strip started -- its first two vertices carry no triangle yet and
    both read 0.
    """
    total = len(winding)
    strips: list[Strip] = []
    i = 0
    while i < total:
        start = i
        # The two lead vertices of a strip define no triangle of their own, and
        # neither does the flag comparison, so step past them unconditionally.
        i = start + 3
        if i > total:
            strips.append(Strip(start, total))
            break
        while i < total and winding[i] != winding[i - 1]:
            i += 1
        strips.append(Strip(start, i))
    return strips


@dataclass
class Flipbook:
    """A texture animation played by swapping whole meshes.

    Some surfaces animate their texture rather than their geometry, and the
    format has no per-frame texture field to express that. Instead the model
    carries the same quad several times over -- identical vertices, one texture
    each -- and the game shows one of them per tick. 119 of these exist in the
    NTSC-U set, covering 1595 meshes; all of them are single quads.

    A viewer that draws every mesh stacks the frames on top of each other, so
    the meshes past the first have to be hidden.
    """

    mesh_indices: list[int]
    texture_indices: list[int]

    @property
    def frame_count(self) -> int:
        return len(self.mesh_indices)


def find_texture_flipbooks(model: "Model", minimum: int = 3) -> list[Flipbook]:
    """Group meshes that share geometry and differ only in the texture they use.

    Requires each candidate to sample exactly one texture, and all of them to
    differ -- otherwise a mesh duplicated for some other reason would qualify.
    """
    by_shape: dict[tuple, list[Mesh]] = {}
    for mesh in model.meshes:
        if not mesh.positions:
            continue
        shape = (mesh.vertex_count, tuple(mesh.positions))
        by_shape.setdefault(shape, []).append(mesh)

    books: list[Flipbook] = []
    for members in by_shape.values():
        if len(members) < minimum:
            continue
        textures = []
        for mesh in members:
            used = {
                model.face_sampling(mesh, f)[1]
                for f in range(len(mesh.face_texture))
                if model.face_sampling(mesh, f)[0] == "texture"
            }
            if len(used) != 1:
                textures = []
                break
            textures.append(used.pop())
        if not textures or len(set(textures)) != len(textures):
            continue
        books.append(Flipbook([m.index for m in members], textures))
    return books


def read_strip_list(data: bytes, offset: int) -> list[tuple[int, int]]:
    """Read the strip list as the game does: `lbu` the high byte of each u16.

    High byte = the strip's triangle count, so it spans count + 2 vertices.
    Low byte is a flag word; bit 0 marks the strip as untextured. A high byte of
    0xFF ends the list.

    This is the authoritative strip layout, and it accounts for every triangle in
    the NTSC-U set. CTR-tools reads the same list but takes the *low* byte as the
    count and the high byte as flags, which is what scrambles its exports.
    """
    out: list[tuple[int, int]] = []
    while offset + 2 <= len(data):
        low, high = data[offset], data[offset + 1]
        if high == 0xFF:
            break
        out.append((high, low))
        offset += 2
    return out


def decode_texture_runs(data: bytes, offset: int, triangle_count: int) -> list[int]:
    """Expand the run-length texture list into one entry per triangle.

    Each u16 is `(run << 9) | texture`, and covers run + 1 triangles. The game
    walks it with a countdown that is never reset between strips, so runs cross
    strip boundaries freely.
    """
    out: list[int] = []
    remaining = 0
    entry = 0
    for _ in range(triangle_count):
        remaining -= 1
        if remaining < 0:
            if offset + 2 > len(data):
                break
            entry = int.from_bytes(data[offset : offset + 2], "little")
            offset += 2
            remaining = (entry >> 9) & 0x3F
        out.append(entry)
    return out


def _triangle_costs(positions: list[Vec3]) -> list[float]:
    """Squared longest edge of the triangle starting at each vertex."""
    out = [0.0] * max(0, len(positions) - 2)
    for t in range(len(out)):
        (ax, ay, az), (bx, by, bz), (cx, cy, cz) = positions[t : t + 3]
        e1 = (ax - bx) ** 2 + (ay - by) ** 2 + (az - bz) ** 2
        e2 = (bx - cx) ** 2 + (by - cy) ** 2 + (bz - cz) ** 2
        e3 = (ax - cx) ** 2 + (ay - cy) ** 2 + (az - cz) ** 2
        out[t] = max(e1, e2, e3)
    return out


def segment_strips_exact(
    positions: list[Vec3], winding: list[int], strip_count: int
) -> list[Strip] | None:
    """Split the pool into exactly `strip_count` strips, as compactly as possible.

    The file pins the strip count down: it states the triangle count, and a pool
    of V vertices cut into K strips always yields V - 2K triangles. The winding
    flags then say where a strip *may* begin -- on two flag=0 vertices, with the
    flag flipping on every triangle after that -- but leave the exact vertex
    ambiguous where a strip the game cut early still alternates across the seam.

    Picking the wrong vertex splices two unrelated runs together, and the
    resulting triangle stretches across the model, so of the valid partitions the
    right one is the one with the least total edge length. This is that partition,
    found exactly rather than by nudging boundaries one at a time.

    Returns None when no partition into `strip_count` valid strips exists.
    """
    total = len(positions)
    if strip_count < 1 or total < 3 * strip_count or total < 3:
        return None

    costs = _triangle_costs(positions)
    prefix = [0.0] * (total - 1)
    for t in range(total - 2):
        prefix[t + 1] = prefix[t] + costs[t]

    # How far a strip opened at s may run before the alternation breaks. A strip
    # needs three vertices, so s never exceeds total - 3.
    limit = [0] * (total - 2)
    for s in range(total - 2):
        end = s + 3
        while end <= total and not (end - 1 > s + 2 and winding[end - 1] == winding[end - 2]):
            end += 1
        limit[s] = min(end, total)

    infinity = float("inf")
    best = [[infinity] * (strip_count + 1) for _ in range(total + 1)]
    came_from = [[-1] * (strip_count + 1) for _ in range(total + 1)]
    best[0][0] = 0.0

    for s in range(total - 2):
        if winding[s] or winding[s + 1]:
            continue
        row = best[s]
        for k in range(strip_count):
            base = row[k]
            if base == infinity:
                continue
            base -= prefix[s]
            for end in range(s + 3, limit[s] + 1):
                cost = base + prefix[end - 2]
                if cost < best[end][k + 1]:
                    best[end][k + 1] = cost
                    came_from[end][k + 1] = s

    if best[total][strip_count] == infinity:
        return None

    strips: list[Strip] = []
    end, k = total, strip_count
    while k > 0:
        start = came_from[end][k]
        strips.append(Strip(start, end))
        end, k = start, k - 1
    strips.reverse()
    return strips


def _strip_cost(positions: list[Vec3], start: int, end: int) -> float:
    """Total squared edge length of a strip's triangles.

    A boundary placed one vertex late splices two unrelated runs together, and
    the resulting triangle spans the model instead of a neighbouring face -- so
    the wrong boundary is reliably the more expensive one.
    """
    cost = 0.0
    for i in range(start, end - 2):
        a, b, c = positions[i], positions[i + 1], positions[i + 2]
        for p, q in ((a, b), (b, c), (a, c)):
            cost += (p[0] - q[0]) ** 2 + (p[1] - q[1]) ** 2 + (p[2] - q[2]) ** 2
    return cost


def refine_strips(strips: list[Strip], positions: list[Vec3], window: int = 2) -> list[Strip]:
    """Nudge each strip boundary to whichever nearby position looks cheapest.

    The winding flags pin down how many strips there are and where they roughly
    break, but not always the exact vertex: a strip the game cut early still
    alternates correctly across the seam. Sliding a boundary keeps both the strip
    count and the total triangle count unchanged, so this only ever trades one
    plausible split for a better-looking one.
    """
    if len(strips) < 2:
        return strips

    out = [Strip(s.start, s.end) for s in strips]
    for i in range(len(out) - 1):
        left, right = out[i], out[i + 1]
        best_at, best_cost = right.start, None
        for candidate in range(right.start - window, right.start + window + 1):
            if candidate - left.start < 3 or right.end - candidate < 3:
                continue
            cost = _strip_cost(positions, left.start, candidate) + _strip_cost(
                positions, candidate, right.end
            )
            if best_cost is None or cost < best_cost:
                best_cost, best_at = cost, candidate
        left.end = best_at
        right.start = best_at
    return out


def _read_bounds(reader: Reader, ptr_bounds: int) -> Bounds:
    """A 0x14-byte block of ten int16 sitting in front of the vertex pool.

    The two corners are stored interleaved -- (minX, maxY, minZ) then
    (maxX, minY, maxZ) -- because PS1 screen space has Y growing downward. The
    centre and radius that follow match the box exactly, which is what pinned
    the field order down.
    """
    reader.seek(ptr_bounds)
    min_x, max_y, min_z, max_x, min_y, max_z = reader.array_i16(6)
    cx, cy, cz, radius = reader.array_i16(4)
    s = GTE_SCALE_SMALL
    return Bounds(
        (min_x * s, min_y * s, min_z * s),
        (max_x * s, max_y * s, max_z * s),
        (cx * s, cy * s, cz * s),
        radius * s,
    )


def _read_u16_list(reader: Reader, start: int, end: int, limit: int) -> list[int]:
    count = max(0, min((end - start) // 2, limit))
    if count == 0:
        return []
    reader.seek(start)
    return list(reader.array_u16(count))


def read_mesh(reader: Reader, index: int, scale: float = GTE_SCALE_SMALL) -> Mesh:
    """Read one mesh header at the reader's position, then its data blocks.

    Leaves the reader parked at the end of the header so headers can be read
    back-to-back.
    """
    base = reader.pos
    zero_a, zero_b = reader.array_i32(2)
    face_count_header, fmt = reader.array_i16(2)
    unk13, unk14 = reader.array_i16(2)

    pointers = []
    for _ in range(6):
        # Self-relative to the field's own position, matching what the game does:
        #   lw v0, 0x10(mesh); addiu v0, v0, 0x10; addu ptr, mesh, v0
        at = reader.pos
        pointers.append(at + reader.i32())
    # 0x28 and 0x2C are self-relative too when set; 0x30 is always zero.
    at_normals = reader.pos
    rel_normals = reader.i32()
    at_attach = reader.pos
    rel_attach = reader.i32()
    zero_end = reader.i32()
    header_end = reader.pos

    mesh = Mesh(
        index=index,
        header_offset=base,
        face_count_header=face_count_header,
        format=fmt,
        unk13=unk13,
        unk14=unk14,
        ptr_bounds=pointers[0],
        ptr_strips=pointers[1],
        ptr_uv_index=pointers[2],
        ptr_texture=pointers[3],
        ptr_colour_index=pointers[4],
        ptr_end=pointers[5],
        ptr_normals=at_normals + rel_normals if rel_normals else 0,
        ptr_attachment=at_attach + rel_attach if rel_attach else 0,
    )

    # 0x00 is a runtime pointer slot the loader fills, so it is zero on disk;
    # 0x04 and 0x30 are the only fields that are genuinely always zero.
    if zero_b or zero_end:
        mesh.warnings.append("header padding is not zero; layout may differ")

    size = len(reader)
    ordered = (
        0 < mesh.ptr_strips < mesh.ptr_bounds < mesh.ptr_uv_index <= mesh.ptr_texture <= size
    )
    if not ordered:
        mesh.warnings.append("pointers out of order; mesh skipped")
        reader.seek(header_end)
        return mesh

    mesh.bounds = _read_bounds(reader, mesh.ptr_bounds)

    # When a mesh carries normals they sit *after* the positions, not
    # interleaved: V position records, then V normal records, same 8-byte
    # stride. Striding the whole block by 16 would read every other vertex and
    # then mistake the normals for positions.
    vertex_start = mesh.ptr_bounds + BOUNDS_SIZE
    vertex_end = mesh.ptr_normals or mesh.ptr_uv_index
    count = (vertex_end - vertex_start) // VERTEX_STRIDE
    if count <= 0:
        mesh.warnings.append("empty vertex pool")
        reader.seek(header_end)
        return mesh

    reader.seek(vertex_start)
    positions: list[Vec3] = []
    flags: list[int] = []
    for _ in range(count):
        x, y, z, w = reader.array_i16(4)
        positions.append((x * scale, y * scale, z * scale))
        flags.append(w)
    mesh.positions = positions
    mesh.vertex_flags = flags
    # Bit 0 is the winding. Bit 1 disables the backface test; bits 2 and 8 also
    # occur, on 130 and 28 vertices respectively, and are unidentified.
    mesh.winding = [w & 1 for w in flags]

    entries = read_strip_list(reader.data, mesh.ptr_strips)
    mesh.strip_flags = [flags for _, flags in entries]

    cursor = 0
    strips: list[Strip] = []
    for triangles, _flags in entries:
        end = cursor + triangles + 2
        if end > count:
            mesh.warnings.append("strip list runs past the vertex pool")
            break
        strips.append(Strip(cursor, end))
        cursor = end

    if strips and sum(s.face_count for s in strips) == face_count_header:
        mesh.strips = strips
        mesh.strips_exact = True
        mesh.face_strip_flags = [
            flags for (triangles, flags), strip in zip(entries, strips)
            for _ in range(strip.face_count)
        ]
    else:
        # Should not happen on retail data; keep the geometric reconstruction as
        # a safety net for prototypes and damaged files.
        mesh.strips = refine_strips(segment_strips(mesh.winding), positions)
        mesh.warnings.append(
            "strip list does not account for the stated triangles; "
            "boundaries reconstructed geometrically"
        )

    if mesh.ptr_normals:
        reader.seek(mesh.ptr_normals)
        for _ in range(count):
            x, y, z, _pad = reader.array_i16(4)
            mesh.normals.append((x * scale, y * scale, z * scale))

    # Both per-triangle arrays hold exactly face_count_header entries; the bytes
    # after them up to the next pointer belong to the following structure.
    mesh.face_uv_index = _read_u16_list(
        reader, mesh.ptr_uv_index, mesh.ptr_texture, max(0, face_count_header)
    )
    mesh.face_colour_index = _read_u16_list(
        reader, mesh.ptr_colour_index, mesh.ptr_end, max(0, face_count_header)
    )
    mesh.face_texture = decode_texture_runs(
        reader.data, mesh.ptr_texture, max(0, face_count_header)
    )
    _read_volumes(reader, mesh, scale)

    reader.seek(header_end)
    return mesh


def _read_volumes(reader: Reader, mesh: Mesh, scale: float) -> None:
    """Open the attachment block at mesh+0x2C into `mesh.volumes` (§8.4)."""
    at = mesh.ptr_attachment
    if not at or at + ATTACHMENT_FIRST > len(reader):
        return
    reader.seek(at + ATTACHMENT_COUNT)
    count = reader.u16()
    if not 0 < count <= 64:
        if count:
            mesh.warnings.append(f"attachment block claims {count} records")
        return
    if at + ATTACHMENT_FIRST + ATTACHMENT_STRIDE * count > len(reader):
        mesh.warnings.append("attachment block runs past the end of the file")
        return
    reader.seek(at + ATTACHMENT_FIRST)
    for _ in range(count):
        f = reader.array_i16(ATTACHMENT_FIELDS)
        mesh.volumes.append(Volume(
            offset=(f[0] * scale, f[1] * scale, f[2] * scale),
            half_width=f[3] * scale,
            height=f[4] * scale,
            depth=f[5] * scale,
            unknown=f[6],
            flags=f[7],
        ))


def read_model(data: bytes | Reader, max_meshes: int = 4096) -> Model:
    reader = data if isinstance(data, Reader) else Reader(data)
    model = Model()

    if len(reader) < MESH_COUNT_OFFSET + 4:
        model.warnings.append("file too small to be an MDL")
        return model

    reader.seek(MESH_COUNT_OFFSET)
    count = reader.i32()
    if not 0 <= count <= max_meshes:
        model.warnings.append(f"implausible mesh count {count}")
        return model

    for i in range(count):
        if reader.remaining < MESH_HEADER_SIZE:
            model.warnings.append(f"truncated after {i} meshes")
            break
        try:
            model.meshes.append(read_mesh(reader, i))
        except (EOFError, IndexError) as exc:
            model.warnings.append(f"mesh {i}: {exc}")
            break

    _read_objects(reader, model)
    _read_instances(reader, model)
    _read_shared_tables(reader, model)
    return model


def _read_instances(reader: Reader, model: Model) -> None:
    """Read the placement list a level stands its set up with (§8.5).

    `model+0x18` holds `[i32 count]` then that many self-relative pointers, and
    each one reaches a sub-object whose +0x1C counts 160-byte placement records
    starting at its +0x34. The loader at 0x8001E0A8 walks exactly that: it takes
    the count from the sub-object's +0x1C and the array base from its +0x20,
    strides by 160, and copies out the translation at +0x04, the 32-byte MATRIX
    at +0x28 and the id at +0x88.
    """
    data = reader.data
    if len(data) < MESH_COUNT_OFFSET + 4:
        return

    count = reader.peek_i32(COUNT_SUBOBJECTS)
    if not 0 < count <= 64:
        return
    table = PTR_SUBOBJECTS + reader.peek_i32(PTR_SUBOBJECTS)
    if not 0 < table < len(data):
        return

    for slot in range(count):
        at = table + 4 + 4 * slot
        if at + 4 > len(data):
            return
        subobject = at + reader.peek_i32(at)
        if not 0 < subobject <= len(data) - SUBOBJECT_HEADER_SIZE:
            model.warnings.append(f"sub-object {slot} points outside the file")
            continue
        records = subobject + SUBOBJECT_RECORDS + reader.peek_i32(
            subobject + SUBOBJECT_RECORDS
        )
        total = reader.peek_i32(subobject + SUBOBJECT_COUNT)
        if not 0 <= total <= 4096 or not 0 < records <= len(data):
            model.warnings.append(f"sub-object {slot} has an implausible record array")
            continue
        for index in range(total):
            base = records + PLACEMENT_STRIDE * index
            if base + PLACEMENT_STRIDE > len(data):
                model.warnings.append(f"sub-object {slot} runs past the end of the file")
                break
            model.instances.append(_read_instance(reader, model, base, index))


def _read_instance(reader: Reader, model: Model, base: int, index: int) -> Instance:
    """One 160-byte record, with its id resolved to the mesh it draws."""
    reader.seek(base + PLACEMENT_MATRIX)
    rotation = tuple(v / GTE_ONE for v in reader.array_i16(9))
    reader.seek(base + PLACEMENT_TRANSLATION)
    translation = tuple(v * GTE_SCALE_SMALL for v in reader.array_i32(3))

    reader.seek(base + PLACEMENT_ID)
    identifier = reader.u16()
    reader.seek(base + PLACEMENT_FLAGS)
    instance = Instance(
        index=index,
        id=identifier,
        record=base,
        flags=reader.u16(),
        translation=translation,  # type: ignore[arg-type]
        rotation=rotation,  # type: ignore[arg-type]
    )

    namespace = identifier & 0x7000
    if namespace == OBJECT_NAMESPACE:
        # 1-based, the same bias the object resolver applies at 0x800159C8.
        slot = (identifier & 0xFFF) - 1
        if 0 <= slot < len(model.objects):
            instance.mesh = model.objects[slot].mesh
    elif namespace == CLIP_NAMESPACE:
        instance.clip = (identifier & CLIP_INDEX_MASK) >> CLIP_INDEX_SHIFT
        instance.frame = identifier & CLIP_FRAME_MASK
        instance.mesh = _clip_mesh(reader, model, instance.clip)
    return instance


def _clip_mesh(reader: Reader, model: Model, clip: int) -> Mesh | None:
    """The mesh a clip drives, matched to a mesh this reader already built.

    The clip record's +0x0C is a self-relative pointer to a mesh header (§9.1),
    so the header offset identifies which of the model's meshes it is.
    """
    data = reader.data
    if len(data) < PTR_CLIPS + 4 or not 0 <= clip < reader.peek_i32(COUNT_CLIPS):
        return None
    record = PTR_CLIPS + reader.peek_i32(PTR_CLIPS) + CLIP_STRIDE * clip
    if not 0 < record + CLIP_DRIVES + 4 <= len(data):
        return None
    header = record + CLIP_DRIVES + reader.peek_i32(record + CLIP_DRIVES)
    for mesh in model.drawn_meshes:
        if mesh.header_offset == header:
            return mesh
    return None


def _read_objects(reader: Reader, model: Model) -> None:
    """Read the object table: the level's static set (§8.3).

    The game reaches a mesh two ways, both ending in the same draw call at
    0x80019D54. An id in namespace 0x2000 indexes the numbered array counted at
    0x54; an id in namespace 0x5000 goes through 0x800159C4 into this table
    instead, and nothing in the file counts its entries -- the array simply runs
    until the scene nodes start. What ends it is the reference field: every
    entry names one of the load-time base addresses at `T(0x3C)`, so the walk
    stops at the first entry that does not.
    """
    data = reader.data
    if len(data) < MESH_COUNT_OFFSET + 4:
        return

    table = PTR_OBJECT_TABLE + reader.peek_i32(PTR_OBJECT_TABLE)
    refs = PTR_MODEL_REFS + reader.peek_i32(PTR_MODEL_REFS)
    pool = PTR_POOL + reader.peek_i32(PTR_POOL)
    if not 0 < pool <= refs <= len(data) or not 0 < table <= len(data):
        return

    # `[T(0x38)] + 1` records of 16 bytes, the first one 4 bytes past the count.
    reference_of = {
        refs + 4 + MODEL_REF_STRIDE * j + MODEL_REF_SLOT: j
        for j in range(reader.peek_i32(COUNT_MODEL_REFS) + 1)
    }

    index = 0
    while table + OBJECT_STRIDE * (index + 1) <= len(data):
        entry = table + OBJECT_STRIDE * index
        # 0x8001DD20: the resolver reads the reference through `entry + 4`, then
        # adds the offset to the base address it finds there.
        slot = entry + 4 + reader.peek_i32(entry + 8)
        reference = reference_of.get(slot)
        if reference is None:
            break
        offset = reader.peek_i32(entry + 4)
        obj = Object(
            id=OBJECT_NAMESPACE + 1 + index, offset=offset, reference=reference
        )
        if reference == 0 and pool <= offset < refs:
            reader.seek(offset)
            try:
                obj.mesh = read_mesh(reader, len(model.meshes) + len(model.objects))
            except (EOFError, IndexError) as exc:
                model.warnings.append(f"object 0x{obj.id:04X}: {exc}")
        model.objects.append(obj)
        index += 1


def _read_shared_tables(reader: Reader, model: Model) -> None:
    """Read the colour and UV tables the file header points at.

    Both pointers are self-relative like every other one, so the table sits at
    `field_offset + value`. Neither table stores its length: the colour table
    runs up to the UV table, and the UV table's extent comes from the largest
    index any triangle actually uses.
    """
    data = reader.data
    if len(data) < PTR_UV_TABLE + 4:
        return

    colour_start = PTR_COLOUR_TABLE + reader.peek_i32(PTR_COLOUR_TABLE)
    uv_start = PTR_UV_TABLE + reader.peek_i32(PTR_UV_TABLE)
    # 22 models (the fonts) have both tables empty at the same address.
    if not 0 < colour_start <= uv_start <= len(data):
        model.warnings.append("colour/UV table pointers are out of range")
        return

    model.colours = [
        (data[o], data[o + 1], data[o + 2]) for o in range(colour_start, uv_start - 3, 4)
    ]

    highest = -1
    for mesh in model.drawn_meshes:
        for index in mesh.face_uv_index:
            highest = max(highest, index)
    if highest < 0:
        return

    needed = highest + 3
    available = (len(data) - uv_start) // 2
    if needed > available:
        model.warnings.append("UV indices run past the end of the file")
        needed = max(0, available)
    model.uvs = [
        (data[uv_start + i * 2], data[uv_start + i * 2 + 1]) for i in range(needed)
    ]
