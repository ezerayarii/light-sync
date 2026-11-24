#!/usr/bin/env python3
"""
switching.py - switching modes without surprises (Oct 21, owner: "sometimes I press something, then something else,
and the system gets confused"). Safe to run again.

  Quick scripts        phone_setup.py (party, movie, gaming, chill, good night, all off) and simplify.py (normal):
                       a mode first cancels a pending All off / Good night, those stop after their wait if a mode was
                       picked meanwhile, a second tap restarts a mode. This posts them.
  Music follows the TV apps   (sh_music_follows_apps, made in Home Assistant earlier) switched Music on whenever the TV
                       box changed app, even right after the owner picked TV or Normal (and Music on ends Normal), and
                       off 30 s after leaving the app, even Music the owner had turned on (phone over Bluetooth). Now:
                       - on only when a media app OPENS and no mode is on (nothing the owner picked is overridden);
                       - off only Music it turned on itself (input_boolean.light_sync_music_by_apps, set together with
                         Music: if Music changed after that, the owner did it, and it stays).
  Light Sync itself    waits 1.5 s with every mode off before it stops (PAUSE_GRACE_S): a switch no longer flashes
                       the lights back to how they were before Light Sync in between.

  py tools/ha_setup/switching.py
"""
import os
import sys

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # tools\ (export_ha)
import export_ha as ex  # noqa: E402

HA = os.getenv("HA_URL", "http://192.168.1.10:8123")
H = {"Authorization": f"Bearer {ex.token()}"}
FLAG = "input_boolean.light_sync_music_by_apps"
BOX = "media_player.x96q_pro_plus"
MODES = ["input_boolean.music_sync", "input_boolean.tv_ambient", "input_boolean.gaming_mode", "input_boolean.normal_mode"]
MEDIA_APPS = ["com.google.android.youtube.tv", "com.amazon.amazonvideo.livingroom", "com.iptvav.av_iptv",
              "com.softwinner.miracastReceiver"]
IN_MEDIA = "{{ is_state('%s','on') and state_attr('%s','app_id') in media_apps }}" % (BOX, BOX)
WAS_IN_MEDIA = ("{{ trigger.from_state is not none and trigger.from_state.state == 'on' "
                "and trigger.from_state.attributes.app_id in media_apps }}")
OURS = ("{{ is_state('%s','on') and is_state('input_boolean.music_sync','on') and "
        "((states.input_boolean.music_sync.last_changed - states.%s.last_changed).total_seconds() | abs) < 5 }}" % (FLAG, FLAG))


def ws_connect():
    ws = ex.WebSocket(HA)
    ws.sock.settimeout(60)
    ws.recv()
    ws.send({"type": "auth", "access_token": ex.token()})
    assert ws.recv().get("type") == "auth_ok"
    return ws


def post_music_automation():
    auto = {
        "id": "sh_music_follows_apps",
        "alias": "Smart home · Music sync follows media apps",
        "description": "A media app opens on the X96Q box (YouTube, Prime Video, IPTV, screen mirroring) and no mode is on: "
                       "Music sync on (with Auto mode on: Movie, tools/ha_setup/auto_mode.py). 30 s after leaving them: Music off, but only if this automation turned it on "
                       "(nothing the owner picked is overridden). tools/ha_setup/switching.py",
        "triggers": [{"trigger": "state", "entity_id": BOX, "attribute": "app_id"},
                     {"trigger": "state", "entity_id": BOX, "to": None}],
        "conditions": [],
        "actions": [{"if": [{"condition": "template", "value_template": IN_MEDIA}],
                     "then": [{"condition": "template", "value_template": "{{ not (%s) }}" % WAS_IN_MEDIA[3:-3]},  # just opened
                              {"condition": "state", "entity_id": MODES, "state": "off"},  # nothing picked
                              {"condition": "state", "entity_id": "input_boolean.light_sync_auto_mode", "state": "off"},  # Auto mode: Movie instead (auto_mode.py)
                              {"action": "input_boolean.turn_on", "target": {"entity_id": "input_boolean.music_sync"}},
                              {"action": "input_boolean.turn_on", "target": {"entity_id": FLAG}}],
                     "else": [{"delay": {"seconds": 30}},
                              {"condition": "template", "value_template": "{{ not (%s) }}" % IN_MEDIA[3:-3]},
                              {"if": [{"condition": "template", "value_template": OURS}],
                               "then": [{"action": "input_boolean.turn_off", "target": {"entity_id": "input_boolean.music_sync"}}]},
                              {"action": "input_boolean.turn_off", "target": {"entity_id": FLAG}}]}],
        "mode": "restart",
        "variables": {"media_apps": MEDIA_APPS},
    }
    requests.post(f"{HA}/api/config/automation/config/sh_music_follows_apps", headers=H, timeout=10, json=auto).raise_for_status()


def main():
    ws = ws_connect()
    if requests.get(f"{HA}/api/states/{FLAG}", headers=H, timeout=10).status_code == 404:
        ws.call(type="input_boolean/create", name="light_sync_music_by_apps", icon="mdi:television-play")  # the id
        ws.call(type="input_boolean/update", input_boolean_id="light_sync_music_by_apps",
                name="Music turned on by the TV apps", icon="mdi:television-play")
        print(f"made {FLAG}")
    ws.call(type="homeassistant/expose_entity", assistants=["conversation"], entity_ids=[FLAG], should_expose=False)
    post_music_automation()
    print("automation 'Music sync follows media apps': on only when an app opens and nothing is on, off only its own")
    # the Quick scripts (their definitions live in phone_setup.py and simplify.py)
    import phone_setup
    phone_setup.post_scripts()
    import simplify
    cfg = simplify.SCRIPTS["normal_light"]
    requests.post(f"{HA}/api/config/script/config/normal_light", headers=H, timeout=10, json=cfg).raise_for_status()
    print("script.normal_light: cancels a pending All off / Good night, restarts on a second tap")


if __name__ == "__main__":
    main()
