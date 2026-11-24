"""Facelet-level Rubik's cube model.

Facelet order follows Kociemba's convention so a state string can be handed
straight to the solver:

    U1..U9, R1..R9, F1..F9, D1..D9, L1..L9, B1..B9   (54 facelets)

Each face is read row by row as you look straight at it, with:
    U: B edge at the top        D: F edge at the top
    R, F, L, B: U edge at the top

Move permutations are generated from 3D geometry (not hand-typed tables), so
they are consistent by construction. Coordinates: x -> R, y -> U, z -> F.
"""
from __future__ import annotations

from functools import lru_cache

FACES = "URFDLB"
SOLVED = "".join(f * 9 for f in FACES)

# face: (normal, right-vector, down-vector) as seen looking at that face
_FACE_FRAME = {
    "U": ((0, 1, 0), (1, 0, 0), (0, 0, 1)),
    "R": ((1, 0, 0), (0, 0, -1), (0, -1, 0)),
    "F": ((0, 0, 1), (1, 0, 0), (0, -1, 0)),
    "D": ((0, -1, 0), (1, 0, 0), (0, 0, -1)),
    "L": ((-1, 0, 0), (0, 0, 1), (0, -1, 0)),
    "B": ((0, 0, -1), (-1, 0, 0), (0, -1, 0)),
}


def _add(*vs):
    return tuple(sum(c) for c in zip(*vs))


def _scale(k, v):
    return tuple(k * c for c in v)


def _dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _facelets():
    """List of (position, normal) for the 54 facelets in Kociemba order."""
    out = []
    for f in FACES:
        n, r, d = _FACE_FRAME[f]
        for row in range(3):
            for col in range(3):
                pos = _add(n, _scale(col - 1, r), _scale(row - 1, d))
                out.append((pos, n))
    return out


_FACELETS = _facelets()
_INDEX = {fl: i for i, fl in enumerate(_FACELETS)}


def _rot_cw(axis, v):
    """Rotate v by 90 degrees clockwise as seen looking at the face along -axis."""
    # Rodrigues with theta = -90 deg: v' = -(a x v) + a (a . v)
    return _add(_scale(-1, _cross(axis, v)), _scale(_dot(axis, v), axis))


@lru_cache(maxsize=None)
def _quarter_perm(face: str) -> tuple[int, ...]:
    """perm[i] = index of the facelet whose sticker ends up at position i."""
    axis = _FACE_FRAME[face][0]
    perm = list(range(54))
    for src, (pos, nrm) in enumerate(_FACELETS):
        if _dot(pos, axis) == 1:
            dst = _INDEX[(_rot_cw(axis, pos), _rot_cw(axis, nrm))]
            perm[dst] = src
    return tuple(perm)


def compose(p: tuple[int, ...], q: tuple[int, ...]) -> tuple[int, ...]:
    """Permutation for 'apply p, then q' (same convention as _quarter_perm)."""
    return tuple(p[q[i]] for i in range(54))


IDENTITY = tuple(range(54))


@lru_cache(maxsize=None)
def move_perm(move: str) -> tuple[int, ...]:
    """Permutation for a single move such as R, R', R2 (also accepts R3)."""
    face = move[0]
    if face not in FACES:
        raise ValueError(f"bad move {move!r}")
    suffix = move[1:]
    turns = {"": 1, "'": 3, "2": 2, "3": 3}.get(suffix)
    if turns is None:
        raise ValueError(f"bad move {move!r}")
    p = IDENTITY
    for _ in range(turns):
        p = compose(p, _quarter_perm(face))
    return p


def seq_perm(moves) -> tuple[int, ...]:
    if isinstance(moves, str):
        moves = moves.split()
    p = IDENTITY
    for m in moves:
        p = compose(p, move_perm(m))
    return p


def apply(state: str, moves) -> str:
    """Apply a move sequence to a 54-char facelet string."""
    p = seq_perm(moves)
    return "".join(state[p[i]] for i in range(54))


def facelet_name(i: int) -> str:
    return f"{FACES[i // 9]}{i % 9 + 1}"


def facelet_index(name: str) -> int:
    return FACES.index(name[0]) * 9 + int(name[1:]) - 1


CENTRES = tuple(f * 9 + 4 for f in range(6))

