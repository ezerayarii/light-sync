"""The Sound test in the app: which source hears the music best."""

import numpy as np
import sys
import time

from parts.config import (
    AC_WINDOW, AUDIO, BLOCK, RATE,
)
from parts import ears, sources


# Oct 20, owner (music playing on the phone, the lights deaf): "make a test so I know what is working". Every sound
# source listens at once for TEST_S seconds; each says whether it hears music (level, a steady beat, the BPM). Only
# levels and the tempo are kept, nothing is recorded.
TEST_S = 20.0  # mic beats need ~8 s of sound before they can lock (AC_WINDOW)

SOUND_TEST = {}  # the last test, for the app (replaced, never changed in place: live_frame may be reading it)

TEST_EARS = {}   # during a test: "room" / "phone" -> the Ear their sound (POSTed to /room, /phone) also goes to

QUIET_RMS = 0.0006  # below this a source is silent (a muted mic's own noise: ~0.0002)

def _test_ear(loopback=False):
    ear = ears.Ear()
    ear.gate, ear.detector = (0.0005, "bass") if loopback else (0.003, "broad")
    return ear

class _Both:
    """One sound to two Ears (the laptop's sound, heard as Laptop sound and as Phone (Bluetooth) listen to it)."""

    def __init__(self, *ears):
        self.ears = ears

    def process(self, samples, now=None):
        for e in self.ears:
            e.process(samples, now)

def _test_row(name, pick, kind, ear, s, done):
    """One source's result: {"source", "pick" (the Sound source option that uses it), "level" (%), "bpm", "ok", "verdict"}."""
    lv = float(np.mean(s["lv"][-20:])) if s["lv"] else 0.0  # the last ~5 s
    level = int(max(0, min(100, (np.log10(max(lv, 1e-5)) + 4) / 3 * 100)))  # 0.0001 -> 0 %, 0.1 -> 100 %
    lock = s["lock"] / s["n"] if s["n"] else 0.0
    bpm = round(float(np.median(s["bpm"]))) if s["bpm"] else 0
    ok = False
    if kind == "missing":
        verdict = "not available (unplugged, or in use by another app)"
    elif kind == "bt" and not s.get("live"):
        verdict = s["note"]
    elif not s["seen"]:
        verdict = ("no sound from the Pi yet" if kind == "room" else
                   "not listening (tap the mic below; it needs the https address)" if kind == "phone" else "no sound coming in")
    elif lv < QUIET_RMS:
        verdict = ("silent: nothing is playing on the laptop" if kind == "loopback" else
                   f"{sources.BT.get('phone')} is connected, but nothing is playing" if kind == "bt" else
                   "silent: is the MeetUp muted (red light), or the music too far away?" if kind == "room" else
                   "silent: muted, or too far from the music")
    elif lock >= 0.35:
        ok, verdict = True, f"hears music · {bpm} BPM" + (f" (from {sources.BT.get('phone')})" if kind == "bt" else "")
    else:
        verdict = "hears sound, no steady beat yet" if not done else "hears sound, but no steady beat (too quiet, or no clear rhythm)"
    return {"source": name, "pick": pick, "level": level if kind != "missing" and (kind != "bt" or s.get("live")) else None,
            "bpm": bpm if ok else 0, "ok": ok, "verdict": verdict}

