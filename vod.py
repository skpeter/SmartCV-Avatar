"""Replay a video file through the real SmartCV detection loop.

Same detectors, same poll cadence and same websocket server as a live
capture, with frames coming from a file instead of OBS or the game window.
Useful for exercising a client integration without running a match, and for
watching the detector work against known footage.

For calibration and regression checking use core/validate_vod.py instead,
which drives the detectors directly and prints a timeline.

Usage (from repo root):
    python vod.py path/to.mp4
    python vod.py path/to.mp4 --start 400 --speed 4
    python vod.py                  # takes [video] path from config.ini

vod.py is the entry point rather than something routines.py imports because
core.core imports routines while it is still executing, before it defines
capture_screen. A patch applied from routines' module body would be
overwritten moments later by that definition. Importing core.core from here
runs it to completion first, so the patch sticks and the submodule stays
untouched.
"""
from __future__ import annotations

import argparse
import configparser
import os
import shutil
import sys
import threading

ROOT = os.path.dirname(os.path.abspath(__file__))
os.chdir(ROOT)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

if not os.path.exists("config.ini"):
    shutil.copy("config.ini.example", "config.ini")

import cv2  # noqa: E402
from PIL import Image  # noqa: E402

import core.core as core  # noqa: E402
import routines  # noqa: E402

BASE_WIDTH = 1920
BASE_HEIGHT = 1080


class VideoSource:
    """Stands in for core.capture_screen, one frame per poll.

    Advances by the configured refresh_rate of *video* time per call, so the
    detectors see exactly the cadence they would live no matter how fast the
    file is being played back. Frames in between are grabbed without being
    decoded, which is far cheaper than seeking for each one.
    """

    def __init__(self, path, step, start=0.0):
        self.cap = cv2.VideoCapture(path)
        if not self.cap.isOpened():
            raise SystemExit(f"vod: cannot open {path}")
        fps = self.cap.get(cv2.CAP_PROP_FPS) or 60.0
        self.skip = max(int(round(fps * step)) - 1, 0)
        if start:
            self.cap.set(cv2.CAP_PROP_POS_MSEC, start * 1000.0)
        self.timestamp = start
        self._started = False
        core.print_with_time(
            f"Replaying {os.path.basename(path)} from {start:.1f}s "
            f"({fps:.2f} fps, {step}s per poll)"
        )

    def now(self):
        """Video clock, for routines' round-start lock."""
        return self.timestamp

    def _finish(self):
        self.cap.release()
        core.print_with_time("End of video.")
        raise SystemExit(0)

    def __call__(self, payload):
        if self._started:
            for _ in range(self.skip):
                if not self.cap.grab():
                    self._finish()
        self._started = True
        ok, frame = self.cap.read()
        if not ok:
            self._finish()
        self.timestamp = self.cap.get(cv2.CAP_PROP_POS_MSEC) / 1000.0
        img = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
        width, height = img.size
        return img, width / BASE_WIDTH, height / BASE_HEIGHT


def main():
    config = configparser.ConfigParser()
    config.read("config.ini")
    step = config.getfloat("settings", "refresh_rate", fallback=0.5)

    parser = argparse.ArgumentParser()
    parser.add_argument("video", nargs="?",
                        default=config.get("video", "path", fallback=""))
    parser.add_argument("--start", type=float,
                        default=config.getfloat("video", "start", fallback=0.0))
    parser.add_argument("--speed", type=float,
                        default=config.getfloat("video", "speed", fallback=1.0),
                        help="wall-clock multiplier; 0 runs flat out")
    args = parser.parse_args()
    if not args.video:
        raise SystemExit(
            "vod: no video given. Pass a path or set [video] path in config.ini."
        )

    source = VideoSource(args.video, step, args.start)
    core.capture_screen = source
    # Measure the round-start lock in video seconds. At anything other than
    # 1x, wall-clock time would drift from the footage and the lock would
    # either expire early or swallow the next round start.
    routines._now = source.now
    if args.speed != 1.0:
        # Floor rather than 0: the websocket sender sleeps on this too, and a
        # true zero turns it into a busy loop.
        core.refresh_rate = step / args.speed if args.speed > 0 else 0.001

    threading.Thread(
        target=core.broadcast.broadcast_device_info,
        args=(routines.client_name,),
        daemon=True,
    ).start()
    threading.Thread(
        target=core.start_websocket_server,
        args=(routines.payload,),
        daemon=True,
    ).start()
    core.print_with_time(
        f"Serving on port {config.getint('settings', 'server_port', fallback=6565)}."
    )
    core.run_detection_loop(routines.states_to_functions, routines.payload)


if __name__ == "__main__":
    main()
