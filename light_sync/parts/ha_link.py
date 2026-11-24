"""Talking to Home Assistant: the signed-in session, reading a state, calling a service."""

import os
import requests
import time

from parts.config import HA_TOKEN, HA_URL


session = requests.Session()

session.headers.update({"Authorization": f"Bearer {HA_TOKEN}"})

OWN_LIGHT_AT = [0.0]  # when Light Sync itself last sent a light command through Home Assistant (not the owner)

def ha_call(service, data):
    OWN_LIGHT_AT[0] = time.time()
    r = session.post(f"{HA_URL}/api/services/light/{service}", json=data, timeout=3)
    r.raise_for_status()

def ha_state(entity_id):
    r = session.get(f"{HA_URL}/api/states/{entity_id}", timeout=3)
    r.raise_for_status()
    return r.json()

def set_switch(entity_id, on):
    try:
        session.post(f"{HA_URL}/api/services/input_boolean/turn_{'on' if on else 'off'}",
                     json={"entity_id": entity_id}, timeout=3)
    except Exception as e:
        print(f"Couldn't switch {entity_id}: {e}", flush=True)

def lan_ip():
    """This machine's address on the home network (the one it uses to reach Home Assistant)."""
    import socket
    from urllib.parse import urlparse
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # UDP connect: nothing is sent. The Pi add-on reaches HA internally, so it probes LAN_PROBE instead
        s.connect((os.getenv("LAN_PROBE") or urlparse(HA_URL).hostname or "192.168.0.1", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()
