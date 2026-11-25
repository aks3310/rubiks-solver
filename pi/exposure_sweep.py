#!/usr/bin/env python3
"""ISO, shutter-speed and white-balance sweep: one photo per combination,
with the face outlines and sticker sample boxes drawn on it (as in
`main.py show-faces`) and the settings written on top.

    python exposure_sweep.py                       # 5 ISO x 7 shutter x 3 white balances
    python exposure_sweep.py --iso 100 200 400 --shutter 1/500 1/250 1/125
    python exposure_sweep.py --wb auto 1.9,1.6 2.2,1.4    # white balances to try
    python exposure_sweep.py --wb auto             # white balance not swept

Load the cube and switch the light on first. Close anything else using the
camera (rpicam-hello, the dashboard while it reads, main.py).

White balances (--wb), any number of them:
    auto      what the camera chose by itself at the start
    warm      auto with red x1.15 and blue / 1.15 (more orange)
    cool      auto with red / 1.15 and blue x1.15 (more blue)
    R,B       fixed red and blue gains, e.g. 1.9,1.6
Default: auto warm cool.

Saved in logs/sweep-<date-time>/:
    iso0400_1-250s_wb-auto.jpg ...  every photo, outlines drawn, settings on top
    auto.jpg                 the camera's own automatic choice, for comparison
    contact_wb-<name>.jpg    one grid per white balance (rows ISO, columns shutter)
    summary.csv              settings asked for and actually used, and for the
                             sampled sticker areas: mean brightness, % blown out
                             (glare) and % very dark

ISO is the camera's analogue gain x 100 (ISO 100 = gain 1.0). The white
balance gains are the camera's red and blue colour gains (green is 1.0).
"""
from __future__ import annotations

import argparse
import csv
import time
from fractions import Fraction
from pathlib import Path

import numpy as np

import main as robot_main
from rubik.vision import CLIPPED

DEFAULT_ISO = [100, 200, 400, 800, 1600]
DEFAULT_SHUTTER = ["1/2000", "1/1000", "1/500", "1/250", "1/125", "1/60", "1/30"]
DEFAULT_WB = ["auto", "warm", "cool"]
WB_STEP = 1.15


def parse_wb(tokens: list[str], auto_gains) -> list[tuple[str, tuple[float, float]]]:
    """--wb tokens -> [(name, (red gain, blue gain))]. Also accepts the old
    form `--wb 1.9 1.6` (one red and one blue gain)."""
    r0, b0 = float(auto_gains[0]), float(auto_gains[1])
    if len(tokens) == 2 and all(_is_number(t) for t in tokens):
        tokens = [f"{tokens[0]},{tokens[1]}"]
    out = []
    for t in tokens:
        t = t.strip().lower()
        if t == "auto":
            out.append(("auto", (r0, b0)))
        elif t == "warm":
            out.append(("warm", (r0 * WB_STEP, b0 / WB_STEP)))
        elif t == "cool":
            out.append(("cool", (r0 / WB_STEP, b0 * WB_STEP)))
        else:
            parts = t.split(",")
            if len(parts) != 2 or not all(_is_number(x) for x in parts):
                raise SystemExit(f"--wb {t!r}: use auto, warm, cool or RED,BLUE such as 1.9,1.6")
            r, b = float(parts[0]), float(parts[1])
            if not (0.1 <= r <= 8 and 0.1 <= b <= 8):
                raise SystemExit(f"--wb {t}: gains must be between 0.1 and 8")
            out.append((f"{r:.2f}-{b:.2f}", (r, b)))
    return out


def _is_number(t: str) -> bool:
    try:
        float(t)
        return True
    except ValueError:
        return False


