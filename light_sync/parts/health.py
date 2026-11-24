"""The home's health, checked by whichever machine drives the lights (the Pi): the internet, the lights, the cameras,
the laptop. Problems first (Nov 7, owner: Telegram "more networking", "problems first"): a message as soon as something
breaks, grouped, with a fix button where there is one, and one when it's fixed. /check and /net answer from here.

TvD stuck (Nov 7: after a Pi restart the strip stayed switched off and ignored commands until it was unplugged): Light
Sync drives TvD but Home Assistant says it's off for 1 min -> it switches it on twice itself; still off -> a message.
Numbers and names only: nothing about who is home."""
import socket
import threading
import time

from parts.config import HA_URL
from parts import config, eyes, ha_link

LIGHTS = {}          # role -> the Light worker (set by start)
NAMES = {"screen": "TvD", "beat": "Corner", "wash": "Top"}
STATE = {"internet": None, "internet_down_at": 0.0, "lights": {}, "tvd_off_at": 0.0, "tvd_fixes": 0, "told": set(),
         "checked_at": 0.0, "speed": None, "tv_off_at": 0.0}
TV_BOX = "media_player.x96q_pro_plus"
TV_IDLE_S = 20 * 60  # TV mode with the TV box off this long: Rocky asks (Nov 7 plan, step 4)
LAPTOP = "192.168.1.20"


def can_reach(host, port, timeout=2.0):
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def internet():
    """Up if either of two big services answers (no data sent: a connection only)."""
    return can_reach("1.1.1.1", 443) or can_reach("8.8.8.8", 53)


def notify(title, message, level="important", buttons=None):
    if not config.HA_TOKEN:  # no key (a test): nothing to send (Nov 8)
        return
    try:
        data = {"title": title, "message": message, "level": level, "topic": "system"}
        if buttons:
            data["buttons"] = buttons
        ha_link.session.post(f"{HA_URL}/api/services/script/notify_me", json=data, timeout=5)
    except Exception as e:
        print(f"Health: couldn't send a message ({type(e).__name__})", flush=True)


def light_states():
    """{role: (Home Assistant state, Light Sync drives it)}."""
    out = {}
    for role, w in LIGHTS.items():
        try:
            st = ha_link.ha_state(w.entity_id)["state"]
        except Exception:
            st = "?"
        out[role] = (st, bool(w.enabled and config.RUNNING and not config.STANDBY))
    return out


def fix_tvd():
    """Switch TvD on over the LAN (twice: a UDP command can drop) and stream again."""
    w = LIGHTS.get("screen")
    if not w:
        return False
    with w.lock:
        w.power_on = 2
        w.razer, w.sent = False, None
    w.wake.set()
    return True


