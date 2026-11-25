#!/usr/bin/env python3
"""Rubik's cube robot - host program for the Raspberry Pi 5.

    python main.py simulate                 # whole pipeline, no hardware
    python main.py ping                     # check the MKS Gen L link
    python main.py drivers                  # check all five TMC2209 over UART
    python main.py move "R L' F2"           # turn faces by hand
    python main.py jog F 10                 # nudge one motor (alignment)
    python main.py motors off
    python main.py show-faces               # draw the face model on a live photo
    python main.py calibrate-rois           # click sticker positions (if not using the face model)
    python main.py calibrate-colours        # scan a SOLVED cube once
    python main.py read                     # scan the cube and print its state
    python main.py solve [--dry-run]        # scan, solve, execute
    python main.py solve --state <54 letters>   # solve a known state (no scan)

Add --json (before the command) to end with one line "RESULT {...}" for the
web UI (pi/web_app.py), e.g.  python main.py --json read

Load the cube the same way every time: [cube] top / front in config.toml
(default white on top, green at the front).
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
import tomllib
from pathlib import Path

import numpy as np

from rubik.cube import FACES, SOLVED, apply, describe, face_colours, facelet_name, net
from rubik.moves import fmt, invert, parallelize, parse, simplify, tokens_to_moves
from rubik.scan_planner import plan_scan, scan_moves
from rubik.solver import known_answer_solver, solve

HERE = Path(__file__).resolve().parent


def load_config(path: str | None) -> dict:
    p = Path(path) if path else HERE / "config.toml"
    with open(p, "rb") as fh:
        cfg = tomllib.load(fh)
    cfg["_dir"] = p.parent
    return cfg


def cfg_path(cfg, key) -> Path:
    return (cfg["_dir"] / cfg["files"][key]).resolve()


def cube_colours(cfg) -> dict[str, str]:
    """Colour on each face as the cube is loaded ([cube] top / front)."""
    c = cfg.get("cube", {})
    try:
        return face_colours(c.get("top", "white"), c.get("front", "green"))
    except ValueError as e:
        raise CommandFailed(f"config.toml: {e}")


def loading_hint(cfg) -> str:
    fc = cube_colours(cfg)
    return f"{fc['U']} on top, {fc['F']} at the front"


def plan_cache(cfg) -> Path:
    """Where finished scan plans are remembered (the search can take ~15 s)."""
    return (cfg["_dir"] / cfg["files"].get("scan_plans", "data/scan_plans.json")).resolve()


class CommandFailed(Exception):
    """Stops a command with a message (and a RESULT line with --json)."""


def emit(args, **data) -> None:
    """--json: print the command's outcome as the last stdout line."""
    if getattr(args, "json", False):
        print("RESULT " + json.dumps(data), flush=True)


def to_tokens(moves: list[str], parallel: bool) -> list[str]:
    m = simplify(parse(" ".join(moves)))
    return parallelize(m) if parallel else fmt(m)


# ----------------------------------------------------------------- hardware
def open_robot(cfg, verbose=False):
    from rubik.serial_link import Robot
    s = cfg["serial"]
    m = cfg["motion"]
    robot = Robot(s["port"], s["baud"], verbose=verbose,
                  quarter_turns_only=m.get("quarter_turns_only", True))
    robot.speed(m["max_steps_per_s"], m["accel_steps_per_s2"])
    robot.settle(m.get("settle_us", 2000))
    robot.overshoot(m.get("overshoot_deg", 10) if m.get("overshoot", False) else 0)
    robot.enable(True)
    return robot


def shake(robot, cfg, deg=None, cycles=None, faces=None) -> int:
    """Run the [shake] wiggle (arguments override config.toml). Returns ms."""
    s = cfg.get("shake", {})
    return robot.shake(deg if deg is not None else s.get("amplitude_deg", 3),
                       cycles if cycles is not None else s.get("cycles", 5),
                       faces if faces is not None else s.get("faces", "FRBLD"))