def draw_outlines(cfg, img) -> None:
    """main.draw_face_outlines, or the same drawing here for an older main.py."""
    fn = getattr(robot_main, "draw_face_outlines", None)
    if fn is not None:
        fn(cfg, img)
        return
    import cv2
    h, w = img.shape[:2]
    model = robot_main.load_face_model(cfg)
    sx, sy = w / model.image_size[0], h / model.image_size[1]
    colour = {"R": (60, 60, 255), "B": (255, 140, 60), "D": (0, 220, 255)}   # BGR
    for face in "RBD":
        for k, line in enumerate(model.grid_lines(face)):
            pts = np.round(line * [sx, sy]).astype(np.int32)
            cv2.polylines(img, [pts], False, colour[face], 3 if k == 0 else 1, cv2.LINE_AA)
    rois = model.rois(cfg["scan"]["visible"], cfg["scan"].get("patch_inset", 0.3))
    for name, quad in rois.get("patches", {}).items():
        pts = np.round(np.array(quad) * [sx, sy]).astype(np.int32)
        cv2.polylines(img, [pts], True, (255, 255, 255), 2, cv2.LINE_AA)
        x, y = np.array(rois["points"][name]) * [sx, sy]
        cv2.putText(img, name, (int(x) - 12, int(y) + 5), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                    (255, 255, 255), 1, cv2.LINE_AA)


def shutter_us(text: str) -> int:
    """'1/250' -> 4000 microseconds; a plain number is microseconds."""
    text = text.strip().lower().removesuffix("s").removesuffix("u")
    if "/" in text:
        return max(1, round(float(Fraction(text)) * 1_000_000))
    return max(1, round(float(text)))


def shutter_label(us: int) -> str:
    if us >= 1_000_000:
        return f"{us / 1e6:g} s"
    return f"1/{round(1_000_000 / us)} s"


def patch_mask(cfg, rois: dict, w: int, h: int) -> np.ndarray:
    """True where stickers are sampled (the white boxes), at this image size."""
    import cv2
    mask = np.zeros((h, w), np.uint8)
    sx = sy = 1.0
    if rois.get("image_size"):
        sx, sy = w / rois["image_size"][0], h / rois["image_size"][1]
    patches = rois.get("patches", {})
    r = max(1, int(round(rois.get("radius", 6) * (sx + sy) / 2)))
    for name, (x, y) in rois["points"].items():
        if name in patches:
            pts = np.round(np.array(patches[name], float) * [sx, sy]).astype(np.int32)
            cv2.fillPoly(mask, [pts], 1)
        else:
            x, y = int(round(x * sx)), int(round(y * sy))
            mask[max(0, y - r):y + r + 1, max(0, x - r):x + r + 1] = 1
    return mask.astype(bool)


def patch_stats(img: np.ndarray, mask: np.ndarray) -> dict:
    import cv2
    px = img[mask]                                      # N x 3 BGR
    lab_l = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)[..., 0][mask]
    top = px.max(axis=1)
    return {"mean_L": float(lab_l.mean()) if len(lab_l) else 0.0,
            "clipped_pct": float((top >= CLIPPED).mean() * 100) if len(top) else 0.0,
            "dark_pct": float((top < 20).mean() * 100) if len(top) else 0.0}


def label(img: np.ndarray, lines: list[str]) -> None:
    import cv2
    h = 30 * len(lines) + 12
    img[:h] = (img[:h] * 0.35).astype(img.dtype)
    for i, text in enumerate(lines):
        y = 30 + 30 * i
        cv2.putText(img, text, (12, y), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2, cv2.LINE_AA)


def capture_at(cam, us: int, gain: float, colour_gains, frames: int = 15):
    """Set exposure and gain, wait until a frame really uses them, return
    (BGR image, metadata, reached)."""
    picam = cam.cam
    frame_us = max(33_333, us + 2_000)
    picam.set_controls({"AeEnable": False, "AwbEnable": False, "ExposureTime": us,
                        "AnalogueGain": gain, "ColourGains": colour_gains,
                        "FrameDurationLimits": (frame_us, frame_us)})
    img = md = None
    for _ in range(frames):
        req = picam.capture_request()
        try:
            md = req.get_metadata()
            img = req.make_array("main")
        finally:
            req.release()
        got_us, got_gain = md.get("ExposureTime", 0), md.get("AnalogueGain", 0)
        got_wb = md.get("ColourGains", colour_gains)
        wb_ok = all(abs(g - c) <= 0.05 * c for g, c in zip(got_wb, colour_gains))
        if abs(got_us - us) <= max(0.06 * us, 30) and abs(got_gain - gain) <= 0.08 * gain and wb_ok:
            return img, md, True
    return img, md, False          # the sensor clamped it; keep the last frame


