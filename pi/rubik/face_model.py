"""Fixed-camera face model: where every sticker of the D, B and R faces is in
the camera image, without clicking points.

data/face_model.json holds, for a reference photo of image_size:
  * the 7 face corners (T, V, BL, BR seen; R1, B3, D1 behind the rods), in
    lens-corrected ("undistorted") coordinates,
  * the lens bend k1 (radial, about the image centre).
Each face is a 3x3 grid. A grid point (col, row), 0..3, maps to the image by
the face's homography (corners -> undistorted image), then the lens bend.
The model was fitted so the grooves between cubies in the photo fall on the
grid lines (about 3 px).
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

# Grid corner of each face at sticker 1, 3, 9, 7 (face seen from outside).
QUAD = {"R": ["R1", "T", "V", "BL"], "B": ["T", "B3", "BR", "V"], "D": ["D1", "BL", "V", "BR"]}
_UNIT = [(0, 0), (3, 0), (3, 3), (0, 3)]


def _homog(src, dst):
    a = []
    for (x, y), (u, v) in zip(src, dst):
        a += [[x, y, 1, 0, 0, 0, -u * x, -u * y, -u], [0, 0, 0, x, y, 1, -v * x, -v * y, -v]]
    return np.linalg.svd(np.array(a, float))[2][-1].reshape(3, 3)


class FaceModel:
    def __init__(self, data: dict):
        self.k1 = float(data["k1"])
        self.centre = np.array(data["centre"], float)
        self.scale = float(data["scale"])
        self.image_size = tuple(data.get("image_size", (2000, 1125)))
        corners = {k: np.array(v, float) for k, v in data["corners_undistorted"].items()}
        self.H = {f: _homog(_UNIT, [corners[c] for c in names]) for f, names in QUAD.items()}

    @classmethod
    def load(cls, path: str | Path) -> "FaceModel":
        return cls(json.loads(Path(path).read_text()))

    # ------------------------------------------------------------ geometry
    def _distort(self, pu: np.ndarray) -> np.ndarray:
        """Undistorted -> image coordinates (inverse of the radial bend)."""
        p = pu.copy()
        for _ in range(60):
            d = (p - self.centre) / self.scale
            p = self.centre + (pu - self.centre) / (1 + self.k1 * (d ** 2).sum(1, keepdims=True))
        return p

    def grid_to_image(self, face: str, uv) -> np.ndarray:
        """Grid points (col, row in 0..3) of a face -> image pixels (reference size)."""
        uv = np.atleast_2d(np.asarray(uv, float))
        q = np.c_[uv, np.ones(len(uv))] @ self.H[face].T
        return self._distort(q[:, :2] / q[:, 2:])

    def centre_of(self, name: str) -> tuple[float, float]:
        row, col = divmod(int(name[1]) - 1, 3)
        x, y = self.grid_to_image(name[0], (col + 0.5, row + 0.5))[0]
        return float(x), float(y)

    def patch_of(self, name: str, inset: float = 0.3) -> list[list[float]]:
        """The middle of a sticker as a 4-corner polygon: the cell shrunk by
        `inset` of a sticker on every side (0.3 keeps the middle 40 %, away
        from the grooves and bevels)."""
        row, col = divmod(int(name[1]) - 1, 3)
        a, b = inset, 1 - inset
        pts = [(col + a, row + a), (col + b, row + a), (col + b, row + b), (col + a, row + b)]
        return [[float(x), float(y)] for x, y in self.grid_to_image(name[0], pts)]

    def rois(self, names: list[str], inset: float = 0.3) -> dict:
        """A rois dict for sample_lab: centres and sampling patches."""
        return {"source": "face_model", "image_size": list(self.image_size),
                "points": {n: list(self.centre_of(n)) for n in names},
                "patches": {n: self.patch_of(n, inset) for n in names}}

    def grid_lines(self, face: str, steps: int = 60) -> list[np.ndarray]:
        """Outline and the four inner lines of a face, as curved polylines."""
        s = np.linspace(0, 3, steps)
        z = np.zeros_like(s)
        lines = [np.r_[np.c_[s, z], np.c_[3 + z, s], np.c_[s[::-1], 3 + z], np.c_[z, s[::-1]]]]
        for i in (1, 2):
            lines += [np.c_[i + z, s], np.c_[s, i + z]]
        return [self.grid_to_image(face, ln) for ln in lines]
