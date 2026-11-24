"""What the app asks the laptop (POST /vibe, the path kept for the Pi's app): Undo of an automatic switch, the For you
card, Normal's "forget what was learned" and the sound test. Nov 6: the Vibes card (ready-made vibes and the AI's
typed vibes) was removed (owner: "delete anything we are not using"); this is what stayed."""

import json
import os
import threading
import time

from parts import helpers, soundtest

UNDO = []      # [(entity_id, previous state)] of the last automatic switch (habits.py), for Undo

HABITS = None  # habits.Habits on the laptop: what the owner usually uses (For you, automatic switching, Normal's curve)

MODE_SWITCHES = ("input_boolean.music_sync", "input_boolean.tv_ambient", "input_boolean.normal_mode",
                 "input_boolean.gaming_mode")

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434")  # local AI model (Ollama), this laptop only: Rocky


def app_request(body):
    """POST /vibe: {"undo": true} | {"foryou": "get" | "use"} | {"normal_forget": true} | {"sound_test": true}."""
    global UNDO
    try:
        data = json.loads(body or b"{}")
    except ValueError:
        return {"error": "bad request"}
    if data.get("undo"):
        if not UNDO:
            return {"error": "nothing to undo"}
        for eid, state in UNDO:  # settings first, then the mode switches: on ones last so they win
            if eid in MODE_SWITCHES and state == "on":
                continue
            helpers.set_helper(eid, state)
        for eid, state in UNDO:
            if eid in MODE_SWITCHES and state == "on":
                time.sleep(0.5)  # let the "off" ones settle, then the old mode comes back on
                helpers.set_helper(eid, state)
        UNDO = []
        return {"ok": True, "summary": "Back to how it was."}
    if data.get("normal_forget"):  # the app's Normal tab: forget what was learned
        if not HABITS:
            return {"error": "learning runs on the laptop: start Light Sync there"}
        HABITS.normal_forget()
        return {"ok": True, "summary": "Normal light forgot what it learned: the built-in day again."}
    if data.get("sound_test"):  # the app's Sound tab: which sound source hears the music
        if soundtest.SOUND_TEST.get("state") != "running":
            threading.Thread(target=soundtest.sound_test, daemon=True, name="sound test").start()
        return {"ok": True, "summary": f"Listening to every sound source for {soundtest.TEST_S:.0f} s…"}
    if data.get("foryou"):  # the app's "For you" card: learned on the laptop
        if not HABITS:
            return {"error": "learning runs on the laptop: start Light Sync there"}
        return HABITS.request(data)
    return {"error": "unknown request"}
