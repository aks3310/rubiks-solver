"""Plan the photo sequence that lets one fixed camera see all 48 movable
stickers.

The camera sees only some facelet positions (for a corner-mounted camera:
most of U, F and R; the F and R centres are behind the motor adapters).
Between photos the robot turns faces so that hidden stickers move into view.
Because every move is a known permutation, each photo tells us the colour of
specific stickers *of the original scramble*.

Greedy set cover: at every step pick the short move sequence (0-3 moves, no
U) that reveals the most not-yet-seen stickers per unit of time.
"""
from __future__ import annotations

from dataclasses import dataclass
from itertools import product

from .cube import CENTRES, IDENTITY, compose, facelet_index, move_perm

ROBOT_MOVES = [f + s for f in "RFDLB" for s in ("", "'", "2")]
OPP = {"R": "L", "L": "R", "F": "B", "B": "F", "D": "U", "U": "D"}


@dataclass
class ScanStep:
    moves: list[str]                 # moves to make before this photo
    perm: tuple[int, ...]            # cumulative permutation at the photo
    reveals: list[int]               # original positions first seen here


def _candidate_sequences(max_len: int):
    yield []
    for n in range(1, max_len + 1):
        for seq in product(ROBOT_MOVES, repeat=n):
            ok = True
            for a, b in zip(seq, seq[1:]):
                # skip same face twice and the redundant order of opposite faces
                if a[0] == b[0] or (OPP[a[0]] == b[0] and a[0] > b[0]):
                    ok = False
                    break
            if ok:
                yield list(seq)


def two_photo_sequence(visible: list[int], max_half: int = 5) -> list[str] | None:
    """Find one move sequence that brings every sticker hidden in the first
    photo into view, so the whole cube is read in 2 photos (the minimum when
    the camera sees half the stickers). Meet-in-the-middle over sequences of
    up to 2 * max_half moves; returns the shortest found, or None."""
    import numpy as np
    vis = sorted(set(visible) - set(CENTRES))
    hidden = sorted(set(range(54)) - set(CENTRES) - set(vis))
    if len(hidden) > len(vis):
        return None
    moves = ROBOT_MOVES
    mp = np.array([move_perm(m) for m in moves], dtype=np.int16)
    is_h = np.zeros(54, bool)
    is_h[hidden] = True
    w = (np.uint64(1) << np.arange(54, dtype=np.uint64))
    perms = [np.arange(54, dtype=np.int16)[None]]
    last = [np.array([-1])]
    parent = [np.array([-1])]
    for _ in range(max_half):
        p, lst = perms[-1], last[-1]
        np_, nl, npar = [], [], []
        for mi, m in enumerate(moves):
            ok = np.ones(len(p), bool)
            for pj, pm in enumerate(moves):
                if pm[0] == m[0] or (OPP[pm[0]] == m[0] and pm[0] > m[0]):
                    ok &= lst != pj
            idx = np.nonzero(ok)[0]
            np_.append(p[idx][:, mp[mi]])
            nl.append(np.full(len(idx), mi))
            npar.append(idx)
        perms.append(np.concatenate(np_))
        last.append(np.concatenate(nl))
        parent.append(np.concatenate(npar))

    # sequence A then B works  <=>  {j : A[j] hidden} == {B[i] : i visible}
    key_a = [(is_h[p].astype(np.uint64) * w).sum(axis=1) for p in perms]
    key_b = []
    for p in perms:
        k = np.zeros(len(p), np.uint64)
        for v in vis:
            k |= w[p[:, v].astype(np.int64)]
        key_b.append(k)

    def path(d, i):
        out = []
        while d > 0:
            out.append(moves[last[d][i]])
            i = parent[d][i]
            d -= 1
        return out[::-1]

    for total in range(1, 2 * max_half + 1):
        for da in range(max(0, total - max_half), min(max_half, total) + 1):
            db = total - da
            common = np.intersect1d(key_a[da], key_b[db])
            if len(common):
                ia = int(np.nonzero(key_a[da] == common[0])[0][0])
                ib = int(np.nonzero(key_b[db] == common[0])[0][0])
                return path(da, ia) + path(db, ib)
    return None


def quarter_turns(seq: list[str]) -> int:
    """Physical 90-degree turns (a half turn is two of them on this robot)."""
    return sum(2 if m.endswith("2") else 1 for m in seq)


