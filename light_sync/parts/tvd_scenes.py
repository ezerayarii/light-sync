"""TvD's own scenes for Party Pro DJ (Nov 6, owner: TvD is the leader and does "the crazy stuff"; Light Sync makes
the scenes instead of Govee's, so Corner and Top can follow the strip).

render() gives each of TvD's parts a colour (0-255, brightness in it) for this frame, from the music: the time since
the last beat, the beat period, the loudness and the Colours palette (pal(x) -> rgb, flow_pos moves it along).
Parts are numbered along the strip (0 = its start). Flashing is allowed on TvD (owner's choice, Nov 6)."""

import math

import numpy as np

MOODS = {"calm": ["breathe", "drift"], "groove": ["wave", "comet"], "high": ["burst", "chase"]}
PEAK = ["strobe"]  # only at real peaks (party_dj.py: loud and fast): the Nov 6 camera test saw it as most of TvD's jumps


def render(name, now, n, beat_age, per, energy, pal, flow_pos):
    per = per or 0.5
    i = np.arange(n, dtype=float)
    x = i / max(1, n - 1)  # 0..1 along the strip
    beat = max(0.0, beat_age)
    if name == "breathe":  # the whole strip breathes in one colour, one breath every 4 beats
        lv = 0.35 + 0.65 * (0.5 - 0.5 * math.cos(2 * math.pi * now / (4 * per)))
        return np.repeat(np.array(pal(flow_pos), float)[None, :] * lv, n, axis=0)
    if name == "drift":  # the palette slides slowly along the strip
        cols = np.array([pal(flow_pos + 0.6 * xi + 0.05 * now) for xi in x], float)
        return cols * (0.55 + 0.45 * energy)
    if name == "wave":  # a bright band runs along the strip, once every 2 beats
        phase = (now / (2 * per)) % 1.0
        lv = 0.2 + 0.8 * np.maximum(0.0, np.cos(2 * math.pi * (x - phase))) ** 2
        cols = np.array([pal(flow_pos + 0.3 * xi) for xi in x], float)
        return cols * lv[:, None]
    if name == "comet":  # every beat a comet shoots from the strip's start to its end, with a tail
        head = min(1.2, beat / max(0.15, 0.6 * per))
        d = head - x
        lv = np.where(d >= 0, np.exp(-d * 6.0), 0.0) * (1.0 if head <= 1.0 else max(0.0, 1 - (head - 1.0) * 5))
        lv = np.maximum(lv, 0.12)
        cols = np.array([pal(flow_pos + 0.15 * xi) for xi in x], float)
        return cols * lv[:, None]
    if name == "burst":  # every beat the light bursts from the strip's middle out to its ends, then fades
        r = beat / 0.25  # how far it has spread (0 = middle, 1 = the ends)
        dist = np.abs(x - 0.5) * 2
        lv = np.where(dist <= r, 1.0, 0.15) * math.exp(-beat * 3.0) + 0.15
        cols = np.array([pal(flow_pos + 0.2 * di) for di in dist], float)
        return cols * np.clip(lv, 0, 1)[:, None]
    if name == "chase":  # every other part lights, swapping on each beat (two palette colours)
        k = int(now / per) % 2
        a, b = np.array(pal(flow_pos), float), np.array(pal(flow_pos + 0.5), float)
        on = (i.astype(int) % 2) == k
        return np.where(on[:, None], a, b * 0.15)
    if name == "strobe":  # a white hit on every beat, the palette colour between (TvD only)
        if beat < 0.06 and energy > 0.3:
            return np.full((n, 3), 255.0)
        return np.repeat(np.array(pal(flow_pos), float)[None, :] * (0.3 + 0.5 * energy), n, axis=0)
    return np.repeat(np.array(pal(flow_pos), float)[None, :], n, axis=0)
