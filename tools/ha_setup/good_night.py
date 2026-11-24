#!/usr/bin/env python3
"""
good_night.py - Good night fades only the lights that are on (Nov 7, owner: "only Top was on; I pressed Good night, all
the lights came on and started to fade - I want just what is on to fade").

Two things switched the others on: switching the modes off makes Light Sync put the lights back the way they were
before it started, and the fade dimmed the whole light group (dimming a group switches every light in it on).
Now Good night notes which lights are on (and how bright) the moment it's pressed, switches off any that come on
during the hand-over, and fades each lit one from its own brightness, one by one, never the group.

  py tools/ha_setup/good_night.py      (phone_setup.py keeps the same Good night, so running that again is safe)
"""
import os
import sys

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # tools\ (export_ha)
import export_ha as ex  # noqa: E402

HA = os.getenv("HA_URL", "http://192.168.1.10:8123")
H = {"Authorization": f"Bearer {ex.token()}"}
GROUP = "light.chambre_lights"
MODES = ["input_boolean.music_sync", "input_boolean.gaming_mode", "input_boolean.tv_ambient", "input_boolean.normal_mode"]

# what's lit right now, and how bright (Light Sync drives them, Home Assistant sees the real lights)
LIT = "{{ expand('" + GROUP + "') | selectattr('state', 'eq', 'on') | map(attribute='entity_id') | list }}"
BRI = "{{ expand('" + GROUP + "') | selectattr('state', 'eq', 'on') | map(attribute='attributes.brightness') | map('int', 128) | list }}"

GOOD_NIGHT = {
    "alias": "Good night",
    "icon": "mdi:weather-night",
    "description": "Same as saying \"good night\": all modes off, then the lights that were on fade out over 60 s "
                   "(the ones that were off stay off). tools/ha_setup/good_night.py",
    "mode": "single",
    "sequence": [
        {"variables": {"lit": LIT, "bri": BRI}},
        {"action": "input_boolean.turn_off", "target": {"entity_id": MODES}},
        {"delay": {"seconds": 3}},
        {"condition": "state", "entity_id": MODES, "state": "off"},
        {"action": "script.turn_on", "target": {"entity_id": "script.fade_chambre_lights_off"},
         "data": {"variables": {"duration_s": 60, "only": "{{ lit }}", "start_bri": "{{ bri }}"}}},
    ],
}

FADE = {
    "alias": "Fade Chambre lights off",
    "icon": "mdi:lightbulb-night-outline",
    "mode": "restart",
    "description": "Fades the lights that are on (or the ones given) down from each one's own brightness, one light at a "
                   "time (never the group: that switches every light on), then off twice (Govee LAN can drop a command). "
                   "Lights that were off are switched off. tools/ha_setup/good_night.py",
    "fields": {
        "duration_s": {"name": "Duration (s)", "default": 60, "selector": {"number": {"min": 5, "max": 600, "unit_of_measurement": "s"}}},
        "only": {"name": "Lights to fade", "description": "The lights that were on (default: the ones on now)"},
        "start_bri": {"name": "Their brightness", "description": "Each one's brightness when it started (default: now)"},
    },
    "sequence": [
        {"delay": {"seconds": 2}},
        {"variables": {
            "steps": 10,
            "dur": "{{ duration_s | default(60) | int }}",
            "targets": "{{ only if only is defined and only else " + LIT[3:-3] + " }}",
            "starts": "{{ start_bri if start_bri is defined and start_bri and (start_bri | count) == (targets | count) else "
                      "(targets | map('state_attr', 'brightness') | map('int', 128) | list) }}",
            "others": "{{ expand('" + GROUP + "') | map(attribute='entity_id') | reject('in', targets) | list }}",
        }},
        {"if": [{"condition": "template", "value_template": "{{ others | count > 0 }}"}],
         "then": [{"action": "light.turn_off", "target": {"entity_id": "{{ others }}"}}]},
        {"if": [{"condition": "template", "value_template": "{{ targets | count > 0 }}"}],
         "then": [{"repeat": {"count": "{{ steps - 1 }}", "sequence": [
             {"variables": {"k": "{{ repeat.index }}"}},
             {"repeat": {"for_each": "{{ range(targets | count) | list }}", "sequence": [
                 {"action": "light.turn_on", "target": {"entity_id": "{{ targets[repeat.item] }}"},
                  "data": {"brightness": "{{ [(starts[repeat.item] * (steps - k) / steps) | round(0) | int, 1] | max }}"}}]}},
             {"delay": {"seconds": "{{ dur / steps }}"}}]}}]},
        {"action": "light.turn_off", "target": {"entity_id": GROUP}},
        {"delay": {"seconds": 3}},
        {"action": "light.turn_off", "target": {"entity_id": GROUP}},
    ],
}


def main():
    for sid, cfg in (("fade_chambre_lights_off", FADE), ("good_night", GOOD_NIGHT)):
        requests.post(f"{HA}/api/config/script/config/{sid}", headers=H, json=cfg, timeout=10).raise_for_status()
        print(f"script.{sid}")


if __name__ == "__main__":
    main()
