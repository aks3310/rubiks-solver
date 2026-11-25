"""Live camera feed for the robot dashboard (MJPEG over HTTP).

The camera is opened only while someone is watching, and is closed about
5 s after the last viewer leaves, so main.py can use it. The dashboard also
pauses the feed (block) while a command that takes photos runs (read, solve),
and resumes it afterwards. Frames use the same sensor mode and field of view
as the scans, and the iso / shutter / white_balance fixed in config.toml
[camera], so what you see is what a scan sees. Optionally the face outlines
and sticker boxes are drawn on top.
"""
from __future__ import annotations

import threading
import time
from pathlib import Path

import numpy as np


# ---------------------------------------------------------------- sources
class PiCameraSource:
    """Picamera2, configured like rubik.vision.Camera (same field of view)."""

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.cam = None

    def open(self) -> None:
        from picamera2 import Picamera2               # Pi only
        from rubik.vision import camera_settings
        c = self.cfg["camera"]
        cam = Picamera2()
        try:
            kwargs = {"main": {"size": (c["width"], c["height"]), "format": "RGB888"}}
            if c.get("full_fov", False):
                kwargs["sensor"] = {"output_size": (2304, 1296)}
            try:
                conf = cam.create_still_configuration(**kwargs)
            except TypeError:
                kwargs.pop("sensor", None)
                kwargs["raw"] = {"size": (2304, 1296)}
                conf = cam.create_still_configuration(**kwargs)
            cam.configure(conf)
            cam.start()
            ctrl = {}
            fixed = camera_settings(c)
            if "gain" in fixed or "shutter_us" in fixed:
                ctrl["AeEnable"] = False
                if "gain" in fixed:
                    ctrl["AnalogueGain"] = fixed["gain"]
                if "shutter_us" in fixed:
                    ctrl["ExposureTime"] = fixed["shutter_us"]
                    frame = max(33_333, fixed["shutter_us"] + 2_000)
                    ctrl["FrameDurationLimits"] = (frame, frame)
            if "colour_gains" in fixed:
                ctrl["AwbEnable"] = False
                ctrl["ColourGains"] = fixed["colour_gains"]
            try:
                from libcamera import controls
                if c.get("lens_position") is not None:
                    ctrl["AfMode"] = controls.AfModeEnum.Manual
                    ctrl["LensPosition"] = c["lens_position"]
                else:
                    ctrl["AfMode"] = controls.AfModeEnum.Continuous
            except ImportError:
                pass
            if ctrl:
                cam.set_controls(ctrl)
        except Exception:
            cam.close()
            raise
        self.cam = cam

    def read(self) -> np.ndarray:
        return self.cam.capture_array("main")         # BGR byte order

    def close(self) -> None:
        if self.cam is not None:
            try:
                self.cam.stop()
            finally:
                self.cam.close()
                self.cam = None


class FakeSource:
    """For trying the dashboard without a camera: a still photo of the robot
    (or a grey picture) with a moving clock, so you can see it is live."""

    def __init__(self, cfg: dict, photo: Path | None = None):
        self.cfg, self.photo, self.base = cfg, photo, None

    def open(self) -> None:
        import cv2
        w, h = self.cfg["camera"]["width"], self.cfg["camera"]["height"]
        img = cv2.imread(str(self.photo)) if self.photo and Path(self.photo).exists() else None
        self.base = cv2.resize(img, (w, h)) if img is not None else np.full((h, w, 3), 70, np.uint8)

    def read(self) -> np.ndarray:
        import cv2
        img = self.base.copy()
        t = time.time()
        x = int((t * 120) % img.shape[1])
        cv2.circle(img, (x, img.shape[0] - 40), 14, (0, 220, 255), -1)
        cv2.putText(img, time.strftime("pretend camera  %H:%M:%S"), (20, img.shape[0] - 70),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 255, 255), 2, cv2.LINE_AA)
        time.sleep(0.03)
        return img

    def close(self) -> None:
        self.base = None