def contact_sheet(rows: list[list[tuple[np.ndarray, str]]], width: int = 400) -> np.ndarray:
    import cv2
    tiles = []
    for row in rows:
        cells = []
        for img, text in row:
            h = int(img.shape[0] * width / img.shape[1])
            t = cv2.resize(img, (width, h), interpolation=cv2.INTER_AREA)
            cv2.rectangle(t, (0, 0), (width, 26), (0, 0, 0), -1)
            cv2.putText(t, text, (6, 19), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1, cv2.LINE_AA)
            cells.append(cv2.copyMakeBorder(t, 2, 2, 2, 2, cv2.BORDER_CONSTANT, value=(40, 40, 40)))
        tiles.append(np.hstack(cells))
    return np.vstack(tiles)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--iso", type=int, nargs="+", default=DEFAULT_ISO,
                    help="ISO values (100 = analogue gain 1.0; Camera Module 3 goes to about 1600)")
    ap.add_argument("--shutter", nargs="+", default=DEFAULT_SHUTTER,
                    help="shutter speeds, e.g. 1/500 1/250, or microseconds, e.g. 4000")
    ap.add_argument("--wb", nargs="+", default=DEFAULT_WB,
                    help="white balances: auto, warm, cool, or RED,BLUE gains such as 1.9,1.6 "
                         "(default: auto warm cool)")
    ap.add_argument("--config", help="config.toml (default: the one next to main.py)")
    ap.add_argument("--out", help="output folder (default: logs/sweep-<date-time>)")
    args = ap.parse_args()

    import cv2
    cfg = robot_main.load_config(args.config)
    shutters = [shutter_us(s) for s in args.shutter]
    out = Path(args.out) if args.out else robot_main.HERE / "logs" / time.strftime("sweep-%Y%m%d-%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    rois = robot_main.load_sticker_positions(cfg)

    print("starting the camera (2 s to settle the automatic exposure and white balance)...")
    try:                                      # automatic, even if [camera] fixes them
        cam = robot_main.open_camera(cfg, fixed=False)
    except TypeError:                         # older main.py: always automatic
        cam = robot_main.open_camera(cfg)
    rows, sheets = [], {}
    try:
        auto = cam.locked                         # what the camera chose by itself
        balances = parse_wb(args.wb, auto["ColourGains"])
        img = cam.capture()
        h, w = img.shape[:2]
        mask = patch_mask(cfg, rois, w, h)
        st = patch_stats(img, mask)
        draw_outlines(cfg, img)
        ar, ab = auto["ColourGains"]
        label(img, [f"AUTO: ISO {auto['AnalogueGain'] * 100:.0f}  {shutter_label(auto['ExposureTime'])}"
                    f" ({auto['ExposureTime']} us)   WB red {ar:.2f} blue {ab:.2f}",
                    f"stickers: mean L {st['mean_L']:.0f}   blown out {st['clipped_pct']:.1f}%"
                    f"   dark {st['dark_pct']:.1f}%"])
        cv2.imwrite(str(out / "auto.jpg"), img)
        rows.append({"file": "auto.jpg", "wb": "auto", "wb_red": round(ar, 3), "wb_blue": round(ab, 3),
                     "iso": round(auto["AnalogueGain"] * 100),
                     "shutter": shutter_label(auto["ExposureTime"]),
                     "exposure_us_asked": auto["ExposureTime"], "exposure_us_used": auto["ExposureTime"],
                     "gain_asked": round(auto["AnalogueGain"], 3), "gain_used": round(auto["AnalogueGain"], 3),
                     "reached": True, **{k: round(v, 2) for k, v in st.items()}})
        print(f"auto: ISO {auto['AnalogueGain'] * 100:.0f}, {shutter_label(auto['ExposureTime'])}, "
              f"white balance red {ar:.2f} blue {ab:.2f}")
        print("white balances: " + ", ".join(f"{n} ({g[0]:.2f}/{g[1]:.2f})" for n, g in balances))

        total, n = len(balances) * len(args.iso) * len(shutters), 0
        for wb_name, wb_gains in balances:
            sheet = sheets.setdefault(wb_name, [])
            for iso in args.iso:
                gain = iso / 100.0
                sheet_row = []
                for us in shutters:
                    n += 1
                    img, md, reached = capture_at(cam, us, gain, wb_gains)
                    st = patch_stats(img, mask)
                    draw_outlines(cfg, img)
                    used_us, used_gain = md.get("ExposureTime", 0), md.get("AnalogueGain", 0)
                    ur, ub = md.get("ColourGains", wb_gains)
                    name = (f"iso{iso:04d}_{shutter_label(us).replace('/', '-').replace(' ', '')}"
                            f"_wb-{wb_name}.jpg")
                    label(img, [f"ISO {iso}  {shutter_label(us)}   WB {wb_name}: red {wb_gains[0]:.2f}"
                                f" blue {wb_gains[1]:.2f}" + ("" if reached else "  NOT REACHED"),
                                f"used: {used_us} us, gain {used_gain:.2f}, WB {ur:.2f}/{ub:.2f}",
                                f"stickers: mean L {st['mean_L']:.0f}   blown out {st['clipped_pct']:.1f}%"
                                f"   dark {st['dark_pct']:.1f}%"])
                    cv2.imwrite(str(out / name), img)
                    sheet_row.append((img, f"ISO {iso} {shutter_label(us)} WB {wb_name}  "
                                           f"L{st['mean_L']:.0f} glare {st['clipped_pct']:.0f}%"))
                    rows.append({"file": name, "wb": wb_name, "wb_red": round(wb_gains[0], 3),
                                 "wb_blue": round(wb_gains[1], 3), "iso": iso,
                                 "shutter": shutter_label(us),
                                 "exposure_us_asked": us, "exposure_us_used": used_us,
                                 "gain_asked": gain, "gain_used": round(used_gain, 3), "reached": reached,
                                 **{k: round(v, 2) for k, v in st.items()}})
                    print(f"[{n}/{total}] {name}: L {st['mean_L']:.0f}, blown out "
                          f"{st['clipped_pct']:.1f}%, dark {st['dark_pct']:.1f}%"
                          + ("" if reached else "  (setting not reached)"))
                sheet.append(sheet_row)
    finally:
        cam.close()

    with open(out / "summary.csv", "w", newline="") as fh:
        wr = csv.DictWriter(fh, fieldnames=list(rows[0]))
        wr.writeheader()
        wr.writerows(rows)
    for wb_name, sheet in sheets.items():
        cv2.imwrite(str(out / f"contact_wb-{wb_name}.jpg"), contact_sheet(sheet))
    print(f"\nsaved {len(rows)} photos, {len(sheets)} contact sheet(s) and summary.csv in {out}")
    for wb_name in sheets:
        good = [r for r in rows[1:] if r["wb"] == wb_name
                and r["clipped_pct"] < 1 and 90 <= r["mean_L"] <= 200]
        if good:
            best = min(good, key=lambda r: (r["iso"], abs(r["mean_L"] - 150)))
            print(f"WB {wb_name}: lowest ISO with under 1% glare, sticker brightness closest to "
                  f"L 150: ISO {best['iso']}, {best['shutter']} ({best['file']})")
        else:
            print(f"WB {wb_name}: no setting had under 1% glare with sticker brightness L 90-200")
    print("Compare the colours in the contact sheets: red and orange should look clearly different.")


if __name__ == "__main__":
    main()
