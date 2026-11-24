"""Move-list utilities: parsing, simplification, the no-U rewrite and
parallel opposite-face turns.

The robot has motors on F, R, B, L and D only. The U face is free (the camera
looks at it), so every U turn in a solver's answer must be rewritten.

Key identity (checked at import time and by the unit tests):

    Y = R L' F2 B2 R L'      is its own inverse (Y Y = identity)
    Y D Y = U                and   Y U Y = D

So a run of U/D turns  U^a D^b  is executed as  D^b  Y D^a Y.  Y is three
parallel steps on the robot (R+L', F2+B2, R+L'), so one U group costs about
seven motor steps. The solver (solver.py) also tries all 24 whole-cube
orientations and keeps the solution that needs the fewest motor steps.
"""
from __future__ import annotations

from .cube import FACES, move_perm, seq_perm

OPPOSITE = {"U": "D", "D": "U", "R": "L", "L": "R", "F": "B", "B": "F"}
FLIP = ["R", "L'", "F2", "B2", "R", "L'"]   # "Y" in the notes above


# ---------------------------------------------------------------- parsing
def parse(text: str) -> list[tuple[str, int]]:
    """'R U2 F'' -> [('R',1), ('U',2), ('F',3)]"""
    out = []
    for tok in text.replace(",", " ").split():
        face, suf = tok[0].upper(), tok[1:]
        if face not in FACES:
            raise ValueError(f"bad move {tok!r}")
        turns = {"": 1, "'": 3, "2": 2, "2'": 2, "3": 3}.get(suf)
        if turns is None:
            raise ValueError(f"bad move {tok!r}")
        out.append((face, turns))
    return out


def fmt_move(m: tuple[str, int]) -> str:
    face, t = m
    return face + {1: "", 2: "2", 3: "'"}[t]


def fmt(moves) -> list[str]:
    return [fmt_move(m) for m in moves]


def invert(moves: list[tuple[str, int]]) -> list[tuple[str, int]]:
    return [(f, (4 - t) % 4) for f, t in reversed(moves)]


# ---------------------------------------------------------- simplification
def simplify(moves: list[tuple[str, int]]) -> list[tuple[str, int]]:
    """Merge same-face turns, also across a commuting opposite-face turn."""
    out: list[tuple[str, int]] = []
    for face, t in moves:
        t %= 4
        if t == 0:
            continue
        if out and out[-1][0] == face:
            nt = (out[-1][1] + t) % 4
            out.pop()
            if nt:
                out.append((face, nt))
            continue
        if (len(out) >= 2 and out[-1][0] == OPPOSITE[face]
                and out[-2][0] == face):
            nt = (out[-2][1] + t) % 4
            other = out.pop()
            out.pop()
            if nt:
                out.append((face, nt))
            out.append(other)
            continue
        out.append((face, t))
    return out


# ------------------------------------------------------ the no-U rewrite
def _check_flip() -> None:
    if seq_perm(FLIP + FLIP) != seq_perm([]):
        raise AssertionError("Y is not its own inverse")
    if seq_perm(FLIP + ["D"] + FLIP) != move_perm("U"):
        raise AssertionError("Y D Y != U")
    if seq_perm(FLIP + ["U"] + FLIP) != move_perm("D"):
        raise AssertionError("Y U Y != D")


_check_flip()


def rewrite_without_u(moves: list[tuple[str, int]]) -> list[tuple[str, int]]:
    """Return a sequence with the same effect that never turns U."""
    flip = parse(" ".join(FLIP))
    out: list[tuple[str, int]] = []
    i = 0
    while i < len(moves):
        if moves[i][0] not in ("U", "D"):
            out.append(moves[i])
            i += 1
            continue
        # collect a run of U/D turns (they commute)
        u = d = 0
        while i < len(moves) and moves[i][0] in ("U", "D"):
            if moves[i][0] == "U":
                u += moves[i][1]
            else:
                d += moves[i][1]
            i += 1
        u %= 4
        d %= 4
        if d:
            out.append(("D", d))
        if u:
            out += flip + [("D", u)] + flip
    return simplify(out)


# ------------------------------------------------- parallel opposite turns
def parallelize(moves: list[tuple[str, int]]) -> list[str]:
    """Join neighbouring opposite-face turns into one 'A+B' token that the
    firmware runs at the same time (opposite faces commute)."""
    tokens: list[str] = []
    i = 0
    while i < len(moves):
        if i + 1 < len(moves) and moves[i + 1][0] == OPPOSITE[moves[i][0]]:
            tokens.append(fmt_move(moves[i]) + "+" + fmt_move(moves[i + 1]))
            i += 2
        else:
            tokens.append(fmt_move(moves[i]))
            i += 1
    return tokens


def tokens_to_moves(tokens: list[str]) -> list[tuple[str, int]]:
    return parse(" ".join(t.replace("+", " ") for t in tokens))


# --------------------------------------------------- quarter turns only
def expand_half_turns(tokens: list[str]) -> list[str]:
    """Replace every 180-degree turn with two 90-degree turns, so the robot
    only ever turns a face by 90 degrees at a time:

        F2      -> F, F
        F2+B2   -> F+B, F+B
        F2+B'   -> F+B', F       (the quarter-turn face moves once)
    """
    out: list[str] = []
    for tok in tokens:
        parts = tok.split("+")
        if not any(p.endswith("2") for p in parts):
            out.append(tok)
            continue
        first = [p[0] if p.endswith("2") else p for p in parts]
        second = [p[0] for p in parts if p.endswith("2")]
        out.append("+".join(first))
        out.append("+".join(second))
    return out
