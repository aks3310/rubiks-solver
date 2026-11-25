"""The robot's sticker positions (data/rois.json, from the TA6 coordinate
guide) and how they are sampled."""
import json
import sys
import types
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from rubik.cube import CENTRES, facelet_index  # noqa: E402
from rubik.scan_planner import plan_scan  # noqa: E402

ROIS = json.loads((Path(__file__).resolve().parent.parent / "data" / "rois.json").read_text())
FAR_EDGES = {"R2", "R4", "B2", "B6", "D2", "D4"}       # glare: never read
FAR_CORNERS = {"R1", "B3", "D1"}                        # behind the motor rods


def test_far_edges_and_hidden_corners_are_not_read():
    pts = set(ROIS["points"])
    assert len(pts) == 15
    assert not pts & FAR_EDGES and not pts & FAR_CORNERS
    assert not any(n.endswith("5") for n in pts)          # centres
    assert set(k for k in ROIS["unreadable"] if not k.startswith("_")) == FAR_EDGES


def test_readable_stickers_scan_in_four_photos(tmp_path):
    steps = plan_scan(list(ROIS["points"]), cache_path=tmp_path / "plans.json")
    assert len(steps) == 4
    seen = set()
    for s in steps:
        seen |= {s.perm[facelet_index(n)] for n in ROIS["points"]}
    assert seen == set(range(54)) - set(CENTRES)


def _sample(img, rois):
    """sample_lab with a stand-in cv2 (Lab = the pixel values); the real cv2,
    if any, is put back afterwards so later tests are not affected."""
    saved = sys.modules.get("cv2")
    sys.modules["cv2"] = types.SimpleNamespace(cvtColor=lambda im, code: im, COLOR_BGR2LAB=0)
    try:
        from rubik.vision import sample_lab
        return sample_lab(img, rois)
    finally:
        if saved is None:
            del sys.modules["cv2"]
        else:
            sys.modules["cv2"] = saved


def test_points_scale_to_the_camera_resolution():
    h, w = 864, 1536
    yy, xx = np.mgrid[0:h, 0:w]
    img = np.stack([xx, yy, np.zeros_like(xx)], axis=2).astype(np.float32)
    rois = {"radius": 6, "image_size": [1280, 720], "points": {"R9": [640, 360]}}
    x, y, _ = _sample(img, rois)["R9"]
    assert (round(x), round(y)) == (768, 432)


def test_glare_pixels_are_ignored():
    img = np.full((50, 50, 3), 120, np.uint8)
    img[20:30, 20:24] = 255                         # a reflection in part of the patch
    rois = {"radius": 6, "points": {"D9": [25, 25]}}
    assert list(_sample(img, rois)["D9"]) == [120, 120, 120]
