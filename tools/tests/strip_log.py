#!/usr/bin/env python3
"""
strip_log.py - logs what TvD (the strip behind the TV) is sent, then looks for flashes (Oct 27, owner during a film:
"each moment it flashes", then "run a test on the strip, log for 2 minutes").

From the laptop's live view (http://127.0.0.1:8089/state), about 10 times a second: every segment's colour as asked,
TvD's brightness, the Cinema look's action level, Corner / Top, the voice cue; and every frame actually sent
(diag.tvd: time, kind, level). Written to logs/strip_<time>.csv. Looked for:

  jump       a segment's brightness up 30 %+ (and 8+ of 255) within 0.2 s, or its colour turning 60+ degrees
  flash      a jump up that comes back down within 1 s
  gap        no frame sent for 0.7 s+ (the strip can fall out of streaming and show its own colour)
  one        a frame with one colour for the whole strip between per-segment frames
  restart    streaming switched on again (stream_on_at changed): on a strip already streaming that blinks it dark
             for ~0.1 s (Oct 28, the owner's video); should only happen when streaming starts or the level changes.
             (The TV camera can't see such a blink: at 12 ms it sees the LEDs' own flicker on the wall, up to 90 %.)

  py tools/tests/strip_log.py          # 2 minutes
  py tools/tests/strip_log.py 60       # 60 s
"""
import colorsys
import csv
import os
import sys
import time

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
URL = "http://127.0.0.1:8089/state"


def bright(c):
    return max(c) if c else 0


def hue(c):
    return colorsys.rgb_to_hsv(*(max(0, min(255, v)) / 255 for v in c))[0] * 360


def sat(c):
    return colorsys.rgb_to_hsv(*(max(0, min(255, v)) / 255 for v in c))[1]


