"""Run with:  python -m pytest -q   (from the pi/ folder)"""
import random

import numpy as np
import pytest

from rubik.cube import (FACES, IDENTITY, ROTATIONS, SOLVED, apply, move_perm,
                        rotate_state, rotation_face_map, seq_perm)
from rubik.moves import (FLIP, fmt, invert, parallelize, parse, rewrite_without_u,
                         simplify, tokens_to_moves)
from rubik.scan_planner import plan_scan, scan_moves
from rubik.solver import known_answer_solver, solve
from rubik.vision import classify, collect_observations

ALL_MOVES = [f + s for f in FACES for s in ("", "'", "2")]
UFR = [f + str(i) for f in "UFR" for i in range(1, 10) if f == "U" or i != 5]
DFL = [f + str(i) for f in "DFL" for i in range(1, 10) if i != 5]
DBR = [f + str(i) for f in "DBR" for i in range(1, 10) if i != 5]   # the real camera


def rand_seq(n=25, rng=random):
    return [rng.choice(ALL_MOVES) for _ in range(n)]


def test_quarter_turn_has_order_four():
    for f in FACES:
        assert seq_perm([f] * 4) == IDENTITY
        assert seq_perm([f, f + "'"]) == IDENTITY


def test_known_u_move():
    # after U, the front face's top row shows the right face's colour
    assert apply(SOLVED, ["U"])[18:21] == "RRR"


def test_flip_identity():
    assert seq_perm(FLIP + FLIP) == IDENTITY
    assert seq_perm(FLIP + ["D"] + FLIP) == move_perm("U")


@pytest.mark.parametrize("seed", range(20))
def test_rewrite_preserves_effect_and_avoids_u(seed):
    rng = random.Random(seed)
    seq = parse(" ".join(rand_seq(rng=rng)))
    out = rewrite_without_u(seq)
    assert all(f != "U" for f, _ in out)
    assert seq_perm(fmt(out)) == seq_perm(fmt(seq))
    assert seq_perm(fmt(simplify(seq))) == seq_perm(fmt(seq))
    assert seq_perm(fmt(tokens_to_moves(parallelize(out)))) == seq_perm(fmt(seq))


def test_rotations_relabel_consistently():
    rng = random.Random(3)
    scr = parse(" ".join(rand_seq(rng=rng)))
    state = apply(SOLVED, fmt(scr))
    for b in ROTATIONS:
        fmap = rotation_face_map(b)
        sol = [(fmap[f], t) for f, t in invert(scr)]
        assert apply(rotate_state(state, b), fmt(sol)) == SOLVED


@pytest.mark.parametrize("visible", [UFR, DFL, DBR, ["U" + str(i) for i in range(1, 10)]])
def test_scan_plan_covers_every_sticker(visible):
    steps = plan_scan(visible)
    seen = set()
    for s in steps:
        seen |= set(s.reveals)
    assert len(seen) == 48
    assert all(m[0] != "U" for m in scan_moves(steps))


@pytest.mark.parametrize("visible", [UFR, DFL, DBR])
def test_corner_camera_needs_only_two_photos(visible):
    steps = plan_scan(visible)
    assert len(steps) == 2
    assert sorted(steps[0].reveals + steps[1].reveals) == sorted(
        p for p in range(54) if p % 9 != 4)


def test_scan_plan_reads_every_sticker_twice():
    from collections import Counter
    from rubik.cube import CENTRES, facelet_index
    steps = plan_scan(UFR, min_views=2)
    vis = [facelet_index(n) for n in UFR if facelet_index(n) not in CENTRES]
    views = Counter(p for s in steps for p in {s.perm[v] for v in vis})
    assert len(views) == 48 and min(views.values()) >= 2


# Camera at D-B-R with two opposite edge stickers per face unreadable (glare).
DBR_GLARE = [n for n in DBR if n not in ("D2", "D8", "B4", "B6", "R2", "R8")]


def test_min_photos_lower_bound():
    from rubik.cube import facelet_index
    from rubik.scan_planner import min_photos_needed
    assert min_photos_needed([facelet_index(n) for n in DBR]) == 2
    assert min_photos_needed([facelet_index(n) for n in DBR_GLARE]) == 4


def test_unreadable_edges_scan_in_four_photos_and_decode(tmp_path):
    from rubik.cube import facelet_index
    cache = tmp_path / "plans.json"
    steps = plan_scan(DBR_GLARE, cache_path=cache)
    assert len(steps) == 4
    assert sorted(p for s in steps for p in s.reveals) == [p for p in range(54) if p % 9 != 4]
    assert all(m[0] != "U" for m in scan_moves(steps))
    again = plan_scan(DBR_GLARE, cache_path=cache)          # from the cache
    assert [s.perm for s in again] == [s.perm for s in steps]
    # the original scramble is recovered from readable stickers only
    rng = np.random.default_rng(1)
    refs = {"U": [235, 128, 128], "R": [120, 185, 165], "F": [150, 85, 170],
            "D": [220, 115, 205], "L": [170, 160, 190], "B": [90, 140, 70]}
    for k in range(3):
        original = apply(SOLVED, rand_seq(rng=random.Random(100 + k)))
        samples, done = [], []
        for st in steps:
            done += st.moves
            cur = apply(original, done)
            samples.append({n: np.array(refs[cur[facelet_index(n)]]) + rng.normal(0, 6, 3)
                            for n in DBR_GLARE})
        state, _ = classify(collect_observations(samples, steps), refs)
        assert state == original


