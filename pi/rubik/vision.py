"""Camera capture, sticker sampling and colour classification.

* Camera: Raspberry Pi Camera Module 3 Wide through picamera2. Exposure,
  white balance and focus are locked after warm-up so colours do not drift
  between the photos of one scan. ISO, shutter speed and white balance can be
  fixed in config.toml [camera] so they are also the same in every run.
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

from .cube import CENTRES, FACES, STANDARD_SCHEME, facelet_index

# ------------------------------------------------------------------ camera


def parse_shutter_us(value) -> int:
    """Shutter speed -> microseconds: "1/250" -> 4000; a number is microseconds."""
    from fractions import Fraction
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        us = float(value)
    else:
        text = str(value).strip().lower().replace(" ", "").removesuffix("s").removesuffix("u")
        try:
            us = float(Fraction(text)) * 1_000_000 if "/" in text else float(text)
        except (ValueError, ZeroDivisionError):
            raise ValueError(f"shutter = {value!r}: use for example \"1/250\" or 4000 (microseconds)")
    if not 10 <= us <= 1_000_000:
        raise ValueError(f"shutter = {value!r}: between 1/100000 and 1 s")
    return int(round(us))


def camera_settings(cam_cfg: dict) -> dict:
    """Fixed exposure settings from config.toml [camera] (anything left out
    stays automatic): iso (100 = analogue gain 1.0), shutter ("1/250" or
    microseconds) and white_balance ([red gain, blue gain])."""
    out = {}
    if cam_cfg.get("iso") is not None:
        iso = float(cam_cfg["iso"])
        if not 50 <= iso <= 3200:
            raise ValueError(f"iso = {cam_cfg['iso']}: use 100-1600 (Camera Module 3)")
        out["gain"] = iso / 100.0
    if cam_cfg.get("shutter") is not None:
        out["shutter_us"] = parse_shutter_us(cam_cfg["shutter"])
    if cam_cfg.get("white_balance") is not None:
        wb = cam_cfg["white_balance"]
        if not (isinstance(wb, (list, tuple)) and len(wb) == 2
                and all(isinstance(g, (int, float)) and 0.1 <= g <= 8 for g in wb)):
            raise ValueError(f"white_balance = {wb!r}: use [red gain, blue gain], e.g. [1.9, 1.6]")
        out["colour_gains"] = (float(wb[0]), float(wb[1]))
    return out


def describe_camera(s: dict) -> str:
    """'ISO 200, 1/250 s, white balance 1.90/1.60' from locked camera settings."""
    us = s["ExposureTime"]
    r, b = s["ColourGains"]
    return (f"ISO {s['AnalogueGain'] * 100:.0f}, 1/{round(1_000_000 / us)} s ({us} us), "
            f"white balance {r:.2f}/{b:.2f}")


def camera_changes(saved: dict, now: dict, tol: float = 0.08) -> list[str]:
    """What differs (by more than tol) between two locked camera settings."""
    out = []
    if abs(now["ExposureTime"] - saved["ExposureTime"]) > tol * saved["ExposureTime"]:
        out.append(f"shutter {saved['ExposureTime']} -> {now['ExposureTime']} us")
    if abs(now["AnalogueGain"] - saved["AnalogueGain"]) > tol * saved["AnalogueGain"]:
        out.append(f"ISO {saved['AnalogueGain'] * 100:.0f} -> {now['AnalogueGain'] * 100:.0f}")
    (r0, b0), (r1, b1) = saved["ColourGains"], now["ColourGains"]
    if abs(r1 - r0) > tol * r0 or abs(b1 - b0) > tol * b0:
        out.append(f"white balance {r0:.2f}/{b0:.2f} -> {r1:.2f}/{b1:.2f}")
    return out


class Camera:
    def __init__(self, width: int = 1536, height: int = 864,
                 lens_position: float | None = None, warmup_s: float = 2.0,
                 full_fov: bool = False, gain: float | None = None,
                 shutter_us: int | None = None,
                 colour_gains: tuple[float, float] | None = None):
        """full_fov: force the full-sensor 2x2-binned mode (Camera Module 3:
        2304x1296). Otherwise libcamera picks the mode from the output size,
        and for 1536x864 that can be a cropped (zoomed-in) mode.
        gain / shutter_us / colour_gains: fixed analogue gain (ISO / 100),
        exposure time and white balance; None = the camera's automatic choice
        after warm-up (see camera_settings)."""
        from picamera2 import Picamera2  # Pi only
        self.fixed = {"AnalogueGain": gain, "ExposureTime": shutter_us,
                      "ColourGains": tuple(colour_gains) if colour_gains else None}
        self.cam = Picamera2()
        kwargs = {"main": {"size": (width, height), "format": "RGB888"}}
        if full_fov:
            kwargs["sensor"] = {"output_size": (2304, 1296)}
        try:
            cfg = self.cam.create_still_configuration(**kwargs)
        except TypeError:            # older picamera2 without sensor=
            kwargs.pop("sensor", None)
            kwargs["raw"] = {"size": (2304, 1296)}
            cfg = self.cam.create_still_configuration(**kwargs)
        self.cam.configure(cfg)
        self.sensor_mode = cfg.get("sensor") or cfg.get("raw")
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
        self.auto_choice = {k: md[k] for k in ("ExposureTime", "AnalogueGain", "ColourGains")}
        ctrl = {
            "AeEnable": False,
            "AwbEnable": False,
            "ExposureTime": int(self.fixed["ExposureTime"] or md["ExposureTime"]),
            "AnalogueGain": float(self.fixed["AnalogueGain"] or md["AnalogueGain"]),
            "ColourGains": tuple(self.fixed["ColourGains"] or md["ColourGains"]),
            "AfMode": controls.AfModeEnum.Manual,
        }
        if self.fixed["ExposureTime"]:          # allow exposures longer than one frame
            frame = max(33_333, ctrl["ExposureTime"] + 2_000)
            ctrl["FrameDurationLimits"] = (frame, frame)
        if lens_position is not None:
            ctrl["LensPosition"] = lens_position
        self.cam.set_controls(ctrl)
        time.sleep(0.3)
        # wait until frames really use the new settings (a few frames at most)
        for _ in range(15):
            md = self.cam.capture_metadata()
            if not camera_changes(ctrl, {k: md.get(k, ctrl[k]) for k in
                                         ("ExposureTime", "AnalogueGain", "ColourGains")}, 0.06):
                break
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


# Colour on each face, loaded white on top and green at the front (the default;
# [cube] top / front in config.toml changes it, see cube.face_colours).
FACE_COLOUR = dict(STANDARD_SCHEME)
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


def save_references(path: str | Path, refs: dict[str, list[float]],
                    face_colour: dict[str, str], camera: dict | None = None) -> None:
    """Save one reference colour per face, plus which colour each face had
    when calibrated (so a later change of loading orientation can be undone)
    and the camera settings used (to warn when a later run differs)."""
    data = {**{f: list(refs[f]) for f in FACES}, "_face_colours": face_colour}
    if camera:
        data["_camera"] = {"ExposureTime": int(camera["ExposureTime"]),
                           "AnalogueGain": float(camera["AnalogueGain"]),
                           "ColourGains": [float(g) for g in camera["ColourGains"]]}
    save_json(path, data)


def calibration_camera(path: str | Path) -> dict | None:
    """The camera settings recorded by calibrate-colours, or None."""
    return json.loads(Path(path).read_text()).get("_camera")


def load_references(path: str | Path, face_colour: dict[str, str]
                    ) -> tuple[dict[str, list[float]], str | None]:
    """Reference colours keyed by face (U R F D L B) for the cube loaded as
    `face_colour`. If they were calibrated with another orientation, they are
    remapped by colour (the stickers look the same whichever face they are on).
    Files without "_face_colours" were calibrated white up, green front.
    Returns (references, note about a remap or None)."""
    data = json.loads(Path(path).read_text())
    refs = {f: data[f] for f in FACES}
    saved = data.get("_face_colours", FACE_COLOUR)
    if saved == face_colour:
        return refs, None
    by_colour = {saved[f]: refs[f] for f in FACES}
    note = (f"colour calibration was made with {saved['U']} up, {saved['F']} front; "
            f"using it for {face_colour['U']} up, {face_colour['F']} front")
    return {f: by_colour[face_colour[f]] for f in FACES}, note


def save_json(path: str | Path, data) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(data, indent=2))
