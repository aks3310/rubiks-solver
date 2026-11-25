"""Pi dashboard server: the three buttons, the busy lock and main.py results."""
import json
import sys
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import web_app  # noqa: E402
from web_app import SOLVED, Controller, FakeRunner, parse_result  # noqa: E402


@pytest.fixture
def server():
    ctl = Controller(FakeRunner(delay_s=0))
    srv = ThreadingHTTPServer(("127.0.0.1", 0), web_app.make_handler(ctl))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}", ctl
    srv.shutdown()


def call(url, path, body=None):
    data = None if body is None else json.dumps(body).encode()
    req = Request(url + path, data=data, method="GET" if body is None else "POST",
                  headers={"Content-Type": "application/json"})
    try:
        with urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read())
    except HTTPError as e:
        return e.code, json.loads(e.read())


def test_page_and_status(server):
    url, _ = server
    with urlopen(url + "/", timeout=10) as r:
        assert b"Robot console" in r.read()
    code, s = call(url, "/api/status")
    assert code == 200 and s["busy"] is False and s["read_state"] is None and s["fake"]


def test_scramble_read_solve_flow(server):
    url, ctl = server
    code, r = call(url, "/api/scramble", {"moves": ["R", "D'", "F2", "L", "B"]})
    assert code == 200 and r["ok"]
    assert ctl.runner.state != SOLVED and ctl.read_state is None
    code, r = call(url, "/api/read", {})
    assert code == 200 and r["facelets"] == ctl.runner.state
    assert ctl.read_state == r["facelets"]
    code, r = call(url, "/api/solve", {})
    assert code == 200 and r["executed"] and r["scanned"] is False   # used the read state
    assert all(t[0] in "FRBLD" for tok in r["tokens"] for t in tok.split("+"))
    assert ctl.runner.state == SOLVED and ctl.read_state == SOLVED


def test_solve_reads_first_after_a_scramble(server):
    url, ctl = server
    call(url, "/api/scramble", {"moves": ["F", "R'"]})
    code, r = call(url, "/api/solve", {})
    assert code == 200 and r["scanned"] is True and ctl.runner.state == SOLVED


@pytest.mark.parametrize("moves,msg", [(["U"], "top"), (["R", "X"], "not a move"),
                                       ([], "1-60"), ("R U", "1-60")])
def test_bad_scrambles_are_refused(server, moves, msg):
    url, ctl = server
    code, r = call(url, "/api/scramble", {"moves": moves})
    assert code == 422 and msg in r["error"] and ctl.runner.state == SOLVED


def test_one_command_at_a_time(server):
    url, ctl = server
    ctl.runner.delay = 0.6
    results = []
    t = threading.Thread(target=lambda: results.append(call(url, "/api/read", {})))
    t.start()
    time.sleep(0.2)
    code, r = call(url, "/api/scramble", {"moves": ["F"]})
    t.join()
    assert code == 409 and "busy" in r["error"]
    assert results[0][0] == 200


def test_solution_is_found_first_then_run(server):
    url, ctl = server
    call(url, "/api/scramble", {"moves": ["R", "D'", "F2"]})
    scrambled = ctl.runner.state
    code, p = call(url, "/api/find-solution", {})
    assert code == 200 and p["executed"] is False and p["scanned"] is True
    assert ctl.runner.state == scrambled                 # nothing turned yet
    assert ctl.read_state == scrambled and ctl.status()["plan_ready"]
    code, r = call(url, "/api/run-solution", {})
    assert code == 200 and r["ok"] and r["moves"] == p["tokens"]
    assert ctl.runner.state == SOLVED and ctl.read_state == SOLVED
    assert not ctl.status()["plan_ready"]
    code, r = call(url, "/api/run-solution", {})       # nothing left to run
    assert code == 409


def test_a_turn_after_finding_the_solution_cancels_it(server):
    url, ctl = server
    call(url, "/api/scramble", {"moves": ["F"]})
    call(url, "/api/find-solution", {})
    call(url, "/api/move", {"moves": ["R"]})              # Live mode click
    assert ctl.plan is None and ctl.read_state is None
    code, r = call(url, "/api/run-solution", {})
    assert code == 409 and ctl.runner.state != SOLVED


def test_live_move_is_one_face(server):
    url, ctl = server
    code, r = call(url, "/api/move", {"moves": ["L"]})
    assert code == 200 and ctl.runner.history == ["L"]
    code, r = call(url, "/api/move", {"moves": ["U"]})
    assert code == 422 and "no motor" in r["error"]


def test_motors_on_off_and_indicator(server):
    url, ctl = server
    assert call(url, "/api/status")[1]["motors"] is None     # unknown at start
    code, r = call(url, "/api/motors", {"on": False})
    assert code == 200 and r["motors"] is False
    assert call(url, "/api/status")[1]["motors"] is False
    call(url, "/api/move", {"moves": ["F"]})                   # moving turns them on
    assert call(url, "/api/status")[1]["motors"] is True
    code, r = call(url, "/api/motors", {"on": "yes"})
    assert code == 422


def test_parse_result_from_main_py_output():
    out = 'scan plan: ...\ndone\nRESULT {"ok": true, "motion_ms": 12}\n'
    assert parse_result(out, 0, out) == {"ok": True, "motion_ms": 12}
    bad = parse_result("Traceback ...\nSerialException: no port\n", 1, "SerialException: no port")
    assert bad["ok"] is False and "no port" in bad["error"]


def test_calibrate_from_the_dashboard(server):
    url, ctl = server
    code, r = call(url, "/api/calibrate", {"scrambled": False})
    assert code == 200 and r["ok"] and r["closest"][0]["distance"] > 15 and r["camera_saved"]
    assert ctl.status()["motors"] is True
    call(url, "/api/scramble", {"moves": ["R", "F"]})
    code, r = call(url, "/api/calibrate", {"scrambled": False})   # not solved any more
    assert code == 422 and "scrambled" in r["error"]
    code, r = call(url, "/api/calibrate", {"scrambled": True})
    assert code == 200 and r["facelets"] == ctl.runner.state
    code, r = call(url, "/api/calibrate", {"scrambled": "yes"})
    assert code == 422
