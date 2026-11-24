#!/usr/bin/env python3
"""
phone_setup.py - the phone features in Home Assistant (safe to run again; it updates what's there):

  Quick buttons (scripts): Party, Movie time, Movie + Music, Gaming, Game + Music, Chill, Good night, All off.
    On the iPhone they can be home-screen widgets, Control Center buttons, Apple Watch actions and Siri shortcuts
    (Home Assistant app). Not exposed to Assist (house rule: lights and media only).
  Home / away (automations, need the HA app's location set to "Always" on the phone):
    - lights and Light Sync off 5 min after you leave home
    - welcome lights when you come home after dark (dim and warm in late-night mode), only if the lights are off
  Dashboard: the Quick buttons on the Smart home front page (tools/ha_setup/dashboard.py, under Lights), two rows of
  four: Normal (from tools/ha_setup/simplify.py), Party, Movie time, Movie + Music / Gaming, Game + Music, Good night, All off. The combos turn on both parts at once
  (Movie time and Gaming are the plain ones: they turn the music part off). Chill stays as a script (Siri / widgets)
  but is no longer on the dashboard. tools/ha_setup/simplify.py then makes each button light up / glow while its mode is on.

  py tools/ha_setup/phone_setup.py
"""
import os
import sys

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # tools\ (export_ha)
import export_ha as ex  # noqa: E402
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import good_night as GN  # noqa: E402

HA = os.getenv("HA_URL", "http://192.168.1.10:8123")
H = {"Authorization": f"Bearer {ex.token()}"}
LIGHTS = "light.chambre_lights"
MODES = ["input_boolean.music_sync", "input_boolean.gaming_mode", "input_boolean.tv_ambient"]  # (Effects removed Nov 6)
IN_SHOW = ["input_boolean.light_sync_use_screen", "input_boolean.light_sync_use_beat", "input_boolean.light_sync_use_wash"]


def on(*ids):
    return {"action": "input_boolean.turn_on", "target": {"entity_id": list(ids)}}


def off(*ids):
    return {"action": "input_boolean.turn_off", "target": {"entity_id": list(ids)}}


def wait(s):
    return {"delay": {"seconds": s}}  # Light Sync needs a moment to hand the lights back when its modes go off


# quick taps (Oct 21, owner: "I press something, then something else, and the system gets confused"): a mode button
# first stops a pending All off / Good night (and its 60 s fade), which would otherwise switch the lights off a few
# seconds later under the new mode; those two do nothing after their wait if a mode was picked meanwhile; a second tap
# restarts a script instead of being ignored.
CANCEL_OFF = {"action": "script.turn_off",
              "target": {"entity_id": ["script.all_lights_off", "script.good_night", "script.fade_chambre_lights_off"]}}
EVERY_MODE = MODES + ["input_boolean.normal_mode"]
STILL_OFF = {"condition": "state", "entity_id": EVERY_MODE, "state": "off"}  # nothing picked during the wait


SCRIPTS = {
    "party_mode": ("Party", "mdi:party-popper", "Music sync with all three lights; no gaming or TV ambient.",
                   [CANCEL_OFF, off("input_boolean.gaming_mode", "input_boolean.tv_ambient"), on(*IN_SHOW),
                    on("input_boolean.music_sync")]),
    "movie_time": ("TV", "mdi:television", "Lights softly follow the TV screen (TV ambient): the film's "
                   "colors only, no music.",
                   [CANCEL_OFF, off("input_boolean.music_sync", "input_boolean.gaming_mode", "input_boolean.tv_music"),
                    on(*IN_SHOW), on("input_boolean.tv_ambient")]),
    "movie_music": ("TV + Music", "mdi:television-play", "TV mode with Music in TV mode: the TV's colors, moving with "
                    "the music's beat.",
                    [CANCEL_OFF, off("input_boolean.music_sync", "input_boolean.gaming_mode"), on(*IN_SHOW),
                     on("input_boolean.tv_music"), on("input_boolean.tv_ambient")]),
    "gaming_time": ("Gaming", "mdi:controller", "Lights follow the game screen fast and bright, no music.",
                    [CANCEL_OFF, off("input_boolean.game_music"), on(*IN_SHOW), on("input_boolean.gaming_mode")]),
    "gaming_music": ("Game + Music", "mdi:headphones", "Gaming with Music in games: the game's colors, and the music's "
                     "beat drives the brightness.",
                     [CANCEL_OFF, on(*IN_SHOW), on("input_boolean.game_music"), on("input_boolean.gaming_mode")]),
    # Nov 7: only the lights that were on fade (good_night.py has the script and the fade it calls)
    "good_night": (GN.GOOD_NIGHT["alias"], GN.GOOD_NIGHT["icon"], GN.GOOD_NIGHT["description"], GN.GOOD_NIGHT["sequence"]),
    "all_lights_off": ("All off", "mdi:lightbulb-group-off-outline", "Light Sync off and all bedroom lights off.",
                       [off(*EVERY_MODE), wait(3), STILL_OFF, {"action": "light.turn_off", "target": {"entity_id": LIGHTS}}]),
}

