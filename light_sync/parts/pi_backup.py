"""The handover: is the laptop alive, and what the Pi takes over from it."""

import requests
import time

from parts.config import (
    HA_HEARTBEAT, HA_URL, HOST, MAIN_TIMEOUT, ROLE,
)
from parts import config, ha_link


MAIN_SEEN = 0.0  # backup: last time the laptop answered

PLAIN = requests.Session()  # no HA token on this one: it only talks to the laptop's show server

def main_alive():
    """Backup: is the laptop's Light Sync running? Its heartbeat in HA says where it is (and "off" when it
    stopped normally); then the laptop itself must answer /alive. A sleeping or closed laptop doesn't answer:
    after MAIN_TIMEOUT seconds of silence the Pi takes over."""
    global MAIN_SEEN
    now = time.time()
    ha_says_stale = False
    try:
        st = ha_link.ha_state(HA_HEARTBEAT)
        config.MAIN_INFO = st.get("attributes", {})
        if st["state"] != "on":
            return False  # stopped normally: take over right away
        # how long since the laptop's last heartbeat (only templates see this live; the REST state's
        # last_reported doesn't move when the same state is posted again)
        r = ha_link.session.post(f"{HA_URL}/api/template", timeout=3, json={
            "template": "{{ (now() - states.%s.last_reported).total_seconds() }}" % HA_HEARTBEAT})
        if float(r.text) < MAIN_TIMEOUT:
            MAIN_SEEN = now
            return True
        ha_says_stale = True
    except Exception:
        pass  # HA restarting, or no heartbeat posted since: ask the laptop at its last known address
    url = config.MAIN_INFO.get("show_url")
    if url:
        try:
            a = PLAIN.get(url.rsplit("/show", 1)[0] + "/alive", timeout=1).json()
            if a.get("host") == "laptop" and not a.get("standby"):
                MAIN_SEEN = now
                return True
        except Exception:
            pass
    if ha_says_stale:
        return False  # silent for MAIN_TIMEOUT already, and not answering either
    return now - MAIN_SEEN < MAIN_TIMEOUT  # HA couldn't tell: keep the last answer a while

def adopt_restore():
    """Light states to go back to that the other machine saved, if it is driving the lights right now
    (so taking over doesn't 'restore' to the show's colors)."""
    try:
        if ROLE == "backup":
            st = {"state": "on", "attributes": config.MAIN_INFO}
        else:
            st = ha_link.ha_state("sensor.music_sync_status")
            if st.get("attributes", {}).get("host") == HOST or st["state"] in ("offline", "paused"):
                return None
        restore = st.get("attributes", {}).get("restore")
        return restore if isinstance(restore, dict) and restore else None
    except Exception:
        return None

MAIN_ALIVE = False  # backup: the laptop's Light Sync is running (checked every second)

def watch_main_loop():
    global MAIN_ALIVE
    while True:
        MAIN_ALIVE = main_alive()
        time.sleep(1)
