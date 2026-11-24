"""Rocky's light cue on TvD while he listens and answers."""

import os
import time

from parts.config import DEMO
from parts import config, web


# Rocky talks with the lights (Oct 27, owner: "I don't like the reply, make it talk by lights"): the MeetUp's speaker
# is off, and TvD shows what the voice assistant is doing, over whatever the lights show: listening (he heard his
# name), thinking, done, or he heard nothing. Soft fades only (no flashes: the owner gets headaches), dimmer at night.
# It follows the microphone's state (HA_VOICE) through the live Home Assistant link.
HA_VOICE = os.getenv("HA_VOICE", "assist_satellite.assist_microphone")

VOICE_RGB = {"listening": (40, 110, 255), "thinking": (150, 70, 255), "done": (40, 220, 110), "missed": (255, 140, 30)}

VOICE_IN_S, VOICE_HOLD_S, VOICE_OUT_S = 0.4, 1.2, 0.8  # fade in / a result stays this long / fade out

VOICE_BRIGHT = (150, 60)                               # the cue's brightness (0-255): day, night

VOICE = {"state": None, "cue": None, "at": 0.0, "from": None, "light": None}  # light: TvD's LightWorker

def voice_state(st):
    """The microphone's new state (idle / listening / processing / responding): the cue TvD shows."""
    prev, VOICE["state"] = VOICE["state"], st
    if prev is None or st == prev:  # the first reading: no cue
        return
    if st == "listening":
        cue = "listening"
    elif st in ("processing", "responding"):
        cue = "thinking"
    elif st == "idle":
        cue = "missed" if prev == "listening" else "done" if prev in ("processing", "responding") else None
    else:
        cue = None  # unavailable: the microphone restarts
    now = time.time()
    VOICE["from"] = voice_cue(now)  # a cue already showing glides into the new one
    VOICE["cue"], VOICE["at"] = cue, now
    if cue and not config.STANDBY:
        web.STATE["voice"] = {"cue": cue, "at": round(now, 1)}  # the live view / tests: the last cue
        print(f"Rocky: {cue}", flush=True)
    if VOICE["light"] is not None:
        VOICE["light"].wake.set()

def voice_cue(now):
    """Rocky's cue now: (colour, strength 0-1), or None."""
    cue = VOICE["cue"]
    if not cue or config.STANDBY or DEMO:
        return None
    t = now - VOICE["at"]
    if cue in ("listening", "thinking"):
        k = 1.0 if t < 30 else 0.0  # stuck for 30 s (a lost state change): let go
    else:
        k = 1.0 if t < VOICE_HOLD_S else 1 - (t - VOICE_HOLD_S) / VOICE_OUT_S
    if k <= 0:
        return None
    rgb, s0 = VOICE["from"] or (VOICE_RGB[cue], 0.0)
    u = min(1.0, t / VOICE_IN_S)
    u = u * u * (3 - 2 * u)
    return tuple(a + (b - a) * u for a, b in zip(rgb, VOICE_RGB[cue])), min(k, s0 + (1.0 - s0) * u)
