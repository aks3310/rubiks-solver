"""Per-scan colour grouping (equal-size k-means started from the calibration)."""
import random
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from rubik.cube import FACES, SOLVED, apply, facelet_index  # noqa: E402
from rubik.scan_planner import plan_scan  # noqa: E402
from rubik.vision import (classify, classify_adaptive, collect_observations,  # noqa: E402
                          references_from_solved, srgb_to_cv_lab)

DBR = [f + str(i) for f in "DBR" for i in range(1, 10) if i != 5]
REAL = {"U": (235, 235, 228), "R": (215, 25, 55), "F": (70, 205, 70),
        "D": (245, 225, 40), "L": (255, 120, 20), "B": (20, 150, 210)}
CAL_LIGHT = (0.85, 0.8, 0.7)
STEPS = plan_scan(DBR)


def observe(cube, light, noise, rng):
    lab = {f: srgb_to_cv_lab(np.clip(np.array(c) * np.array(light), 0, 255)) for f, c in REAL.items()}
    samples, done = [], []
    for st in STEPS:
        done += st.moves
        cur = apply(cube, done)
        samples.append({n: lab[cur[facelet_index(n)]] + rng.normal(0, noise, 3) for n in DBR})
    return collect_observations(samples, STEPS)


def scramble(seed):
    r = random.Random(seed)
    return apply(SOLVED, [r.choice("URFDLB") + r.choice(["", "'", "2"]) for _ in range(25)])


def calibrated(rng):
    return references_from_solved(observe(SOLVED, CAL_LIGHT, 3, rng))


def accept_all(state):
    return True


def test_same_light_reads_every_cube_with_small_drift():
    rng = np.random.default_rng(0)
    refs = calibrated(rng)
    for seed in range(10):
        cube = scramble(seed)
        state, conf, info = classify_adaptive(observe(cube, CAL_LIGHT, 5, rng), refs, is_valid=accept_all)
        assert state == cube and info["method"] == "adaptive"
        assert set(info["drift"]) == set(FACES) and max(info["drift"].values()) < 10
        assert len(conf) == 48


def test_changed_light_fewer_misread_stickers_than_fixed_references():
    rng = np.random.default_rng(0)
    refs = calibrated(rng)
    fixed_wrong = adaptive_wrong = 0
    for seed in range(40):
        cube = scramble(seed)
        obs = observe(cube, (1.05, 0.72, 0.5), 9, rng)        # much warmer light, noisy
        fixed, _ = classify(obs, refs)
        state, _, info = classify_adaptive(obs, refs, is_valid=accept_all)
        fixed_wrong += sum(a != b for a, b in zip(fixed, cube))
        adaptive_wrong += sum(a != b for a, b in zip(state, cube))
    assert adaptive_wrong < fixed_wrong
    assert max(info["drift"].values()) > 25                  # the change is reported


def test_impossible_naming_is_renamed():
    rng = np.random.default_rng(1)
    refs = calibrated(rng)
    cube = scramble(5)
    obs = observe(cube, CAL_LIGHT, 5, rng)
    from rubik.cube import CENTRES
    swap = {"R": "L", "L": "R"}                                # R and L groups swapped,
    target = "".join(c if i in CENTRES else swap.get(c, c)     # centres keep their face
                     for i, c in enumerate(cube))
    state, _, info = classify_adaptive(obs, refs, is_valid=lambda s: s == target)
    assert state == target and info["method"] == "adaptive, renamed"


def test_falls_back_to_fixed_references_when_grouping_is_not_a_cube():
    rng = np.random.default_rng(2)
    refs = calibrated(rng)
    cube = scramble(7)
    obs = observe(cube, CAL_LIGHT, 5, rng)
    fixed, _ = classify(obs, refs)
    calls = []

    def second_call_only(state):          # rejects the adaptive result, accepts the next
        calls.append(state)
        return len(calls) == 2

    state, _, info = classify_adaptive(obs, refs, is_valid=second_call_only)
    assert info["method"] == "fixed" and state == fixed