def open_camera(cfg, fixed: bool = True):
    """Start the camera. With fixed (default), the iso / shutter /
    white_balance set in config.toml [camera] are used; anything not set is
    the camera's automatic choice, held for this run."""
    from rubik.vision import Camera, camera_settings, describe_camera
    c = cfg["camera"]
    try:
        settings = camera_settings(c) if fixed else {}
    except ValueError as e:
        raise CommandFailed(f"config.toml [camera]: {e}")
    cam = Camera(c["width"], c["height"], c.get("lens_position"),
                 full_fov=c.get("full_fov", False), **settings)
    names = {"gain": "ISO", "shutter_us": "shutter", "colour_gains": "white balance"}
    how = ("fixed: " + ", ".join(names[k] for k in settings) if settings
           else "automatic (set iso / shutter / white_balance in config.toml [camera] to fix them)")
    if settings and len(settings) < 3:
        how += "; the rest automatic"
    print(f"camera: {describe_camera(cam.locked)}  ({how})")
    return cam


def check_camera_matches_calibration(cfg, cam) -> None:
    """Warn if this run's camera settings differ from calibrate-colours'."""
    from rubik.vision import calibration_camera, camera_changes
    saved = calibration_camera(cfg_path(cfg, "colours"))
    if not saved:
        return
    changes = camera_changes(saved, cam.locked)
    if changes:
        print("WARNING: the camera settings differ from the colour calibration ("
              + "; ".join(changes) + "). Colours may be misread (red/orange). Fix iso, shutter "
              "and white_balance in config.toml [camera], then run calibrate-colours again.")


# MKS socket of each face; must match the STEP pins in firmware/cube_firmware/config.h
SOCKET = {"D": "X", "L": "Y", "F": "Z", "B": "E0", "R": "E1"}
FACE_NAME = {"F": "front", "R": "right", "B": "back", "L": "left", "D": "bottom"}


def wait_for_key(prompt: str) -> None:
    """Wait for one key press (no Enter needed on the Pi). Ctrl+C aborts."""
    print(prompt, end="", flush=True)
    try:
        import termios
        import tty
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
    except Exception:
        input()   # not a terminal (or Windows): press Enter instead
        return
    try:
        tty.setcbreak(fd)          # read one key without waiting for Enter
        ch = sys.stdin.read(1)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
    print()
    if ch == "\x03":
        raise KeyboardInterrupt


def describe_turn(tok: str) -> str:
    parts = []
    for p in tok.split("+"):
        way = {"": "clockwise", "'": "anticlockwise", "2": "180 degrees"}[p[1:]]
        parts.append(f"{p} ({FACE_NAME[p[0]]}, {SOCKET[p[0]]} motor, {way})")
    return " + ".join(parts)


def run_moves(robot, moves: list[str], parallel: bool, turn_delay_s: float = 0.0,
              confirm: bool = False, done: list[str] | None = None):
    """Send moves to the robot. With turn_delay_s > 0 or confirm, each turn is
    sent on its own; confirm waits for a key press before every turn.
    done: if given, every move the board confirmed is appended to it."""
    tokens = to_tokens(moves, parallel)
    if getattr(robot, "quarter_turns_only", False):
        from rubik.moves import expand_half_turns
        tokens = expand_half_turns(tokens)   # so the pause also comes between the two halves
    if turn_delay_s <= 0 and not confirm:
        if tokens:
            robot.run(tokens)
            if done is not None:
                done.extend(fmt(tokens_to_moves(tokens)))
        return
    for i, tok in enumerate(tokens, 1):
        if confirm:
            wait_for_key(f"  turn {i}/{len(tokens)}: {describe_turn(tok)} - press any key ")
        robot.run([tok])
        if done is not None:
            done.extend(fmt(tokens_to_moves([tok])))
        if turn_delay_s > 0:
            time.sleep(turn_delay_s)


