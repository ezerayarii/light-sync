#!/usr/bin/env python3
"""
learn_setup.py - memory and learning in Home Assistant (safe to run again):

  Last mode     input_text.light_sync_last_mode: the mode last used for a minute or more (party, ambient, effects,
                gaming, normal), kept by the automation "Remember the last mode" - so it works whichever machine drives.
  Resume        script.light_sync_resume: that mode back on (the looks are kept in the helpers anyway).
                Automation "Resume when the lights come back on": the lights switched on by hand (app, Govee app,
                dashboard) after being off for 30 s or more, with no mode on and the last mode under 12 hours old.
                Not when an automation or script switched them on (sunrise, presence, welcome home).
  Learning      input_boolean.light_sync_auto_learn ("Switch by itself at my usual times", on): habits.py on the
                laptop switches to the usual mode and look when a habit is strong (see habits.py for the rules).

All hidden from Assist (house rule: AI agents see lights and media only).

  py tools/ha_setup/learn_setup.py
"""
import os
import sys

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # tools\ (export_ha)
import export_ha as ex  # noqa: E402

HA = os.getenv("HA_URL", "http://192.168.1.10:8123")
H = {"Authorization": f"Bearer {ex.token()}"}
LAST = "input_text.light_sync_last_mode"
AUTO = "input_boolean.light_sync_auto_learn"
SWITCHES = {"party": "input_boolean.music_sync", "ambient": "input_boolean.tv_ambient",
            "gaming": "input_boolean.gaming_mode",
            "normal": "input_boolean.normal_mode"}

SCRIPT = {
    "alias": "Resume", "icon": "mdi:play-circle-outline", "mode": "single",
    "description": "The Light Sync mode last used comes back on (Music, TV, Effects, Gaming or Normal light).",
    "sequence": [{"choose": [
        {"conditions": [{"condition": "state", "entity_id": LAST, "state": "party"}],
         "sequence": [{"action": "input_boolean.turn_off", "target": {"entity_id": SWITCHES["ambient"]}},
                      {"action": "input_boolean.turn_on", "target": {"entity_id": SWITCHES["party"]}}]},
        {"conditions": [{"condition": "state", "entity_id": LAST, "state": "normal"}],
         "sequence": [{"action": "script.turn_on", "target": {"entity_id": "script.normal_light"}}]},
        *[{"conditions": [{"condition": "state", "entity_id": LAST, "state": m}],
           "sequence": [{"action": "input_boolean.turn_on", "target": {"entity_id": SWITCHES[m]}}]}
          for m in ("ambient", "gaming")]]}],
}

AUTOMATIONS = {
    "sh_remember_last_mode": {
        "alias": "Smart home · Remember the last mode",
        "description": "A Light Sync mode (or Normal light) on for a minute: remembered as the last mode, for Resume.",
        "triggers": [{"trigger": "state", "entity_id": eid, "to": "on", "for": {"minutes": 1}, "id": m}
                     for m, eid in SWITCHES.items()],
        "conditions": [],
        "actions": [{"action": "input_text.set_value", "target": {"entity_id": LAST},
                     "data": {"value": "{{ trigger.id }}"}}],
        "mode": "queued",
    },
    "sh_resume_on_lights_on": {
        "alias": "Smart home · Resume when the lights come back on",
        "description": "The lights switched on by hand after 30 s or more off, no mode on: the last mode comes back "
                       "(if it was used in the last 12 hours). Not when an automation or script switched them on.",
        "triggers": [{"trigger": "state", "entity_id": "light.chambre_lights", "from": "off", "to": "on"}],
        "conditions": [
            {"condition": "template", "value_template":
             "{{ (trigger.to_state.last_changed - trigger.from_state.last_changed).total_seconds() >= 30 }}"},
            {"condition": "template", "value_template": "{{ trigger.to_state.context.parent_id is none }}"},
            {"condition": "state", "entity_id": list(SWITCHES.values()), "state": "off"},
            {"condition": "state", "entity_id": "script.sunrise_chambre", "state": "off"},
            {"condition": "template", "value_template":
             "{{ states('" + LAST + "') in ['party', 'ambient', 'gaming', 'normal'] and "
             "(now() - states." + LAST + ".last_changed).total_seconds() < 12 * 3600 }}"}],
        "actions": [{"delay": {"seconds": 2}},  # let a light that's still waking up settle
                    {"action": "script.turn_on", "target": {"entity_id": "script.light_sync_resume"}}],
        "mode": "single",
    },
}


def state(eid):
    r = requests.get(f"{HA}/api/states/{eid}", headers=H, timeout=10)
    return r.json() if r.ok else None


def main():
    ws = ex.WebSocket(HA)
    ws.recv()
    ws.send({"type": "auth", "access_token": ex.token()})
    assert ws.recv().get("type") == "auth_ok"
    if not state(LAST):
        ws.call(type="input_text/create", name="light sync last mode", min=0, max=20, icon="mdi:history")
    ws.call(type="input_text/update", input_text_id="light_sync_last_mode", name="Last mode", min=0, max=20,
            icon="mdi:history")
    if not state(AUTO):
        ws.call(type="input_boolean/create", name="light sync auto learn", icon="mdi:brain")
        requests.post(f"{HA}/api/services/input_boolean/turn_on", headers=H, timeout=10, json={"entity_id": AUTO})
    ws.call(type="input_boolean/update", input_boolean_id="light_sync_auto_learn",
            name="Switch by itself at my usual times", icon="mdi:brain")
    requests.post(f"{HA}/api/config/script/config/light_sync_resume", headers=H, timeout=10, json=SCRIPT).raise_for_status()
    for aid, cfg in AUTOMATIONS.items():
        requests.post(f"{HA}/api/config/automation/config/{aid}", headers=H, timeout=10,
                      json={"id": aid, **cfg}).raise_for_status()
    ws.call(type="homeassistant/expose_entity", assistants=["conversation"], should_expose=False,
            entity_ids=[LAST, AUTO, "script.light_sync_resume"])
    if state(LAST)["state"] in ("", "unknown"):  # start from what's on now
        on = next((m for m, eid in SWITCHES.items() if (state(eid) or {}).get("state") == "on"), None)
        if on:
            requests.post(f"{HA}/api/services/input_text/set_value", headers=H, timeout=10,
                          json={"entity_id": LAST, "value": on})
    print(f"Last mode: {state(LAST)['state'] or '-'} | learning switch: {state(AUTO)['state']} | "
          f"resume script + 2 automations saved")


if __name__ == "__main__":
    main()
