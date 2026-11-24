"""Light Sync add-on entry: settings from the add-on options, then the same light_sync.py as the laptop,
as the backup (stands by while the laptop's Light Sync runs). No secrets here: HA is reached through the
Supervisor with the token it gives the add-on, the camera through HA."""
import json
import os
import signal
import sys

with open("/data/options.json", encoding="utf-8") as f:
    opts = json.load(f)

os.environ.update({
    "ROLE": "backup",
    "LIGHT_SYNC_HOST": "pi",
    "HA_URL": "http://supervisor/core",
    "HA_TOKEN": os.environ.get("SUPERVISOR_TOKEN", ""),
    "LIGHT_SCREEN": opts["light_screen"],
    "LIGHT_BEAT": opts["light_beat"],
    "LIGHT_WASH": opts["light_wash"],
    "GOVEE_SCREEN": opts.get("govee_screen") or "",
    "GOVEE_BEAT": opts.get("govee_beat") or "",
    "GOVEE_WASH": opts.get("govee_wash") or "",
    "GOVEE_FIND": "arp",
    "SNAPSHOT_URL": f"http://supervisor/core/api/camera_proxy/{opts['camera']}" if opts.get("camera") else "",
    "ROOM_CAM_URL": f"http://supervisor/core/api/camera_proxy/{opts['room_camera']}" if opts.get("room_camera") else "",
    "AUDIO": "pulse",
    "VIEW_PORT": "0",  # no live view on the Pi (it would show the bedroom camera)
    "SHOW_PORT": str(opts.get("show_port", 8090)),
    "GAME_FROM": opts.get("game_from") or "192.168.1.20",  # the laptop's game helper: a game's colours (Nov 6)
    "LAN_PROBE": "1.1.1.1",  # find the Pi's home-network address for the show link (nothing is sent)
    "AMBIENT_BRIGHT": str(opts.get("ambient_bright", 150)),
    "SCREEN_SEGMENTS": str(opts.get("screen_segments", 0)),
    "SCREEN_SEGMENTS_FROM_RIGHT": "1" if opts.get("screen_segments_from_right") else "0",
})
sys.argv.append("--no-browser")

# the home's time zone (Nov 8): Home Assistant's, so the local clock is the owner's (it was UTC: no tzdata in the image)
if not os.environ.get("TZ"):
    try:
        import urllib.request
        req = urllib.request.Request("http://supervisor/core/api/config", headers={"Authorization": "Bearer " + os.environ.get("SUPERVISOR_TOKEN", "")})
        os.environ["TZ"] = json.load(urllib.request.urlopen(req, timeout=10))["time_zone"]
    except Exception as e:
        print(f"Time zone not found ({e}) - the clock is UTC", flush=True)
import time  # noqa: E402
time.tzset()
print(f"Time zone: {os.environ.get('TZ', 'UTC')} - now {time.strftime('%H:%M %Z')}", flush=True)


def stop(*_):
    raise KeyboardInterrupt  # add-on stopped: restore the lights (if driving them) and tell the dashboard


signal.signal(signal.SIGTERM, stop)
sys.path.insert(0, "/app")
import light_sync  # noqa: E402  (reads the settings above when imported)
import app  # noqa: E402

app.start(8097)  # the sidebar app (ingress_port in config.yaml)
light_sync.main()