def run_scan(cfg, robot, cam, rois, steps, save_dir: Path | None,
             parallel: bool | None = None, turn_delay_s: float = 0.0,
             confirm: bool = False):
    """Execute the scan plan and return per-photo Lab samples.

    The cube always ends in the scramble it was loaded with: after the last
    photo every scan move is undone, and if the scan stops early (error,
    Ctrl+C) the moves made so far are undone."""
    import cv2
    from rubik.vision import sample_lab
    settle = cfg["camera"]["settle_s"]
    if parallel is None:
        parallel = cfg["motion"]["parallel_opposite"]
    if cfg.get("shake", {}).get("before_scan", False):
        print(f"shake before scan: {shake(robot, cfg)} ms")
    samples: list = []
    done: list[str] = []             # scan moves the board has carried out
    try:
        for k, step in enumerate(steps):
            if step.moves:
                run_moves(robot, step.moves, parallel, turn_delay_s, confirm, done)
            time.sleep(settle)
            img = cam.capture()
            if save_dir:
                save_dir.mkdir(parents=True, exist_ok=True)
                cv2.imwrite(str(save_dir / f"photo_{k}.jpg"), img)
            samples.append(sample_lab(img, rois))
    finally:
        undo_moves(robot, done, parallel, turn_delay_s, confirm)
    return samples


def undo_moves(robot, done: list[str], parallel: bool, turn_delay_s: float = 0.0,
               confirm: bool = False) -> None:
    """Turn the cube back through `done` in reverse (inverse moves)."""
    back = fmt(invert(parse(" ".join(done))))
    if not back:
        return
    print(f"turning the cube back to its scramble ({len(back)} moves): {' '.join(back)}")
    try:
        run_moves(robot, back, parallel, turn_delay_s, confirm)
    except BaseException:
        print("Could not turn the cube back. To restore the scramble by hand or with "
              f"`move`, do: {' '.join(back)}")
        raise


# ----------------------------------------------------------------- commands
def cmd_simulate(cfg, args):
    """Random scrambles through planner, noisy 'vision', solver and rewrite."""
    try:
        import kociemba  # noqa: F401
        have_kociemba = True
    except ImportError:
        have_kociemba = False
        print("kociemba not installed: using the known-answer stand-in solver\n")
    rng = np.random.default_rng(args.seed)
    random.seed(args.seed)
    visible = cfg["scan"]["visible"]
    steps = plan_scan(visible, cfg["scan"]["max_seq_len"],
                      min_views=cfg["scan"].get("min_views", 1),
                      min_photos=cfg["scan"].get("min_photos", True),
                      cache_path=plan_cache(cfg))
    smoves = scan_moves(steps)
    print(f"scan plan: {len(steps)} photos, {len(smoves)} moves: "
          + " | ".join(" ".join(s.moves) or "(as loaded)" for s in steps))
    # fake reference colours, roughly like real stickers in Lab (OpenCV scale)
    refs = {"U": [235, 128, 128], "R": [120, 185, 165], "F": [150, 85, 170],
            "D": [220, 115, 205], "L": [170, 160, 190], "B": [90, 140, 70]}
    from rubik.vision import classify, collect_observations
    counts, total_tokens = 0, []
    for n in range(args.n):
        scramble = [random.choice(FACES) + random.choice(["", "'", "2"]) for _ in range(25)]
        original = apply(SOLVED, scramble)
        samples = []
        for st in steps:
            cur = apply(original, [m for s in steps[:steps.index(st) + 1] for m in s.moves])
            samples.append({name: np.array(refs[cur[_idx(name)]]) + rng.normal(0, args.noise, 3)
                            for name in visible})
        state0, _ = classify(collect_observations(samples, steps), refs)
        if state0 != original:
            print(f"#{n}: colour classification error")
            continue
        # the robot undoes the scan moves, so it solves the original scramble
        assert apply(original, smoves + fmt(invert(parse(" ".join(smoves))))) == original
        current = original
        solver_fn = None
        if not have_kociemba:
            solver_fn = known_answer_solver(current, fmt(invert(parse(" ".join(scramble)))))
        plan = solve(current, solver_fn=solver_fn,
                     parallel=cfg["motion"]["parallel_opposite"],
                     quarter_turns_only=cfg["motion"].get("quarter_turns_only", True))
        assert apply(current, plan.robot_moves) == SOLVED
        counts += 1
        total_tokens.append(len(plan.tokens))
        if n < 3:
            print(f"#{n} solver: {' '.join(plan.solver_moves)}")
            print(f"   robot ({len(plan.tokens)} steps): {' '.join(plan.tokens)}")
    print(f"\n{counts}/{args.n} solved in simulation; "
          f"robot steps mean {np.mean(total_tokens):.1f}, max {max(total_tokens)}")


