"""Fixed-camera face model (data/face_model.json) and sampling from it."""
import sys
import types
from pathlib import Path

import numpy as np
import pytest

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
from rubik.face_model import FaceModel  # noqa: E402
from rubik.vision import _inside_quad  # noqa: E402

MODEL = FaceModel.load(HERE / "data" / "face_model.json")
READ = ["R3", "R6", "R7", "R8", "R9", "B1", "B4", "B7", "B8", "B9",
        "D3", "D6", "D7", "D8", "D9"]


def test_corners_land_where_they_were_measured():
    import json
    data = json.loads((HERE / "data" / "face_model.json").read_text())
    seen = data["corners_image"]
    for face, (c1, c3, c9, c7) in {"R": ("R1", "T", "V", "BL"), "B": ("T", "B3", "BR", "V"),
                                   "D": ("D1", "BL", "V", "BR")}.items():
        got = MODEL.grid_to_image(face, [(0, 0), (3, 0), (3, 3), (0, 3)])
        want = np.array([seen[c] for c in (c1, c3, c9, c7)], float)
        assert np.abs(got - want).max() < 1.5


def test_shared_edges_agree_between_faces():
    # the R-B seam (T..V) is the same curve seen from both faces
    s = np.linspace(0, 3, 7)
    r_side = MODEL.grid_to_image("R", np.c_[np.full_like(s, 3), s])
    b_side = MODEL.grid_to_image("B", np.c_[np.zeros_like(s), s])
    assert np.abs(r_side - b_side).max() < 15


def test_patches_sit_inside_their_sticker():
    for n in READ:
        quad = np.array(MODEL.patch_of(n))
        cell = np.array(MODEL.patch_of(n, inset=0.0))
        cx, cy = MODEL.centre_of(n)
        assert quad.min(0)[0] <= cx <= quad.max(0)[0] and quad.min(0)[1] <= cy <= quad.max(0)[1]
        area = lambda q: 0.5 * abs(np.dot(q[:, 0], np.roll(q[:, 1], 1)) - np.dot(q[:, 1], np.roll(q[:, 0], 1)))
        assert 0.1 < area(quad) / area(cell) < 0.25          # middle 40 % per side = 16 %


def test_each_sticker_reads_its_own_colour():
    """Paint every sticker cell a different value, sample the camera-size image."""
    sys.modules["cv2"] = types.SimpleNamespace(cvtColor=lambda im, code: im, COLOR_BGR2LAB=0)
    from rubik.vision import sample_lab
    w, h = MODEL.image_size
    img = np.zeros((h, w, 3), np.uint8)
    value = {}
    for i, n in enumerate(f + str(k) for f in "RBD" for k in range(1, 10)):
        value[n] = 20 + 8 * i
        ys, xs = _inside_quad(np.array(MODEL.patch_of(n, inset=0.0)), w, h)
        img[ys, xs] = value[n]
    small = img[::2, ::2]                                     # camera at a different size
    got = sample_lab(small, MODEL.rois(READ))
    for n in READ:
        assert got[n][0] == pytest.approx(value[n]), n


def test_scale_and_offset_move_the_whole_grid():
    m = FaceModel.load(HERE / "data" / "face_model.json")
    x0, y0 = m.centre_of("D9")
    mid = np.array(m.image_size) / 2
    m.adjust(1.5, (12, -8), image_size=(1536, 864))     # offset in camera pixels
    x1, y1 = m.centre_of("D9")
    k = m.image_size[0] / 1536
    assert x1 == pytest.approx(mid[0] + 1.5 * (x0 - mid[0]) + 12 * k)
    assert y1 == pytest.approx(mid[1] + 1.5 * (y0 - mid[1]) - 8 * k)


def test_main_applies_face_scale_from_config():
    import main
    cfg = main.load_config(None)
    plain = main.load_sticker_positions(cfg)["points"]["R9"]
    cfg["scan"]["face_scale"] = 1.2
    grown = main.load_sticker_positions(cfg)["points"]["R9"]
    mid = np.array(MODEL.image_size) / 2
    assert np.allclose(np.array(grown) - mid, 1.2 * (np.array(plain) - mid))


def test_main_reads_stickers_from_the_face_model():
    import main
    cfg = main.load_config(None)
    rois = main.load_sticker_positions(cfg)
    assert rois["source"] == "face_model"
    assert sorted(rois["points"]) == sorted(cfg["scan"]["visible"]) == sorted(READ)
