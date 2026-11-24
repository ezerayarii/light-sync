#!/usr/bin/env python3
"""
sound_setup.py - the sound options in Home Assistant (safe to run again):

  input_select.light_sync_part_screen / _beat / _wash   "TvD / Corner / Top listens to": everything, bass, mids, highs,
                                                        or mix (color from the sound: bass red, mids green, highs blue)
  input_boolean.light_sync_mood_colors                  "Mood colours": the music's mood picks the palette
  input_number.light_sync_reaction                      "Reaction speed" 1-10 (Comfort keeps it slow anyway)
  input_number.light_sync_bright_min / _bright_max      "Brightness min / max" (%)
  input_number.light_sync_beat_lock                     "Beat lock strictness" 0.3-0.8
  input_number.light_sync_latency                       "Latency offset" (ms): fire the beat light this early
  input_select.light_sync_colors                        "Colours": mood / neon / sunset / ocean / forest / fire / mine
  input_text.light_sync_my_colors                       "My colours": up to 6 "#rrggbb" for "mine"
  input_select.light_sync_color_change                  "Change colour": every beat / 2 / 4 / 8 beats
  input_select.music_style                              renamed "Movement": flow / flow back / together / each own

The dashboard layout (Settings tab → Music) is made by tools/ha_setup/simplify.py; beat lock and latency are off the screens
there (they keep their values). The live analysis is in the Light Sync app.

  py tools/ha_setup/sound_setup.py
"""
import os
import sys

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # tools\ (export_ha)
import export_ha as ex  # noqa: E402

HA = os.getenv("HA_URL", "http://192.168.1.10:8123")
H = {"Authorization": f"Bearer {ex.token()}"}
PARTS = ["everything", "bass", "mids", "highs", "mix"]  # mix: color from the sound (bass red, mids green, highs blue)
COLOR_OPTIONS = ["mood", "neon", "sunset", "ocean", "forest", "fire", "mine"]
MY_COLORS = "#ff2bd6,#8b5cff,#22e4ff,#ffb02e"
CHANGE_OPTIONS = ["every beat", "2 beats", "4 beats", "8 beats"]
MOVEMENTS = ["flow", "flow back", "bounce", "together", "each own"]  # bounce: TvD -> Top, then back
SELECTS = [("light_sync_part_screen", "TvD listens to", "mdi:monitor-shimmer", "everything"),
           ("light_sync_part_beat", "Corner listens to", "mdi:lamp", "bass"),
           ("light_sync_part_wash", "Top listens to", "mdi:ceiling-light", "highs")]
NUMBERS = [("light_sync_reaction", "Reaction speed", "mdi:speedometer", 1, 10, 1, 5, None),
           ("light_sync_bright_min", "Brightness min", "mdi:brightness-4", 0, 100, 1, 62, "%"),
           ("light_sync_bright_max", "Brightness max", "mdi:brightness-7", 10, 100, 1, 82, "%"),
           ("light_sync_beat_lock", "Beat lock strictness", "mdi:metronome-tick", 0.3, 0.8, 0.05, 0.45, None),
           ("light_sync_latency", "Latency offset", "mdi:timer-sand", 0, 300, 10, 70, "ms")]


def state(eid):
    r = requests.get(f"{HA}/api/states/{eid}", headers=H, timeout=10)
    return r.json() if r.ok else None