def sound_test():
    """Listens to every sound source at once for TEST_S seconds (laptop: its own sound, which includes a phone playing
    over Bluetooth, each microphone, the room mic through the Pi, a phone listening in the app; Pi: the MeetUp mic)."""
    global SOUND_TEST
    t0 = time.time()
    SOUND_TEST = {"state": "running", "at": t0, "until": t0 + TEST_S, "rows": []}
    tests, opened, bt = [], [], None  # tests: [(name, pick, kind, ear)]
    try:
        if AUDIO == "pulse":  # the Pi drives the lights: its MeetUp mic
            ear = _test_ear()
            m = ears.PulseMic(ear)
            m.start()
            opened.append(m)
            tests.append(("Room mic (MeetUp)", None, "mic", ear))
        elif sys.platform == "win32":
            import sounddevice as sd
            ear = _test_ear(loopback=True)
            bt_ear = _test_ear()  # Phone (Bluetooth): the same laptop sound, with mic beats (as that source listens)
            m = sources.LoopbackStream(_Both(ear, bt_ear))
            m.start()
            opened.append(m)
            tests.append((sources.LOOPBACK, sources.LOOPBACK, "loopback", ear))
            tests.append((sources.PHONE_BT, sources.PHONE_BT, "bt", bt_ear))
            TEST_EARS["room"] = _test_ear()
            tests.append(("Room mic (MeetUp, via the Pi)", sources.ROOM_MIC, "room", TEST_EARS["room"]))  # room_wanted() asks the Pi for it now
            for label, idx in sources.list_mics().items():
                if isinstance(idx, int):
                    ear = _test_ear()
                    try:
                        m = sd.InputStream(device=idx, channels=1, samplerate=RATE, blocksize=BLOCK, callback=ear.callback)
                        m.start()
                        opened.append(m)
                        tests.append((label, label, "mic", ear))
                    except Exception:
                        tests.append((label, label, "missing", None))
            if sources.BT.get("state") in (None, "off"):  # Phone (Bluetooth) not picked: is a phone paired at all?
                bt = sources.bt_paired()
        TEST_EARS["phone"] = _test_ear()
        tests.append(("This phone's mic (Light Sync app)", None, "phone", TEST_EARS["phone"]))
        stats = {t[0]: {"lv": [], "lock": 0, "n": 0, "bpm": [], "seen": False} for t in tests}
        done = False
        while not done:
            time.sleep(0.25)
            now = time.time()
            done = now >= t0 + TEST_S
            for name, pick, kind, ear in tests:
                s = stats[name]
                if kind == "bt":
                    s["live"] = sources.BT.get("state") == "connected"  # connected: measured like the others (below)
                if kind == "bt" and not s["live"]:
                    if sources.BT.get("state") not in (None, "off"):
                        s["note"] = sources.bt_label().replace("Phone (Bluetooth): ", "")
                    elif bt is None:
                        s["note"] = "couldn't check Bluetooth"
                    elif bt:
                        s["note"] = f"paired: {', '.join(bt)}. Pick it to play the phone's music on the laptop"
                    else:
                        s["note"] = "no phone paired with the laptop yet (Windows Settings > Bluetooth & devices > Add device)"
                    continue
                if ear is None:
                    continue
                if ear.odf_t and now - ear.odf_t[-1] < 1.0:  # its sound is coming in
                    s["seen"] = True
                    s["lv"].append(ear.rms)
                    if now - t0 > AC_WINDOW:  # judged once a tempo can be worked out (8 s of sound)
                        s["n"] += 1
                        p = ear.period(now)
                        if p:
                            s["lock"] += 1
                            s["bpm"].append(60 / p)
            rows = [_test_row(name, pick, kind, ear, stats[name], done) for name, pick, kind, ear in tests]
            rows = [r for r in rows if r["ok"] or r["source"] != "This phone's mic (Light Sync app)" or stats[r["source"]]["seen"]
                    or done]
            SOUND_TEST = {"state": "done" if done else "running", "at": t0, "until": t0 + TEST_S, "rows": rows,
                          "best": next((r["pick"] for r in rows if r["ok"] and r["pick"]), None)}
    except Exception as e:
        SOUND_TEST = {"state": "done", "at": t0, "rows": [], "error": f"the test stopped ({type(e).__name__}: {e})"}
        print(f"Sound test failed: {e}", flush=True)
    finally:
        TEST_EARS.clear()
        try:
            print("Sound test: " + "; ".join(f"{r['source']}: {r['verdict']}" for r in SOUND_TEST.get("rows", [])), flush=True)
        except Exception:
            pass
        for m in opened:
            try:
                m.stop()
                m.close()
            except Exception:
                pass