def _layers_by_quarter_turns(max_cost: int):
    """Every move sequence (no U, no same face twice in a row, one order for
    opposite faces) grouped by its number of 90-degree turns. Returns
    perms[c] (N x 54 cumulative permutations), and a path() to rebuild one."""
    import numpy as np
    moves = ROBOT_MOVES
    cost = [quarter_turns([m]) for m in moves]
    mp = np.array([move_perm(m) for m in moves], dtype=np.int16)
    # allowed[prev][m]: may move m follow move prev?
    allowed = np.ones((len(moves) + 1, len(moves)), bool)
    for pi, pm in enumerate(moves):
        for mi, m in enumerate(moves):
            if pm[0] == m[0] or (OPP[pm[0]] == m[0] and pm[0] > m[0]):
                allowed[pi, mi] = False
    start = len(moves)                       # "no previous move"
    perms = [np.arange(54, dtype=np.int16)[None]]
    last = [np.array([start])]
    parent = [np.array([[-1, -1]])]          # (cost layer, row) of the prefix
    for c in range(1, max_cost + 1):
        np_, nl, npar = [], [], []
        for mi, m in enumerate(moves):
            pc = c - cost[mi]
            if pc < 0:
                continue
            ok = allowed[last[pc], mi]
            idx = np.nonzero(ok)[0]
            if len(idx) == 0:
                continue
            np_.append(perms[pc][idx][:, mp[mi]])
            nl.append(np.full(len(idx), mi))
            npar.append(np.stack([np.full(len(idx), pc), idx], axis=1))
        if np_:
            perms.append(np.concatenate(np_))
            last.append(np.concatenate(nl))
            parent.append(np.concatenate(npar))
        else:
            perms.append(np.zeros((0, 54), np.int16))
            last.append(np.zeros(0, int))
            parent.append(np.zeros((0, 2), int))

    def path(c, i):
        out = []
        while c > 0:
            out.append(moves[last[c][i]])
            c, i = (int(x) for x in parent[c][i])
        return out[::-1]
    return perms, path


def min_photos_needed(visible: list[int]) -> int:
    """Lower bound on photos: turns never change a sticker's type, so the
    24 edge stickers must be read through the visible edge positions and the
    24 corner stickers through the visible corner positions."""
    import math
    vis = set(visible) - set(CENTRES)
    edges = sum(1 for v in vis if v % 9 in (1, 3, 5, 7))
    corners = sum(1 for v in vis if v % 9 in (0, 2, 6, 8))
    if edges == 0 or corners == 0:
        return 10 ** 6
    return max(math.ceil(24 / edges), math.ceil(24 / corners))


def few_photo_sequences(visible: list[int], photos: int, step_cost: int = 4,
                        beam: int = 400) -> list[list[str]] | None:
    """Look for `photos` photos (the first as loaded) that together see all 48
    movable stickers, using few 90-degree turns. Beam search: after each
    photo keep the `beam` cheapest partial plans that can still finish
    (enough unseen edge and corner stickers left for the photos remaining).
    Each gap between photos is at most `step_cost` quarter turns.
    Returns the move lists before photos 2..n, or None."""
    import numpy as np
    vis = np.array(sorted(set(visible) - set(CENTRES)))
    is_edge = np.array([i % 9 in (1, 3, 5, 7) for i in range(54)])
    movable = np.ones(54, bool)
    movable[list(CENTRES)] = False
    vis_edges = int(is_edge[vis].sum())
    vis_corners = len(vis) - vis_edges
    perms, path = _layers_by_quarter_turns(step_cost)
    seqs = [(c, i) for c in range(1, step_cost + 1) for i in range(len(perms[c]))]
    P = np.concatenate([perms[c] for c in range(1, step_cost + 1)]).astype(np.int64)
    cost = np.array([c for c, _ in seqs])

    seen0 = np.zeros(54, bool)
    seen0[vis] = True
    states = [(0, np.arange(54), seen0, [])]   # (turns, cumulative perm, seen, gaps)
    for k in range(1, photos):
        left = photos - 1 - k                   # photos still to take after this one
        children = []
        for turns, cum, seen, gaps in states:
            nxt = cum[P]                         # compose(cum, seq)
            shown = nxt[:, vis]                  # original positions in view
            new = ~seen[shown]
            new_e = (new & is_edge[shown]).sum(axis=1)
            new_c = new.sum(axis=1) - new_e
            un_e = int((~seen & movable & is_edge).sum()) - new_e
            un_c = int((~seen & movable & ~is_edge).sum()) - new_c
            ok = (un_e <= left * vis_edges) & (un_c <= left * vis_corners)
            idx = np.nonzero(ok)[0]
            gain = new.sum(axis=1)
            idx = idx[np.lexsort((cost[idx], -gain[idx]))][:beam]
            for j in idx:
                children.append((turns + int(cost[j]), -int(new[j].sum()), j, turns,
                                 cum, seen, gaps))
        if not children:
            return None
        children.sort(key=lambda t: (t[1], t[0]))   # most stickers seen, then fewest turns
        nxt_states, keys = [], set()
        for tot, _, j, turns, cum, seen, gaps in children:
            c2 = cum[P[j]]
            s2 = seen.copy()
            s2[c2[vis]] = True
            key = s2.tobytes() + c2.astype(np.int8).tobytes()
            if key in keys:
                continue
            keys.add(key)
            nxt_states.append((tot, c2, s2, gaps + [path(*seqs[j])]))
            if len(nxt_states) >= beam:
                break
        states = nxt_states
    done = [st for st in states if st[2][movable].all()]
    if not done:
        return None
    return min(done, key=lambda st: st[0])[3]


