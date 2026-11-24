#!/usr/bin/env python3
"""
room_cctv.py - the room camera, live, for the owner (Nov 7, owner: "I want the CCTV of the room back"; chose the app,
the dashboard and a Telegram snapshot; live only, nothing saved; the room camera only).

  Dashboard   a "Room camera" tab on the Smart home dashboard: the live picture (signed-in users only).
  Telegram    /cam sends one picture of the room to the owner's chat (it goes through Telegram's servers: the owner's
              choice). Added by telegram_setup.py; this file only makes the tab.
  App         More -> Room camera (pi_addon app.py /roomcam).
Nothing is recorded or kept anywhere.

  py tools/ha_setup/room_cctv.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # tools\ (export_ha)
import export_ha as ex  # noqa: E402

DASH = "smart-home"
CAM = "camera.bedroom_camera"  # the room camera (motionEye Camera1); the Pi add-on's room_camera

view = {"title": "Room camera", "path": "camera", "icon": "mdi:cctv", "type": "panel",
        "cards": [{"type": "picture-entity", "entity": CAM, "camera_view": "live", "show_name": False, "show_state": False,
                   "tap_action": {"action": "more-info"}}]}


def main():
    ws = ex.WebSocket(os.getenv("HA_URL", "http://192.168.1.10:8123"))
    ws.recv()
    ws.send({"type": "auth", "access_token": ex.token()})
    assert ws.recv().get("type") == "auth_ok", "HA login failed"
    cfg = ws.call(type="lovelace/config", url_path=DASH)
    views = [v for v in cfg["views"] if v.get("path") not in ("room-camera", "camera")]  # the old "Camera" tab too
    at = next((i + 1 for i, v in enumerate(views) if v.get("path") == "remote"), len(views))
    views.insert(at, view)
    cfg["views"] = views
    ws.call(type="lovelace/config/save", url_path=DASH, config=cfg)
    print("Room camera tab saved:", [v.get("title") for v in cfg["views"]])


if __name__ == "__main__":
    main()
