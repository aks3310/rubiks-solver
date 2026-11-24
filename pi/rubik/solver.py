"""Solve a facelet string and turn the answer into robot motor steps.

Kociemba's two-phase algorithm gives ~20 moves but may use U. We try the
cube in all 24 whole-cube orientations, rewrite each answer without U
(moves.rewrite_without_u), and keep the one with the fewest motor steps.
"""
from __future__ import annotations

from dataclasses import dataclass

from .cube import ROTATIONS, SOLVED, apply, is_valid_counts, rotate_state, rotation_face_map
from .moves import (expand_half_turns, fmt, parallelize, parse, rewrite_without_u,
                    tokens_to_moves)


@dataclass
class Plan:
    solver_moves: list[str]   # what Kociemba returned (best orientation, mapped back)
    robot_moves: list[str]    # same effect, no U, simplified
    tokens: list[str]         # robot steps; 'R+L'' means both at once


def _kociemba(state: str) -> str:
    import kociemba  # imported lazily so the rest works without it
    return kociemba.solve(state)


def step_cost(tokens: list[str]) -> float:
    """Rough time cost: with the default speed profile a half turn takes
    ~1.6x a quarter turn (estimate; update after `main.py bench`)."""
    cost = 0.0
    for t in tokens:
        cost += 1.6 if "2" in t else 1.0
    return cost


def known_answer_solver(state: str, solution: list[str]):
    """Stand-in for Kociemba in tests/simulation when the C library is not
    installed: answers only for `state` (in any orientation)."""
    sol = parse(" ".join(solution))
    table = {}
    for basis in ROTATIONS:
        fmap = rotation_face_map(basis)
        table[rotate_state(state, basis)] = " ".join(fmt([(fmap[f], t) for f, t in sol]))

    def _solve(s: str) -> str:
        return table[s]
    return _solve


def solve(state: str, try_orientations: bool = True, parallel: bool = True,
          solver_fn=None, time_budget_s: float | None = None,
          quarter_turns_only: bool = False) -> Plan:
    """time_budget_s: stop trying further orientations once this much time has
    passed (the first orientation is always solved).
    quarter_turns_only: every 180-degree turn becomes two 90-degree steps, and
    the fastest orientation is chosen with that in mind."""
    import time
    t0 = time.perf_counter()
    if not is_valid_counts(state):
        raise ValueError("each colour must appear exactly 9 times")
    if state == SOLVED:
        return Plan([], [], [])
    solver_fn = solver_fn or _kociemba
    best: Plan | None = None
    rots = ROTATIONS if try_orientations else ROTATIONS[:1]
    for basis in rots:
        if best is not None and time_budget_s is not None \
                and time.perf_counter() - t0 > time_budget_s:
            break
        fmap = rotation_face_map(basis)
        back = {v: k for k, v in fmap.items()}
        sol = solver_fn(rotate_state(state, basis))
        moves = [(back[f], t) for f, t in parse(sol)]
        robot = rewrite_without_u(moves)
        tokens = parallelize(robot) if parallel else fmt(robot)
        if quarter_turns_only:
            tokens = expand_half_turns(tokens)
        plan = Plan(fmt(moves), fmt(robot), tokens)
        if best is None or step_cost(plan.tokens) < step_cost(best.tokens):
            best = plan
    assert best is not None
    # final safety check on the model before anything moves
    if apply(state, fmt(tokens_to_moves(best.tokens))) != SOLVED:
        raise AssertionError("internal error: plan does not solve the cube")
    return best