_FACE_WORD = {"U": "top", "D": "bottom", "F": "front", "B": "back", "L": "left", "R": "right"}


def describe(name: str) -> str:
    """Human hint for a facelet, e.g. 'U1: top face, corner touching L and B'."""
    pos, nrm = _FACELETS[facelet_index(name)]
    others = [_NORMAL_TO_FACE[tuple(c if k == ax else 0 for k, c in enumerate(pos))]
              for ax in range(3) if pos[ax] != 0 and nrm[ax] == 0]
    kind = {0: "centre", 1: "edge", 2: "corner"}[len(others)]
    tail = f", touching {' and '.join(others)}" if others else ""
    return f"{name}: {_FACE_WORD[name[0]]} face, {kind}{tail}"


# Sticker colour of each face when the cube is loaded white up, green front.
COLOUR_LETTER = {"U": "W", "R": "R", "F": "G", "D": "Y", "L": "O", "B": "B"}
_ANSI = {"U": "97", "R": "91", "F": "92", "D": "93", "L": "38;5;208", "B": "94"}


def net(state: str, colours: bool = False, ansi: bool = False) -> str:
    """Text drawing of a state as an unfolded cube (U on top, then L F R B, then D).

    colours: show sticker colours (W white, R red, G green, Y yellow, O orange,
             B blue) instead of face letters, with the faces labelled.
    ansi:    also colour each letter for a terminal."""
    def cell(x: str) -> str:
        if not colours:
            return x
        c = COLOUR_LETTER[x]
        return f"\033[1;{_ANSI[x]}m{c}\033[0m" if ansi else c

    f = {face: [cell(x) for x in state[i * 9:(i + 1) * 9]] for i, face in enumerate(FACES)}
    rows = []
    if colours:
        rows.append("       top")
    for r in range(3):
        rows.append("       " + " ".join(f["U"][r * 3:r * 3 + 3]))
    for r in range(3):
        rows.append("  ".join(" ".join(f[x][r * 3:r * 3 + 3]) for x in "LFRB"))
    for r in range(3):
        rows.append("       " + " ".join(f["D"][r * 3:r * 3 + 3]))
    if colours:
        rows.append("       bottom")
        rows.insert(4, "left   front  right  back")
        rows.insert(4, "")
        rows.append("(W white, R red, G green, Y yellow, O orange, B blue; "
                    "held white up, green front)")
    return "\n".join(rows)


# ------------------------------------------------ whole-cube orientations
_NORMAL_TO_FACE = {_FACE_FRAME[f][0]: f for f in FACES}


def _quarter_about(axis):
    return lambda v: _rot_cw(axis, v)


def _all_rotations():
    """The 24 whole-cube rotations, each as a function on 3D vectors,
    built by closing the set under quarter turns about x and y."""
    gens = [_quarter_about((1, 0, 0)), _quarter_about((0, 1, 0))]
    basis = ((1, 0, 0), (0, 1, 0), (0, 0, 1))
    seen = {basis: None}
    frontier = [basis]
    while frontier:
        nxt = []
        for b in frontier:
            for g in gens:
                nb = tuple(g(v) for v in b)
                if nb not in seen:
                    seen[nb] = None
                    nxt.append(nb)
        frontier = nxt
    return list(seen)  # each item: images of the x, y, z unit vectors


ROTATIONS = _all_rotations()
assert len(ROTATIONS) == 24


def _apply_basis(basis, v):
    return _add(_scale(v[0], basis[0]), _scale(v[1], basis[1]), _scale(v[2], basis[2]))


def rotation_face_map(basis) -> dict[str, str]:
    """Face f moves to face face_map[f] under this rotation."""
    return {f: _NORMAL_TO_FACE[_apply_basis(basis, _FACE_FRAME[f][0])] for f in FACES}


def rotate_state(state: str, basis) -> str:
    """Re-describe the same physical cube as if it were rotated by `basis`.
    Colours are relabelled so the result is a valid solver input."""
    fmap = rotation_face_map(basis)
    out = [""] * 54
    for i, (pos, nrm) in enumerate(_FACELETS):
        j = _INDEX[(_apply_basis(basis, pos), _apply_basis(basis, nrm))]
        out[j] = fmap[state[i]]
    return "".join(out)


def is_valid_counts(state: str) -> bool:
    return len(state) == 54 and all(state.count(f) == 9 for f in FACES)
