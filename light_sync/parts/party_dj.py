"""Party Pro DJ (Nov 6, owner: "make the party mode pro and DJ ... based on the music, dynamic"; then: TvD leads and
Light Sync makes the scenes, not Govee, so Corner and Top follow the strip).

In Party with "Party Pro" on and music playing, the DJ picks one of TvD's scenes (parts/tvd_scenes.py) by the music's
mood; light_sync.py draws it on TvD's parts and Corner / Top copy the strip as always.
  calm (slow or soft)        breathe, drift
  groove (in between)        wave, comet
  high (fast and loud)       burst, chase, strobe
A new scene when the mood holds a new level for MOOD_HOLD_S, when a new song starts (after a silence), and every
ROTATE_S within the same mood; never twice within MIN_GAP_S. No music, another mode, Party Pro off: no scene."""

import random
import time

from parts import config, ha_link, web

HA_PRO = "input_boolean.light_sync_party_pro"
from parts import tvd_scenes
from parts.tvd_scenes import MOODS as SCENES
MOOD_HOLD_S = 16
ROTATE_S = 60
MIN_GAP_S = 20
SILENCE_S = 3            # a gap this long, then music again: a new song

SCENE = None             # the scene TvD plays now (light_sync.py draws it), or None


def music_level():
    """(beat steady?, bpm, loudness 0-1) from the Ear."""
    e = web.EAR
    try:
        steady = bool(e) and e.period() is not None
        return steady, (e.bpm() if e else 0), float(web.STATE.get("energy") or 0.0)
    except Exception:
        return False, 0, 0.0


def mood_of(bpm, loud):
    if bpm and bpm >= 118 and loud >= 0.45:
        return "high"
    if not bpm or bpm < 90 or loud < 0.2:
        return "calm"
    return "groove"


def play(scene):
    global SCENE
    SCENE = scene
    return True


def party_dj_loop():
    global SCENE
    pro, checked = False, 0.0
    loud_s, mood, cand, cand_at = 0.0, None, None, 0.0
    scene, scene_at, quiet_at = None, 0.0, 0.0
    while True:
        time.sleep(1)
        now = time.time()
        if now - checked > 5:
            checked = now
            try:
                pro = ha_link.ha_state(HA_PRO)["state"] == "on"
            except Exception:
                pass
        steady, bpm, loud = music_level()
        party = not config.GAMING_ON and ((config.MUSIC_ON and not config.AMBIENT_ON) or (config.AMBIENT_ON and config.TV_MUSIC))  # + TV + Music
        if not steady:
            quiet_at = quiet_at or now
        want = pro and party and not config.STANDBY and config.RUNNING and (steady or now - quiet_at < 8)
        if not want:
            if SCENE:
                print("Party Pro: scene off", flush=True)
            SCENE, scene, mood, cand = None, None, None, None
            web.STATE.pop("dj", None)
            continue
        loud_s += (loud - loud_s) * 0.15  # loudness over ~7 s
        new_song = steady and quiet_at and now - quiet_at > SILENCE_S
        if steady:
            quiet_at = 0.0
        m = mood_of(bpm, loud_s)
        if m != cand:
            cand, cand_at = m, now
        if mood is None or new_song or (cand != mood and now - cand_at >= MOOD_HOLD_S):
            mood = cand
            change = True
        else:
            change = scene is not None and now - scene_at >= ROTATE_S
        if (change or scene is None) and (scene is None or now - scene_at >= MIN_GAP_S or new_song):
            pool = SCENES[mood] + (tvd_scenes.PEAK if mood == "high" and loud_s >= 0.75 and bpm >= 120 else [])
            pick = [s for s in pool if s != scene] or pool
            nxt = random.choice(pick)
            if play(nxt):
                print(f"Party Pro: {mood} ({bpm} BPM, loudness {loud_s:.0%}) - TvD {nxt}", flush=True)
                scene, scene_at = nxt, now
        web.STATE["dj"] = {"scene": scene, "mood": mood, "bpm": bpm}
