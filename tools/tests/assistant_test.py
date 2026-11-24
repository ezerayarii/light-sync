#!/usr/bin/env python3
"""
assistant_test.py - tests the voice assistant's AI (conversation.room_assistant) without it doing anything.

Oct 27: asked "what can you do?", llama3.2 3B called the All off script. Like the voice pipeline, a sentence a
fixed command catches (checked with Home Assistant's sentence debugger, nothing runs) never reaches Aura. While this runs, Light Sync's AI doorway on
the laptop (ollama_proxy in light_sync.py) keeps the model's tool calls aside instead of passing them to Home
Assistant, so nothing is switched; the calls it wanted are compared with what each sentence should do. A second
safety net: the lights' and modes' states are compared before and after.

  py tools/tests/assistant_test.py              # every sentence
  py tools/tests/assistant_test.py "dim the top light"   # one sentence: what it would do
"""
import json
import os
import sys
import tempfile
import time

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # tools\ (export_ha)
import export_ha as ex  # noqa: E402

HA = os.getenv("HA_URL", "http://192.168.1.10:8123")
H = {"Authorization": f"Bearer {ex.token()}"}
AGENT = "conversation.room_assistant"
FLAG = os.path.join(tempfile.gettempdir(), "lightsync_assistant_test")  # the same as ASSIST_TEST_FLAG in light_sync.py
LOG = FLAG + ".jsonl"
READ_ONLY = {"GetLiveContext", "GetDateTime", "HassGetState", "HassGetWeather", "todo_get_items"}
WATCH = ["light.chambre_tvd", "light.h6004", "light.h6004_2", "input_boolean.music_sync", "input_boolean.tv_ambient",
         "input_boolean.gaming_mode", "input_boolean.normal_mode"]
# (sentence, what it should do: None = nothing (questions; reading is fine), or the tools any of which is right)
CASES = [
    ("What can you do?", None),
    ("Which lights are on?", None),
    ("Is the TV on?", None),
    ("What mode is the room in?", None),
    ("Hello", None),
    ("Thanks, that's all", None),
    ("What time is it?", None),
    ("How bright is the corner light?", None),
    ("Tell me a joke", None),
    ("Make it cozy for a movie", {"script__movie_time", "script__movie_music"}),
    ("Party time", {"script__party_mode"}),
    ("I'm going to sleep", {"script__good_night"}),
    ("Turn off all the lights", {"script__all_lights_off", "HassTurnOff"}),
    ("I'm going to play a game", {"script__gaming_time", "script__gaming_music"}),
    ("Normal light please", {"script__normal_light", "trigger"}),
    ("Dim the top light to 30 percent", {"HassLightSet"}),
    ("Make the corner light blue", {"HassLightSet"}),
    ("Pause the TV", {"HassMediaPause"}),
    ("Can you turn off the lights?", {"script__all_lights_off", "HassTurnOff"}),
    ("Could you tell me which lights are on?", None),
    ("It's too bright", {"HassLightSet", "HassTurnOff", "script__normal_light"}),
    ("I'm bored", None),
    ("Good morning", None),
    # two turns, no wake word in between (Oct 27: Aura keeps the conversation going); the last one is judged
    (("I'm bored", "a movie"), {"script__movie_time", "script__movie_music"}),
    (("I'm bored", "yes please"), None),  # yes to "a movie, a game or some music?" is ambiguous: ask, do nothing
    (("Party time", "no thanks"), None),
    ("Make the corner blue and dim the top light", {"HassLightSet"}),
    # Oct 27, the owner on the phone: the model used the Salon's TV and wrote its action out as JSON
    ("Can you put the TV light on blue", {"HassLightSet"}),
    ("Turn all the lights on", {"script__normal_light", "HassTurnOn"}),
    ("All the lights off", {"script__all_lights_off", "HassTurnOff"}),
]


def states():
    return {e: requests.get(f"{HA}/api/states/{e}", headers=H, timeout=10).json().get("state") for e in WATCH}


_WS = None


