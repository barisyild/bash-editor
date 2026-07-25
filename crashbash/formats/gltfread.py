"""Read poses back out of a glTF binary, the other half of `gltf.export_glb`.

This is what makes a round trip through a modelling tool possible: export a
model, pose or animate it there by whatever means the tool offers, and bring the
result back as keyframes. Nothing here tries to guess how a character should
move -- the poses are read, not invented.

A mesh's morph targets are the keyframes, matching what the exporter writes. The
vertices are matched back to the model's own by position in the rest pose rather
than by index, so a file that has been through a tool that reordered, merged or
split vertices still lands correctly.
"""

from __future__ import annotations

import json
import struct
from dataclasses import dataclass

import numpy as np

from ..binreader import GTE_SCALE_SMALL
from .gltf import AXIS_FLIP

GLB_MAGIC = 0x46546C67
CHUNK_JSON = 0x4E4F534A
CHUNK_BIN = 0x004E4942

COMPONENT_DTYPE = {
    5120: "<i1", 5121: "<u1", 5122: "<i2", 5123: "<u2", 5125: "<u4", 5126: "<f4",
}
TYPE_COUNT = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}

# How close a glTF vertex must sit to a model vertex to be called the same one,
# in model units. Anything further and the match is refused rather than guessed.
MATCH_TOLERANCE = 1e-3


@dataclass
class Glb:
    json: dict
    binary: bytes

    def accessor(self, index: int) -> np.ndarray:
        spec = self.json["accessors"][index]
        count = spec["count"]
        width = TYPE_COUNT[spec["type"]]
        dtype = COMPONENT_DTYPE[spec["componentType"]]
        if "bufferView" not in spec:
            return np.zeros((count, width), dtype=dtype).squeeze()
        view = self.json["bufferViews"][spec["bufferView"]]
        start = view.get("byteOffset", 0) + spec.get("byteOffset", 0)
        raw = np.frombuffer(
            self.binary, dtype=dtype, count=count * width, offset=start
        )
        return raw.reshape(count, width) if width > 1 else raw


def read_glb(path) -> Glb:
    data = open(path, "rb").read()
    magic, _version, _length = struct.unpack_from("<3I", data, 0)
    if magic != GLB_MAGIC:
        raise ValueError(f"{path} is not a glTF binary")
    document: dict | None = None
    binary = b""
    at = 12
    while at + 8 <= len(data):
        size, kind = struct.unpack_from("<2I", data, at)
        chunk = data[at + 8 : at + 8 + size]
        if kind == CHUNK_JSON:
            document = json.loads(chunk.decode("utf-8"))
        elif kind == CHUNK_BIN:
            binary = chunk
        at += 8 + size + (-size % 4)
    if document is None:
        raise ValueError(f"{path} has no JSON chunk")
    return Glb(document, binary)


def _mesh_index(glb: Glb, name: str) -> int:
    for i, mesh in enumerate(glb.json.get("meshes", [])):
        if mesh.get("name") == name:
            return i
    raise ValueError(
        f"no mesh named {name!r}; the file has "
        f"{[m.get('name') for m in glb.json.get('meshes', [])]}"
    )


def read_poses(path, mesh_name: str, positions) -> np.ndarray:
    """Every morph target of `mesh_name`, in the model's own vertex order.

    `positions` is the model mesh's rest positions, which the glTF vertices are
    matched against. The result is `(targets, len(positions), 3)` in int16 model
    units, ready for `animwrite.ClipSpec`.
    """
    glb = read_glb(path)
    mesh = glb.json["meshes"][_mesh_index(glb, mesh_name)]

    rest: list[np.ndarray] = []
    targets: list[list[np.ndarray]] = []
    for primitive in mesh["primitives"]:
        rest.append(glb.accessor(primitive["attributes"]["POSITION"]))
        deltas = [glb.accessor(t["POSITION"]) for t in primitive.get("targets", [])]
        targets.append(deltas)
    counts = {len(t) for t in targets}
    if len(counts) != 1:
        raise ValueError(f"{mesh_name}: primitives disagree on how many targets")
    target_count = counts.pop()

    vertices = np.concatenate(rest)
    posed = [
        vertices + np.concatenate([t[i] for t in targets])
        for i in range(target_count)
    ]

    # glTF is Y-up and the model is not; undo the exporter's flip and scale.
    def to_model(points: np.ndarray) -> np.ndarray:
        return np.asarray(points, dtype=np.float64) / AXIS_FLIP / GTE_SCALE_SMALL

    model_rest = np.asarray(positions, dtype=np.float64) / GTE_SCALE_SMALL
    incoming = to_model(vertices)

    distance = np.linalg.norm(
        model_rest[:, None, :] - incoming[None, :, :], axis=2
    )
    nearest = distance.argmin(axis=1)
    worst = distance[np.arange(len(model_rest)), nearest].max() * GTE_SCALE_SMALL
    if worst > MATCH_TOLERANCE:
        raise ValueError(
            f"{mesh_name}: a model vertex sits {worst:.4f} units from the nearest "
            "glTF vertex; this file is not that mesh"
        )

    out = np.empty((target_count, len(model_rest), 3), dtype=np.int16)
    for i, pose in enumerate(posed):
        out[i] = np.clip(np.round(to_model(pose)[nearest]), -32768, 32767)
    return out
