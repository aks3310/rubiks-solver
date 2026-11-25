"""calibrate-colours writes its camera settings into config.toml [camera]."""
import sys
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from rubik.config_edit import set_keys, set_keys_in_file  # noqa: E402
from rubik.vision import camera_settings  # noqa: E402

REAL = (Path(__file__).resolve().parent.parent / "config.toml").read_text()
CAM = {"iso": 130, "shutter": 7900, "white_balance": [2.05, 1.45]}


def without_camera_exposure(cfg: dict) -> dict:
    cam = {k: v for k, v in cfg["camera"].items() if k not in CAM}
    return {**cfg, "camera": cam}


def test_real_config_gets_the_values_and_nothing_else_changes():
    new = set_keys(REAL, "camera", CAM, note="set by calibrate-colours")
    before, after = tomllib.loads(REAL), tomllib.loads(new)
    assert {k: after["camera"][k] for k in CAM} == CAM
    assert without_camera_exposure(after) == without_camera_exposure(before)
    assert camera_settings(after["camera"]) == {"gain": 1.3, "shutter_us": 7900,
                                                "colour_gains": (2.05, 1.45)}
    # the commented examples were replaced, other comments kept
    assert "# iso = 200" not in new and "# lens_position = 6.5" in new
    assert new.count("set by calibrate-colours") == 3


def test_running_again_replaces_the_values():
    once = set_keys(REAL, "camera", CAM)
    twice = set_keys(once, "camera", {"iso": 200, "shutter": 4000, "white_balance": [1.9, 1.6]})
    cam = tomllib.loads(twice)["camera"]
    assert (cam["iso"], cam["shutter"], cam["white_balance"]) == (200, 4000, [1.9, 1.6])
    assert twice.count("\niso = ") == 1 and twice.count("\nshutter = ") == 1


def test_missing_keys_are_added_inside_the_section():
    text = "[camera]\nwidth = 1536\n\n[motion]\nspeed = 1\n"
    new = set_keys(text, "camera", CAM)
    cfg = tomllib.loads(new)
    assert cfg["camera"]["iso"] == 130 and cfg["motion"] == {"speed": 1}
    assert new.index("iso = 130") < new.index("[motion]")


def test_missing_section_is_added():
    cfg = tomllib.loads(set_keys("[serial]\nport = \"x\"\n", "camera", CAM))
    assert cfg["camera"] == CAM and cfg["serial"] == {"port": "x"}


def test_windows_line_endings_are_kept(tmp_path):
    path = tmp_path / "config.toml"
    path.write_bytes("[camera]\r\nwidth = 1\r\n# iso = 200\r\n".encode())
    set_keys_in_file(path, "camera", CAM)
    raw = path.read_bytes().decode()
    assert "\r\n" in raw and "\n" not in raw.replace("\r\n", "")
    assert tomllib.loads(raw)["camera"]["iso"] == 130
    assert not (tmp_path / "config.toml.tmp").exists()


def test_main_saves_what_the_calibration_used(tmp_path):
    import main
    path = tmp_path / "config.toml"
    path.write_text(REAL)
    cfg = main.load_config(str(path))
    main.save_camera_to_config(cfg, {"ExposureTime": 7900, "AnalogueGain": 1.3,
                                     "ColourGains": (2.0512, 1.4488)})
    cam = main.load_config(str(path))["camera"]
    assert (cam["iso"], cam["shutter"], cam["white_balance"]) == (130, 7900, [2.0512, 1.4488])


def test_calibration_lets_the_camera_choose_unless_switched_off():
    import main
    assert main.calibration_picks_camera({}) is True
    assert main.calibration_picks_camera({"calibration": {"auto_camera": True}}) is True
    assert main.calibration_picks_camera({"calibration": {"auto_camera": False}}) is False
