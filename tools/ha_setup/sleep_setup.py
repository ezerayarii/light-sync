#!/usr/bin/env python3
"""
sleep_setup.py - the sleep calculator's Home Assistant side (Oct 21, owner: "I use sleepytime.cc each time, can you
have it in my app"). The calculator itself is in the Light Sync app's Sleep tab (light_sync/web/app.html): sleep
cycles of 90 min plus the time to fall asleep (15 min, like Sleepytime). Safe to run again.

  input_datetime.light_sync_bedtime          the bedtime picked in the app ("Remind me")
  input_boolean.light_sync_bedtime_reminder  on: a notification at that bedtime, once (then it switches itself off)
  Smart home · Bedtime reminder              at the bedtime: "Time for bed" on Rocky, with the wake-up time and how
                                             many cycles that gives (the sunrise wake-up, input_datetime.wake_up_time)

  py tools/ha_setup/sleep_setup.py
"""
import os
import sys

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # tools\ (export_ha)
import export_ha as ex  # noqa: E402

HA = os.getenv("HA_URL", "http://192.168.1.10:8123")
H = {"Authorization": f"Bearer {ex.token()}"}
BEDTIME = "input_datetime.light_sync_bedtime"
REMIND = "input_boolean.light_sync_bedtime_reminder"
WAKE = "input_datetime.wake_up_time"
NOTIFY = "script.notify_me"  # Telegram (telegram_setup.py)
FALL_MIN = 15  # minutes to fall asleep, like Sleepytime (the app uses the same)


def ws_connect():
    ws = ex.WebSocket(HA)
    ws.sock.settimeout(60)
    ws.recv()
    ws.send({"type": "auth", "access_token": ex.token()})
    assert ws.recv().get("type") == "auth_ok"
    return ws


def main():
    ws = ws_connect()
    if requests.get(f"{HA}/api/states/{BEDTIME}", headers=H, timeout=10).status_code == 404:
        ws.call(type="input_datetime/create", name="light_sync_bedtime", has_date=False, has_time=True)  # the id
        ws.call(type="input_datetime/update", input_datetime_id="light_sync_bedtime", name="Bedtime",
                has_date=False, has_time=True, icon="mdi:bed-clock")
        print(f"made {BEDTIME}")
    if requests.get(f"{HA}/api/states/{REMIND}", headers=H, timeout=10).status_code == 404:
        ws.call(type="input_boolean/create", name="light_sync_bedtime_reminder")
        ws.call(type="input_boolean/update", input_boolean_id="light_sync_bedtime_reminder", name="Bedtime reminder",
                icon="mdi:bell-sleep")
        print(f"made {REMIND}")
    ws.call(type="homeassistant/expose_entity", assistants=["conversation"], entity_ids=[BEDTIME, REMIND], should_expose=False)
    # the message: the wake-up time and the sleep it gives (falling asleep FALL_MIN after the reminder)
    msg = ("{% set w = states('" + WAKE + "')[:5] %}"
           "{% set b = now().hour * 60 + now().minute + " + str(FALL_MIN) + " %}"
           "{% set wm = (w[:2] | int) * 60 + (w[3:5] | int) %}"
           "{% set mins = (wm - b) % 1440 %}"
           "{% set cycles = (mins / 90) | round(0, 'floor') | int %}"
           "Fall asleep in the next ~" + str(FALL_MIN) + " min and you wake up at {{ w }} after {{ cycles }} sleep "
           "cycles ({{ (mins / 60) | round(1) }} h){{ ', with the sunrise' if is_state('input_boolean.wake_up_light', 'on') else '' }}.")
    auto = {
        "id": "sh_bedtime_reminder",
        "alias": "Smart home · Bedtime reminder",
        "description": "The bedtime picked in the Light Sync app's sleep calculator: a notification on Rocky, once "
                       "(tools/ha_setup/sleep_setup.py).",
        "triggers": [{"trigger": "time", "at": BEDTIME}],
        "conditions": [{"condition": "state", "entity_id": REMIND, "state": "on"}],
        "actions": [{"action": NOTIFY, "data": {"title": "Time for bed", "message": msg, "level": "info", "topic": "sleep"}},
                    {"action": "input_boolean.turn_off", "target": {"entity_id": REMIND}}],
        "mode": "single",
    }
    requests.post(f"{HA}/api/config/automation/config/sh_bedtime_reminder", headers=H, timeout=10, json=auto).raise_for_status()
    print("automation 'Smart home · Bedtime reminder'")


if __name__ == "__main__":
    main()
