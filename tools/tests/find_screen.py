#!/usr/bin/env python3
"""
find_screen.py - finds where the TV/monitor is in the camera picture, for TV_BOX (the part Light Sync reads).

Privacy: works on numbers only. It grabs ~30 frames over ~15 s, keeps a tiny grayscale detail map in memory,
and prints coordinates. No picture is saved or shown. Run it while the screen shows something that MOVES
(a video, a game): the screen is the area whose fine detail keeps changing; walls, furniture and the soft
glow of the lights barely do.

  py tools/tests/find_screen.py            # measure and suggest a TV_BOX
  py tools/tests/find_screen.py --apply    # ... and save it in Home Assistant (input_text.light_sync_tv_box), which both
                                     #     the laptop and the Pi read every few seconds
  py tools/tests/find_screen.py --frames 40
  py tools/tests/find_screen.py --room --apply   # the TV in the room camera's picture (ROOM_CAM_URL, Oct 27): Room guard
                                                 #     and Lights follow me leave it out (input_text.light_sync_room_tv_box)
  py tools/tests/find_screen.py --quad --apply   # the screen's four corners instead of a box (Oct 28: the TV camera sees
                                                 #     it from below, a trapezoid; a box would take in the wall)
"""
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "light_sync"))  # the program
sys.argv = [sys.argv[0]] + [a for a in sys.argv[1:]]
import light_sync as ms  # noqa: E402  (camera login + frame grab, same as Light Sync)

W, H = 160, 90  # analysis grid


def frames(n, gap):
    import cv2
    auth = ms.stream_auth()
    headers = {"Authorization": f"Bearer {ms.HA_TOKEN}"} if "/api/camera_proxy/" in ms.SNAPSHOT_URL else {}
    out = []
    for _ in range(n):
        status, ctype, data = ms.fetch_frame(ms.SNAPSHOT_URL, auth, headers)
        img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_GRAYSCALE) if data else None
        if img is None:
            sys.exit(f"camera didn't return a picture (HTTP {status}, {ctype})")
        small = cv2.resize(img, (W, H), interpolation=cv2.INTER_AREA).astype(np.float32)
        out.append(np.abs(cv2.Laplacian(small, cv2.CV_32F, ksize=3)))  # fine detail only
        del img, data
        time.sleep(gap)
    return np.stack(out)


def corners(stack):
    """The screen's four corners from the detail maps (Oct 28: the camera in front of the TV sees it from below; in
    15 s not every part of the screen moves, so the most-changing 15 s window counts for each cell). The outline
    (convex hull) of the moving area, cut to four corners, ordered round from the top-left, pulled in 6 %.
    (quad as fractions, the outline as a W x H mask), or (None, None)."""
    import cv2
    win = max(4, len(stack) // 6)
    change = np.max([stack[i:i + win].std(axis=0) for i in range(0, len(stack) - win + 1, win)], axis=0)
    change = cv2.GaussianBlur(change, (5, 5), 0)
    mask = (change > max(np.percentile(change, 80), 3 * float(np.median(change)) + 1e-6)).astype(np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask)
    if count < 2:
        return None, None
    blob = (labels == 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))).astype(np.uint8)
    hull = cv2.convexHull(cv2.findNonZero(blob))
    outline = np.zeros_like(blob)
    cv2.fillPoly(outline, [hull], 1)
    peri, pts = cv2.arcLength(hull, True), None
    for eps in np.linspace(0.005, 0.2, 60):
        ap = cv2.approxPolyDP(hull, eps * peri, True).reshape(-1, 2).astype(float)
        if len(ap) == 4:
            pts = ap
            break
    if pts is None:
        return None, None
    cx, cy = pts.mean(axis=0)
    pts = pts[np.argsort(np.arctan2(pts[:, 1] - cy, pts[:, 0] - cx))]  # round the picture (y points down)
    pts = np.roll(pts, -int(np.argmin(pts.sum(axis=1))), axis=0)    # from the top-left
    quad = tuple((((x + 0.5) + (cx - x) * 0.06) / W, ((y + 0.5) + (cy - y) * 0.06) / H) for x, y in pts)
    return quad, outline.astype(bool)


