"""The switched-off extras, inside the modes they belong to (Nov 6, owner: "put them inside modes"). Safe to run again.

  Smart home · Comfort at night         Late-night mode on (the schedule, 23:00-06:00) -> Comfort on (softer, slower,
                                        warmer, dimmer: input_boolean.light_sync_comfort); late-night off -> Comfort off.
  Smart home · Party extras             Music sync on -> Top pulses on the beat + reaction follows the song's rhythm;
                                        Music sync off -> both off again (TvD and Corner keep their own pulse switches).
  Smart home · Bedtime reminder every evening
                                        18:00: the bedtime reminder armed again (it switches itself off after it rings),
                                        so the bedtime picked once in the Sleep tab reminds every night.
  Effects stays a mode of its own (under "More modes" in the app).
  Dusk -> Normal moved to Light Sync (light_sync/parts/guard.py): it waits until the owner is in the room; the old
  "Auto mode: dusk" automation turned the lights on in an empty room and Lights follow me switched them off again.

Run: py tools/ha_setup/inside_modes.py"""
import os
import sys

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # tools\ (export_ha)
import export_ha as ex  # noqa: E402

HA = os.getenv("HA_URL", "http://192.168.1.10:8123")
H = {"Authorization": f"Bearer {ex.token()}"}
COMFORT = "input_boolean.light_sync_comfort"
LATE = "input_boolean.late_night_mode"
MUSIC = "input_boolean.music_sync"
PARTY_EXTRAS = ["input_boolean.light_sync_pulse_wash", "input_boolean.light_sync_reaction_auto"]  # wash = Top
REMIND = "input_boolean.light_sync_bedtime_reminder"


def follow(alias, desc, src, targets):
    """targets on when src turns on, off when it turns off (and set right after a restart)."""
    return {"alias": alias, "description": desc + " tools/ha_setup/inside_modes.py",
            "triggers": [{"trigger": "state", "entity_id": src, "to": ["on", "off"]},
                         {"trigger": "homeassistant", "event": "start"}],
            "conditions": [],
            "actions": [{"if": [{"condition": "state", "entity_id": src, "state": "on"}],
                         "then": [{"action": "input_boolean.turn_on", "target": {"entity_id": targets}}],
                         "else": [{"action": "input_boolean.turn_off", "target": {"entity_id": targets}}]}],
            "mode": "queued"}


AUTOMATIONS = {
    "sh_comfort_at_night": follow("Smart home · Comfort at night",
                                  "Late-night mode on: Comfort on (softer, slower, warmer, dimmer); off with it.",
                                  LATE, [COMFORT]),
    "sh_party_extras": follow("Smart home · Party extras",
                              "Music sync on: Top pulses on the beat and the reaction follows the song's rhythm; off with it.",
                              MUSIC, PARTY_EXTRAS),
    "sh_bedtime_reminder_daily": {
        "alias": "Smart home · Bedtime reminder every evening",
        "description": "18:00: the bedtime reminder is armed again, so the bedtime picked in the Sleep tab reminds every "
                       "night. tools/ha_setup/inside_modes.py",
        "triggers": [{"trigger": "time", "at": "18:00:00"}],
        "conditions": [],
        "actions": [{"action": "input_boolean.turn_on", "target": {"entity_id": REMIND}}],
        "mode": "single"},
}


PARTY_PRO = "input_boolean.light_sync_party_pro"  # Party Pro DJ: TvD plays Govee scenes by the music (parts/party_dj.py)
TV_CORNER = "input_select.light_sync_tv_corner"  # TV mode: Corner's colour rule (light_sync/parts/config.py)
TV_CORNER_RULES = ["Colour harmony", "True to the film", "Contrast", "Film mood"]


def main():
    import export_ha
    fv = "input_number.light_sync_full_volume"  # the volume dimmer's "full brightness at volume" (0 = Auto)
    if requests.get(f"{HA}/api/states/{fv}", headers=H, timeout=10).status_code == 404:
        ws = export_ha.WebSocket(HA)
        ws.recv()
        ws.send({"type": "auth", "access_token": export_ha.token()})
        ws.recv()
        ws.call(type="input_number/create", name="light_sync_full_volume", min=0, max=100, step=5, initial=0,
                mode="slider", unit_of_measurement="%", icon="mdi:volume-high")
        ws.call(type="homeassistant/expose_entity", assistants=["conversation"], entity_ids=[fv], should_expose=False)
        print(f"made {fv} (0 = Auto)")
    if requests.get(f"{HA}/api/states/{PARTY_PRO}", headers=H, timeout=10).status_code == 404:
        ws = export_ha.WebSocket(HA)
        ws.recv()
        ws.send({"type": "auth", "access_token": export_ha.token()})
        ws.recv()
        ws.call(type="input_boolean/create", name="light_sync_party_pro", icon="mdi:album")
        ws.call(type="homeassistant/expose_entity", assistants=["conversation"], entity_ids=[PARTY_PRO], should_expose=False)
        requests.post(f"{HA}/api/services/input_boolean/turn_on", headers=H, timeout=10, json={"entity_id": PARTY_PRO})
        print(f"made {PARTY_PRO} (on)")
    if requests.get(f"{HA}/api/states/{TV_CORNER}", headers=H, timeout=10).status_code == 404:
        ws = export_ha.WebSocket(HA)
        ws.recv()
        ws.send({"type": "auth", "access_token": export_ha.token()})
        ws.recv()
        ws.call(type="input_select/create", name="light_sync_tv_corner", options=TV_CORNER_RULES, icon="mdi:palette")
        ws.call(type="homeassistant/expose_entity", assistants=["conversation"], entity_ids=[TV_CORNER], should_expose=False)
        print(f"made {TV_CORNER} (Colour harmony)")
    for aid, cfg in AUTOMATIONS.items():
        requests.post(f"{HA}/api/config/automation/config/{aid}", headers=H, timeout=10, json={"id": aid, **cfg}).raise_for_status()
        print(f"automation {cfg['alias']}")
    # the old dusk rule: now in Light Sync (waits for the owner in the room)
    r = requests.delete(f"{HA}/api/config/automation/config/sh_auto_dusk", headers=H, timeout=10)
    print("Auto mode: dusk ->", "moved to Light Sync" if r.ok else f"not there ({r.status_code})")
    # set everything right now
    late = requests.get(f"{HA}/api/states/{LATE}", headers=H, timeout=10).json()["state"]
    music = requests.get(f"{HA}/api/states/{MUSIC}", headers=H, timeout=10).json()["state"]
    for ids, on in (([COMFORT], late == "on"), (PARTY_EXTRAS, music == "on")):
        requests.post(f"{HA}/api/services/input_boolean/turn_{'on' if on else 'off'}", headers=H, timeout=10, json={"entity_id": ids})
    requests.post(f"{HA}/api/services/input_boolean/turn_on", headers=H, timeout=10, json={"entity_id": REMIND})
    print(f"now: Comfort {'on' if late == 'on' else 'off'}, Party extras {'on' if music == 'on' else 'off'}, bedtime reminder armed")


if __name__ == "__main__":
    main()