def plan_scan(visible_names: list[str], max_len: int = 3,
              photo_cost: float = 1.5, min_views: int = 1,
              min_photos: bool = True, cache_path=None) -> list[ScanStep]:
    """visible_names: facelets the camera can read, e.g. ['U1', ..., 'R9'].
    cache_path: JSON file that remembers plans, so the (up to ~15 s) search
                runs only once for each set of readable stickers.

    min_photos: try the fewest photos possible first: 2 photos when the
                camera reads at least half the stickers, else the fewest the
                readable edge/corner positions allow (min_photos_needed).
    min_views:  if > 1, use the greedy planner so every sticker appears in at
                least that many photos (more readings, more photos).
    """
    visible = [facelet_index(n) for n in visible_names]
    visible = [v for v in visible if v not in CENTRES]
    if not visible:
        raise ValueError("no visible non-centre facelets")
    if cache_path is not None:
        import json
        from pathlib import Path
        cache_path = Path(cache_path)
        key = json.dumps([sorted(visible), max_len, min_views, bool(min_photos)])
        try:
            cache = json.loads(cache_path.read_text())
        except (OSError, ValueError):
            cache = {}
        if key in cache:
            return _steps_from_gaps(visible, cache[key])
        steps = plan_scan(visible_names, max_len, photo_cost, min_views, min_photos)
        cache[key] = [s.moves for s in steps[1:]]
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(cache, indent=1))
        return steps
    if min_photos and min_views <= 1:
        seq = two_photo_sequence(visible)
        if seq is not None:
            p = _seq_perm(seq)
            first = sorted(visible)
            second = sorted({p[v] for v in visible} - set(first))
            return [ScanStep([], IDENTITY, first), ScanStep(seq, p, second)]
        # Fewest photos first, then allow longer gaps, then one more photo.
        n0 = max(3, min_photos_needed(visible))
        for photos, step_cost, beam in ((n0, 4, 400), (n0, 5, 300), (n0 + 1, 4, 400)):
            gaps = few_photo_sequences(visible, photos, step_cost, beam)
            if gaps is not None:
                return _steps_from_gaps(visible, gaps)
    need = sorted(set(range(54)) - set(CENTRES))
    views = {p: 0 for p in need}
    cum = IDENTITY
    steps: list[ScanStep] = []
    cands = [(seq, _seq_perm(seq)) for seq in _candidate_sequences(max_len)]

    first = True
    while any(c < min_views for c in views.values()):
        best = None
        for seq, p in cands:
            if first and seq:
                continue          # always take the first photo as loaded
            if not first and not seq:
                continue
            nxt = compose(cum, p)
            shown = {nxt[v] for v in visible}
            # first sightings count double: they matter most
            gain = sum(2 if views[q] == 0 else 1
                       for q in shown if views[q] < min_views)
            if gain == 0:
                continue
            score = gain / (len(seq) + photo_cost)
            if best is None or score > best[0]:
                best = (score, seq, nxt)
        if best is None:
            raise RuntimeError(
                "cannot reach some stickers with sequences of length "
                f"{max_len}; increase max_len")
        _, seq, cum = best
        shown = {cum[v] for v in visible}
        new = sorted(q for q in shown if views[q] == 0)
        for q in shown:
            views[q] += 1
        steps.append(ScanStep(moves=seq, perm=cum, reveals=new))
        first = False
    return steps


def _steps_from_gaps(visible: list[int], gaps: list[list[str]]) -> list[ScanStep]:
    """Photo 1 as loaded, then one photo after each list of moves."""
    steps = [ScanStep([], IDENTITY, sorted(visible))]
    seen = set(visible)
    cum = IDENTITY
    for g in gaps:
        cum = compose(cum, _seq_perm(g))
        shown = {cum[v] for v in visible}
        steps.append(ScanStep(list(g), cum, sorted(shown - seen)))
        seen |= shown
    return steps


def _seq_perm(seq):
    p = IDENTITY
    for m in seq:
        p = compose(p, move_perm(m))
    return p


def scan_moves(steps: list[ScanStep]) -> list[str]:
    return [m for s in steps for m in s.moves]
