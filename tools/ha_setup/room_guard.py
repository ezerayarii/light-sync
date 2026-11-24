#!/usr/bin/env python3
"""
room_guard.py - "Movement in your room" on the owner's phone, only while away (Oct 20, owner: use the bedroom camera's
motionEye motion detection instead of a door sensor; changed the house rule "No camera-based presence in the bedroom"
to: motion alerts only while away, text only, nothing recorded). Safe to run again.

  input_boolean.room_guard   "Room guard": alerts only while it's on. On by itself 5 min after the owner leaves home
                             (person.ezer, needs the Home Assistant app's location set to Always), off when back; or by hand.
  Webhook (local only)       motionEye -> Camera1 -> Motion Notifications -> Call A Web Hook (when motion has started),
                             POST to the address this prints. Nothing else can reach it from outside the home network.
  Smart home · Movement in my room    webhook + Room guard on -> notification on Rocky, at most one every 2 minutes.
  Smart home · Room guard follows me  person.ezer away 5 min -> on; home -> off.
  Dashboard                  Room guard and Lights follow me on the front page's Home (tools/ha_setup/dashboard.py).
  input_boolean.lights_follow_me  "Lights follow me" (on): Light Sync turns all lights off 10 min after the owner
                             leaves the room and Normal on when they're back (the camera as numbers, the laptop in use,
                             the TV playing; Oct 21, the owner allowed the camera for this while home too).
  Room camera (Oct 27)       a second MeetUp, low, in the old spot: Room guard and Lights follow me read it (ROOM_CAM_URL;
                             the Pi add-on's room_camera option), the TV camera keeps the TV colours.
                             input_text.light_sync_room_tv_box: where the TV is in its picture, left out.

  py tools/ha_setup/room_guard.py
"""
import os
import secrets
import sys

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # tools\ (export_ha)
import export_ha as ex  # noqa: E402

HA = os.getenv("HA_URL", "http://192.168.1.10:8123")
H = {"Authorization": f"Bearer {ex.token()}"}
GUARD = "input_boolean.room_guard"
FOLLOW = "input_boolean.lights_follow_me"
ROOM_TV_BOX = "input_text.light_sync_room_tv_box"  # the TV in the room camera's picture, left out (Oct 27)
OWNER = "person.ezer"
PHONE_WIFI = "input_boolean.phone_on_wifi"  # Light Sync: the owner's phone on the home Wi-Fi (Nov 3, light_sync/parts/wifi.py)
# away: the location says not home and the phone isn't on the home Wi-Fi. The Wi-Fi only proves "home": Nov 5 the
# laptop lost the sleeping iPhone for 20 min while its GPS said home (5 m), and the lights went off and Room guard sent
# 17 "Movement in your room" alerts in 2.5 h with the owner in the room.
AWAY = {"condition": "and", "conditions": [
    {"condition": "not", "conditions": [{"condition": "state", "entity_id": PHONE_WIFI, "state": "on"}]},
    {"condition": "not", "conditions": [{"condition": "state", "entity_id": OWNER, "state": "home"}]}]}
NOTIFY = "notify.mobile_app_rocky"


def ws_connect():
    ws = ex.WebSocket(HA)
    ws.sock.settimeout(60)
    ws.recv()
    ws.send({"type": "auth", "access_token": ex.token()})
    assert ws.recv().get("type") == "auth_ok"
    return ws


