"""Export a model, its textures and its animation to glTF 2.0 (.glb).

glTF is the one interchange format that carries everything this game's models
hold. The mapping is exact rather than approximate:

    keyframe                     -> morph target (POSITION deltas)
    frame record: A, B, weight   -> a weights channel sample, two entries set
    A + (B - A) * w              -> what a morph target already means
    per-triangle gouraud colour  -> COLOR_0
    per-triangle texture         -> one primitive per texture, one material each

Reconstructing every pose as a weighted sum of keyframe poses -- which is
literally what a glTF renderer does with the weights channel -- reproduces the
decoder's own output to within 0.0039 model units, and that number is exactly
1/256: one step of the fixed point the positions are stored in. The difference
is the game's rounding, not a structural mismatch.

Colours are written at the scale the console draws them. A textured triangle's
colour is a *multiplier* -- the blend is `texel * colour / 128`, above 128 it
brightens, and the true value runs to 2.0 where glTF display stops at 1. An
untextured triangle's colour is the *pixel itself*: the console draws it
directly, no texel, no doubling. So textured corners carry `colour / 127.5`
unclamped, untextured corners carry `colour / 255` (swatch triangles fold
their palette texel in and land on the same displayed-pixel scale), and the
importer inverts by the primitive's material. One scale for both was the old
mistake in the other direction: flat surfaces rendered twice as bright as the
textured ones beside them.

The values live in two attributes:

* `COLOR_0` -- viewers that multiply vertex colours as given (the three.js
  family) show the game's own brightening; strict ones clamp at 1 and show
  textured hot corners paler. Display only, never data. Blender is a clamper:
  its importer quantises COLOR_0 into a byte attribute, which is why COLOR_0
  alone cannot round-trip.
* `_CRASHBASH_COLOR` -- the same accessor under an application name. Blender
  imports it as a float attribute untouched and writes it back exactly when
  "Attributes" is ticked in its glTF export -- measured: values of 2.0
  survive the full Blender pass to the last bit, where COLOR_0 comes back
  clamped and COLOR_1 comes back clamped *and* quantised. The importer
  prefers it and recovers every colour byte exactly; clamping used to crush
  128..255 to 128 on re-import, which drained the cutscene models built on
  hot baked lighting.

One thing does not survive the trip, deliberately: triangle strips. glTF has
loose triangles, so a re-import has to re-strip the mesh -- and because a clip
indexes vertices by their position in the pool, that means rewriting the clips
too. Model and animation cannot be imported separately.
"""

from __future__ import annotations

import base64
import json
import struct
import zlib
from dataclasses import dataclass, field

import numpy as np

from . import mdl as mdl_module
from . import tex as tex_module

GLB_MAGIC = 0x46546C67
GLB_VERSION = 2
CHUNK_JSON = 0x4E4F534A
CHUNK_BIN = 0x004E4942

COMPONENT_FLOAT = 5126
COMPONENT_USHORT = 5123
TARGET_ARRAY_BUFFER = 34962
TARGET_ELEMENT_ARRAY = 34963
MODE_TRIANGLES = 4

# PS1 is Y-down with Z into the screen; glTF is Y-up, right-handed, -Z forward.
AXIS_FLIP = np.array([1.0, -1.0, -1.0], dtype=np.float32)

_IDENTITY_MATRIX = [1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0,
                    0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0]


def _node_matrix(rotation, translation) -> list[float]:
    """A placement record as glTF's column-major 4x4, in the exporter's axes.

    Positions are written out flipped by `AXIS_FLIP`, so the model-space
    transform is conjugated by that flip: the flip is diagonal and its own
    inverse, which makes `F(Rp + t)` into `(F R F)(Fp) + Ft`.
    """
    flip = AXIS_FLIP.astype(np.float64)
    basis = np.asarray(rotation, dtype=np.float64).reshape(3, 3)
    matrix = np.eye(4)
    matrix[:3, :3] = basis * flip[:, None] * flip[None, :]
    matrix[:3, 3] = np.asarray(translation, dtype=np.float64) * flip
    return matrix.T.reshape(-1).tolist()

# The game bakes one animation record per tick. Nothing in the executable states
# the tick rate, so this is the viewer's assumption carried over -- see the
# playback note in docs/FORMAT.md.
FRAMES_PER_SECOND = 30.0


def encode_png(rgba: np.ndarray) -> bytes:
    """Minimal RGBA PNG writer, so exporting needs no imaging library."""
    height, width = rgba.shape[0], rgba.shape[1]
    raw = b"".join(b"\x00" + rgba[y].tobytes() for y in range(height))

    def chunk(tag: bytes, payload: bytes) -> bytes:
        body = tag + payload
        return struct.pack(">I", len(payload)) + body + struct.pack(">I", zlib.crc32(body))

    return b"".join(
        [
            b"\x89PNG\r\n\x1a\n",
            chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)),
            chunk(b"IDAT", zlib.compress(raw, 6)),
            chunk(b"IEND", b""),
        ]
    )