def main():
    secs = float(sys.argv[1]) if len(sys.argv) > 1 else 120
    path = os.path.join(ROOT, "logs", time.strftime("strip_%Y%m%d_%H%M%S.csv"))
    rows, sent, restarts, t0 = [], {}, [], time.time()
    last_diag, stream_on = 0.0, None
    print(f"Logging TvD for {secs:.0f} s ...", flush=True)
    while time.time() - t0 < secs:
        t = time.time()
        try:
            d = requests.get(URL, timeout=2).json()
        except requests.RequestException as e:
            rows.append({"t": round(t - t0, 2), "error": str(e)[:60]})
            time.sleep(0.5)
            continue
        segs = d.get("tvd_segments") or []
        a = d.get("tv_action") or {}
        L = d.get("lights") or {}
        rows.append({"t": round(t - t0, 2), "mode": d.get("mode"), "tvd_bri": (L.get("screen") or {}).get("bri"),
                     "action": a.get("raw"), "bulbs_level": a.get("level"), "tvd_level": a.get("tvd"),
                     "corner": (L.get("beat") or {}).get("bri"), "top": (L.get("wash") or {}).get("bri"),
                     "voice": (d.get("voice") or {}).get("cue") if t - (d.get("voice") or {}).get("at", 0) < 5 else "",
                     "segs": segs})
        diag = d.get("diag") or {}
        if t - last_diag > 1.0:  # the sent frames (the last ~60) and streaming restarts
            last_diag = t
            for ft, kind, lvl in diag.get("tvd", []):
                if ft >= t0:
                    sent[ft] = (kind, lvl)
            so = diag.get("stream_on_at")
            if stream_on is not None and so != stream_on and so and so >= t0:
                restarts.append(round(so - t0, 2))
            stream_on = so
        time.sleep(max(0.0, 0.1 - (time.time() - t)))

    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        n = max((len(r.get("segs") or []) for r in rows), default=0)
        w.writerow(["t", "mode", "tvd_bri", "action", "bulbs_level", "tvd_level", "corner", "top", "voice"]
                   + [f"seg{i + 1}" for i in range(n)])
        for r in rows:
            w.writerow([r.get(k, "") for k in ("t", "mode", "tvd_bri", "action", "bulbs_level", "tvd_level", "corner",
                                                "top", "voice")] + ["/".join(map(str, c)) for c in r.get("segs") or []])
        w.writerow([])
        w.writerow(["sent_t", "kind", "level"])
        for ft in sorted(sent):
            w.writerow([round(ft - t0, 2), *sent[ft]])

    # what happened
    ok_rows = [r for r in rows if r.get("segs")]
    jumps, flashes = [], []
    for i in range(1, len(ok_rows)):
        a, b = ok_rows[i - 1], ok_rows[i]
        if b["t"] - a["t"] > 0.3 or len(a["segs"]) != len(b["segs"]):
            continue
        for s, (ca, cb) in enumerate(zip(a["segs"], b["segs"])):
            ba, bb = bright(ca), bright(cb)
            dh = abs((hue(cb) - hue(ca) + 180) % 360 - 180)
            up = bb - ba >= 8 and bb >= 1.3 * max(1, ba)
            turn = dh >= 60 and min(ba, bb) >= 20 and min(sat(ca), sat(cb)) >= 0.25  # near-grey: its hue is noise
            if up or turn:
                jumps.append((b["t"], s + 1, ca, cb, "brighter" if up else f"colour {dh:.0f} deg"))
                if up:  # back down within 1 s?
                    for c in ok_rows[i + 1:]:
                        if c["t"] - b["t"] > 1.0:
                            break
                        if len(c["segs"]) > s and bright(c["segs"][s]) <= ba + 0.3 * (bb - ba):
                            flashes.append((b["t"], s + 1, ba, bb, round(c["t"] - b["t"], 1)))
                            break
    ts = sorted(sent)
    gaps = [(round(x - t0, 2), round(y - x, 2)) for x, y in zip(ts, ts[1:]) if y - x >= 0.7]
    ones = [round(ft - t0, 2) for ft in ts if sent[ft][0] != "seg"]
    voice = sorted({r["t"] for r in rows if r.get("voice")})
    errs = [r for r in rows if r.get("error")]
    bri = [r["tvd_bri"] for r in ok_rows if r.get("tvd_bri") is not None]
    steps = [abs(y["tvd_bri"] - x["tvd_bri"]) for x, y in zip(ok_rows, ok_rows[1:])
             if x.get("tvd_bri") is not None and y.get("tvd_bri") is not None]
    modes = sorted({r.get("mode") for r in ok_rows})

    print(f"\n{len(rows)} readings, {len(sent)} frames sent ({len(sent) / max(1, secs):.1f}/s), mode {', '.join(map(str, modes))}")
    if bri:
        print(f"TvD brightness {min(bri)}-{max(bri)}/255, biggest change between readings {max(steps or [0])}")
    print(f"jumps (a segment 30 %+ brighter or turning 60+ degrees in ~0.1 s): {len(jumps)}")
    for j in jumps[:8]:
        print(f"   {j[0]:6.1f} s  segment {j[1]:2d}  {j[2]} -> {j[3]}  ({j[4]})")
    print(f"flashes (up and back down within 1 s): {len(flashes)}")
    for fl in flashes[:8]:
        print(f"   {fl[0]:6.1f} s  segment {fl[1]:2d}  {fl[2]} -> {fl[3]} and back in {fl[4]} s")
    print(f"gaps without a frame (0.7 s+): {len(gaps)} {gaps[:6]}")
    print(f"one-colour frames: {len(ones)} {ones[:6]}")
    print(f"streaming switched on again: {len(restarts)} {restarts[:6]}")
    print(f"voice cue showing: {'at ' + ', '.join(f'{v:.0f} s' for v in voice[:6]) if voice else 'no'}")
    if errs:
        print(f"live view didn't answer: {len(errs)} times")
    print(f"\nLog: {path}")


if __name__ == "__main__":
    main()