def main():
    ws = ws_connect()
    if requests.get(f"{HA}/api/states/{GUARD}", headers=H, timeout=10).status_code == 404:
        ws.call(type="input_boolean/create", name="Room guard", icon="mdi:shield-home")
        print("made input_boolean.room_guard")
    if requests.get(f"{HA}/api/states/{FOLLOW}", headers=H, timeout=10).status_code == 404:
        ws.call(type="input_boolean/create", name="Lights follow me", icon="mdi:walk", initial=True)
        requests.post(f"{HA}/api/services/input_boolean/turn_on", headers=H, timeout=10, json={"entity_id": FOLLOW})
        print("made input_boolean.lights_follow_me (on)")
    if requests.get(f"{HA}/api/states/{PHONE_WIFI}", headers=H, timeout=10).status_code == 404:
        ws.call(type="input_boolean/create", name="phone on wifi", icon="mdi:cellphone-wireless")
        print(f"made {PHONE_WIFI} (Light Sync keeps it up to date)")
    if requests.get(f"{HA}/api/states/{ROOM_TV_BOX}", headers=H, timeout=10).status_code == 404:
        ws.call(type="input_text/create", name="light sync room tv box", min=0, max=40, icon="mdi:television-off")
        print(f"made {ROOM_TV_BOX} (tools/tests/find_screen.py --room --apply fills it)")
    r = requests.get(f"{HA}/api/config/automation/config/sh_room_motion", headers=H, timeout=10)
    hook = r.json()["triggers"][0]["webhook_id"] if r.ok else "room-motion-" + secrets.token_urlsafe(18)
    autos = {
        "sh_room_motion": {
            "alias": "Smart home · Movement in my room",
            "description": "motionEye (bedroom camera) saw movement, Room guard is on and the owner isn't home: a notification on "
                           "Rocky, at most one every 2 minutes. Text only, nothing recorded (tools/ha_setup/room_guard.py). Never "
                           "while home, even with Room guard on (Nov 2: switched on by hand after coming home, it alerted for 2 days).",
            "triggers": [{"trigger": "webhook", "webhook_id": hook, "allowed_methods": ["POST", "GET"], "local_only": True}],
            "conditions": [{"condition": "state", "entity_id": GUARD, "state": "on"}, AWAY],
            "actions": [{"action": NOTIFY, "data": {"title": "Movement in your room",
                                                    "message": "The bedroom camera saw movement at {{ now().strftime('%H:%M') }} "
                                                               "while you're away."}},
                        {"delay": {"minutes": 2}}],
            "mode": "single", "max_exceeded": "silent"},
        # (Nov 11: replaced by tools/ha_setup/presence_setup.py - Light Sync's "away"; run that after this)
        "sh_room_guard_follows_me": {
            "alias": "Smart home · Room guard follows me",
            "description": "Room guard on 5 min after the owner leaves home, off when back: the phone's location "
                           "(person.ezer) or the phone off / back on the home Wi-Fi (input_boolean.phone_on_wifi, Light Sync). "
                           "It can also be switched by hand.",
            "triggers": [{"trigger": "state", "entity_id": OWNER, "to": "not_home", "for": {"minutes": 5}, "id": "away"},
                         {"trigger": "state", "entity_id": PHONE_WIFI, "from": "on", "to": "off", "id": "away"},
                         {"trigger": "state", "entity_id": OWNER, "to": "home", "id": "home"},
                         {"trigger": "state", "entity_id": PHONE_WIFI, "from": "off", "to": "on", "id": "home"}],
            "conditions": [],
            "actions": [{"choose": [
                {"conditions": [{"condition": "trigger", "id": "away"}, AWAY],
                 "sequence": [{"action": "input_boolean.turn_on", "target": {"entity_id": GUARD}}]},
                {"conditions": [{"condition": "trigger", "id": "home"}],
                 "sequence": [{"action": "input_boolean.turn_off", "target": {"entity_id": GUARD}}]}]}],
            "mode": "single"},
    }
    for aid, cfg in autos.items():
        requests.post(f"{HA}/api/config/automation/config/{aid}", headers=H, timeout=10, json={"id": aid, **cfg}).raise_for_status()
        print(f"automation {cfg['alias']}")
    ws.call(type="homeassistant/expose_entity", assistants=["conversation"], entity_ids=[GUARD, FOLLOW, ROOM_TV_BOX, PHONE_WIFI], should_expose=False)
    # dashboard: the switches are on the front page's Home (tools/ha_setup/dashboard.py)
    import dashboard
    dashboard.build_overview(ws)
    print(f"\nmotionEye webhook (Camera1 > Motion Notifications > Call A Web Hook, method POST):\n  {HA}/api/webhook/{hook}")


if __name__ == "__main__":
    main()
