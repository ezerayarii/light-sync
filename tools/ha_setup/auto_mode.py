"""Auto mode (Nov 3, owner: "make everything as my plan", the plan: the room picks the mode by itself, you only correct it).

  input_boolean.light_sync_auto_mode  "Auto mode" (on). Off: nothing below happens; the older automatic things stay as they were.
  Smart home · Auto mode: the TV       a film app opens on the box and no mode is on: Prime Video / IPTV -> Movie,
                                       YouTube / mirroring -> Movie + music. 30 s after leaving the apps: Normal, but only
                                       if Auto mode switched the film on (nothing the owner picked is overridden).
  Dusk -> Normal: in Light Sync since Nov 6 (light_sync/parts/guard.py), so it waits until the owner is in the room.
  Light Sync (light_sync/parts/guard.py): with Auto mode on, Lights follow me fades the lights out with Good night
  at night instead of switching them off at once.
  While Auto mode is on, "Music sync follows media apps" (tools/ha_setup/switching.py) leaves the box to the TV rule.

Run: py tools/ha_setup/auto_mode.py"""
import os
import sys

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # tools\ (export_ha)
import export_ha as ex  # noqa: E402

HA = os.getenv("HA_URL", "http://192.168.1.10:8123")
H = {"Authorization": f"Bearer {ex.token()}"}
AUTO = "input_boolean.light_sync_auto_mode"
FLAG = "input_boolean.light_sync_film_by_auto"  # Auto mode switched the film on (so only that one is switched back)
BOX = "media_player.x96q_pro_plus"
MODES = ["input_boolean.music_sync", "input_boolean.tv_ambient", "input_boolean.gaming_mode", "input_boolean.normal_mode"]
FILM_APPS = ["com.amazon.amazonvideo.livingroom", "com.iptvav.av_iptv"]
SOUND_APPS = ["com.google.android.youtube.tv", "com.softwinner.miracastReceiver"]
IN_APPS = "{{ is_state('%s','on') and state_attr('%s','app_id') in film_apps + sound_apps }}" % (BOX, BOX)
WAS_IN_APPS = ("trigger.from_state is not none and trigger.from_state.state == 'on' "
               "and trigger.from_state.attributes.app_id in film_apps + sound_apps")
# home, as in tools/ha_setup/room_guard.py: the phone on the home Wi-Fi or the location says home
HOME = {"condition": "or", "conditions": [
    {"condition": "state", "entity_id": "input_boolean.phone_on_wifi", "state": "on"},
    {"condition": "state", "entity_id": "person.ezer", "state": "home"}]}


def ws_connect():
    ws = ex.WebSocket(HA)
    ws.sock.settimeout(60)
    ws.recv()
    ws.send({"type": "auth", "access_token": ex.token()})
    assert ws.recv().get("type") == "auth_ok"
    return ws


AUTOMATIONS = {
    "sh_auto_tv": {
        "alias": "Smart home · Auto mode: the TV",
        "description": "Auto mode on, a film app opens on the X96Q box and no mode is on: Prime Video / IPTV -> Movie, "
                       "YouTube / mirroring -> Movie + music. 30 s after leaving the apps: Normal, only if this automation "
                       "switched the film on. tools/ha_setup/auto_mode.py",
        "triggers": [{"trigger": "state", "entity_id": BOX, "attribute": "app_id"},
                     {"trigger": "state", "entity_id": BOX, "to": None}],
        "conditions": [{"condition": "state", "entity_id": AUTO, "state": "on"}],
        "actions": [{"if": [{"condition": "template", "value_template": IN_APPS}],
                     "then": [{"condition": "template", "value_template": "{{ not (%s) }}" % WAS_IN_APPS},  # just opened
                              {"condition": "state", "entity_id": MODES, "state": "off"},  # nothing picked
                              {"if": [{"condition": "template",
                                       "value_template": "{{ state_attr('%s','app_id') in film_apps }}" % BOX}],
                               "then": [{"action": "script.turn_on", "target": {"entity_id": "script.movie_time"}}],
                               "else": [{"action": "script.turn_on", "target": {"entity_id": "script.movie_music"}}]},
                              {"action": "input_boolean.turn_on", "target": {"entity_id": FLAG}}],
                     "else": [{"delay": {"seconds": 30}},
                              {"condition": "template", "value_template": "{{ not (%s) }}" % IN_APPS[3:-3]},
                              {"condition": "state", "entity_id": FLAG, "state": "on"},
                              {"action": "input_boolean.turn_off", "target": {"entity_id": FLAG}},
                              {"condition": "state", "entity_id": "input_boolean.tv_ambient", "state": "on"},
                              {"action": "script.turn_on", "target": {"entity_id": "script.normal_light"}}]}],
        "mode": "restart",
        "variables": {"film_apps": FILM_APPS, "sound_apps": SOUND_APPS},
    },
    # "sh_auto_dusk" (Nov 3) moved to Light Sync on Nov 6 (light_sync/parts/guard.py: waits for the owner in the room)
}


def main():
    ws = ws_connect()
    if requests.get(f"{HA}/api/states/{AUTO}", headers=H, timeout=10).status_code == 404:
        ws.call(type="input_boolean/create", name="light sync auto mode", icon="mdi:auto-mode")
        requests.post(f"{HA}/api/services/input_boolean/turn_on", headers=H, timeout=10, json={"entity_id": AUTO})
        print(f"made {AUTO} (on)")
    if requests.get(f"{HA}/api/states/{FLAG}", headers=H, timeout=10).status_code == 404:
        ws.call(type="input_boolean/create", name="light sync film by auto", icon="mdi:movie-cog")
        print(f"made {FLAG}")
    ws.call(type="homeassistant/expose_entity", assistants=["conversation"], entity_ids=[AUTO, FLAG], should_expose=False)
    for aid, cfg in AUTOMATIONS.items():
        requests.post(f"{HA}/api/config/automation/config/{aid}", headers=H, timeout=10, json={"id": aid, **cfg}).raise_for_status()
        print(f"automation {cfg['alias']}")


if __name__ == "__main__":
    main()