# ------------------------------------------------------------------- feed
class LiveFeed:
    def __init__(self, make_source, draw_outlines=None, fps: float = 8.0,
                 width: int = 768, idle_close_s: float = 5.0):
        """make_source(): a new source (open / read / close).
        draw_outlines(img): draws the face outlines on a BGR image in place."""
        self.make_source, self.draw_outlines = make_source, draw_outlines
        self.fps, self.width, self.idle_close_s = fps, width, idle_close_s
        self.cond = threading.Condition()
        self.viewers = 0
        self.last_viewer = 0.0
        self.blocked: str | None = None       # why the camera is lent out
        self.error: str | None = None
        self.crashed = False                  # the capture thread stopped on an error
        self.camera_open = False
        self.thread: threading.Thread | None = None
        self.seq = 0
        self.raw: np.ndarray | None = None
        self.jpegs: dict[bool, bytes] = {}
        self.overlay = None                   # (image, mask) of the outlines at stream size

    # -------------------------------------------------------- public
    def status(self) -> dict:
        with self.cond:
            if self.blocked:
                state = f"paused: {self.blocked}"
            elif self.error:
                state = "error"
            elif self.camera_open:
                state = "live"
            else:
                state = "off"
            return {"state": state, "viewers": self.viewers, "error": self.error}

    def block(self, reason: str, timeout: float = 6.0) -> None:
        """Release the camera for a command that takes photos (waits for it)."""
        with self.cond:
            self.blocked = reason
            self.cond.notify_all()
            end = time.time() + timeout
            while self.camera_open and time.time() < end:
                self.cond.wait(0.1)

    def unblock(self) -> None:
        with self.cond:
            self.blocked = None
            self.cond.notify_all()
            self._ensure_thread()

    def frames(self, outlines: bool = False):
        """JPEG frames for one viewer, as they arrive (a generator)."""
        with self.cond:
            self.viewers += 1
            self._ensure_thread()
        try:
            seen = -1
            while True:
                with self.cond:
                    if self.seq == seen:
                        self.cond.wait(1.0)
                    if self.crashed and self.thread is None:
                        return                # the page reconnects and restarts it
                    if self.seq == seen or self.raw is None:
                        continue
                    seen = self.seq
                    jpg = self._jpeg(outlines)
                yield jpg
        finally:
            with self.cond:
                self.viewers -= 1
                self.last_viewer = time.time()

    # -------------------------------------------------------- inside
    def _ensure_thread(self) -> None:
        """Start the capture thread unless one is running (lock held)."""
        if self.thread is None:
            self.crashed = False
            self.thread = threading.Thread(target=self._loop, name="live-feed", daemon=True)
            self.thread.start()

    def _publish(self, img: np.ndarray) -> None:
        import cv2
        h, w = img.shape[:2]
        if w != self.width:
            img = cv2.resize(img, (self.width, int(h * self.width / w)), interpolation=cv2.INTER_AREA)
        with self.cond:
            self.raw, self.jpegs = img, {}
            self.seq += 1
            self.cond.notify_all()

    def _jpeg(self, outlines: bool) -> bytes:
        """Encode the current frame (called with the lock held; cached)."""
        import cv2
        outlines = bool(outlines and self.draw_outlines)
        if outlines not in self.jpegs:
            img = self.raw
            if outlines:
                over = self._overlay(img.shape)
                if over is not None:
                    img = img.copy()
                    img[over[1]] = over[0][over[1]]
            ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 75])
            self.jpegs[outlines] = buf.tobytes()
        return self.jpegs[outlines]

    def _overlay(self, shape):
        if self.overlay is None or self.overlay[0].shape != shape:
            canvas = np.zeros(shape, np.uint8)
            try:
                self.draw_outlines(canvas)
            except Exception as e:             # face model missing etc.
                self.error = f"outlines: {e}"
                return None
            self.overlay = (canvas, canvas.any(axis=2))
        return self.overlay

    def _placeholder(self, text: str) -> np.ndarray:
        import cv2
        base = self.raw if self.raw is not None else np.full((432, self.width, 3), 40, np.uint8)
        img = (base * 0.3).astype(np.uint8)
        cv2.putText(img, text, (20, img.shape[0] // 2), cv2.FONT_HERSHEY_SIMPLEX, 0.8,
                    (255, 255, 255), 2, cv2.LINE_AA)
        return img

    def _loop(self) -> None:
        src = None
        try:
            while True:
                with self.cond:
                    idle = self.viewers == 0 and time.time() - self.last_viewer > self.idle_close_s
                    blocked = self.blocked
                if idle:                                     # nobody watching: free the camera
                    if src is not None:
                        src.close()
                        src = None
                    with self.cond:
                        self.camera_open = False
                        self.raw, self.jpegs = None, {}      # the next viewer waits for a fresh frame
                        self.cond.notify_all()
                        if self.viewers == 0:                # nobody arrived meanwhile
                            self.thread = None
                            return
                    continue
                if blocked or src is None:
                    if src is not None and blocked:
                        src.close()
                        src = None
                        with self.cond:
                            self.camera_open = False
                            self.cond.notify_all()
                    if blocked:
                        self._publish(self._placeholder(f"Camera in use: {blocked}"))
                        with self.cond:
                            self.cond.wait(0.5)
                        continue
                    try:
                        src = self.make_source()
                        src.open()
                        with self.cond:
                            self.camera_open, self.error = True, None
                    except Exception as e:
                        src = None
                        with self.cond:
                            self.error = f"camera not available: {e}"
                        self._publish(self._placeholder("Camera not available (in use by main.py?)"))
                        time.sleep(2.0)
                        continue
                t0 = time.time()
                try:
                    img = src.read()
                except Exception as e:
                    with self.cond:
                        self.error = f"camera stopped: {e}"
                    src.close()
                    src = None
                    with self.cond:
                        self.camera_open = False
                    continue
                self._publish(img)
                time.sleep(max(0.0, 1.0 / self.fps - (time.time() - t0)))
        except Exception as e:            # e.g. OpenCV missing: end the viewers' streams
            with self.cond:
                self.error, self.crashed = f"live feed stopped: {e}", True
        finally:
            if src is not None:
                src.close()
            with self.cond:
                self.camera_open = False
                if self.thread is threading.current_thread():   # stopped by an error
                    self.thread = None
                self.cond.notify_all()
