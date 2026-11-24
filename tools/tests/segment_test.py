#!/usr/bin/env python3
"""
segment_test.py - can TvD (Govee H612F RGBIC strip) show several colors at once over the LAN?

Sends Govee's DreamView/Razer streaming packets (LAN command "razer") and measures, with the camera, the light glow
around the screen: numbers only (left / right hue of the area around TV_BOX), no picture kept or shown.
Phases: normal red (reference) -> razer all blue -> razer rainbow -> razer off.
TvD should be out of Light Sync while this runs (selftest/caller handles that).

  py tools/tests/segment_test.py 192.168.1.31
"""
import base64
import colorsys
import json
import os
import socket
import sys
import time

import numpy as np

sys.argv = [sys.argv[0]] + sys.argv[1:]
IP = sys.argv[1] if len(sys.argv) > 1 else "192.168.1.31"
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "light_sync"))  # the program
import light_sync as ms  # noqa: E402

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)


def send(cmd, data):
    sock.sendto(json.dumps({"msg": {"cmd": cmd, "data": data}}).encode(), (IP, 4003))


def razer(body):
    b = bytes(body)
    cs = 0
    for x in b:
        cs ^= x
    send("razer", {"pt": base64.b64encode(b + bytes([cs])).decode()})


def frame(colors, gradient=0):
    flat = [int(v) for c in colors for v in c]
    razer([0xBB, 0x00, 2 + 3 * len(colors), 0xB0, gradient, len(colors)] + flat)


def glow():
    """Mean hue/saturation of the left and right parts of the area around the screen (camera, numbers only)."""
    import cv2
    st, ct, data = ms.fetch_frame(ms.SNAPSHOT_URL, ms.stream_auth(), {})
    img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    del data
    h, w = img.shape[:2]
    ms.poll_helpers()
    x1, y1, x2, y2 = ms.TV_BOX
    bw, bh = x2 - x1, y2 - y1
    X1, X2 = max(0, x1 - 0.6 * bw), min(1, x2 + 0.6 * bw)
    Y1, Y2 = max(0, y1 - 0.6 * bh), min(1, y2 + 0.3 * bh)
    out = {}
    for side, (a, b) in {"left": (X1, x1), "right": (x2, X2)}.items():
        crop = img[int(Y1 * h):int(Y2 * h), int(a * w):int(b * w)]
        bgr = crop.reshape(-1, 3).mean(axis=0)
        hh, ss, vv = colorsys.rgb_to_hsv(bgr[2] / 255, bgr[1] / 255, bgr[0] / 255)
        out[side] = (round(hh * 360), round(ss, 2), round(vv, 2))
    del img
    return out


def hold(seconds, every=None):
    end = time.time() + seconds
    while time.time() < end:
        if every:
            every()
        time.sleep(0.1)


def main():
    if not ms.SNAPSHOT_URL:
        ms.SNAPSHOT_URL = "http://192.168.1.10:8081/"
    send("turn", {"value": 0})
    hold(2.5)
    print("TvD off (baseline):        ", glow())
    send("turn", {"value": 1})
    send("brightness", {"value": 100})
    time.sleep(0.5)
    for name, rgb in (("red", (255, 0, 0)), ("green", (0, 255, 0))):
        send("colorwc", {"color": dict(zip("rgb", rgb)), "colorTemInKelvin": 0})
        hold(2.5)
        print(f"normal {name:6} (reference):  ", glow())
    razer([0xBB, 0x00, 0x01, 0xB1, 0x01])      # DreamView/Razer streaming on
    n = int(os.getenv("SEGMENTS", "10"))
    a = [(255, 0, 0)] * (n // 2) + [(0, 255, 0)] * (n - n // 2)
    b = [(0, 255, 0)] * (n // 2) + [(255, 0, 0)] * (n - n // 2)
    for _ in range(2):
        hold(3, lambda: frame(a))
        print("razer red | green:         ", glow())
        hold(3, lambda: frame(b))
        print("razer green | red:         ", glow())
    razer([0xBB, 0x00, 0x01, 0xB1, 0x00])      # streaming off
    print("done (streaming off)")


if __name__ == "__main__":
    main()
