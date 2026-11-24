#!/usr/bin/env python3
"""
presence_setup.py - home and away the reliable way (Nov 11, owner: "never turn the lights on when I'm not in the room";
a day out: the sunrise put TvD on at 6:51 with nobody home, Maghrib put Normal on at 16:51, and Room guard never armed,
because the phone's location had been stuck on "home" since Friday and "away" needed it).

Light Sync (the Pi, light_sync/parts/guard.py) decides and keeps two switches up to date:
  input_boolean.phone_home_now  the phone on the home Wi-Fi in the last 15 min (proof of home: lights may turn on)
  input_boolean.owner_away      the phone gone 20 min (60 at night) AND nobody seen in the room 20 min AND the location
                                not freshly "home" -> Light Sync switches everything off and sends "You left"
Here:
  Room guard follows me         on when owner_away goes on, off when it goes off (no longer the stuck location)
  Sunrise wake-up               only with phone_home_now on

  py tools/ha_setup/presence_setup.py
"""
import os
import sys

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # tools\ (export_ha)
import export_ha as ex  # noqa: E402

HA = os.getenv("HA_URL", "http://192.168.1.10:8123")
H = {"Authorization": f"Bearer {ex.token()}"}
AWAY, HOME_NOW, GUARD = "input_boolean.owner_away", "input_boolean.phone_home_now", "input_boolean.room_guard"


def main():
    ws = ex.WebSocket(HA)
    ws.sock.settimeout(60)
    ws.recv()
    ws.send({"type": "auth", "access_token": ex.token()})
    assert ws.recv().get("type") == "auth_ok"
    have = {x["id"] for x in ws.call(type="input_boolean/list")}
    for key, name, icon in (("owner_away", "Away (Light Sync)", "mdi:home-export-outline"),
                            ("phone_home_now", "Phone home now (Light Sync)", "mdi:cellphone-wireless")):
        if key not in have:
            r = ws.call(type="input_boolean/create", name=key.replace("_", " "), icon=icon)
            ws.call(type="input_boolean/update", input_boolean_id=r["id"], name=name, icon=icon)
            print(f"made input_boolean.{r['id']}")
    ws.call(type="homeassistant/expose_entity", assistants=["conversation"], entity_ids=[AWAY, HOME_NOW], should_expose=False)

    auto = {"id": "sh_room_guard_follows_me", "alias": "Smart home · Room guard follows me",
            "description": "Room guard on when Light Sync says you've left (your phone off the home Wi-Fi and nobody in the "
                           "room), off when you're back. tools/ha_setup/presence_setup.py (Nov 11: the location was stuck).",
            "triggers": [{"trigger": "state", "entity_id": AWAY, "to": "on", "id": "away"},
                         {"trigger": "state", "entity_id": AWAY, "to": "off", "id": "home"}],
            "conditions": [],
            "actions": [{"choose": [
                {"conditions": [{"condition": "trigger", "id": "away"}],
                 "sequence": [{"action": "input_boolean.turn_on", "target": {"entity_id": GUARD}}]},
                {"conditions": [{"condition": "trigger", "id": "home"}],
                 "sequence": [{"action": "input_boolean.turn_off", "target": {"entity_id": GUARD}}]}]}],
            "mode": "single"}
    requests.post(f"{HA}/api/config/automation/config/{auto['id']}", headers=H, json=auto, timeout=10).raise_for_status()
    print("automation Room guard follows me")

    sid = "sh_sunrise_wakeup"
    cfg = requests.get(f"{HA}/api/config/automation/config/{sid}", headers=H, timeout=10).json()
    cond = {"condition": "state", "entity_id": HOME_NOW, "state": "on"}
    if cond not in cfg.get("conditions", []):
        cfg["conditions"] = cfg.get("conditions", []) + [cond]
        cfg["description"] = cfg.get("description", "") + " Only with your phone home (Nov 11: it lit TvD at 6:51 with nobody home)."
    requests.post(f"{HA}/api/config/automation/config/{sid}", headers=H, json={**cfg, "id": sid}, timeout=10).raise_for_status()
    print("automation Sunrise wake-up: only with the phone home")


if __name__ == "__main__":
    main()
