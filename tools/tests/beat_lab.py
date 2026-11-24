#!/usr/bin/env python3
"""
beat_lab.py - compares beat-detection settings on the same real audio (what the laptop is playing right now).

Records N seconds of the laptop's sound (loopback, kept in memory only, never saved), then runs Light Sync's beat
detector over it with different settings and prints, for each: beats per minute caught, how often the tempo is
locked (steady), and the BPM it settled on.

  py tools/tests/beat_lab.py            # 30 s
  py tools/tests/beat_lab.py 45
"""
import os
import sys
import warnings

import numpy as np

warnings.filterwarnings("ignore")
sys.argv = [sys.argv[0]] + sys.argv[1:]
SECONDS = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 30
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "light_sync"))  # the program
import light_sync as ms  # noqa: E402


def record(seconds):
    import soundcard as sc
    spk = sc.default_speaker()
    parts = []
    with sc.get_microphone(spk.id, include_loopback=True).recorder(samplerate=ms.RATE * 2, channels=1) as r:
        for _ in range(int(seconds * ms.RATE / ms.BLOCK)):  # small chunks, exactly like Light Sync (no gaps)
            parts.append(r.record(numframes=ms.BLOCK * 2)[:, 0])
    x = np.concatenate(parts)
    return x[:len(x) // 2 * 2].reshape(-1, 2).mean(axis=1)  # 22.05 kHz like Light Sync


def run(audio, sens, gate, onset, grid=True):
    ms.SENSITIVITY = sens
    ear = ms.Ear()
    ear.gate, ear.onset = gate, onset
    t, locked, n, bpms = 0.0, 0, 0, []
    beats = 0
    for i in range(0, len(audio) - ms.BLOCK, ms.BLOCK):
        t += ms.BLOCK / ms.RATE
        ear.process(audio[i:i + ms.BLOCK], now=1000 + t)
        if ear.pop_beat():
            beats += 1
        if i % (ms.BLOCK * 8) == 0:  # ~5 checks a second, like the main loop's view of it
            n += 1
            per = ear.period(1000 + t) if grid else period_at(ear, 1000 + t)
            if per:
                locked += 1
                bpms.append(60 / per)
    return beats * 60 / t, 100 * locked / max(1, n), (int(np.median(bpms)) if bpms else 0)


def period_at(ear, now):
    """Ear.period() but at the recording's own clock."""
    t = list(ear.beat_times)
    if len(t) < 5 or now - t[-1] > 2.0:
        return None
    gaps = np.diff(t[-9:])
    m = float(np.median(gaps))
    if not 0.3 < m < 1.0:
        return None
    good = gaps[np.abs(gaps - m) < 0.12 * m]
    return float(good.mean()) if len(good) >= 0.7 * len(gaps) else None


def main():
    print(f"Recording {SECONDS} s of what the laptop is playing (memory only)...", flush=True)
    audio = record(SECONDS)
    print(f"sound level (RMS): {np.sqrt(np.mean(audio ** 2)):.4f}\n")
    print(f"{'setting':50} {'beats/min':>9} {'locked':>7} {'BPM':>5}")
    for label, sens, gate, grid, strict in (
            ("old lock, sensitivity 2.0 (before today)", 2.0, 0.003, False, 0.6),
            ("grid lock 60-150 BPM, sensitivity 1.4", 1.4, 0.0005, True, 0.6),
            ("grid lock 60-150 BPM, sensitivity 1.4, forgiving", 1.4, 0.0005, True, 0.5),
            ("grid lock 60-150 BPM, sensitivity 1.3, forgiving", 1.3, 0.0005, True, 0.5)):
        ms.Ear.GRID_MIN = strict
        bpm_rate, locked, bpm = run(audio, sens, gate, False, grid)
        print(f"{label:50} {bpm_rate:9.0f} {locked:6.0f}% {bpm:5}")


if __name__ == "__main__":
    main()
