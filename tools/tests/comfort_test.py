#!/usr/bin/env python3
"""
comfort_test.py - measures how hard the lights are on the eyes, with and without Comfort, on the demo track
(no real lights touched). Per light: flashes per second (brightness jumps of 40+ within 0.1 s), how deep the
brightness pulses (modulation (max-min)/(max+min) over each 1 s window), average color saturation, how fast the
hue moves, and whether there's blue at night.

  py tools/tests/comfort_test.py
"""
import colorsys
import json
import os
import subprocess
import sys
import time
import urllib.request

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def measure(extra_args, seconds=12):
    env = dict(os.environ, VIEW_PORT="8096", SHOW_PORT="0", SNAPSHOT_URL="", STYLE="flow", FLASH="strobe")
    p = subprocess.Popen([sys.executable, os.path.join(ROOT, "light_sync", "light_sync.py"), "--demo", "--no-browser", *extra_args],
                         env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=ROOT)
    try:
        time.sleep(6)
        rows, t0 = [], time.time()
        while time.time() - t0 < seconds:
            rows.append(json.load(urllib.request.urlopen("http://127.0.0.1:8096/state", timeout=3))["lights"])
            time.sleep(0.05)
    finally:
        p.kill()
    out = {}
    for k, name in (("screen", "TvD"), ("beat", "Corner"), ("wash", "Top")):
        bri = np.array([r[k]["bri"] for r in rows], float)
        hsv = [colorsys.rgb_to_hsv(*(c / 255 for c in r[k]["rgb"])) for r in rows]
        sat = float(np.mean([s for h, s, v in hsv]))
        hues = np.array([h for h, s, v in hsv])
        dh = np.abs((np.diff(hues) + 0.5) % 1 - 0.5) * 360 / 0.05
        flashes = int(np.sum(np.diff(bri[::2]) > 40)) / seconds
        mods = [(w.max() - w.min()) / max(1, w.max() + w.min()) for w in np.array_split(bri, seconds)]
        blue = float(np.mean([(170 / 360 < h < 300 / 360) and s > 0.2 for h, s, v in hsv]))
        out[name] = (flashes, 100 * float(np.median(mods)), sat, float(np.median(dh)), blue)
    return out


def main():
    print(f"{'':10}{'light':8}{'flashes/s':>10}{'pulse depth':>12}{'saturation':>11}{'hue speed':>11}")
    for label, args in (("before", ["--raw"]), ("comfort", [])):
        for name, (fl, mod, sat, dh, blue) in measure(args).items():
            print(f"{label:10}{name:8}{fl:10.1f}{mod:11.0f}%{sat:11.2f}{dh:9.0f}°/s")
    print("\n(guidelines: flashes under 3/s and ideally none; pulse depth as low as possible at beat speed;"
          " evenings without blue)")


if __name__ == "__main__":
    main()
