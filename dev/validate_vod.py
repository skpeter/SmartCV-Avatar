"""Replay an Avatar Legends VOD at SmartCV's poll interval and print events.

Usage (from repo root):
    python dev/validate_vod.py
    python dev/validate_vod.py path/to.mp4 --start 400 --end 900 --step 0.5
    python dev/validate_vod.py --probes      # dump probe values for one frame
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
os.chdir(ROOT)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

if not os.path.exists("config.ini"):
    shutil.copy("config.ini.example", "config.ini")

import cv2  # noqa: E402
from PIL import Image  # noqa: E402

import routines  # noqa: E402


DEFAULT_VOD = (
    r"D:\vod_library\YTDown.com_YouTube_Avatar-Legends-Tournament-219-"
    r"Throwdown-_Media_sokj1D2s15Q_001_1080p.mp4"
)


def _points():
    """Every probe coordinate, as (label, x, y) at 1080p."""
    out = []
    for i, ((x, y), _) in enumerate(routines.VS_PROBES):
        out.append((f"VS_PROBES[{i}]", x, y))
    for i, ((x, y), _) in enumerate(routines.CSS_PROBES):
        out.append((f"CSS_PROBES[{i}]", x, y))
    boxes = (
        [(f"P1_ROUND_SLOTS[{i}]", b) for i, b in enumerate(routines.P1_ROUND_SLOTS)]
        + [(f"P2_ROUND_SLOTS[{i}]", b) for i, b in enumerate(routines.P2_ROUND_SLOTS)]
        + [(f"HUD_DIVIDERS[{i}]", b) for i, b in enumerate(routines.HUD_DIVIDERS)]
        + [("PLATE_INTERIOR", routines.PLATE_INTERIOR)]
        + [("HP_P1", routines.HP_P1), ("HP_P2", routines.HP_P2)]
        + [("RES_RED", routines.RES_RED), ("RES_BLUE", routines.RES_BLUE)]
    )
    for label, (x0, y0, x1, y1) in boxes:
        out.append((label, x0, y0))
        out.append((label, x1 - 1, y1 - 1))
    for label, (x, y, w, h) in (
        ("P1_NAME_RECT", routines.P1_NAME_RECT),
        ("P2_NAME_RECT", routines.P2_NAME_RECT),
    ):
        out.append((label, x, y))
        out.append((label, x + w - 1, y + h - 1))
    return out


def check_overlay():
    """No probe may sit inside the broadcast overlay box."""
    ox0, oy0, ox1, oy1 = routines.IGNORE_REGION
    bad = [
        (label, x, y)
        for label, x, y in _points()
        if ox0 <= x <= ox1 and oy0 <= y <= oy1
    ]
    if bad:
        for label, x, y in bad:
            print(f"  OVERLAY COLLISION {label} at ({x},{y})")
        raise SystemExit(f"{len(bad)} probe point(s) inside {routines.IGNORE_REGION}")
    print(f"overlay {routines.IGNORE_REGION}: clear, {len(_points())} probe points")


def _frame(cap, t):
    cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000.0)
    ok, frame = cap.read()
    if not ok:
        return None, 1.0, 1.0
    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    h, w = rgb.shape[:2]
    return Image.fromarray(rgb), w / 1920, h / 1080


def dump_probes(path, t):
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        raise SystemExit(f"cannot open {path}")
    img, sx, sy = _frame(cap, t)
    cap.release()
    if img is None:
        raise SystemExit(f"no frame at {t}s")
    print(f"probe dump at t={t}s")
    for label, (x, y), color in (
        (f"VS_PROBES[{i}]", p, c) for i, (p, c) in enumerate(routines.VS_PROBES)
    ):
        got = routines._px(img, x, y, sx, sy)
        print(f"  {label:<16} ({x},{y}) want {color} got {got}")
    for label, (x, y), color in (
        (f"CSS_PROBES[{i}]", p, c) for i, (p, c) in enumerate(routines.CSS_PROBES)
    ):
        got = routines._px(img, x, y, sx, sy)
        print(f"  {label:<16} ({x},{y}) want {color} got {got}")
    for label, box in (
        ("P1 round 1", routines.P1_ROUND_SLOTS[0]),
        ("P1 round 2", routines.P1_ROUND_SLOTS[1]),
        ("P2 round 1", routines.P2_ROUND_SLOTS[0]),
        ("P2 round 2", routines.P2_ROUND_SLOTS[1]),
        ("HUD div L", routines.HUD_DIVIDERS[0]),
        ("HUD div R", routines.HUD_DIVIDERS[1]),
        ("plate interior", routines.PLATE_INTERIOR),
        ("HP P1 tip", routines.HP_P1),
        ("HP P2 tip", routines.HP_P2),
        ("RES red", routines.RES_RED),
        ("RES blue", routines.RES_BLUE),
    ):
        mean = routines._region_mean(img, box, sx, sy)
        print(f"  {label:<16} {box} mean {tuple(round(v, 1) for v in mean)}")
    print(f"  HUD visible      {routines._hud_visible(img, sx, sy)}")
    print(f"  markers          {routines._read_markers(img, sx, sy)}")
    print(f"  health full      {routines._health_full(img, sx, sy)}")


def snapshot(payload):
    p = payload["players"]
    return (
        payload["state"],
        payload["round"],
        p[0]["rounds"],
        p[1]["rounds"],
        p[0]["games"],
        p[1]["games"],
        p[0]["character"],
        p[1]["character"],
    )


def fmt(payload):
    p = payload["players"]
    chars = f"{p[0]['character'] or '-'} vs {p[1]['character'] or '-'}"
    return (
        f"state={payload['state']} round={payload['round']} "
        f"rounds={p[0]['rounds']}-{p[1]['rounds']} "
        f"games={p[0]['games']}-{p[1]['games']} {chars}"
    )


def run(path, start, end, step, ocr):
    """Decode sequentially and drop frames between polls.

    Seeking per poll costs about half a second a frame, which makes a
    full-VOD sweep unusable; grabbing without decoding is roughly 10x faster.
    """
    routines.ocr_enabled = ocr
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        raise SystemExit(f"cannot open {path}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 60.0
    skip = max(int(round(fps * step)) - 1, 0)
    if start:
        cap.set(cv2.CAP_PROP_POS_MSEC, start * 1000.0)

    prev = None
    print(f"scan {path}")
    print(f"range {start}s .. {end}s step {step}s fps={fps:.3f} ocr={ocr}")
    while True:
        t = cap.get(cv2.CAP_PROP_POS_MSEC) / 1000.0
        if t > end:
            break
        ok, frame = cap.read()
        if not ok:
            break
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        h, w = rgb.shape[:2]
        img, sx, sy = Image.fromarray(rgb), w / 1920, h / 1080
        routines._now = lambda ts=t: ts
        funcs = routines.states_to_functions.get(routines.payload.get("state"), [])
        for func in funcs:
            func(routines.payload, img, sx, sy)
        cur = snapshot(routines.payload)
        if cur != prev:
            print(f"  t={t:7.1f}  ({int(t // 60)}:{t % 60:04.1f})  {fmt(routines.payload)}")
            prev = cur
        for _ in range(skip):
            if not cap.grab():
                break
    cap.release()
    print("done", fmt(routines.payload))


def main():
    p = argparse.ArgumentParser()
    p.add_argument("vod", nargs="?", default=DEFAULT_VOD)
    p.add_argument("--start", type=float, default=400.0)
    p.add_argument("--end", type=float, default=900.0)
    p.add_argument("--step", type=float, default=0.5)
    p.add_argument("--ocr", action="store_true", help="run one-shot nameplate OCR")
    p.add_argument("--probes", type=float, nargs="?", const=411.5, default=None,
                   metavar="T", help="dump probe values for the frame at T seconds")
    args = p.parse_args()
    check_overlay()
    if args.probes is not None:
        dump_probes(args.vod, args.probes)
        return
    run(args.vod, args.start, args.end, args.step, args.ocr)


if __name__ == "__main__":
    main()
