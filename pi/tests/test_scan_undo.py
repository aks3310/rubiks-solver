"""The scan must always leave the cube in the scramble it was loaded with,
both after a full scan and when the scan stops part-way."""
import random
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.modules.setdefault("cv2", types.SimpleNamespace(imwrite=lambda *a, **k: True))

import main  # noqa: E402
import rubik.vision  # noqa: E402
from rubik.cube import FACES, SOLVED, apply  # noqa: E402
from rubik.moves import tokens_to_moves, fmt  # noqa: E402
from rubik.scan_planner import plan_scan, scan_moves  # noqa: E402

DBR_GLARE = [f + str(i) for f in "DBR" for i in (1, 2, 3, 4, 6, 7, 8, 9)
             if f + str(i) not in ("D2", "D8", "B4", "B6", "R2", "R8")]
CFG = {"camera": {"settle_s": 0}, "motion": {"parallel_opposite": True}}


class FakeRobot:
    """Keeps the cube state; can fail on the n-th run() call."""
    quarter_turns_only = True

    def __init__(self, state, fail_on=None):
        self.state, self.calls, self.fail_on = state, 0, fail_on

    def run(self, tokens):
        self.calls += 1
        if self.fail_on is not None and self.calls == self.fail_on:
            raise RuntimeError("motor jam")
        self.state = apply(self.state, fmt(tokens_to_moves(tokens)))
        return 0


class FakeCamera:
    def __init__(self):
        self.photos = 0

    def capture(self):
        self.photos += 1
        return None


@pytest.fixture(scope="module")
def steps():
    return plan_scan(DBR_GLARE)          # 4 photos; searched once for this file


@pytest.fixture(autouse=True)
def no_real_vision(monkeypatch):
    monkeypatch.setattr(rubik.vision, "sample_lab", lambda img, rois: {})


def _scramble(seed):
    rng = random.Random(seed)
    return apply(SOLVED, [rng.choice(FACES) + rng.choice(["", "'", "2"]) for _ in range(25)])


@pytest.mark.parametrize("delay,confirm", [(0.0, False), (0.0001, False)])
def test_full_scan_returns_cube_to_scramble(steps, delay, confirm):
    assert scan_moves(steps)
    original = _scramble(1)
    robot, cam = FakeRobot(original), FakeCamera()
    main.run_scan(CFG, robot, cam, {}, steps, None, turn_delay_s=delay, confirm=confirm)
    assert cam.photos == len(steps)
    assert robot.state == original


@pytest.mark.parametrize("fail_on", [2, 3])
def test_scan_stopped_part_way_still_returns_cube(steps, fail_on):
    original = _scramble(2)
    robot = FakeRobot(original, fail_on=fail_on)
    with pytest.raises(RuntimeError):
        main.run_scan(CFG, robot, FakeCamera(), {}, steps, None)
    assert robot.state == original       # the moves made before the jam were undone
