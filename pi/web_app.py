#!/usr/bin/env python3
"""Robot dashboard, hosted on the Raspberry Pi.

    python web_app.py                 # http://<pi-ip>:8080 from any browser on the network
    python web_app.py --fake-robot    # no hardware: a pretend robot (for testing the page)

Every button runs main.py, so the dashboard does exactly what the command
line does (same config.toml, speed, overshoot, scan plan and solver):

    Scramble  ->  python main.py --json move "<moves>"
    Read cube ->  python main.py --json read
    Solve     ->  python main.py --json solve --state <last read state>
                  (or a full scan first if the cube has not been read since
                  the last scramble)

Only one command runs at a time (they share the USB link and the camera).
While this server runs, do not use main.py by hand at the same time.
Python standard library only.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import threading
import time
import tomllib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

HERE = Path(__file__).resolve().parent
PAGE = HERE / "web" / "index.html"
SOLVED = "U" * 9 + "R" * 9 + "F" * 9 + "D" * 9 + "L" * 9 + "B" * 9
MOVE_RE = re.compile(r"^[FRBLD](2|')?$")      # no U: the top face has no motor
TIMEOUT_S = {"move": 120, "read": 300, "solve": 400}


# --------------------------------------------------------------- runners
class MainRunner:
    """Runs main.py and returns its RESULT line plus the printed output."""

    def __init__(self, config: str | None = None):
        self.config = config

    def run(self, args: list[str]) -> tuple[dict, str]:
        cmd = [sys.executable, str(HERE / "main.py"), "--json"]
        if self.config:
            cmd += ["--config", self.config]
        cmd += args
        try:
            proc = subprocess.run(cmd, cwd=HERE, capture_output=True, text=True,
                                  timeout=TIMEOUT_S.get(args[0], 300))
        except subprocess.TimeoutExpired as e:
            out = (e.stdout or "") if isinstance(e.stdout, str) else ""
            return {"ok": False, "error": f"main.py {args[0]} took too long and was stopped"}, out
        log = proc.stdout + (("\n" + proc.stderr) if proc.stderr.strip() else "")
        return parse_result(proc.stdout, proc.returncode, log), log


def parse_result(stdout: str, returncode: int, log: str) -> dict:
    for line in reversed(stdout.splitlines()):
        if line.startswith("RESULT "):
            try:
                return json.loads(line[len("RESULT "):])
            except ValueError:
                break
    tail = "\n".join(log.strip().splitlines()[-3:])
    return {"ok": False, "error": f"main.py stopped without a result (exit {returncode}): {tail}"}


class FakeRunner:
    """A pretend robot for trying the page without hardware: it keeps a cube
    in memory, starting solved, and answers like main.py would."""

    def __init__(self, delay_s: float = 0.4):
        sys.path.insert(0, str(HERE))
        from rubik.cube import apply
        self.apply = apply
        self.state, self.history, self.delay = SOLVED, [], delay_s

    def run(self, args: list[str]) -> tuple[dict, str]:
        from rubik.moves import fmt, invert, parse
        from rubik.solver import known_answer_solver, solve
        time.sleep(self.delay)
        if args[0] == "move":
            moves = args[1].split()
            self.state = self.apply(self.state, moves)
            self.history += moves
            return {"ok": True, "moves": moves, "motion_ms": 90 * len(moves)}, \
                f"[pretend robot] done: {len(moves)} move(s)"
        if args[0] == "read":
            return {"ok": True, "facelets": self.state, "valid": True,
                    "least_certain": ["D2", "B4", "R6"], "scan_s": 3.1,
                    "photos": "logs/pretend"}, "[pretend robot] scanned the cube"
        if args[0] == "solve":
            state = args[args.index("--state") + 1] if "--state" in args else self.state
            if state != self.state:
                return {"ok": False, "error": "the given state is not the robot's cube"}, ""
            fn = known_answer_solver(state, fmt(invert(parse(" ".join(self.history)))))
            plan = solve(state, solver_fn=fn, quarter_turns_only=True)
            self.state, self.history = SOLVED, []
            return {"ok": True, "facelets": state, "scanned": "--state" not in args,
                    "solver_moves": plan.solver_moves, "robot_moves": plan.robot_moves,
                    "tokens": plan.tokens, "scan_s": 0.0 if "--state" in args else 3.1,
                    "software_s": 0.05, "executed": True, "shake_ms": 0,
                    "motion_ms": 95 * len(plan.tokens), "total_s": 0.1 * len(plan.tokens)}, \
                "[pretend robot] solved"
        return {"ok": False, "error": f"unknown command {args[0]}"}, ""


# ------------------------------------------------------------ robot state
class Controller:
    """One command at a time; remembers the cube state from the last read."""

    def __init__(self, runner, config: str | None = None):
        self.runner = runner
        self.config = Path(config) if config else HERE / "config.toml"
        self.lock = threading.Lock()
        self.running: str | None = None
        self.read_state: str | None = None      # from the camera, still valid
        self.last_log = ""

    def face_colours(self) -> tuple[dict[str, str], str | None]:
        """Colour on each face as loaded ([cube] top / front in config.toml);
        read every time, so an edit shows without restarting the server."""
        from rubik.cube import STANDARD_SCHEME, face_colours
        try:
            with open(self.config, "rb") as fh:
                c = tomllib.load(fh).get("cube", {})
            return face_colours(c.get("top", "white"), c.get("front", "green")), None
        except (OSError, ValueError, tomllib.TOMLDecodeError) as e:
            return dict(STANDARD_SCHEME), f"config.toml [cube]: {e}"

    def status(self) -> dict:
        colours, error = self.face_colours()
        return {"busy": self.running is not None, "running": self.running,
                "read_state": self.read_state, "face_colours": colours,
                "config_error": error,
                "fake": isinstance(self.runner, FakeRunner)}

    def _run(self, label: str, args: list[str]) -> tuple[int, dict]:
        if not self.lock.acquire(blocking=False):
            return 409, {"ok": False, "error": f"the robot is busy ({self.running})"}
        self.running = label
        try:
            result, log = self.runner.run(args)
            self.last_log = log
            result["log"] = log[-4000:]
            return (200 if result.get("ok") else 422), result
        finally:
            self.running = None
            self.lock.release()

    def scramble(self, moves) -> tuple[int, dict]:
        if not isinstance(moves, list) or not moves or len(moves) > 60:
            return 422, {"ok": False, "error": "send 1-60 moves"}
        bad = [m for m in moves if not isinstance(m, str) or not MOVE_RE.match(m)]
        if bad:
            msg = (f"the top ({self.face_colours()[0]['U']}) face has no motor; "
                   "use F, R, B, L, D"
                   if any(isinstance(m, str) and m.upper().startswith("U") for m in bad)
                   else f"not a move: {bad[0]!r}")
            return 422, {"ok": False, "error": msg}
        code, result = self._run("scrambling", ["move", " ".join(moves)])
        if result.get("ok") or code == 422:
            self.read_state = None       # the cube changed (or may have): read it again
        return code, result

    def read(self) -> tuple[int, dict]:
        code, result = self._run("reading the cube", ["read"])
        if result.get("ok") and result.get("valid", True):
            self.read_state = result["facelets"]
        return code, result

    def solve(self) -> tuple[int, dict]:
        args = ["solve"] + (["--state", self.read_state] if self.read_state else [])
        label = "solving" if self.read_state else "reading and solving"
        code, result = self._run(label, args)
        if result.get("ok") and result.get("executed"):
            self.read_state = SOLVED
        return code, result


# ------------------------------------------------------------------ HTTP
def make_handler(ctl: Controller):
    class Handler(BaseHTTPRequestHandler):
        def _reply(self, status: int, data) -> None:
            body = json.dumps(data).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _body(self) -> dict:
            n = int(self.headers.get("Content-Length") or 0)
            if not n:
                return {}
            try:
                data = json.loads(self.rfile.read(n))
            except ValueError:
                return {}
            return data if isinstance(data, dict) else {}

        def do_GET(self):
            route = urlparse(self.path).path
            if route in ("/", "/index.html"):
                body = PAGE.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
            elif route == "/api/status":
                self._reply(200, ctl.status())
            elif route == "/api/log":
                self._reply(200, {"log": ctl.last_log[-4000:]})
            else:
                self._reply(404, {"error": "not found"})

        def do_POST(self):
            route = urlparse(self.path).path
            if route == "/api/scramble":
                self._reply(*ctl.scramble(self._body().get("moves")))
            elif route == "/api/read":
                self._reply(*ctl.read())
            elif route == "/api/solve":
                self._reply(*ctl.solve())
            else:
                self._reply(404, {"error": "not found"})

        def log_message(self, fmt, *args):
            if not self.path.startswith("/api/status"):
                sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))
    return Handler


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--config", help="config.toml to pass to main.py")
    ap.add_argument("--fake-robot", action="store_true", help="pretend robot, no hardware")
    args = ap.parse_args()
    runner = FakeRunner() if args.fake_robot else MainRunner(args.config)
    ctl = Controller(runner, args.config)
    server = ThreadingHTTPServer((args.host, args.port), make_handler(ctl))
    where = "localhost" if args.host in ("0.0.0.0", "") else args.host
    print(f"Robot dashboard on http://{where}:{args.port}"
          + ("  (pretend robot)" if args.fake_robot else "")
          + "  - open it from another computer with the Pi's IP address. Ctrl+C stops.")
    colours, error = ctl.face_colours()
    print(error or f"Cube loaded {colours['U']} on top, {colours['F']} at the front ([cube] in config.toml)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