def test_full_pipeline_with_noisy_colours():
    rng = np.random.default_rng(0)
    refs = {"U": [235, 128, 128], "R": [120, 185, 165], "F": [150, 85, 170],
            "D": [220, 115, 205], "L": [170, 160, 190], "B": [90, 140, 70]}
    steps = plan_scan(UFR)
    smoves = scan_moves(steps)
    for k in range(5):
        scramble = rand_seq(rng=random.Random(k))
        original = apply(SOLVED, scramble)
        samples, done = [], []
        for st in steps:
            done += st.moves
            cur = apply(original, done)
            from rubik.cube import facelet_index
            samples.append({n: np.array(refs[cur[facelet_index(n)]]) + rng.normal(0, 6, 3)
                            for n in UFR})
        state, _ = classify(collect_observations(samples, steps), refs)
        assert state == original
        current = apply(original, smoves)
        fn = known_answer_solver(current, fmt(invert(parse(" ".join(scramble + smoves)))))
        plan = solve(current, solver_fn=fn)
        assert apply(current, fmt(tokens_to_moves(plan.tokens))) == SOLVED
        assert all("U" not in t for t in plan.tokens)


def _realistic_obs(original, steps, visible, rng, light=(0.85, 0.8, 0.7), noise=4.0):
    """Sticker Lab readings for a cube whose colours differ from the nominal
    ones (bright stickerless plastic, cyan-ish blue) under warm, dim light."""
    from rubik.cube import facelet_index
    from rubik.vision import srgb_to_cv_lab
    real_rgb = {"U": (235, 235, 228), "R": (215, 25, 55), "F": (70, 205, 70),
                "D": (245, 225, 40), "L": (255, 120, 20), "B": (20, 150, 210)}
    lab = {f: srgb_to_cv_lab(np.array(c) * np.array(light)) for f, c in real_rgb.items()}
    samples, done = [], []
    for st in steps:
        done += st.moves
        cur = apply(original, done)
        samples.append({n: lab[cur[facelet_index(n)]] + rng.normal(0, noise, 3)
                        for n in visible})
    return collect_observations(samples, steps)


@pytest.mark.parametrize("seed", range(10))
def test_colour_calibration_from_a_scrambled_cube(seed):
    from rubik.vision import references_from_scrambled
    DBR = [f + str(i) for f in "DBR" for i in range(1, 10) if i != 5]
    rng = np.random.default_rng(seed)
    original = apply(SOLVED, rand_seq(rng=random.Random(seed)))
    steps = plan_scan(DBR)
    obs = _realistic_obs(original, steps, DBR, rng)
    # best naming by colour alone (no solver available to check validity)
    refs, state = references_from_scrambled(obs, is_valid=lambda s: True)
    assert state == original
    # the references then classify a different scramble correctly
    other = apply(SOLVED, rand_seq(rng=random.Random(seed + 100)))
    state2, _ = classify(_realistic_obs(other, steps, DBR, rng), refs)
    assert state2 == other


def test_scrambled_calibration_uses_validity_check():
    """If the most likely naming is not a real cube, the next valid one wins."""
    from rubik.vision import references_from_scrambled
    DBR = [f + str(i) for f in "DBR" for i in range(1, 10) if i != 5]
    rng = np.random.default_rng(42)
    original = apply(SOLVED, rand_seq(rng=random.Random(42)))
    steps = plan_scan(DBR)
    obs = _realistic_obs(original, steps, DBR, rng)
    seen = []

    def only_truth(s):
        seen.append(s)
        return s == original and len(seen) > 1   # reject the first naming
    try:
        references_from_scrambled(obs, is_valid=only_truth)
    except ValueError:
        pass   # acceptable: the true naming was rejected on purpose
    assert len(seen) >= 2


def test_expand_half_turns_keeps_effect_and_removes_half_turns():
    from rubik.moves import expand_half_turns
    assert expand_half_turns(["F2"]) == ["F", "F"]
    assert expand_half_turns(["F2+B2"]) == ["F+B", "F+B"]
    assert expand_half_turns(["F2+B'", "R"]) == ["F+B'", "F", "R"]
    rng = random.Random(9)
    for _ in range(100):
        seq = parse(" ".join(rand_seq(rng=rng)))
        toks = parallelize(rewrite_without_u(seq))
        out = expand_half_turns(toks)
        assert not any("2" in t for t in out)
        assert seq_perm(fmt(tokens_to_moves(out))) == seq_perm(fmt(seq))


def test_solver_plan_has_only_quarter_turns():
    rng = random.Random(5)
    scramble = rand_seq(rng=rng)
    state = apply(SOLVED, scramble)
    fn = known_answer_solver(state, fmt(invert(parse(" ".join(scramble)))))
    plan = solve(state, solver_fn=fn, quarter_turns_only=True)
    assert plan.tokens and not any("2" in t for t in plan.tokens)
    assert apply(state, fmt(tokens_to_moves(plan.tokens))) == SOLVED
