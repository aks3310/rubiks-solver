"""Live camera feed: streams JPEGs, lends the camera to scans, frees it when idle."""
import sys
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.request import urlopen

import numpy as np
import pytest

pytest.importorskip("cv2")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import web_app  # noqa: E402
from live_feed import LiveFeed  # noqa: E402


class Counts:
    """How often one feed's pretend camera was opened / is open (per test)."""
    def __init__(self):
        self.opened = self.closed = self.open_now = 0


class CountingSource:
    def __init__(self, counts):
        self.counts = counts

    def open(self):
        self.counts.opened += 1
        self.counts.open_now += 1

    def read(self):
        time.sleep(0.01)
        return np.full((864, 1536, 3), 120, np.uint8)

    def close(self):
        self.counts.closed += 1
        self.counts.open_now -= 1


def make_feed(**kw):
    counts = Counts()
    feed = LiveFeed(lambda: CountingSource(counts),
                    draw_outlines=lambda img: img[10:20, 10:20].fill(255),
                    fps=30, idle_close_s=0.3, **kw)
    return feed, counts


def wait_for(cond, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


def test_streams_jpegs_with_and_without_outlines():
    feed, counts = make_feed()
    plain = feed.frames(False)
    jpg = next(plain)
    assert jpg[:2] == b"\xff\xd8" and feed.status()["state"] == "live"
    with feed.cond:
        outlined = feed._jpeg(True)
    assert outlined != jpg                                    # outlines drawn
    plain.close()


def test_camera_is_freed_when_nobody_watches():
    feed, counts = make_feed()
    gen = feed.frames(False)
    next(gen)
    assert counts.open_now == 1
    gen.close()                                               # the page was closed
    assert wait_for(lambda: counts.open_now == 0 and feed.thread is None)
    gen = feed.frames(False)                                  # someone looks again
    next(gen)
    assert counts.opened == 2 and counts.open_now == 1
    gen.close()


def test_camera_is_lent_to_a_scan_and_taken_back():
    feed, counts = make_feed()
    gen = feed.frames(False)
    next(gen)
    feed.block("reading the cube")
    assert counts.open_now == 0                       # free for main.py
    assert feed.status()["state"] == "paused: reading the cube"
    next(gen)                                                 # placeholder frames keep coming
    feed.unblock()
    assert wait_for(lambda: counts.open_now == 1 and feed.status()["state"] == "live")
    gen.close()


def test_block_without_viewers_returns_at_once():
    feed, counts = make_feed()
    t = time.time()
    feed.block("solving")
    feed.unblock()
    assert time.time() - t < 1 and counts.opened == 0


class CameraCheckingRunner(web_app.FakeRunner):
    def __init__(self, counts):
        super().__init__(delay_s=0)
        self.counts, self.open_during = counts, {}

    def run(self, args):
        self.open_during[args[0]] = self.counts.open_now
        return super().run(args)


def test_dashboard_streams_and_lends_the_camera_for_read():
    feed, counts = make_feed()
    runner = CameraCheckingRunner(counts)
    ctl = web_app.Controller(runner, live=feed)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), web_app.make_handler(ctl))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        stream = urlopen(url + "/stream.mjpg?outlines=1", timeout=10)
        assert stream.headers["Content-Type"].startswith("multipart/x-mixed-replace")
        head = stream.read(200)
        assert b"--frame" in head and b"image/jpeg" in head
        assert wait_for(lambda: ctl.status()["camera"]["state"] == "live")
        ctl.read()                                            # takes photos: camera lent
        assert wait_for(lambda: counts.open_now == 1)  # taken back afterwards
        ctl.scramble(["F"])                                   # only moves: feed keeps running
        assert runner.open_during == {"read": 0, "move": 1}
        stream.close()
    finally:
        srv.shutdown()


def test_without_feed_status_says_disabled():
    ctl = web_app.Controller(web_app.FakeRunner(delay_s=0))
    assert ctl.status()["camera"] == {"state": "disabled"}
