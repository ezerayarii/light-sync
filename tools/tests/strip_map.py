#!/usr/bin/env python3
"""
strip_map.py - where each part (segment) of TvD is, seen by the TV camera, so that in TV mode each part takes its
colour from the nearest edge of the screen (Oct 28, owner: the strip starts at the top of the TV, runs right and ends
on the desk; nothing on the left - it was read as a line under the screen).

Numbers only, never a picture: TvD is taken out of Light Sync for about a minute (its dashboard switch), all parts at a
dim base, then each part brighter in turn (soft fades); the camera's grey numbers before and during say where the wall
lit up. That place, seen through the screen's four corners, gives the screen edge the part belongs to. Saved in Home
Assistant (input_text.light_sync_strip_map, created if missing): Light Sync reads it in TV mode. TvD goes back to Light
Sync at the end, also after an error. Pause the film first (the screen itself is left out anyway).

  py tools/tests/strip_map.py           # measure, show, save
  py tools/tests/strip_map.py --dry     # measure and show, save nothing
"""
import os
import sys
import threading
import time

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for line in open(os.path.join(ROOT, "start_lights.bat"), encoding="utf-8"):  # the camera's address and login, as Light Sync
    if line.lower().startswith("set ") and "=" in line:
        k, v = line[4:].strip().split("=", 1)
        if k in ("SNAPSHOT_URL", "SNAP_USER", "HA_URL", "SCREEN_SEGMENTS", "LIGHT_SCREEN"):
            os.environ.setdefault(k, v)
sys.path.insert(0, os.path.join(ROOT, "light_sync"))
sys.path.insert(0, os.path.join(ROOT, "vendor"))
import cv2  # noqa: E402
import requests  # noqa: E402

import light_sync as ms  # noqa: E402

HELPER = "input_text.light_sync_strip_map"
W, H = 160, 90
BASE, LIT = 10, 255  # the parts' level while waiting / while lit (of 255; Oct 28: 160 was too faint with the room lit)


def grey(cam, n=3, gap=0.15):
    """The newest n pictures as 160 x 90 grey numbers, averaged."""
    out, last = [], cam.at
    t0 = time.time()
    while len(out) < n and time.time() - t0 < 5:
        if cam.at > last and cam.jpg:
            last = cam.at
            img = cv2.imdecode(np.frombuffer(cam.jpg, np.uint8), cv2.IMREAD_GRAYSCALE)
            if img is not None:
                out.append(cv2.resize(img, (W, H), interpolation=cv2.INTER_AREA).astype(np.float32))
            time.sleep(gap)
        else:
            time.sleep(0.03)
    return np.mean(out, axis=0) if out else None