def check_once():
    now = time.time()
    # the internet
    up = internet()
    was = STATE["internet"]
    if up is False and was is not False:
        STATE["internet_down_at"] = now
        print("Health: the internet is down", flush=True)
    if up and was is False:
        down = now - STATE["internet_down_at"]
        print(f"Health: the internet is back after {down / 60:.0f} min", flush=True)
        if down > 120:  # a message can only go out now, through the internet
            notify("Internet", f"The internet was down from {time.strftime('%H:%M', time.localtime(STATE['internet_down_at']))} "
                   f"to {time.strftime('%H:%M')} ({down / 60:.0f} min). The lights kept working (they're local).")
    STATE["internet"] = up
    # the lights
    states = light_states()
    for role, (st, driving) in states.items():
        key = f"gone_{role}"
        if st == "unavailable":
            STATE["lights"].setdefault(role, now)
            if now - STATE["lights"][role] > 300 and key not in STATE["told"]:
                STATE["told"].add(key)
                notify("Light not answering", f"{NAMES[role]} hasn't answered for 5 min. Check it's plugged in; "
                       "unplugging it for 10 s usually brings it back.")
        else:
            STATE["lights"].pop(role, None)
            if key in STATE["told"]:
                STATE["told"].discard(key)
                notify("Light back", f"{NAMES[role]} answers again.", level="info")
    st, driving = states.get("screen", ("?", False))
    w = LIGHTS.get("screen")
    meant_off = w is not None and (getattr(w, "forced_dark", False) or getattr(w, "level", 1) <= 0)  # Normal, or level 0
    if driving and st == "off" and not meant_off:
        STATE["tvd_off_at"] = STATE["tvd_off_at"] or now
        if now - STATE["tvd_off_at"] > 60:
            if STATE["tvd_fixes"] < 2:
                STATE["tvd_fixes"] += 1
                print(f"Health: TvD is off while Light Sync drives it - switching it on (try {STATE['tvd_fixes']})", flush=True)
                fix_tvd()
                if STATE["tvd_fixes"] == 1:
                    from parts import learner
                    learner.did("fix_tvd")
                STATE["tvd_off_at"] = now - 30  # look again in ~30 s
            elif "tvd_stuck" not in STATE["told"]:
                STATE["told"].add("tvd_stuck")
                notify("TvD is stuck", "TvD stays switched off and ignores Light Sync (I tried twice). "
                       "Unplug its power for 10 s, then plug it back in.", buttons=["Try again:/fixtvd"])
    else:
        if "tvd_stuck" in STATE["told"] and st == "on":
            notify("TvD is back", "TvD is on and following Light Sync again.", level="info")
        STATE["tvd_off_at"], STATE["tvd_fixes"] = 0.0, 0
        STATE["told"].discard("tvd_stuck")
    # Rocky asks (Nov 7 plan, step 4: Rocky as the house's brain): TV mode on, the TV box off for 20 min -> one message
    # with buttons (nothing about who is home; works with the laptop off)
    try:
        box = ha_link.ha_state(TV_BOX)["state"]
    except Exception:
        box = "?"
    tv_mode = config.RUNNING and config.AMBIENT_ON and not config.GAMING_ON
    if tv_mode and box in ("off", "standby", "unavailable"):
        STATE["tv_off_at"] = STATE["tv_off_at"] or now
        if now - STATE["tv_off_at"] > TV_IDLE_S and "tv_idle" not in STATE["told"]:
            STATE["told"].add("tv_idle")
            from parts import learner
            learner.did("tv_nudge")
            notify("TV mode", "TV mode is on but the TV box has been off for 20 min. Switch?", level="info",
                   buttons=["Normal:/normal, All off:/off"])
    else:
        STATE["tv_off_at"] = 0.0
        STATE["told"].discard("tv_idle")
    STATE["checked_at"] = now


def health_loop():
    time.sleep(60)  # let everything start first
    while True:
        if not config.STANDBY:
            try:
                check_once()
            except Exception as e:
                print(f"Health check: {e}", flush=True)
        time.sleep(60)


def check_text():
    """/check: everything at once, problems first."""
    states = light_states()
    bad, good = [], []
    for role, (st, driving) in states.items():
        line = f"{NAMES[role]}: {st}" + (" (Light Sync drives it)" if driving else "")
        (bad if st in ("unavailable", "?") or (role == "screen" and driving and st == "off") else good).append(line)
    up = internet()
    (good if up else bad).append(f"Internet: {'up' if up else 'DOWN'}")
    cam = "reading the TV" if not eyes.CAM_IDLE and eyes.CAM_OK else "idle (only TV mode and Gaming read it)" if eyes.CAM_IDLE else "NO PICTURE"
    (bad if cam == "NO PICTURE" else good).append(f"TV camera: {cam}")
    lap = can_reach(config.laptop_ip() or LAPTOP, 8090, 1.0)
    good.append(f"Laptop helper: {'on (Rocky AI ready)' if lap else 'off or asleep (fixed commands only)'}")
    head = f"Problems ({len(bad)}):\n" + "\n".join(bad) + "\n\n" if bad else "All good.\n\n"
    return head + "\n".join(good)


def net_text():
    """/net: the internet and the home network."""
    from parts import wifi
    up = internet()
    t0 = time.time()
    rtt = None
    if up:
        if can_reach("1.1.1.1", 443):
            rtt = (time.time() - t0) * 1000
    try:
        sp = {k: ha_link.ha_state(f"sensor.speedtest_{k}")["state"] for k in ("download", "upload")}
    except Exception:
        sp = {}
    rows = wifi.wifi_frame()["rows"]
    online = sum(1 for r in rows if r["online"])
    lines = [f"Internet: {'up' if up else 'DOWN'}" + (f", answer in {rtt:.0f} ms" if rtt else "")]
    if sp.get("download") not in (None, "unknown", "unavailable"):
        lines.append(f"Last speed test: {sp['download']} down / {sp.get('upload')} up (Mbit/s)")
    if STATE["internet_down_at"] and STATE["internet"]:
        lines.append(f"Last outage started {time.strftime('%a %H:%M', time.localtime(STATE['internet_down_at']))}")
    lines.append(f"Wi-Fi: {online} devices online now, {len(rows)} seen recently (/wifi for the list)")
    return "\n".join(lines)


def start(roles):
    for role, w in roles:
        LIGHTS[role] = w
    threading.Thread(target=health_loop, daemon=True, name="health").start()
