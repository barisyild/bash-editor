"""Move one character's animation onto another's geometry.

These models carry no skeleton -- a pose is a whole set of vertex positions --
so there is nothing to copy joint by joint. But a skeleton is implicit in the
animation itself: vertices belonging to the same limb move together, and the
clips say which those are. Grouping the source's vertices by how they move
recovers the rigid parts, and a rigid part is exactly what transfers between
characters of different proportions.

So: cluster the source's vertices by their motion, fit one rotation and
translation per cluster per pose, decide how much of each cluster speaks for
each target vertex, and blend. A limb keeps its own length because it is turned
about its joint rather than dragged toward the source's.

Three things this replaces, all of which were tried and all of which fail on
characters with different proportions:

* a deformation cage drags the target into the source's shape, so short arms are
  stretched out into long ones;
* copying the displacement moves a limb by a distance that belongs to the
  source's limb, and a long arm swinging down carries a short one into the body;
* fitting a rotation per vertex is far too noisy on a low-polygon mesh and
  shatters the model.

Correspondence is found in a normalised box, one axis at a time, so the target's
arm tip maps to the source's arm tip and not to whatever is the same absolute
distance out.
"""

from __future__ import annotations

import numpy as np

SEGMENTS = 12
NEIGHBOURS = 8
FALLOFF = 2.0
SMOOTHING = 2
EPSILON = 1e-6


def _extent(points: np.ndarray) -> np.ndarray:
    low, high = points.min(axis=0), points.max(axis=0)
    return np.where(high - low < EPSILON, 1.0, high - low)


def _normalise(points: np.ndarray) -> np.ndarray:
    return (points - points.min(axis=0)) / _extent(points)


def segment(
    static: np.ndarray, poses: np.ndarray, count: int = SEGMENTS, rounds: int = 40
) -> np.ndarray:
    """Group vertices by how they move: one label per vertex.

    The feature is the vertex's whole trajectory -- its displacement in every
    pose -- with its rest position appended so that two parts that happen to
    move alike still separate if they are far apart on the body.
    """
    static = np.asarray(static, dtype=np.float64)
    poses = np.asarray(poses, dtype=np.float64)
    motion = (poses - static).transpose(1, 0, 2).reshape(static.shape[0], -1)
    scale = np.abs(motion).max() or 1.0
    feature = np.concatenate(
        [motion / scale, _normalise(static) * 0.5], axis=1
    )

    count = min(count, feature.shape[0])
    # Farthest-point seeding, then Lloyd's algorithm. Deterministic: no random
    # start, so the same model always segments the same way.
    centres = [feature[0]]
    for _ in range(count - 1):
        distance = np.min(
            [np.linalg.norm(feature - c, axis=1) for c in centres], axis=0
        )
        centres.append(feature[int(distance.argmax())])
    centres = np.stack(centres)

    label = np.zeros(feature.shape[0], dtype=np.int32)
    for _ in range(rounds):
        distance = np.linalg.norm(feature[:, None, :] - centres[None, :, :], axis=2)
        fresh = distance.argmin(axis=1)
        if np.array_equal(fresh, label):
            break
        label = fresh
        for c in range(count):
            member = label == c
            if member.any():
                centres[c] = feature[member].mean(axis=0)
    return label


def correspondence(
    source_static: np.ndarray,
    target_static: np.ndarray,
    neighbours: int = NEIGHBOURS,
    falloff: float = FALLOFF,
) -> tuple[np.ndarray, np.ndarray]:
    """For each target vertex, which source vertices speak for it and how much."""
    source = _normalise(np.asarray(source_static, dtype=np.float64))
    target = _normalise(np.asarray(target_static, dtype=np.float64))
    count = min(neighbours, source.shape[0])
    distance = np.linalg.norm(target[:, None, :] - source[None, :, :], axis=2)
    nearest = np.argpartition(distance, count - 1, axis=1)[:, :count]
    picked = np.take_along_axis(distance, nearest, axis=1)
    weight = 1.0 / np.power(picked + 1e-4, falloff)
    weight /= weight.sum(axis=1, keepdims=True)
    return nearest, weight


