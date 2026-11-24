"""Five-face-motor solution executor for the Raspberry Pi.

The robot has one motor on each of F, R, B, L and D (MKS Gen L v1.0 sockets
Z, E1, E0, Y, X; see firmware/cube_firmware/config.h). The top (U) face has no motor and the cube is never
regripped. Every U turn in the solver's answer is rewritten into turns of the
other five faces by pi/rubik/moves.py:

    U = Y D Y,   Y = R L' F2 B2 R L'   (Y is its own inverse)

Opposite faces that follow each other are sent as one step ("R+L'") so the
firmware turns them at the same time. Motor commands go over USB to the
firmware in firmware/cube_firmware (protocol: docs/protocol.md).

Without --execute this is a dry run: it prints the robot steps only.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from urllib.request import Request, urlopen

# Reuse the move logic from the pi/ package (pure standard library).
PI_DIR = Path(__file__).resolve().parent.parent / "pi"
sys.path.insert(0, str(PI_DIR))
from rubik.cube import seq_perm  # noqa: E402
from rubik.moves import (expand_half_turns, fmt, parallelize, parse,  # noqa: E402
                         rewrite_without_u)

FACE_MOTORS = ("F", "R", "B", "L", "D")   # MKS sockets Z, E1, E0, Y, X


def plan_robot_steps(solver_moves: str | list[str], parallel: bool = True,
                     quarter_turns_only: bool = True) -> list[str]:
    """Turn a solver answer (may contain U) into robot steps that never use U.
    With quarter_turns_only, every 180-degree turn becomes two 90-degree steps."""
    tokens = solver_moves.split() if isinstance(solver_moves, str) else solver_moves
    moves = parse(" ".join(tokens))
    robot = rewrite_without_u(moves)
    # Safety check on the cube model before anything moves.
    if seq_perm(fmt(robot)) != seq_perm(fmt(moves)):
        raise AssertionError("internal error: rewritten moves differ from the solver's")
    if any(face not in FACE_MOTORS for face, _ in robot):
        raise AssertionError("internal error: a move needs a face without a motor")
    steps = parallelize(robot) if parallel else fmt(robot)
    return expand_half_turns(steps) if quarter_turns_only else steps


def fetch_solution(server: str, facelets: str) -> list[str]:
    body = json.dumps({"facelets": facelets}).encode("utf-8")
    request = Request(server.rstrip("/") + "/solve", data=body,
                      headers={"Content-Type": "application/json"})
    with urlopen(request, timeout=60) as response:
        result = json.loads(response.read().decode("utf-8"))
    return result["moves"]


def execute(steps: list[str], port: str) -> int:
    """Send the steps to the MKS firmware; returns its measured motion time (ms)."""
    from rubik.serial_link import Robot   # needs pyserial, only when executing
    robot = Robot(port)
    try:
        robot.enable(True)
        return robot.run(steps)
    finally:
        robot.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("facelets", help="54 facelets in URFDLB order")
    parser.add_argument("--server", required=True,
                        help="solver API URL, e.g. http://127.0.0.1:8011 on the Pi")
    parser.add_argument("--execute", action="store_true",
                        help="actually turn the motors (otherwise dry run)")
    parser.add_argument("--port", default="/dev/ttyUSB0", help="MKS Gen L serial port")
    parser.add_argument("--no-parallel", action="store_true",
                        help="send opposite faces one after the other")
    args = parser.parse_args()

    solution = fetch_solution(args.server, args.facelets)
    steps = plan_robot_steps(solution, parallel=not args.no_parallel)
    print(f"Solver ({len(solution)} moves): {' '.join(solution)}")
    print(f"Robot  ({len(steps)} steps, no U): {' '.join(steps)}")
    if args.execute:
        ms = execute(steps, args.port)
        print(f"Done: motion time {ms} ms (firmware-measured)")
    else:
        print("Dry run: add --execute to turn the motors")