def _idx(name):
    from rubik.cube import facelet_index
    return facelet_index(name)


def cmd_ping(cfg, args):
    robot = open_robot(cfg, verbose=True)
    print(robot.banner, "| ping ok" if robot.ping() else "| ping FAILED")
    robot.close()


def cmd_drivers(cfg, args):
    robot = open_robot(cfg, verbose=False)
    for line in robot.drivers():
        print(line)
    robot.close()


def cmd_move(cfg, args):
    """Send the moves exactly as typed (no simplifying), e.g. "F F'" or "R+L'"."""
    tokens = args.moves.split()
    if not tokens:
        raise CommandFailed('Give at least one move, e.g.  python main.py move "F"')
    try:
        faces = [f for f, _ in parse(" ".join(t.replace("+", " ") for t in tokens))]
    except ValueError as e:
        raise CommandFailed(f"{e}. Use F R B L D with ' or 2, e.g. F  F'  F2  R+L'")
    if "U" in faces:
        raise CommandFailed("U has no motor on this robot; use F, R, B, L or D")
    robot = open_robot(cfg, verbose=not args.json)
    try:
        ms = robot.run(tokens)
        print(f"done: {len(tokens)} move(s), motion {ms} ms")
    finally:
        robot.close()
    emit(args, ok=True, moves=tokens, motion_ms=ms)


def cmd_jog(cfg, args):
    robot = open_robot(cfg, verbose=True)
    robot.jog(args.face.upper(), args.steps)
    robot.close()


def cmd_shake(cfg, args):
    robot = open_robot(cfg, verbose=True)
    try:
        ms = shake(robot, cfg, args.deg, args.cycles, args.faces)
        print(f"done: shake took {ms} ms")
    finally:
        robot.close()


def cmd_motors(cfg, args):
    robot = open_robot(cfg)
    try:
        robot.enable(args.state == "on")
    finally:
        robot.close()
    print(f"motors {args.state}")
    emit(args, ok=True, motors=args.state == "on")