def _kabsch(rest: np.ndarray, posed: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """The rotation and the two centroids taking `rest` onto `posed`."""
    rest_centre = rest.mean(axis=0)
    posed_centre = posed.mean(axis=0)
    covariance = (rest - rest_centre).T @ (posed - posed_centre)
    u, _, vt = np.linalg.svd(covariance)
    flip = np.sign(np.linalg.det(vt.T @ u.T))
    rotation = vt.T @ np.diag([1.0, 1.0, flip]) @ u.T
    return rotation, rest_centre, posed_centre


def skin_weights(
    labels: np.ndarray,
    nearest: np.ndarray,
    weight: np.ndarray,
    segments: int,
    target_neighbours: np.ndarray,
    smoothing: int = SMOOTHING,
) -> np.ndarray:
    """How much of each segment speaks for each target vertex, `(target, segments)`.

    A vertex takes the segments of the source vertices nearest it, and the field
    is then smoothed among the target's own neighbours so that the seam between
    two limbs bends instead of tearing.
    """
    out = np.zeros((nearest.shape[0], segments))
    np.add.at(out, (np.repeat(np.arange(nearest.shape[0]), nearest.shape[1]),
                    labels[nearest].ravel()), weight.ravel())
    out /= out.sum(axis=1, keepdims=True)
    for _ in range(smoothing):
        out = out[target_neighbours].mean(axis=1)
        out /= out.sum(axis=1, keepdims=True)
    return out


def transfer(
    source_static: np.ndarray,
    source_poses: np.ndarray,
    target_static: np.ndarray,
    segments: int = SEGMENTS,
    neighbours: int = NEIGHBOURS,
    ground: bool = True,
) -> np.ndarray:
    """Pose the target the way the source is posed.

    `source_poses` is `(poses, source vertices, 3)`; the result is
    `(poses, target vertices, 3)` in the target's own units.
    """
    source_static = np.asarray(source_static, dtype=np.float64)
    target_static = np.asarray(target_static, dtype=np.float64)
    source_poses = np.asarray(source_poses, dtype=np.float64)

    labels = segment(source_static, source_poses, segments)
    used = int(labels.max()) + 1
    nearest, weight = correspondence(source_static, target_static, neighbours)
    own, _ = correspondence(target_static, target_static, neighbours)
    influence = skin_weights(labels, nearest, weight, used, own)

    # Rotations only mean anything between shapes at the same scale, so the
    # target is matched to the source's height first and put back afterwards.
    # Scaling is about the model's own origin and nothing else: every character
    # in this game stands centred on it with its feet at y = 0, so lining up
    # bounding boxes instead would shift a narrower character sideways by half
    # the difference in width.
    scale = float(_extent(source_static)[1] / _extent(target_static)[1])
    aligned = target_static * scale

    members = [np.flatnonzero(labels == c) for c in range(used)]
    share = influence.sum(axis=0)
    # Each segment turns about the target's own joint, not the source's: the
    # rotation is the source's, but the centre it turns about and the place that
    # centre lands are both measured on the target. Otherwise a short arm is
    # swung about a shoulder that belongs to a long one, and it flies off.
    rest_centres = np.stack([
        (influence[:, c, None] * aligned).sum(axis=0) / max(share[c], EPSILON)
        for c in range(used)
    ])

    out = np.empty((source_poses.shape[0], target_static.shape[0], 3))
    for i, pose in enumerate(source_poses):
        goal = (pose[nearest] * weight[..., None]).sum(axis=1)
        moved = np.zeros_like(aligned)
        for c, member in enumerate(members):
            if member.size < 3 or share[c] <= EPSILON:
                continue
            rotation, _, _ = _kabsch(source_static[member], pose[member])
            centre = (influence[:, c, None] * goal).sum(axis=0) / share[c]
            part = (aligned - rest_centres[c]) @ rotation.T + centre
            moved += influence[:, c, None] * part
        if ground:
            # Blending several rotations does not preserve the foot level, and a
            # character standing a fraction of a unit into the floor is the one
            # error the eye catches immediately. Put the lowest point where the
            # source's is, which also carries a jump across rather than flattening
            # it: y grows downward in this format, so the lowest point is the
            # largest.
            moved[:, 1] += pose[:, 1].max() - moved[:, 1].max()
        out[i] = moved / scale
    return out