def main():
    dry = "--dry" in sys.argv
    st = requests.get("http://127.0.0.1:8089/state", timeout=3).json()
    ctrl = (st.get("control") or {}).get(ms.LIGHT_SCREEN, "")
    if "LAN" not in ctrl:
        sys.exit(f"TvD isn't on LAN control ({ctrl or 'Light Sync not running?'})")
    ip = ctrl.split()[-1]
    n = ms.SCREEN_SEGMENTS or 10
    ms.poll_helpers()
    quad = ms.TV_QUAD
    if not quad:
        sys.exit("No four-corner TV area set (find_screen.py --quad --apply first)")
    src = np.array([(x * W, y * H) for x, y in quad], np.float32)
    to_flat = cv2.getPerspectiveTransform(src, np.array([(0, 0), (1, 0), (1, 1), (0, 1)], np.float32))
    screen = ms.quad_cells(quad, W, H)
    screen = cv2.dilate(screen.astype(np.uint8), np.ones((9, 9), np.uint8)).astype(bool)  # the screen and its rim

    cam = ms.CamStream(ms.SNAPSHOT_URL, ms.SNAPSHOT_URL, ms.stream_auth(), {})
    lan = ms.GoveeLan()
    frame = {"cols": [(BASE,) * 3] * n, "on": True}

    def stream():  # the strip wants a frame every so often to stay in streaming mode
        while frame["on"]:
            lan.razer_frame(ip, frame["cols"])
            time.sleep(0.1)

    use = ms.HA_USE["screen"]
    ms.session.post(f"{ms.HA_URL}/api/services/input_boolean/turn_off", json={"entity_id": use}, timeout=5)
    print(f"TvD taken out of Light Sync for the test ({ip}, {n} parts)...", flush=True)
    found = []
    try:
        time.sleep(2.5)  # Light Sync lets go (it switches the strip off)
        lan.power(ip, True)
        time.sleep(0.8)
        lan.razer_mode(ip, True, set_brightness=True, brightness=100)
        threading.Thread(target=stream, daemon=True).start()
        time.sleep(1.5)
        base0 = grey(cam, 6)
        for i in range(n):
            for k in (0.33, 0.66, 1.0):  # soft fade in
                frame["cols"] = [(int(BASE + (LIT - BASE) * k),) * 3 if j == i else (BASE,) * 3 for j in range(n)]
                time.sleep(0.1)
            time.sleep(0.6)  # the camera's delay
            lit = grey(cam, 3)
            for k in (0.66, 0.33, 0.0):  # and out
                frame["cols"] = [(int(BASE + (LIT - BASE) * k),) * 3 if j == i else (BASE,) * 3 for j in range(n)]
                time.sleep(0.1)
            time.sleep(0.4)
            found.append(lit)
        base1 = grey(cam, 6)
    finally:
        frame["on"] = False
        time.sleep(0.2)
        cam.stop()
        ms.session.post(f"{ms.HA_URL}/api/services/input_boolean/turn_on", json={"entity_id": use}, timeout=5)
        print("TvD back in Light Sync.", flush=True)

    base = (base0 + base1) / 2
    drift = float(np.abs(base1 - base0)[~screen].mean())
    rows = []
    for i, lit in enumerate(found):
        d = np.clip(lit - base, 0, None)
        d[screen] = 0
        peak = float(d.max())
        if peak < max(6.0, 4 * drift):
            rows.append((i, None, peak))
            continue
        m = d >= 0.5 * peak
        ys, xs = np.nonzero(m)
        wts = d[m]
        x, y = float((xs * wts).sum() / wts.sum()) + 0.5, float((ys * wts).sum() / wts.sum()) + 0.5
        u, v = cv2.perspectiveTransform(np.array([[[x, y]]], np.float32), to_flat)[0, 0]
        rows.append((i, (x / W, y / H, float(u), float(v)), peak))
    # the screen edge each part takes its colour from: its place seen through the screen's corners, onto the edge
    seen = [r for r in rows if r[1]]
    if len(seen) < 2:
        sys.exit(f"The camera saw only {len(seen)} of {n} parts light up (wall change too small: strengths "
                 f"{[round(r[2], 1) for r in rows]}, drift {drift:.1f}) - nothing saved. Dim the room (the Cinema look on, "
                 "Corner and Top low) and try again.")
    # each seen part's place on the screen's edge as one number going round it: 0-1 the top (left to right), 1-2 the
    # right side (down), 2-3 the bottom (right to left), 3-4 the left side (up)
    def round_of(u, v):
        u, v = min(1.0, max(0.0, u)), min(1.0, max(0.0, v))
        d = {"top": v, "right": 1 - u, "bottom": 1 - v, "left": u}
        side = min(d, key=d.get)
        return {"top": u, "right": 1 + v, "bottom": 3 - u, "left": 4 - v}[side]

    def edge_at(s):
        s %= 4
        return [(s, 0.0), (1.0, s - 1), (3 - s, 1.0), (0.0, 4 - s)][min(3, int(s))]

    # far off the screen the four corners' maths turns wild (Oct 28: the desk's glow far to the right came out
    # top-right and bottom-left): only places within half a screen of it count
    ss = [round_of(pos[2], pos[3]) if pos and -0.5 <= pos[2] <= 1.5 and -0.5 <= pos[3] <= 1.5 else None
          for i, pos, peak in rows]
    # and the parts must go round the screen in the strip's order: the longest run going one way is kept, the rest
    # counts as not seen
    seen_i = [i for i in range(n) if ss[i] is not None]
    best_run = []
    for way in (1, -1):
        for start in range(len(seen_i)):
            run = [seen_i[start]]
            for j in seen_i[start + 1:]:
                step = ((ss[j] - ss[run[-1]] + 2) % 4 - 2) * way
                if -0.1 <= step <= 1.0 * (j - run[-1]):  # on round the same way (a little back: noise)
                    run.append(j)
            if len(run) > len(best_run):
                best_run = run
    ss = [s if i in best_run else None for i, s in enumerate(ss)]
    for i in range(n):  # parts the camera didn't see (behind the TV's edge, on the desk)
        if ss[i] is None:
            prev = next((j for j in range(i - 1, -1, -1) if ss[j] is not None), None)
            nxt = next((j for j in range(i + 1, n) if ss[j] is not None), None)
            if prev is not None and nxt is not None:  # between their seen neighbours, round the edge (the short way)
                d = (ss[nxt] - ss[prev] + 2) % 4 - 2
                ss[i] = (ss[prev] + d * (i - prev) / (nxt - prev)) % 4
            else:  # past the last seen one: on along the strip's way, to the end of that side of the screen
                near = prev if prev is not None else nxt
                seen_on = [j for j in (range(near - 1, -1, -1) if prev is not None else range(near + 1, n))
                           if ss[j] is not None]
                other = seen_on[-1] if seen_on else None  # the farthest seen one: the strip's way, not the noise
                way = 1 if other is None or (ss[near] - ss[other] + 2) % 4 - 2 >= 0 else -1
                ss[i] = (int(ss[near]) + 1.0) % 4 if way > 0 else float(int(ss[near]))
    uv = [edge_at(s) for s in ss]
    where = lambda u, v: ("top" if v <= 0.05 else "bottom" if v >= 0.95 else "middle") + "-" + \
        ("left" if u <= 0.05 else "right" if u >= 0.95 else "centre")
    print(f"\nWall drift between the start and the end: {drift:.1f} (small = a still room)")
    print(" part | lit up at (picture x, y) | strength | screen edge it takes (u across, v down)")
    for (i, pos, peak), (u, v) in zip(rows, uv):
        at = f"{pos[0]:.2f}, {pos[1]:.2f}" if pos else "not seen"
        print(f"  {i + 1:3d} | {at:24s} | {peak:8.1f} | {u:.2f}, {v:.2f}  {where(u, v)}")
    value = ",".join(f"{u:.2f}:{v:.2f}" for u, v in uv)
    if dry:
        print(f"\n(--dry) would save {HELPER} = {value}")
        return
    if requests.get(f"{ms.HA_URL}/api/states/{HELPER}", headers=ms.session.headers, timeout=5).status_code == 404:
        sys.path.insert(0, os.path.join(ROOT, "tools"))
        import export_ha as ex
        ws = ex.WebSocket(ms.HA_URL)
        ws.sock.settimeout(30)
        ws.recv()
        ws.send({"type": "auth", "access_token": ex.token()})
        ws.recv()
        ws.call(type="input_text/create", name="Light Sync strip map", max=255, min=0, mode="text",
                icon="mdi:led-strip-variant")
        print(f"created {HELPER}")
    r = ms.session.post(f"{ms.HA_URL}/api/services/input_text/set_value", json={"entity_id": HELPER, "value": value}, timeout=5)
    print(f"\nSaved {HELPER} = {value} (HTTP {r.status_code})")


if __name__ == "__main__":
    main()
