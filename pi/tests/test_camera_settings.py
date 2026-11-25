"""config.toml [camera] iso / shutter / white_balance and the calibration check."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from rubik.cube import FACES, face_colours  # noqa: E402
from rubik.vision import (calibration_camera, camera_changes, camera_settings,  # noqa: E402
                          describe_camera, load_references, parse_shutter_us,
                          save_references)


@pytest.mark.parametrize("value,us", [("1/250", 4000), ("1/1000", 1000), (4000, 4000),
                                      ("8000", 8000), ("1/30s", 33333), (" 1 / 60 ", 16667)])
def test_shutter_values(value, us):
    assert parse_shutter_us(value) == us


@pytest.mark.parametrize("value", ["fast", "1/0", 0, "2"])
def test_bad_shutter_values(value):
    with pytest.raises(ValueError):
        parse_shutter_us(value)


def test_nothing_set_means_automatic():
    assert camera_settings({"width": 1536}) == {}


def test_all_three_fixed():
    s = camera_settings({"iso": 200, "shutter": "1/250", "white_balance": [1.9, 1.6]})
    assert s == {"gain": 2.0, "shutter_us": 4000, "colour_gains": (1.9, 1.6)}


@pytest.mark.parametrize("cfg", [{"iso": 10}, {"iso": 99999}, {"white_balance": [1.9]},
                                 {"white_balance": "warm"}, {"white_balance": [0, 1.6]}])
def test_bad_camera_settings(cfg):
    with pytest.raises(ValueError):
        camera_settings(cfg)


CAL = {"ExposureTime": 4000, "AnalogueGain": 2.0, "ColourGains": (1.9, 1.6)}


def test_describe_and_compare():
    assert describe_camera(CAL) == "ISO 200, 1/250 s (4000 us), white balance 1.90/1.60"
    assert camera_changes(CAL, dict(CAL)) == []
    assert camera_changes(CAL, {**CAL, "ExposureTime": 4100}) == []         # within 8 %
    warm = camera_changes(CAL, {**CAL, "ColourGains": (2.2, 1.4)})
    assert len(warm) == 1 and "white balance 1.90/1.60 -> 2.20/1.40" in warm[0]
    assert len(camera_changes(CAL, {"ExposureTime": 8000, "AnalogueGain": 4.0,
                                    "ColourGains": (1.9, 1.6)})) == 2


def test_calibration_records_camera_and_still_loads(tmp_path):
    path = tmp_path / "colours.json"
    refs = {f: [float(i), 1.0, 2.0] for i, f in enumerate(FACES)}
    save_references(path, refs, face_colours(), camera=CAL)
    assert calibration_camera(path) == {"ExposureTime": 4000, "AnalogueGain": 2.0,
                                        "ColourGains": [1.9, 1.6]}
    loaded, note = load_references(path, face_colours())
    assert loaded == refs and note is None


def test_old_calibration_has_no_camera(tmp_path):
    path = tmp_path / "colours.json"
    path.write_text(json.dumps({f: [1.0, 2.0, 3.0] for f in FACES}))
    assert calibration_camera(path) is None