def cmd_calibrate_rois(cfg, args):
    """Click each visible sticker centre on a live photo (needs a desktop/VNC)."""
    import cv2
    from rubik.vision import save_json
    if args.image:
        img = cv2.imread(args.image)
    else:
        cam = open_camera(cfg)
        img = cam.capture()
        cam.close()
        cfg_path(cfg, "rois").parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(cfg_path(cfg, "rois").with_suffix(".jpg")), img)
    names = [f + str(i) for f in cfg["scan"]["candidate_faces"] for i in range(1, 10)]
    names = [n for n in names if not n.endswith("5") or n.startswith("U")]
    points: dict[str, list[float]] = {}
    i = 0
    click = {}
    cv2.namedWindow("rois", cv2.WINDOW_NORMAL)
    cv2.setMouseCallback("rois", lambda e, x, y, f, p: click.update(xy=(x, y))
                         if e == cv2.EVENT_LBUTTONDOWN else None)
    print("Left-click the centre of each sticker asked for.\n"
          "  s = not visible (skip)   u = undo   q = finish and save")
    while i < len(names):
        name = names[i]
        view = img.copy()
        for n, (x, y) in points.items():
            cv2.circle(view, (int(x), int(y)), 6, (0, 255, 0), 2)
            cv2.putText(view, n, (int(x) + 8, int(y)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1)
        cv2.putText(view, "click " + describe(name), (10, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2)
        cv2.imshow("rois", view)
        key = cv2.waitKey(30) & 0xFF
        if "xy" in click:
            points[name] = list(click.pop("xy"))
            i += 1
        elif key == ord("s"):
            i += 1
        elif key == ord("u") and i > 0:
            i -= 1
            points.pop(names[i], None)
        elif key == ord("q"):
            break
    cv2.destroyAllWindows()
    save_json(cfg_path(cfg, "rois"), {"radius": cfg["camera"]["roi_radius"],
                                      "image_size": [img.shape[1], img.shape[0]],
                                      "points": points})
    print(f"saved {len(points)} points to {cfg_path(cfg, 'rois')}")
    print("now set [scan] visible in config.toml to:\n  visible = "
          + str(list(points)).replace("'", '"'))


def load_sticker_positions(cfg) -> dict:
    """Where to read each sticker in the camera image.

    [scan] sticker_source = "face_model" (default): from data/face_model.json,
    for the stickers listed in [scan] visible. "rois": the points clicked with
    calibrate-rois (data/rois.json)."""
    from rubik.vision import load_rois
    if cfg["scan"].get("sticker_source", "face_model") == "face_model":
        return load_face_model(cfg).rois(cfg["scan"]["visible"], cfg["scan"].get("patch_inset", 0.3))
    return load_rois(cfg_path(cfg, "rois"))


def load_face_model(cfg, scale=None, offset=None):
    """The face model with [scan] face_scale / face_offset applied (offset in
    pixels of the camera image, [camera] width x height)."""
    from rubik.face_model import FaceModel
    sc = cfg["scan"]
    cam = (cfg["camera"]["width"], cfg["camera"]["height"])
    return FaceModel.load(cfg_path(cfg, "faces")).adjust(
        sc.get("face_scale", 1.0) if scale is None else scale,
        sc.get("face_offset", [0, 0]) if offset is None else offset, image_size=cam)


def _scan_setup(cfg):
    rois = load_sticker_positions(cfg)
    steps = plan_scan(list(rois["points"]), cfg["scan"]["max_seq_len"],
                      min_views=cfg["scan"].get("min_views", 1),
                      min_photos=cfg["scan"].get("min_photos", True),
                      cache_path=plan_cache(cfg))
    smoves = scan_moves(steps)
    print(f"scan plan: {len(steps)} photos, {len(smoves)} moves: "
          + " | ".join(" ".join(s.moves) or "(as loaded)" for s in steps))
    return rois, steps


def draw_face_outlines(cfg, img, scale=None, offset=None) -> None:
    """Draw the face model on a BGR image in place: the curved face outlines
    and sticker grid (R red, B blue, D yellow lines) and a labelled white box
    where each read sticker is sampled. scale / offset default to config.toml."""
    import cv2
    h, w = img.shape[:2]
    sc = cfg["scan"]
    model = load_face_model(cfg, scale, offset)
    sx, sy = w / model.image_size[0], h / model.image_size[1]
    colour = {"R": (60, 60, 255), "B": (255, 140, 60), "D": (0, 220, 255)}   # BGR
    for face in "RBD":
        for k, line in enumerate(model.grid_lines(face)):
            pts = np.round(line * [sx, sy]).astype(np.int32)
            cv2.polylines(img, [pts], False, colour[face], 3 if k == 0 else 1, cv2.LINE_AA)
    rois = model.rois(cfg["scan"]["visible"], sc.get("patch_inset", 0.3))
    for name, quad in rois.get("patches", {}).items():
        pts = np.round(np.array(quad) * [sx, sy]).astype(np.int32)
        cv2.polylines(img, [pts], True, (255, 255, 255), 2, cv2.LINE_AA)
        x, y = np.array(rois["points"][name]) * [sx, sy]
        cv2.putText(img, name, (int(x) - 12, int(y) + 5), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                    (0, 0, 0), 3, cv2.LINE_AA)
        cv2.putText(img, name, (int(x) - 12, int(y) + 5), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                    (255, 255, 255), 1, cv2.LINE_AA)


def cmd_show_faces(cfg, args):
    """Draw the face model (curved outlines, sticker grid, sampled patches) on a
    camera photo, to check it still matches after moving anything."""
    import cv2
    if args.image:
        img = cv2.imread(args.image)
    else:
        cam = open_camera(cfg)
        img = cam.capture()
        cam.close()
    h, w = img.shape[:2]
    sc = cfg["scan"]
    scale = sc.get("face_scale", 1.0) if args.scale is None else args.scale
    off = list(sc.get("face_offset", [0, 0]))
    if args.dx is not None:
        off[0] = args.dx
    if args.dy is not None:
        off[1] = args.dy
    draw_face_outlines(cfg, img, scale, off)
    out = cfg_path(cfg, "faces").with_name("faces_check.jpg")
    out.parent.mkdir(parents=True, exist_ok=True)
    cv2.putText(img, f"face_scale = {scale:g}   face_offset = [{off[0]:g}, {off[1]:g}]   ({w}x{h})",
                (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 0), 4, cv2.LINE_AA)
    cv2.putText(img, f"face_scale = {scale:g}   face_offset = [{off[0]:g}, {off[1]:g}]   ({w}x{h})",
                (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2, cv2.LINE_AA)
    cv2.imwrite(str(out), img)
    print(f"saved {out} with face_scale = {scale:g}, face_offset = [{off[0]:g}, {off[1]:g}]")
    print("The lines must sit on the grooves between cubies and every white box inside one "
          "sticker. Try other values with --scale/--dx/--dy, then put them in config.toml [scan].")


def cmd_calibrate_colours(cfg, args):
    """Store one reference colour per face.

    Default: the cube must be SOLVED. With --scrambled the cube can be in any
    state (still loaded as in [cube] top / front): the 48 stickers are split
    into 6 groups of 8 and named, and the result must be a real cube.
    """
    from rubik.vision import (collect_observations, references_from_scrambled,
                              references_from_solved, save_references)
    camera_used = None
    fc = cube_colours(cfg)
    print(f"Cube must be loaded {loading_hint(cfg)} ([cube] in config.toml).")
    rois, steps = _scan_setup(cfg)
    cal = cfg.get("calibration", {})
    parallel = cal.get("parallel_opposite", False)
    delay = cal.get("turn_delay_s", 1.0)
    confirm = cal.get("confirm_each_turn", True)
    if confirm:
        print("Each turn waits for a key press. Check the face landed square before "
              "pressing; Ctrl+C stops.")
    robot, cam = open_robot(cfg), open_camera(cfg)
    try:
        camera_used = dict(cam.locked)
        samples = run_scan(cfg, robot, cam, rois, steps, HERE / "logs" / "calib",
                           parallel=parallel, turn_delay_s=delay, confirm=confirm)
        obs = collect_observations(samples, steps)   # run_scan put the cube back
    finally:
        cam.close()
        robot.close()
    if args.scrambled:
        try:
            refs, state = references_from_scrambled(obs, face_colour=fc)
        except ValueError as e:
            print(f"Calibration failed: {e}")
            sys.exit(2)
        print("detected cube (check it matches the real cube):\n"
              + net(state, colours=True, ansi=sys.stdout.isatty(), face_colour=fc))
    else:
        refs = references_from_solved(obs)
    save_references(cfg_path(cfg, "colours"), refs, fc, camera=camera_used)
    names = list(refs)
    gaps = sorted((float(np.linalg.norm(np.subtract(refs[a], refs[b]))), a, b)
                  for i, a in enumerate(names) for b in names[i + 1:])
    print("reference colours saved. Closest pairs (bigger is better):")
    for d, a, b in gaps[:3]:
        print(f"  {a}-{b} ({fc[a]}-{fc[b]}): {d:.1f}")
    if gaps[0][0] < 15:
        print("WARNING: two faces look very similar - improve lighting and recalibrate")


def scan_cube(cfg, robot, cam, stamp: str):
    """Scan (photos, then the scan moves are undone, so the cube is back in
    the scramble it was given) and work out the colours.
    Returns (facelets, least certain stickers, scan seconds)."""
    from rubik.vision import classify, collect_observations, load_references
    fc = cube_colours(cfg)
    rois, steps = _scan_setup(cfg)
    colours = cfg_path(cfg, "colours")
    if not colours.exists():
        raise CommandFailed("no colour calibration yet: run  python main.py calibrate-colours")
    refs, note = load_references(colours, fc)
    if note:
        print(note)
    check_camera_matches_calibration(cfg, cam)
    t0 = time.time()
    samples = run_scan(cfg, robot, cam, rois, steps, HERE / "logs" / stamp,
                       turn_delay_s=cfg["scan"].get("turn_delay_s", 0.0))
    scan_s = time.time() - t0
    original, conf = classify(collect_observations(samples, steps), refs)
    print("detected cube:\n" + net(original, colours=True, ansi=sys.stdout.isatty(),
                                     face_colour=fc))
    worst = sorted(conf.items(), key=lambda kv: -kv[1])[:3]
    print("least certain stickers:", ", ".join(f"{facelet_name(p)} ({c:.0f})" for p, c in worst))
    print(f"scan {scan_s:.2f} s ({len(scan_moves(steps))} scan moves, then undone); "
          f"photos in logs/{stamp}")
    return original, [facelet_name(p) for p, _ in worst], scan_s


def check_state(text: str) -> str:
    from rubik.cube import is_valid_counts
    state = text.upper().replace(" ", "")
    if len(state) != 54 or any(c not in FACES for c in state):
        raise CommandFailed("a cube state is 54 letters from U R F D L B (URFDLB order)")
    if not is_valid_counts(state):
        raise CommandFailed("each of U R F D L B must appear exactly 9 times")
    return state


def cmd_read(cfg, args):
    """Scan the cube and print its state; the cube ends as it was loaded."""
    from rubik.vision import _kociemba_valid
    stamp = time.strftime("%Y%m%d-%H%M%S")
    robot, cam = open_robot(cfg), open_camera(cfg)
    try:
        state, unsure, scan_s = scan_cube(cfg, robot, cam, stamp)
    finally:
        cam.close()
        robot.close()
    valid = _kociemba_valid(state)
    print("facelets:", state)
    if not valid:
        print("This is not a possible cube: check the lighting / sticker positions.")
    emit(args, ok=True, facelets=state, valid=valid, least_certain=unsure,
         scan_s=round(scan_s, 2), photos=f"logs/{stamp}")


def cmd_solve(cfg, args):
    """Scan (or take --state), solve, and turn the solution on the robot."""
    stamp = time.strftime("%Y%m%d-%H%M%S")
    known = check_state(args.state) if args.state else None
    robot = open_robot(cfg)
    cam = None
    try:
        scan_s = 0.0
        if known:
            original = known
            print("using the given cube state (no scan)")
        else:
            cam = open_camera(cfg)
            original, _, scan_s = scan_cube(cfg, robot, cam, stamp)
        t1 = time.time()
        try:
            plan = solve(original, try_orientations=cfg["solver"]["try_orientations"],
                         parallel=cfg["motion"]["parallel_opposite"],
                         time_budget_s=cfg["solver"].get("time_budget_s", 1.0),
                         quarter_turns_only=cfg["motion"].get("quarter_turns_only", True))
        except Exception as e:      # bad counts, or Kociemba: impossible cube
            where = "given state" if known else f"scan (photos in logs/{stamp})"
            raise CommandFailed(f"not a valid cube ({e}); check the {where}. "
                                "The cube is in the scramble it was given.")
        t2 = time.time()
        print(f"solver ({len(plan.solver_moves)} moves): {' '.join(plan.solver_moves)}")
        print(f"robot  ({len(plan.tokens)} steps): {' '.join(plan.tokens)}")
        result = dict(ok=True, facelets=original, scanned=not known,
                      solver_moves=plan.solver_moves, robot_moves=plan.robot_moves,
                      tokens=plan.tokens, scan_s=round(scan_s, 2),
                      software_s=round(t2 - t1, 3))
        if args.dry_run:
            print("dry run: the cube is in its scramble; the solution was not run")
            emit(args, **result, executed=False)
            return
        shake_ms = 0
        if cfg.get("shake", {}).get("before_solve", False):
            shake_ms = shake(robot, cfg)
        motion_ms = robot.run(plan.tokens)
        t3 = time.time()
        if not known:
            print(f"\nscan {scan_s:.2f} s (not timed)")
        print(f"software  {t2 - t1:.3f} s   (solver, target < 2 s)")
        if shake_ms:
            print(f"shake     {shake_ms / 1000:.3f} s   (before the solution)")
        print(f"physical  {motion_ms / 1000:.3f} s   (firmware-measured motion, target < 2 s)")
        print(f"after-scan total incl. USB {t3 - t1:.3f} s")
        emit(args, **result, executed=True, shake_ms=shake_ms, motion_ms=motion_ms,
             total_s=round(t3 - t1, 3))
    finally:
        if cam is not None:
            cam.close()
        robot.close()


def cmd_bench(cfg, args):
    """Time the motors: a random U-free sequence, then its inverse (the cube
    ends where it started). Use it to tune SPEED / accel / current."""
    rng = random.Random(args.seed)
    faces = "FRBLD"
    seq: list[str] = []
    while len(seq) < args.n:
        m = rng.choice(faces) + rng.choice(["", "'", "2"])
        if not seq or seq[-1][0] != m[0]:
            seq.append(m)
    parallel = cfg["motion"]["parallel_opposite"]
    fwd = to_tokens(seq, parallel)
    back = to_tokens(fmt(invert(parse(" ".join(seq)))), parallel)
    robot = open_robot(cfg)
    if args.speed:
        robot.speed(args.speed, args.accel or cfg["motion"]["accel_steps_per_s2"])
    try:
        for name, toks in (("forward", fwd), ("inverse", back)):
            ms = robot.run(toks)
            print(f"{name}: {len(toks)} steps in {ms} ms = {ms / len(toks):.0f} ms per step")
    finally:
        robot.close()
    print("Check the cube is back to its starting state. If it is off, turns are skipping:"
          " lower speed/accel or raise current.")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config")
    ap.add_argument("--json", action="store_true",
                    help="end with one line 'RESULT {...}' (used by the web UI)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("simulate")
    p.add_argument("-n", type=int, default=50)
    p.add_argument("--noise", type=float, default=6.0, help="Lab noise (std dev)")
    p.add_argument("--seed", type=int, default=1)
    sub.add_parser("ping")
    sub.add_parser("drivers")
    p = sub.add_parser("move")
    p.add_argument("moves")
    p = sub.add_parser("jog")
    p.add_argument("face")
    p.add_argument("steps", type=int)
    p = sub.add_parser("shake", help="wiggle the faces so the cube settles ([shake] in config.toml)")
    p.add_argument("--deg", type=float, help="swing each way, degrees (0.5-15)")
    p.add_argument("--cycles", type=int, help="number of back-and-forth swings (1-50)")
    p.add_argument("--faces", help="which faces, e.g. FB (default: config.toml)")
    p = sub.add_parser("motors")
    p.add_argument("state", choices=["on", "off"])
    p = sub.add_parser("show-faces", help="draw the face model on a camera photo")
    p.add_argument("--image", help="use a saved photo instead of the camera")
    p.add_argument("--scale", type=float, help="try a face_scale (grow the grid about the centre)")
    p.add_argument("--dx", type=float, help="try a face_offset x, camera pixels (+ = right)")
    p.add_argument("--dy", type=float, help="try a face_offset y, camera pixels (+ = down)")
    p = sub.add_parser("calibrate-rois")
    p.add_argument("--image", help="use a saved photo instead of the camera")
    p = sub.add_parser("calibrate-colours")
    p.add_argument("--scrambled", action="store_true",
                   help="cube does not need to be solved (still loaded as in [cube])")
    sub.add_parser("read", help="scan the cube and print its state (cube ends as loaded)")
    p = sub.add_parser("solve")
    p.add_argument("--dry-run", action="store_true",
                   help="scan and solve, but do not execute the solution")
    p.add_argument("--state", help="54-letter cube state (URFDLB); skips the scan")
    p = sub.add_parser("bench", help="time the motors with a random sequence and its inverse")
    p.add_argument("-n", type=int, default=20, help="moves in the sequence")
    p.add_argument("--speed", type=int, help="override max steps/s")
    p.add_argument("--accel", type=int, help="override steps/s^2")
    p.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()
    cfg = load_config(args.config)
    command = {
        "simulate": cmd_simulate, "ping": cmd_ping, "drivers": cmd_drivers,
        "bench": cmd_bench,
        "move": cmd_move, "jog": cmd_jog, "motors": cmd_motors, "shake": cmd_shake,
        "calibrate-rois": cmd_calibrate_rois, "show-faces": cmd_show_faces,
        "calibrate-colours": cmd_calibrate_colours, "solve": cmd_solve,
        "read": cmd_read,
    }[args.cmd]
    try:
        command(cfg, args)
    except CommandFailed as e:
        print(e)
        emit(args, ok=False, error=str(e))
        sys.exit(2)
    except Exception as e:        # serial / camera problems: report, then fail
        if args.json:
            emit(args, ok=False, error=f"{type(e).__name__}: {e}")
            sys.exit(1)
        raise


if __name__ == "__main__":
    main()
