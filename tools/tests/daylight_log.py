#!/usr/bin/env python3
"""
daylight_log.py - how the room camera sees the window's daylight, as numbers only, to set up lights that follow it (Nov 2,
owner: "make it auto from the light of the window of my room"). Every 2 minutes: the room camera's own exposure time (it
exposes longer as the room gets darker), the picture's overall brightness (median, 90th and 99th percentile of grey), the sun's
height, and how bright our own lights are, so daylight and lamps can be told apart. No picture is kept, shown or sent.

  py tools/tests/daylight_log.py            -> logs/daylight.csv (stop with Ctrl+C)
"""
import csv
import os
import sys
import time

import numpy as np
import requests

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import export_ha as ex  # noqa: E402

os.environ.setdefault("ROOM_CAM_URL", "http://192.168.1.10:8081/")
os.environ.setdefault("SNAPSHOT_URL", "http://192.168.1.10:8082/")
os.environ.setdefault("SNAP_USER", "ha-stream")
os.environ.setdefault("ROLE", "probe")
sys.argv = sys.argv[:1]
sys.path.insert(0, os.path.join(ROOT, "light_sync"))
import light_sync as ms  # noqa: E402  (the camera login, the same as Light Sync)

HA = "http://192.168.1.10:8123"
CAM = os.environ["ROOM_CAM_URL"]
OUT = os.path.join(ROOT, "logs", "daylight.csv")
EVERY_S = 120


def headers():
    return {"Authorization": "Bearer " + ex.token()}


def frame_grey():
    """One picture from the room camera -> grey numbers, then dropped."""
    import cv2
    r = requests.get(CAM, auth=ms.stream_auth(CAM), stream=True, timeout=15)
    buf, grey = b"", None
    for chunk in r.iter_content(16384):
        buf += chunk
        a = buf.find(b"\xff\xd8")
        b = buf.find(b"\xff\xd9", a + 2) if a >= 0 else -1
        if a >= 0 and b > a:
            grey = cv2.imdecode(np.frombuffer(buf[a:b + 2], np.uint8), cv2.IMREAD_GRAYSCALE)
            break
    r.close()
    return grey


def exposure():
    """The room camera's exposure time (0.1 ms units) and mode, from the Pi add-on."""
    ws = ex.WebSocket(HA)
    ws.sock.settimeout(30)
    ws.recv()
    ws.send({"type": "auth", "access_token": ex.token()})
    ws.recv()
    info = ws.call(type="supervisor/api", endpoint="/addons/local_light_sync/info", method="get")
    sess = ws.call(type="supervisor/api", endpoint="/ingress/session", method="post")["session"]
    d = requests.get(HA + info["ingress_url"].rstrip("/") + "/cams", cookies={"ingress_session": sess}, timeout=20).json()
    cam = next(c for c in d["cams"] if c.get("which") == "room")
    v = {p["name"]: p["value"] for p in cam["picture"]}
    return v.get("Exposure time"), v.get("Exposure mode"), v.get("Gain")


def main():
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    new = not os.path.exists(OUT)
    with open(OUT, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["time", "exposure", "exp_mode", "gain", "median", "p90", "p99", "sun_elev", "corner", "top", "tvd", "weather"])
        while True:
            t0 = time.time()
            try:
                g = frame_grey()
                exp, mode, gain = exposure()
                st = {s["entity_id"]: s for s in requests.get(f"{HA}/api/states", headers=headers(), timeout=20).json()}
                bri = lambda e: round((st[e]["attributes"].get("brightness") or 0) / 2.55) if st.get(e, {}).get("state") == "on" else 0
                row = [time.strftime("%Y-%m-%d %H:%M:%S"), exp, mode, gain,
                       *(round(float(np.percentile(g, q)), 1) for q in (50, 90, 99)),
                       round(st["sun.sun"]["attributes"].get("elevation", 0), 1),
                       bri("light.h6004_2"), bri("light.h6004"), bri("light.chambre_tvd"), st.get("weather.forecast_maison", {}).get("state")]
                del g
                w.writerow(row)
                f.flush()
                print(*row, sep="  ", flush=True)
            except Exception as e:
                print("missed:", type(e).__name__, str(e)[:80], flush=True)
            time.sleep(max(5, EVERY_S - (time.time() - t0)))


if __name__ == "__main__":
    main()