class _Buffer:
    """Accumulates the binary chunk and hands back accessor indices."""

    def __init__(self) -> None:
        self.data = bytearray()
        self.views: list[dict] = []
        self.accessors: list[dict] = []

    def _view(self, payload: bytes, target: int | None = None) -> int:
        while len(self.data) % 4:
            self.data.append(0)
        offset = len(self.data)
        self.data += payload
        view = {"buffer": 0, "byteOffset": offset, "byteLength": len(payload)}
        if target is not None:
            view["target"] = target
        self.views.append(view)
        return len(self.views) - 1

    def vec(self, array: np.ndarray, kind: str, minmax: bool = False) -> int:
        """Add a float accessor for an (n, c) array."""
        array = np.ascontiguousarray(array, dtype=np.float32)
        view = self._view(array.tobytes(), TARGET_ARRAY_BUFFER)
        accessor = {
            "bufferView": view,
            "componentType": COMPONENT_FLOAT,
            "count": int(array.shape[0]),
            "type": kind,
        }
        if minmax:
            accessor["min"] = [float(v) for v in array.min(axis=0)]
            accessor["max"] = [float(v) for v in array.max(axis=0)]
        self.accessors.append(accessor)
        return len(self.accessors) - 1

    def scalar(self, array: np.ndarray, minmax: bool = False) -> int:
        array = np.ascontiguousarray(array, dtype=np.float32)
        view = self._view(array.tobytes())
        accessor = {
            "bufferView": view,
            "componentType": COMPONENT_FLOAT,
            "count": int(array.shape[0]),
            "type": "SCALAR",
        }
        if minmax:
            accessor["min"] = [float(array.min())]
            accessor["max"] = [float(array.max())]
        self.accessors.append(accessor)
        return len(self.accessors) - 1

    def indices(self, array: np.ndarray) -> int:
        array = np.ascontiguousarray(array, dtype=np.uint16)
        view = self._view(array.tobytes(), TARGET_ELEMENT_ARRAY)
        self.accessors.append(
            {
                "bufferView": view,
                "componentType": COMPONENT_USHORT,
                "count": int(array.shape[0]),
                "type": "SCALAR",
            }
        )
        return len(self.accessors) - 1

    def image(self, png: bytes) -> int:
        return self._view(png)


@dataclass
class _Group:
    """One primitive's worth of triangles: those sharing a texture."""

    texture: int | None
    corners: list[int] = field(default_factory=list)  # pool index per vertex
    uvs: list[tuple[float, float]] = field(default_factory=list)
    colours: list[tuple[float, float, float, float]] = field(default_factory=list)


def _group_triangles(model, mesh, pack) -> list[_Group]:
    """Split a mesh's triangles into one group per texture.

    A glTF primitive carries a single material, so a mesh that samples eleven
    textures becomes eleven primitives. Flat-coloured triangles -- the ones the
    game paints from a single swatch texel -- have no texture of their own and
    collect into one untextured group with the colour folded in.
    """
    swatch = None
    if pack is not None:
        swatch = next((t for t in pack.textures if t.is_swatch), None)
    cells = swatch.indices() if swatch is not None else None

    groups: dict[int | None, _Group] = {}
    for a, b, c, face in mesh.indexed_triangles():
        sampling = model.face_sampling(mesh, face)
        kind, index = sampling if sampling else ("none", 0)
        triple = model.face_colours(mesh, face)
        base = (
            np.array(triple, dtype=np.float32) / 255.0
            if triple
            else np.full((3, 3), 0.75, dtype=np.float32)
        )
        uvs = model.face_uvs(mesh, face)

        key: int | None = None
        corner_uvs = [(0.0, 0.0)] * 3
        # Textured triangles carry a multiplier, untextured ones carry the
        # pixel itself: the console draws an untextured polygon with the
        # colour directly, no texel and no doubling. One factor per kind, and
        # the importer inverts by the primitive's material accordingly.
        factor = 1.0
        if kind == "texture" and uvs is not None and pack is not None:
            if 0 <= index < len(pack.textures):
                texture = pack.textures[index]
                key = index
                factor = 2.0
                corner_uvs = [
                    ((u + 0.5) / texture.width, (v + 0.5) / texture.height)
                    for u, v in uvs
                ]
        elif kind == "swatch" and uvs is not None and cells is not None:
            u, v = uvs[0]
            cell = int(cells[min(v, swatch.height - 1), min(u, swatch.width - 1)])
            if index < len(pack.palettes) and cell < pack.palettes[index].shape[0]:
                # Fold the swatch texel in once, doubled as the blend doubles:
                # the folded value is the displayed colour, like any other
                # untextured corner's.
                texel = pack.palettes[index][cell][:3].astype(np.float32) / 255.0
                base = base * texel
                factor = 2.0

        group = groups.setdefault(key, _Group(key))
        for corner, vertex in enumerate((a, b, c)):
            group.corners.append(vertex)
            group.uvs.append(corner_uvs[corner])
            # Unclamped: a textured corner's multiplier runs to 2.0. Blender
            # and the three.js family use float vertex colours as they are;
            # strict viewers clamp at 1 and lose only display, never data --
            # the importer's inversion is exact.
            rgb = base[corner] * factor
            group.colours.append((float(rgb[0]), float(rgb[1]), float(rgb[2]), 1.0))
    return list(groups.values())


