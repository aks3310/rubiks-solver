"""Laptop dashboard host that forwards solver calls to the Raspberry Pi.

The robot has no motor on the top (U, white) face, so every solution sent to
the browser is rewritten without U turns (U = Y D Y, see pi/rubik/moves.py).

If the Pi cannot be reached the dashboard keeps working on the laptop:
  * /solve and /validate use the bundled pure-Python Kociemba solver
    (raspberry_pi/vendor/pure_python or server/vendor/pure_python) when present;
  * /state keeps the last uploaded cube state in memory.
"""
from __future__ import annotations

import json
import os
import sys
import threading
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from fastapi import FastAPI, HTTPException, Request as FastAPIRequest
from fastapi.responses import FileResponse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pi"))
from rubik.moves import fmt, parse, rewrite_without_u, simplify  # noqa: E402

app = FastAPI(title="TA6 Cube Dashboard", version="0.3.0")
PI_SERVER_URL = os.environ.get("PI_SERVER_URL", "http://raspberrypi.local:8011").rstrip("/")
FACE_ORDER = "URFDLB"
ROBOT_FACES = "FRBLD"            # faces with a motor; U (white, top) is fixed

_local_state: str | None = None
_state_lock = threading.Lock()


# ------------------------------------------------------------------ the Pi
class PiUnavailable(Exception):
    pass


def pi_request(path: str, method: str = "GET", payload: dict | None = None,
               timeout: float = 120) -> dict:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = Request(
        f"{PI_SERVER_URL}{path}",
        data=data,
        method=method,
        headers={"Content-Type": "application/json"} if data is not None else {},
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        try:
            detail = json.loads(detail).get("detail", detail)
        except (ValueError, AttributeError):
            pass
        raise HTTPException(exc.code, str(detail)) from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise PiUnavailable(f"Cannot reach the Raspberry Pi solver at {PI_SERVER_URL}: {exc}") from exc


def forward(path: str, method: str = "GET", payload: dict | None = None) -> dict:
    # Pi claims the first laptop proxy to contact /health, then accepts only that source IP.
    if path != "/health":
        pi_request("/health", timeout=3)
    return pi_request(path, method, payload)


# ------------------------------------------------------- laptop fallbacks
_local_solver = None


def local_solver():
    """The bundled pure-Python Kociemba solver, or None if it is not here."""
    global _local_solver
    if _local_solver is None:
        for folder in (ROOT / "raspberry_pi" / "vendor" / "pure_python",
                       ROOT / "server" / "vendor" / "pure_python"):
            if folder.is_dir():
                sys.path.insert(0, str(folder))
                try:
                    import kociemba  # type: ignore
                    _local_solver = kociemba
                    break
                except ImportError:
                    sys.path.remove(str(folder))
        else:
            try:
                import kociemba  # type: ignore
                _local_solver = kociemba
            except ImportError:
                _local_solver = False
    return _local_solver or None


def check_state(facelets) -> str:
    if not isinstance(facelets, str):
        raise HTTPException(422, "facelets must be a string")
    state = facelets.upper().replace(" ", "")
    if len(state) != 54 or any(letter not in FACE_ORDER for letter in state):
        raise HTTPException(422, "facelets must be 54 letters from URFDLB")
    counts = {face: state.count(face) for face in FACE_ORDER}
    if any(count != 9 for count in counts.values()):
        raise HTTPException(422, f"each face letter must occur 9 times; got {counts}")
    return state


def solve_locally(state: str) -> list[str]:
    solver = local_solver()
    if solver is None:
        raise HTTPException(
            503, "The Raspberry Pi is offline and this laptop has no Kociemba solver. "
                 "Start ./run_solver.sh on the Pi, or put the bundled solver in "
                 "raspberry_pi/vendor/pure_python.")
    try:
        notation = solver.solve(state)
    except Exception as exc:
        raise HTTPException(422, f"Impossible or invalid cube state: {exc}") from exc
    return notation.split() if notation else []


def robot_moves(solver_moves: list[str]) -> list[str]:
    """Same effect, but only F R B L D turns (no top-face motor)."""
    out = fmt(simplify(rewrite_without_u(parse(" ".join(solver_moves)))))
    assert all(m[0] in ROBOT_FACES for m in out)
    return out


# ------------------------------------------------------------------ routes
@app.get("/")
def dashboard() -> FileResponse:
    return FileResponse(Path(__file__).parent / "web" / "index.html")


@app.get("/health")
def health() -> dict:
    local = "bundled Kociemba" if local_solver() else None
    try:
        pi = pi_request("/health", timeout=3)
    except (PiUnavailable, HTTPException) as exc:
        detail = exc.detail if isinstance(exc, HTTPException) else str(exc)
        return {"status": "unavailable", "connected": False, "error": detail,
                "local_solver": local}
    return {"status": "ok", "connected": True, "pi_solver": pi, "local_solver": local}


@app.get("/state")
def get_state() -> dict:
    try:
        data = forward("/state")
        return {**data, "source": "pi"}
    except PiUnavailable:
        with _state_lock:
            state = _local_state
        if state is None:
            raise HTTPException(404, "The Raspberry Pi is offline and no state has been "
                                     "uploaded on this laptop yet.")
        return {"facelets": state, "source": "laptop"}


@app.post("/state")
async def set_state(request: FastAPIRequest) -> dict:
    global _local_state
    state = check_state((await request.json()).get("facelets"))
    try:
        data = forward("/state", "POST", {"facelets": state})
        return {**data, "source": "pi"}
    except PiUnavailable:
        with _state_lock:
            _local_state = state
        return {"saved": True, "facelets": state, "source": "laptop"}


@app.post("/solve")
async def solve(request: FastAPIRequest) -> dict:
    state = check_state((await request.json()).get("facelets"))
    try:
        data = forward("/solve", "POST", {"facelets": state})
        solver_moves, source = data.get("moves", []), "pi"
    except PiUnavailable:
        solver_moves, source = solve_locally(state), "laptop"
    moves = robot_moves(solver_moves)
    return {"facelets": state, "moves": moves, "move_count": len(moves),
            "solver_moves": solver_moves, "source": source}


@app.post("/validate")
async def validate(request: FastAPIRequest) -> dict:
    state = check_state((await request.json()).get("facelets"))
    try:
        return {**forward("/validate", "POST", {"facelets": state}), "source": "pi"}
    except PiUnavailable:
        solve_locally(state)
        return {"valid": True, "facelets": state, "source": "laptop",
                "message": "Cube state is physically solvable"}