def main():
    ws = ex.WebSocket(HA)
    ws.recv()
    ws.send({"type": "auth", "access_token": ex.token()})
    assert ws.recv().get("type") == "auth_ok"
    # create with a name that gives the entity id, then give it its friendly name
    for oid, name, icon, initial in SELECTS:
        if not state(f"input_select.{oid}"):
            ws.call(type="input_select/create", name=oid.replace("_", " "), options=PARTS, icon=icon)
            requests.post(f"{HA}/api/services/input_select/select_option", headers=H, timeout=10,
                          json={"entity_id": f"input_select.{oid}", "option": initial})
        ws.call(type="input_select/update", input_select_id=oid, name=name, options=PARTS, icon=icon)
        print(f"input_select.{oid:24} {name}: {state(f'input_select.{oid}')['state']}")
    for oid, name, icon, lo, hi, step, initial, unit in NUMBERS:
        if not state(f"input_number.{oid}"):
            ws.call(type="input_number/create", name=oid.replace("_", " "), min=lo, max=hi, step=step, mode="slider",
                    icon=icon, initial=initial, **({"unit_of_measurement": unit} if unit else {}))
        ws.call(type="input_number/update", input_number_id=oid, name=name, min=lo, max=hi, step=step, mode="slider",
                icon=icon, **({"unit_of_measurement": unit} if unit else {}))
        print(f"input_number.{oid:24} {name}: {state(f'input_number.{oid}')['state']}")
    if not state("input_boolean.light_sync_mood_colors"):
        ws.call(type="input_boolean/create", name="light sync mood colors", icon="mdi:palette-swatch-variant")
        requests.post(f"{HA}/api/services/input_boolean/turn_on", headers=H, timeout=10,
                      json={"entity_id": "input_boolean.light_sync_mood_colors"})
    ws.call(type="input_boolean/update", input_boolean_id="light_sync_mood_colors", name="Mood colours",
            icon="mdi:palette-swatch-variant")
    print(f"input_boolean.light_sync_mood_colors   Mood colours: {state('input_boolean.light_sync_mood_colors')['state']}")

    # each light: pulse on the beat (Corner on, TvD and Top off) and its own brightness level (100 %);
    # 0 = that light off (owner, Oct 19: "the last value: off, not less bright")
    for role, name, pulse in (("screen", "TvD", False), ("beat", "Corner", True), ("wash", "Top", False)):
        bid, nid = f"light_sync_pulse_{role}", f"light_sync_level_{role}"
        if not state(f"input_boolean.{bid}"):
            ws.call(type="input_boolean/create", name=bid.replace("_", " "), icon="mdi:pulse")
            requests.post(f"{HA}/api/services/input_boolean/turn_{'on' if pulse else 'off'}", headers=H, timeout=10,
                          json={"entity_id": f"input_boolean.{bid}"})
        ws.call(type="input_boolean/update", input_boolean_id=bid, name=f"{name} pulses on the beat", icon="mdi:pulse")
        if not state(f"input_number.{nid}"):
            ws.call(type="input_number/create", name=nid.replace("_", " "), min=0, max=100, step=5, initial=100,
                    mode="slider", unit_of_measurement="%", icon="mdi:brightness-6")
        ws.call(type="input_number/update", input_number_id=nid, name=f"{name} brightness (0 = off)", min=0, max=100,
                step=5, mode="slider", unit_of_measurement="%", icon="mdi:brightness-6")
        ws.call(type="homeassistant/expose_entity", assistants=["conversation"], should_expose=False,
                entity_ids=[f"input_boolean.{bid}", f"input_number.{nid}"])
        print(f"{name}: pulse {state(f'input_boolean.{bid}')['state']}, brightness {state(f'input_number.{nid}')['state']} %")

    # Auto DJ (on): the song's part picks the movement and the beat pulses (party mode); off = the owner's own choices
    if not state("input_boolean.light_sync_auto_dj"):
        ws.call(type="input_boolean/create", name="light sync auto dj", icon="mdi:robot-happy-outline")
        requests.post(f"{HA}/api/services/input_boolean/turn_on", headers=H, timeout=10,
                      json={"entity_id": "input_boolean.light_sync_auto_dj"})
    ws.call(type="input_boolean/update", input_boolean_id="light_sync_auto_dj", name="Auto DJ", icon="mdi:robot-happy-outline")
    ws.call(type="homeassistant/expose_entity", assistants=["conversation"], should_expose=False,
            entity_ids=["input_boolean.light_sync_auto_dj"])
    print(f"input_boolean.light_sync_auto_dj Auto DJ: {state('input_boolean.light_sync_auto_dj')['state']}")

    # reaction speed follows the rhythm (on): the speed comes from the song's tempo; off = the slider
    if not state("input_boolean.light_sync_reaction_auto"):
        ws.call(type="input_boolean/create", name="light sync reaction auto", icon="mdi:metronome")
        requests.post(f"{HA}/api/services/input_boolean/turn_on", headers=H, timeout=10,
                      json={"entity_id": "input_boolean.light_sync_reaction_auto"})
    ws.call(type="input_boolean/update", input_boolean_id="light_sync_reaction_auto", name="Reaction follows the rhythm",
            icon="mdi:metronome")
    ws.call(type="homeassistant/expose_entity", assistants=["conversation"], should_expose=False,
            entity_ids=["input_boolean.light_sync_reaction_auto"])
    print(f"input_boolean.light_sync_reaction_auto Reaction follows the rhythm: {state('input_boolean.light_sync_reaction_auto')['state']}")

    # the colors and how they move: Colours (replaces the Mood colours switch), My colours, Change colour, Movement
    if not state("input_select.light_sync_colors"):
        ws.call(type="input_select/create", name="light sync colors", options=COLOR_OPTIONS, icon="mdi:palette")
        mood_on = state("input_boolean.light_sync_mood_colors")["state"] != "off"
        requests.post(f"{HA}/api/services/input_select/select_option", headers=H, timeout=10,
                      json={"entity_id": "input_select.light_sync_colors", "option": "mood" if mood_on else "neon"})
    ws.call(type="input_select/update", input_select_id="light_sync_colors", name="Colours", options=COLOR_OPTIONS,
            icon="mdi:palette")
    if not state("input_text.light_sync_my_colors"):
        ws.call(type="input_text/create", name="light sync my colors", min=7, max=60, initial=MY_COLORS,
                pattern=r"^#[0-9a-fA-F]{6}(,#[0-9a-fA-F]{6}){0,5}$", icon="mdi:eyedropper")
    ws.call(type="input_text/update", input_text_id="light_sync_my_colors", name="My colours", min=7, max=60,
            pattern=r"^#[0-9a-fA-F]{6}(,#[0-9a-fA-F]{6}){0,5}$", icon="mdi:eyedropper")
    if not state("input_select.light_sync_color_change"):
        ws.call(type="input_select/create", name="light sync color change", options=CHANGE_OPTIONS, icon="mdi:metronome")
        requests.post(f"{HA}/api/services/input_select/select_option", headers=H, timeout=10,
                      json={"entity_id": "input_select.light_sync_color_change", "option": "8 beats"})
    ws.call(type="input_select/update", input_select_id="light_sync_color_change", name="Change colour",
            options=CHANGE_OPTIONS, icon="mdi:metronome")
    # Style -> Movement: vibe is now "each own"; rainbow (ignores the colors) is no longer offered
    old = state("input_select.music_style")["state"]
    storage_id = next(i["id"] for i in ws.call(type="input_select/list") if i.get("name") in ("Music style", "Movement")
                      or i["id"] == "music_style")
    ws.call(type="input_select/update", input_select_id=storage_id, name="Movement", options=MOVEMENTS,
            icon="mdi:swap-horizontal")
    new = {"vibe": "each own", "rainbow": "flow"}.get(old, old if old in MOVEMENTS else "flow")
    requests.post(f"{HA}/api/services/input_select/select_option", headers=H, timeout=10,
                  json={"entity_id": "input_select.music_style", "option": new})
    for e in ("input_select.light_sync_colors", "input_text.light_sync_my_colors", "input_select.light_sync_color_change",
              "input_select.music_style"):
        print(f"{e:40} {state(e)['attributes'].get('friendly_name')}: {state(e)['state']}")
    ws.call(type="homeassistant/expose_entity", assistants=["conversation"], should_expose=False,
            entity_ids=["input_select.light_sync_colors", "input_text.light_sync_my_colors", "input_select.light_sync_color_change"])
    print("dashboard: the Settings tab's Music category shows them (py tools/ha_setup/simplify.py)")


if __name__ == "__main__":
    main()