def fixed(text):
    """What a fixed command (sentence trigger or Home Assistant's built-in sentences) would do with this sentence,
    checked without running it: the voice pipeline tries these before Aura. None = it goes to Aura."""
    global _WS
    if _WS is None:
        _WS = ex.WebSocket(HA)
        _WS.sock.settimeout(60)
        _WS.recv()
        _WS.send({"type": "auth", "access_token": ex.token()})
        assert _WS.recv().get("type") == "auth_ok"
    res = _WS.call(type="conversation/agent/homeassistant/debug", sentences=[text], language="en")["results"][0]
    if not res or not res.get("match"):
        return None
    return (res.get("intent") or {}).get("name") or "trigger: " + res.get("sentence_template", "")


def body(text, conv):
    b = {"text": text, "language": "en", "agent_id": AGENT}
    if conv:
        b["conversation_id"] = conv  # the same conversation: the earlier turns are in its history
    return b


def ask(text):
    """One sentence, or a few in one conversation (only the last one's tool calls count)."""
    turns = [text] if isinstance(text, str) else list(text)
    conv = None
    for t in turns[:-1]:
        conv = requests.post(f"{HA}/api/conversation/process", headers=H, timeout=180,
                             json=body(t, conv)).json().get("conversation_id")
    start = os.path.getsize(LOG) if os.path.exists(LOG) else 0
    t0 = time.time()
    r = requests.post(f"{HA}/api/conversation/process", headers=H, timeout=180,
                      json=body(turns[-1], conv)).json()
    took = time.time() - t0
    said = r.get("response", {}).get("speech", {}).get("plain", {}).get("speech", "")
    calls = []
    if os.path.exists(LOG):
        with open(LOG, encoding="utf-8") as f:
            f.seek(start)
            for line in f:
                for c in json.loads(line).get("calls", []):  # "light__HassLightSet" -> "HassLightSet"; scripts keep theirs
                    name = c.get("function", {}).get("name", "")
                    calls.append(name if name.startswith("script__") else name.split("__")[-1])
    return said, calls, took


def main():
    cases = [(" ".join(sys.argv[1:]), "show")] if len(sys.argv) > 1 else CASES
    open(FLAG, "w").close()  # Light Sync's AI doorway: keep the tool calls aside from now on
    before = states()
    bad = 0
    try:
        for text, want in cases:
            last = text if isinstance(text, str) else text[-1]
            fx = fixed(last) if want != "show" else None
            if fx:  # a fixed command catches it first (the real voice path): Aura never sees it
                reads = fx.startswith("HassGet") or fx in ("HassRespond", "HassNevermind")  # answers only
                verdict = ("OK (fixed: " if (want is None) == reads else "WRONG (fixed: ") + fx[:30] + ")"
                bad += verdict.startswith("WRONG")
                print(f"{verdict:42s} {'':7s} | {last!r}", flush=True)
                continue
            said, calls, took = ask(text)
            acts = [c for c in calls if c not in READ_ONLY]
            if want == "show":
                verdict = "would do: " + (", ".join(calls) or "nothing")
            elif want is None:
                verdict = "OK" if not acts else "WRONG: would do " + ", ".join(acts)
            else:
                verdict = "OK" if set(acts) & want else "WRONG: would do " + (", ".join(acts) or "nothing")
            bad += verdict.startswith("WRONG")
            shown = text if isinstance(text, str) else " / ".join(text)
            print(f"{verdict:42s} {took:5.1f} s  | {shown!r} -> {said[:90]!r}", flush=True)
    finally:
        os.remove(FLAG)  # back to normal: the assistant's actions pass again
    after = states()
    changed = {e: (before[e], after[e]) for e in WATCH if before[e] != after[e]}
    print(f"\n{len(cases) - bad} of {len(cases)} right" if cases[0][1] != "show" else "")
    print("nothing was switched (as it should be)" if not changed else f"CHANGED meanwhile (Auto gaming? you?): {changed}")


if __name__ == "__main__":
    main()