def _clip_targets(clips) -> tuple[list[tuple[int, int]], dict[tuple[int, int], int]]:
    """Every keyframe of every clip on one mesh, as the mesh's morph targets.

    Morph targets belong to the mesh, not to a clip, so the clips driving a mesh
    share one list and each addresses its own slice of it.
    """
    order: list[tuple[int, int]] = []
    lookup: dict[tuple[int, int], int] = {}
    for clip in clips:
        for offset in clip.keyframes():
            key = (clip.index, offset)
            if key not in lookup:
                lookup[key] = len(order)
                order.append(key)
    return order, lookup


def export_glb(
    model: "mdl_module.Model",
    pack: "tex_module.TexturePack | None" = None,
    animations: list | None = None,
    name: str = "model",
) -> bytes:
    """Build a single-file glTF binary holding geometry, textures and clips."""
    animations = animations or []
    buffer = _Buffer()

    images: list[dict] = []
    textures: list[dict] = []
    materials: list[dict] = []
    material_of: dict[int | None, int] = {}

    def material_for(texture_index: int | None) -> int:
        if texture_index in material_of:
            return material_of[texture_index]
        entry = {
            "pbrMetallicRoughness": {
                "metallicFactor": 0.0,
                "roughnessFactor": 1.0,
            },
            "doubleSided": True,
        }
        if texture_index is not None and pack is not None:
            rgba = pack.textures[texture_index].to_rgba(pack.palettes)
            images.append({"bufferView": buffer.image(encode_png(rgba)),
                           "mimeType": "image/png"})
            textures.append({"source": len(images) - 1, "sampler": 0})
            entry["pbrMetallicRoughness"]["baseColorTexture"] = {
                "index": len(textures) - 1
            }
            entry["name"] = pack.textures[texture_index].name
            entry["alphaMode"] = "MASK"
            entry["alphaCutoff"] = 0.5
        else:
            entry["name"] = "flat"
        materials.append(entry)
        material_of[texture_index] = len(materials) - 1
        return material_of[texture_index]

    meshes: list[dict] = []
    nodes: list[dict] = []
    gltf_animations: list[dict] = []

    # A level's set is object meshes, not numbered ones, and leaving them out
    # exports a warp room as an empty sky. They are named after the id the game
    # reaches them by, which also keeps them clear of the `_meshNN` the importer
    # matches on: an object has no slot in the numbered array to be written back
    # into, so an edited one must not look importable.
    mesh_names = {mesh.index: f"{name}_mesh{mesh.index:02d}" for mesh in model.meshes}
    mesh_names.update({
        obj.mesh.index: f"{name}_object{obj.id:04X}"
        for obj in model.objects if obj.mesh is not None
    })

    # Where each mesh ended up, so the placement pass below can hang extra nodes
    # off the one glTF mesh instead of writing its geometry again.
    mesh_nodes: dict[int, tuple[int, int]] = {}

    for mesh in model.drawn_meshes:
        groups = _group_triangles(model, mesh, pack)
        if not groups:
            continue

        clips = [c for c in animations if c.mesh_index == mesh.index and c.frame_count]
        target_order, target_lookup = _clip_targets(clips)
        poses = {}
        for clip in clips:
            for offset in clip.keyframes():
                poses[(clip.index, offset)] = clip.keyframe_pose(offset)

        base_positions = np.asarray(mesh.positions, dtype=np.float32) * AXIS_FLIP

        primitives = []
        for group in groups:
            corners = np.asarray(group.corners, dtype=np.int32)
            positions = base_positions[corners]
            colours = buffer.vec(np.asarray(group.colours), "VEC4")
            attributes = {
                "POSITION": buffer.vec(positions, "VEC3", minmax=True),
                "COLOR_0": colours,
                # The same accessor again under an application name: Blender
                # quantises and clamps COLOR_0 on import, but carries this one
                # through as untouched floats, so a round trip through it can
                # keep the multipliers above 1 (see the module docstring).
                "_CRASHBASH_COLOR": colours,
            }
            if group.texture is not None:
                attributes["TEXCOORD_0"] = buffer.vec(np.asarray(group.uvs), "VEC2")

            primitive = {
                "attributes": attributes,
                "indices": buffer.indices(np.arange(len(corners))),
                "material": material_for(group.texture),
                "mode": MODE_TRIANGLES,
            }
            if target_order:
                primitive["targets"] = [
                    {
                        "POSITION": buffer.vec(
                            (np.asarray(poses[key], dtype=np.float32) * AXIS_FLIP)[corners]
                            - positions,
                            "VEC3",
                            minmax=True,
                        )
                    }
                    for key in target_order
                ]
            primitives.append(primitive)

        entry = {"primitives": primitives, "name": mesh_names[mesh.index]}
        if target_order:
            entry["weights"] = [0.0] * len(target_order)
        meshes.append(entry)
        nodes.append({"mesh": len(meshes) - 1, "name": entry["name"]})
        node_index = len(nodes) - 1
        mesh_nodes[mesh.index] = (node_index, len(meshes) - 1)

        for clip in clips:
            times = np.arange(clip.frame_count, dtype=np.float32) / FRAMES_PER_SECOND
            weights = np.zeros((clip.frame_count, len(target_order)), dtype=np.float32)
            for frame in clip.frames:
                blend = frame.weight / 4096.0
                weights[frame.index, target_lookup[(clip.index, frame.key_a)]] = 1.0 - blend
                if frame.weight:
                    weights[frame.index, target_lookup[(clip.index, frame.key_b)]] = blend
            sampler_input = buffer.scalar(times, minmax=True)
            sampler_output = buffer.scalar(weights.reshape(-1))
            gltf_animations.append(
                {
                    "name": clip.label,
                    # Samples sit one per game tick, so LINEAR reproduces the
                    # game exactly at every tick and interpolates between them.
                    "samplers": [
                        {
                            "input": sampler_input,
                            "output": sampler_output,
                            "interpolation": "LINEAR",
                        }
                    ],
                    "channels": [
                        {"sampler": 0, "target": {"node": node_index, "path": "weights"}}
                    ],
                }
            )

    # A level stands its set up through the placement list (§8.5), so a mesh
    # several records name is one glTF mesh under several nodes -- writing its
    # geometry once per copy would bloat the file for nothing. The first record
    # moves the mesh's own node so the animation channels above keep pointing at
    # a node that is really in the scene.
    seen: set[int] = set()
    for instance in model.instances:
        if instance.mesh is None or not instance.is_drawn:
            continue
        placement = mesh_nodes.get(instance.mesh.index)
        if placement is None:
            continue
        node_index, gltf_mesh = placement
        matrix = _node_matrix(instance.rotation, instance.translation)
        if instance.mesh.index in seen:
            nodes.append({
                "mesh": gltf_mesh,
                "name": f"{mesh_names[instance.mesh.index]}_copy{instance.index:03d}",
                "matrix": matrix,
            })
        else:
            seen.add(instance.mesh.index)
            if matrix != _IDENTITY_MATRIX:
                nodes[node_index]["matrix"] = matrix

    document = {
        "asset": {"version": "2.0", "generator": "Bash Editor"},
        "scene": 0,
        "scenes": [{"nodes": list(range(len(nodes)))}],
        "nodes": nodes,
        "meshes": meshes,
        "buffers": [{"byteLength": len(buffer.data)}],
        "bufferViews": buffer.views,
        "accessors": buffer.accessors,
        "materials": materials,
    }
    if images:
        document["images"] = images
        document["textures"] = textures
        # Nearest sampling: these textures are 16 or 32 pixels across and were
        # drawn to be seen unfiltered.
        document["samplers"] = [{"magFilter": 9728, "minFilter": 9728}]
    if gltf_animations:
        document["animations"] = gltf_animations

    return _pack_glb(document, bytes(buffer.data))


def _pack_glb(document: dict, binary: bytes) -> bytes:
    json_chunk = json.dumps(document, separators=(",", ":")).encode("utf-8")
    json_chunk += b" " * (-len(json_chunk) % 4)
    binary += b"\x00" * (-len(binary) % 4)

    total = 12 + 8 + len(json_chunk) + (8 + len(binary) if binary else 0)
    out = bytearray()
    out += struct.pack("<III", GLB_MAGIC, GLB_VERSION, total)
    out += struct.pack("<II", len(json_chunk), CHUNK_JSON) + json_chunk
    if binary:
        out += struct.pack("<II", len(binary), CHUNK_BIN) + binary
    return bytes(out)
