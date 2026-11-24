"""Camera capture, sticker sampling and colour classification.

* Camera: Raspberry Pi Camera Module 3 Wide through picamera2. Exposure,
  white balance and focus are locked after warm-up so colours do not drift
  between the photos of one scan.
* Sampling: each visible facelet has a pixel position (picked once with
  `main.py calibrate-rois`). We take the median colour of a small square
  around it and convert to CIELAB.
* Classification: a solved cube is scanned once (`calibrate-colours`) to get
  a reference Lab colour per face. For a scramble we assign the 48 movable
  stickers to faces with the Hungarian algorithm, forcing exactly 8 per face,
  which removes most red/orange and white/yellow mix-ups.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np

from .cube import CENTRES, FACES, facelet_index

# ------------------------------------------------------------------ camera


class Camera:
    def __init__(self, width: int = 1536, height: int = 864,
                 lens_position: float | None = None, warmup_s: float = 2.0):
        from picamera2 import Picamera2  # Pi only
        self.cam = Picamera2()
        cfg = self.cam.create_still_configuration(
            main={"size": (width, height), "format": "RGB888"})
        self.cam.configure(cfg)
        self.cam.start()
        time.sleep(warmup_s)
        self._lock(lens_position)

    def _lock(self, lens_position):
        from libcamera import controls
        if lens_position is None:
            self.cam.set_controls({"AfMode": controls.AfModeEnum.Auto})
            self.cam.autofocus_cycle()
            lens_position = self.cam.capture_metadata().get("LensPosition")
        md = self.cam.capture_metadata()
        ctrl = {
            "AeEnable": False,
            "AwbEnable": False,
            "ExposureTime": md["ExposureTime"],
            "AnalogueGain": md["AnalogueGain"],
            "ColourGains": md["ColourGains"],
            "AfMode": controls.AfModeEnum.Manual,
        }
        if lens_position is not None:
            ctrl["LensPosition"] = lens_position
        self.cam.set_controls(ctrl)
        time.sleep(0.3)
        self.locked = ctrl

    def capture(self) -> np.ndarray:
        """Return a BGR image (picamera2 'RGB888' is BGR byte order)."""
        return self.cam.capture_array("main")

    def close(self):
        self.cam.stop()
        self.cam.close()


# --------------------------------------------------------- sticker sampling


def load_rois(path: str | Path) -> dict:
    data = json.loads(Path(path).read_text())
    if "points" not in data:
        raise ValueError(f"{path}: missing 'points'")
    return data


def sample_lab(img_bgr: np.ndarray, rois: dict) -> dict[str, np.ndarray]:
    """Median Lab colour of each sticker -> {facelet name: Lab}.

    rois["patches"][name] (4 corners, from the face model) is the area sampled
    when present; otherwise a square of `radius` around rois["points"][name].
    If rois has "image_size" [w, h] (the photo the positions were taken on) and
    the camera image is a different size, everything is scaled to it (same
    field of view assumed, e.g. 2000x1125 positions on a 1536x864 image)."""
    import cv2
    lab = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2LAB).astype(np.float32)
    h, w = lab.shape[:2]
    sx = sy = 1.0
    if rois.get("image_size"):
        sx, sy = w / rois["image_size"][0], h / rois["image_size"][1]
    r = max(1, int(round(rois.get("radius", 6) * (sx + sy) / 2)))
    patches = rois.get("patches", {})
    out = {}
    for name, (x, y) in rois["points"].items():
        if name in patches:
            quad = np.array(patches[name], float) * [sx, sy]
            ys, xs = _inside_quad(quad, w, h)
        else:
            x, y = int(round(x * sx)), int(round(y * sy))
            ys, xs = np.mgrid[max(0, y - r):min(h, y + r + 1), max(0, x - r):min(w, x + r + 1)]
            ys, xs = ys.ravel(), xs.ravel()
        if len(xs) == 0:
            raise ValueError(f"sticker {name} is outside the {w}x{h} image")
        patch = lab[ys, xs]
        # ignore glare: pixels where a colour channel is clipped (blown out)
        ok = np.asarray(img_bgr)[ys, xs].max(axis=1) < CLIPPED
        if ok.sum() >= max(4, patch.shape[0] // 5):
            patch = patch[ok]
        out[name] = np.median(patch, axis=0)
    return out


def _inside_quad(quad: np.ndarray, w: int, h: int):
    """Pixel rows/cols inside a convex 4-corner polygon (clipped to the image)."""
    x0, y0 = np.floor(quad.min(0)).astype(int)
    x1, y1 = np.ceil(quad.max(0)).astype(int)
    x0, y0, x1, y1 = max(x0, 0), max(y0, 0), min(x1, w - 1), min(y1, h - 1)
    if x1 < x0 or y1 < y0:
        return np.zeros(0, int), np.zeros(0, int)
    ys, xs = np.mgrid[y0:y1 + 1, x0:x1 + 1]
    ys, xs = ys.ravel(), xs.ravel()
    sign = None
    keep = np.ones(len(xs), bool)
    for i in range(4):
        ax, ay = quad[i]
        bx, by = quad[(i + 1) % 4]
        cross = (bx - ax) * (ys - ay) - (by - ay) * (xs - ax)
        if sign is None:
            sign = 1 if ((bx - ax) * (quad[(i + 2) % 4][1] - ay)
                         - (by - ay) * (quad[(i + 2) % 4][0] - ax)) >= 0 else -1
        keep &= cross * sign >= 0
    return ys[keep], xs[keep]


CLIPPED = 250   # 8-bit channel value treated as blown out by a reflection


# ------------------------------------------------------ colour classification


def collect_observations(samples_per_photo, steps) -> dict[int, list[np.ndarray]]:
    """Map photo samples back to the original scramble's facelet positions.

    samples_per_photo[k] = {facelet name: Lab} for photo k
    steps[k].perm        = cumulative permutation at photo k
    """
    obs: dict[int, list[np.ndarray]] = {}
    for samples, step in zip(samples_per_photo, steps):
        for name, lab in samples.items():
            v = facelet_index(name)
            if v in CENTRES:
                continue
            obs.setdefault(step.perm[v], []).append(lab)
    return obs


def references_from_solved(obs: dict[int, list[np.ndarray]]) -> dict[str, list[float]]:
    """With a solved cube, original position p has colour FACES[p // 9]."""
    per_face: dict[str, list[np.ndarray]] = {f: [] for f in FACES}
    for p, labs in obs.items():
        per_face[FACES[p // 9]].extend(labs)
    missing = [f for f, v in per_face.items() if not v]
    if missing:
        raise ValueError(f"no samples for faces {missing}")
    return {f: np.mean(v, axis=0).tolist() for f, v in per_face.items()}


# Standard colour scheme, loaded white on top and green at the front.
FACE_COLOUR = {"U": "white", "R": "red", "F": "green",
               "D": "yellow", "L": "orange", "B": "blue"}
# Typical sticker colours (sRGB). Only used to NAME the six colour groups found
# on a scrambled cube; the saved references are the measured colours.
NOMINAL_RGB = {"white": (255, 255, 255), "yellow": (255, 213, 0),
               "red": (196, 30, 58), "orange": (255, 88, 0),
               "green": (0, 158, 96), "blue": (0, 81, 186)}


def srgb_to_cv_lab(rgb) -> np.ndarray:
    """sRGB (0-255) -> CIELAB in OpenCV's 8-bit scale (L*2.55, a+128, b+128)."""
    c = np.asarray(rgb, dtype=float) / 255.0
    c = np.where(c > 0.04045, ((c + 0.055) / 1.055) ** 2.4, c / 12.92)
    m = np.array([[0.4124, 0.3576, 0.1805],
                  [0.2126, 0.7152, 0.0722],
                  [0.0193, 0.1192, 0.9505]])
    xyz = m @ c / np.array([0.95047, 1.0, 1.08883])
    f = np.where(xyz > 0.008856, np.cbrt(xyz), 7.787 * xyz + 16 / 116)
    L, a, b = 116 * f[1] - 16, 500 * (f[0] - f[1]), 200 * (f[1] - f[2])
    return np.array([L * 255 / 100, a + 128, b + 128])


def references_from_scrambled(obs: dict[int, list[np.ndarray]],
                              face_colour: dict[str, str] = FACE_COLOUR,
                              is_valid=None, max_iter: int = 30
                              ) -> tuple[dict[str, list[float]], str]:
    """Colour references from a cube in ANY state (no need to solve it first).

    Every cube has exactly 8 movable stickers of each colour, so the 48 sticker
    colours are split into 6 groups of 8 (equal-size k-means, Hungarian
    assignment). The groups are then named by matching them to typical sticker
    colours. `is_valid(state)` (default: Kociemba accepts it) picks the best
    naming that gives a real cube; this catches a red/orange or white/yellow
    swap. Returns (references, detected 54-letter state).
    """
    from itertools import permutations
    from scipy.optimize import linear_sum_assignment
    positions = sorted(p for p in range(54) if p not in CENTRES)
    missing = [p for p in positions if p not in obs]
    if missing:
        raise ValueError(f"{len(missing)} stickers were never seen")
    feats = np.array([np.mean(obs[p], axis=0) for p in positions])      # 48 x 3
    nominal = np.array([srgb_to_cv_lab(NOMINAL_RGB[face_colour[f]]) for f in FACES])

    # 1. split into 6 groups of exactly 8, starting from the typical colours
    centres = nominal.copy()
    group = None
    for _ in range(max_iter):
        dist = np.linalg.norm(feats[:, None, :] - centres[None, :, :], axis=2)
        rows, cols = linear_sum_assignment(np.repeat(dist, 8, axis=1))
        new = np.empty(48, dtype=int)
        new[rows] = cols // 8
        if group is not None and np.array_equal(new, group):
            break
        group = new
        centres = np.array([feats[group == k].mean(axis=0) for k in range(6)])

    # 2. name the groups: try namings from most to least likely
    name_cost = np.linalg.norm(centres[:, None, :] - nominal[None, :, :], axis=2)
    if is_valid is None:
        is_valid = _kociemba_valid
    orders = sorted(permutations(range(6)),
                    key=lambda perm: sum(name_cost[k, perm[k]] for k in range(6)))
    for perm in orders[:120]:
        state = [FACES[i // 9] for i in range(54)]
        for idx, p in enumerate(positions):
            state[p] = FACES[perm[group[idx]]]
        state = "".join(state)
        if is_valid(state):
            refs = {FACES[perm[k]]: centres[k].tolist() for k in range(6)}
            return refs, state
    raise ValueError("could not find a colour naming that gives a real cube; "
                     "check the lighting and sticker positions")


def _kociemba_valid(state: str) -> bool:
    try:
        import kociemba
    except ImportError:
        return True        # cannot check without the solver; accept best naming
    try:
        kociemba.solve(state)
        return True
    except Exception:
        return False


def classify(obs: dict[int, list[np.ndarray]], refs: dict[str, list[float]]
             ) -> tuple[str, dict[int, float]]:
    """Return (54-char state, per-position assignment cost)."""
    from scipy.optimize import linear_sum_assignment
    positions = sorted(p for p in range(54) if p not in CENTRES)
    missing = [p for p in positions if p not in obs]
    if missing:
        raise ValueError(f"{len(missing)} stickers were never seen")
    feats = np.array([np.mean(obs[p], axis=0) for p in positions])      # 48 x 3
    ref = np.array([refs[f] for f in FACES])                            # 6 x 3
    dist = np.linalg.norm(feats[:, None, :] - ref[None, :, :], axis=2)  # 48 x 6
    cost = np.repeat(dist, 8, axis=1)                                   # 48 x 48
    rows, cols = linear_sum_assignment(cost)
    state = [FACES[i // 9] for i in range(54)]  # centres keep their own face
    conf = {}
    for r, c in zip(rows, cols):
        p = positions[r]
        state[p] = FACES[c // 8]
        conf[p] = float(cost[r, c])
    return "".join(state), conf


def save_json(path: str | Path, data) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(data, indent=2))