def main():
    import cv2
    n = int(sys.argv[sys.argv.index("--frames") + 1]) if "--frames" in sys.argv else (150 if "--quad" in sys.argv else 30)
    room = "--room" in sys.argv  # the room camera: where the TV is in its picture (left out of Room guard / Lights follow me)
    if room:
        if not ms.ROOM_CAM_URL:
            sys.exit("ROOM_CAM_URL is not set (run from start_lights.bat's settings, or set it)")
        ms.SNAPSHOT_URL = ms.ROOM_CAM_URL  # frames() and the login read this one
    if not ms.SNAPSHOT_URL:
        sys.exit("SNAPSHOT_URL is not set (run from start_lights.bat's settings, or set it)")
    print(f"Grabbing {n} frames over ~{n * 0.5:.0f} s - keep something moving on the screen...", flush=True)
    stack = frames(n, 0.5)
    change = stack.std(axis=0)                       # how much the detail changes over time, per cell
    change = cv2.GaussianBlur(change, (5, 5), 0)
    thr = max(np.percentile(change, 90), 3 * float(np.median(change)) + 1e-6)
    mask = (change > thr).astype(np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask)
    if count < 2:
        sys.exit("Nothing on screen was moving - play a video and run again.")
    best = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    x, y, w, h, area = stats[best]
    strength = float(change[labels == best].mean() / (np.median(change) + 1e-6))
    # a little inside the edges: the bezel and the light spilling around the screen aren't the picture
    ix, iy = w * 0.1, h * 0.1
    box = ((x + ix) / W, (y + iy) / H, (x + w - ix) / W, (y + h - iy) / H)
    print(f"Screen found: {w}x{h} cells of {W}x{H} ({100 * area / (W * H):.0f}% of the picture), "
          f"moving {strength:.1f}x more than the rest")
    ms.poll_helpers()  # the box Light Sync uses now (from Home Assistant if set there)
    cur, helper, name = (ms.ROOM_TV_BOX, ms.HA_ROOM_TV_BOX, "ROOM_TV_BOX") if room else (ms.TV_BOX, ms.HA_TV_BOX, "TV_BOX")
    cur = cur or (0.0, 0.0, 0.0, 0.0)
    cx1, cy1, cx2, cy2 = (int(cur[0] * W), int(cur[1] * H), int(cur[2] * W), int(cur[3] * H))
    inside = float((labels[cy1:cy2, cx1:cx2] == best).mean()) if cx2 > cx1 and cy2 > cy1 else 0.0
    print(f"Current {name} {','.join(f'{v:.2f}' for v in cur)}: {100 * inside:.0f}% of it is on the screen")
    print(f"Suggested {name}={','.join(f'{v:.2f}' for v in box)}")
    value = ",".join(f"{v:.2f}" for v in box)
    if "--quad" in sys.argv and not room:  # the four corners, and how well they fit
        quad, outline = corners(stack)
        if quad is None:
            sys.exit("Couldn't fit four corners - play a bright, moving video and run again.")
        inq = ms.quad_cells(quad, W, H)
        value = ",".join(f"{v:.2f}" for pt in quad for v in pt)
        print(f"Four corners (top-left, top-right, bottom-right, bottom-left): {value}")
        print(f"  {100 * float(outline[inq].mean()):.0f}% of that shape is inside the screen's outline; it covers "
              f"{100 * float(inq[outline].mean()):.0f}% of it (pulled in a little, off the bezel)")
        print("  outline map (# screen, * inside the corners):")
        for y in range(0, H, 3):
            print("  " + "".join("*" if inq[y:y + 3, x:x + 2].mean() > 0.5 else "#" if outline[y:y + 3, x:x + 2].mean() > 0.5
                                 else "." for x in range(0, W, 2)))
    if strength < 2.5:
        print("(weak result: the screen may be dark or still - try again with a bright moving video)")
    elif "--apply" in sys.argv:
        r = ms.session.post(f"{ms.HA_URL}/api/services/input_text/set_value", timeout=5,
                            json={"entity_id": helper, "value": value})
        print(f"Saved in Home Assistant ({helper} = {value}): HTTP {r.status_code}")


if __name__ == "__main__":
    main()
