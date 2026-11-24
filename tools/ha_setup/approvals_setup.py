#!/usr/bin/env python3
"""
approvals_setup.py - Home Assistant's automatic rules ask first / tell after (Nov 12, owner: "for any light on or off or
any changes I want to approve or see a notice of it"; chose: on -> ask, off -> do + tell, other changes -> ask).

Each rule that turned lights or modes ON (or changed a mode) by itself now writes "ask|<key>|<time>" into
input_text.light_sync_telegram_ask; Light Sync on the Pi (parts/approve.py, HA_ASKS) asks on Telegram with Yes / No and
does the rule's steps only on Yes. Rules that switch things OFF still do it, then write "tell|<text>".
  Sunrise wake-up            ask "sunrise"
  Late-night mode            ask "latenight" when the schedule starts; its end at 06:00 happens and tells
  Auto mode: the TV          ask "tvfilm" / "tvmusic" when a film app opens; "tvend" when it closes
  Music sync follows apps    ask "appmusic"; switching it off again tells
  Away: lights off           tells
  Welcome home after dark    switched off: it lit the room when the phone came home, not when the owner was in the
                             room (Light Sync asks when the owner is back in the room instead)
Safe to run again (rules already asking are left alone). Run after telegram_setup.py (the buttons).

  py tools/ha_setup/approvals_setup.py
"""
import json
import os
import sys

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # tools\ (export_ha)
import export_ha as ex  # noqa: E402

HA = os.getenv("HA_URL", "http://192.168.1.10:8123")
H = {"Authorization": f"Bearer {ex.token()}"}
ASK = "input_text.light_sync_telegram_ask"


def ask(key):
    return {"action": "input_text.set_value", "target": {"entity_id": ASK},
            "data": {"value": "{{ 'ask|" + key + "|' ~ (now().timestamp() | int | string) }}"}}


def tell(text):
    return {"action": "input_text.set_value", "target": {"entity_id": ASK},
            "data": {"value": "{{ 'tell|" + text.replace(" ", "_") + "|' ~ (now().timestamp() | int | string) }}"}}


def get(aid):
    return requests.get(f"{HA}/api/config/automation/config/{aid}", headers=H, timeout=10).json()


def put(aid, cfg):
    requests.post(f"{HA}/api/config/automation/config/{aid}", headers=H, json={**cfg, "id": aid}, timeout=10).raise_for_status()
    print(f"automation {cfg.get('alias')}")


def main():
    # Sunrise wake-up: ask
    c = get("sh_sunrise_wakeup")
    if "ask|" not in json.dumps(c["actions"]):
        c["actions"] = [ask("sunrise")]
        c["description"] += " Asks first (Nov 12, approvals_setup.py)."
        put("sh_sunrise_wakeup", c)

    # Late-night mode: ask when the schedule starts; the end happens and tells
    c = get("sh_late_night_sync")
    if "ask|" not in json.dumps(c["actions"]):
        c["actions"] = [{"if": [{"condition": "state", "entity_id": "schedule.late_night", "state": "on"}],
                         "then": [{"condition": "template", "value_template": "{{ trigger.platform == 'state' }}"},
                                  {"condition": "state", "entity_id": "input_boolean.late_night_mode", "state": "off"},
                                  ask("latenight")],
                         "else": [{"condition": "state", "entity_id": "input_boolean.late_night_mode", "state": "on"},
                                  {"action": "input_boolean.turn_off", "target": {"entity_id": "input_boolean.late_night_mode"}},
                                  tell("Late-night mode ended for the day.")]}]
        c["description"] += " Asks before it starts; its end tells (Nov 12)."
        put("sh_late_night_sync", c)

    # Auto mode: the TV - the film / music start asks, the end asks
    c = get("sh_auto_tv")
    s = json.dumps(c["actions"])
    if "ask|" not in s:
        then = c["actions"][0]["then"]
        c["actions"][0]["then"] = then[:2] + [{"if": [{"condition": "template",
                                                       "value_template": "{{ state_attr('media_player.x96q_pro_plus','app_id') in film_apps }}"}],
                                               "then": [ask("tvfilm")], "else": [ask("tvmusic")]}]
        els = c["actions"][0]["else"]
        c["actions"][0]["else"] = [ask("tvend") if a.get("action") == "script.turn_on"
                                   and a.get("target", {}).get("entity_id") == "script.normal_light" else a for a in els]
        c["description"] += " Asks first (Nov 12)."
        put("sh_auto_tv", c)

    # Music sync follows media apps: asks; switching off tells
    c = get("sh_music_follows_apps")
    if "ask|" not in json.dumps(c["actions"]):
        then = c["actions"][0]["then"]
        c["actions"][0]["then"] = [a for a in then if "action" not in a] + [ask("appmusic")]
        els = c["actions"][0]["else"]
        for a in els:
            if "if" in a:
                a["then"] = a["then"] + [tell("The media app closed: Music sync off.")]
        c["description"] += " Asks first; off tells (Nov 12)."
        put("sh_music_follows_apps", c)

    # Away: lights off - tells
    c = get("sh_away_lights_off")
    if "tell|" not in json.dumps(c["actions"]):
        c["actions"] = c["actions"] + [tell("You left: lights off.")]
        put("sh_away_lights_off", c)

    # Welcome home after dark: off (Light Sync asks when the owner is back in the room)
    requests.post(f"{HA}/api/services/automation/turn_off", headers=H, timeout=10,
                  json={"entity_id": "automation.smart_home_welcome_home_after_dark", "stop_actions": True}).raise_for_status()
    print("automation Welcome home after dark: switched off")


if __name__ == "__main__":
    main()
