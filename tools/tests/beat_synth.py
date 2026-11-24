#!/usr/bin/env python3
"""
beat_synth.py - tests the tempo lock on a controlled, messy track: a 124 BPM kick with extra off-beat hits,
missed kicks and noise bursts (what voice chat or game sounds over music do). Compares the old lock with the grid
lock at a few strictness levels. No sound card needed.

  py tools/tests/beat_synth.py
"""
import os
import sys

import numpy as np

sys.argv = [sys.argv[0]]
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "light_sync"))  # the program
import light_sync as ms  # noqa: E402
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from beat_lab import run  # noqa: E402


def track(seconds=40, bpm=124, extra=0.2, missed=0.15, seed=7):
    rate, period = ms.RATE, 60 / bpm
    rng = np.random.default_rng(seed)
    x = 0.02 * rng.standard_normal(seconds * rate)
    tt = np.arange(int(0.25 * rate)) / rate
    kick = np.sin(2 * np.pi * 60 * tt) * np.exp(-tt * 18)
    hits = []
    b = 0.0
    while b < seconds - 0.3:
        if rng.random() > missed:
            hits.append((b, 0.6))
        if rng.random() < extra:
            hits.append((b + period / 2 + rng.normal(0, 0.02), 0.45))  # an off-beat hit
        b += period
    for t0, amp in hits:
        i = int(t0 * rate)
        x[i:i + len(kick)] += amp * kick[:len(x) - i]
    for _ in range(seconds // 3):  # noise bursts (talking, game sounds)
        i = rng.integers(0, len(x) - rate // 4)
        x[i:i + rate // 4] += 0.15 * rng.standard_normal(rate // 4)
    return x


def main():
    audio = track()
    print("controlled track: 124 BPM, 20 % extra off-beats, 15 % missed kicks, noise bursts\n")
    print(f"{'setting':42} {'beats/min':>9} {'locked':>7} {'BPM':>5}")
    for bpm_true in (124, 96, 140):
        audio = track(bpm=bpm_true)
        print(f"-- {bpm_true} BPM")
        for label, grid, strict in (("old lock", False, 0.6), ("grid lock, strictness 0.5", True, 0.5),
                                    ("grid lock, strictness 0.45", True, 0.45), ("grid lock, strictness 0.4", True, 0.4)):
            ms.Ear.GRID_MIN = strict
            rate, locked, bpm = run(audio, 1.4, 0.003, False, grid)
            print(f"{label:42} {rate:9.0f} {locked:6.0f}% {bpm:5}")


if __name__ == "__main__":
    main()