QUICK = ["normal_light", "party_mode", "movie_time", "movie_music",  # the dashboard row: two rows of four
         "gaming_time", "gaming_music", "good_night", "all_lights_off"]
BUTTONS = {"normal_light": ("Normal", "mdi:lightbulb-on-outline"), **{s: v[:2] for s, v in SCRIPTS.items()}}

AUTOMATIONS = {
    "sh_away_lights_off": {
        "alias": "Smart home · Away: lights off when I leave",
        "description": "5 min after the phone (person.ezer) leaves home, or when the phone has been off the home Wi-Fi "
                       "for 20 min (input_boolean.phone_on_wifi, Light Sync) and the location doesn't say home: Light Sync modes "
                       "off, bedroom lights off. Never while the phone is on the Wi-Fi or the location says home.",
        "triggers": [{"trigger": "state", "entity_id": "person.ezer", "from": "home", "to": "not_home", "for": {"minutes": 5}},
                     {"trigger": "state", "entity_id": "input_boolean.phone_on_wifi", "from": "on", "to": "off"}],
        "conditions": [{"condition": "not", "conditions": [{"condition": "state", "entity_id": "input_boolean.phone_on_wifi", "state": "on"}]},
                       # Nov 5: the Wi-Fi alone said away while the GPS said home: both must agree now
                       {"condition": "not", "conditions": [{"condition": "state", "entity_id": "person.ezer", "state": "home"}]}],
        "actions": [off(*MODES), wait(3), {"action": "light.turn_off", "target": {"entity_id": LIGHTS}}],
        "mode": "single",
    },
    "sh_welcome_home": {
        "alias": "Smart home · Welcome home after dark",
        "description": "When the phone (person.ezer) comes home after sunset and the bedroom lights are off: warm light "
                       "(15 % in late-night mode, 70 % otherwise). Needs the HA app's location permission set to Always.",
        "triggers": [{"trigger": "state", "entity_id": "person.ezer", "from": "not_home", "to": "home"}],
        "conditions": [{"condition": "state", "entity_id": "sun.sun", "state": "below_horizon"},
                       {"condition": "state", "entity_id": LIGHTS, "state": "off"}],
        "actions": [{"if": [{"condition": "state", "entity_id": "input_boolean.late_night_mode", "state": "on"}],
                     "then": [{"action": "light.turn_on", "target": {"entity_id": LIGHTS},
                               "data": {"brightness_pct": 15, "color_temp_kelvin": 2200}}],
                     "else": [{"action": "light.turn_on", "target": {"entity_id": LIGHTS},
                               "data": {"brightness_pct": 70, "color_temp_kelvin": 3000}}]}],
        "mode": "single",
    },
}


def quick_button(sid):
    """One Quick button: runs its script (tools/ha_setup/simplify.py points `entity` at the mode's sensor so it lights up)."""
    alias, icon = BUTTONS[sid]
    return {"type": "button", "entity": f"script.{sid}", "name": alias, "icon": icon, "show_state": False,
            "icon_height": "32px", "grid_options": {"columns": 3, "rows": 2},
            "tap_action": {"action": "perform-action", "perform_action": "script.turn_on",
                           "target": {"entity_id": f"script.{sid}"}}}


def post_scripts(ids=None):
    for sid, (alias, icon, desc, seq) in SCRIPTS.items():
        if ids is None or sid in ids:
            mode = "single" if sid in ("good_night", "all_lights_off") else "restart"  # a mode: a second tap restarts it
            r = requests.post(f"{HA}/api/config/script/config/{sid}", headers=H, timeout=10,
                              json={"alias": alias, "icon": icon, "description": desc, "sequence": seq, "mode": mode})
            print(f"script.{sid:15} {alias:14} {r.status_code}")


def main():
    post_scripts()
    for aid, cfg in AUTOMATIONS.items():
        r = requests.post(f"{HA}/api/config/automation/config/{aid}", headers=H, timeout=10, json={"id": aid, **cfg})
        print(f"automation {aid:20} {r.status_code}")

    ws = ex.WebSocket(HA)
    ws.recv()
    ws.send({"type": "auth", "access_token": ex.token()})
    assert ws.recv().get("type") == "auth_ok"
    # keep the scripts away from Assist (lights and media only)
    ws.call(type="homeassistant/expose_entity", assistants=["conversation"],
            entity_ids=[f"script.{s}" for s in SCRIPTS], should_expose=False)
    # dashboard: the Quick buttons are on the front page (tools/ha_setup/dashboard.py, Lights)
    import dashboard
    dashboard.build_overview(ws)


if __name__ == "__main__":
    main()
