"""The app's system check (Oct 27, owner: "a Test button in the app"): what works and what doesn't, from the Pi.
Read only: it changes nothing, and a camera picture is only looked at as numbers (does one arrive, how fast).
Each check: {"name", "status": "ok" | "warn" | "fail" | "info", "detail"}."""
import json
import time

import cameras
import light_sync as ms

LIGHTS = (("TvD", "LIGHT_SCREEN"), ("Corner", "LIGHT_BEAT"), ("Top", "LIGHT_WASH"))


def _state(eid):
    try:
        return ms.ha_state(eid)
    except Exception:
        return {}


def _ago(iso):
    try:
        from datetime import datetime, timezone
        return (datetime.now(timezone.utc) - datetime.fromisoformat(iso)).total_seconds()
    except Exception:
        return None


def run(grab_img):
    out = []

    def add(name, status, detail=""):
        out.append({"name": name, "status": status, "detail": detail})

    # who drives the lights, and the laptop
    st = _state("sensor.light_sync").get("state", "unknown")
    driver = "the laptop" if ms.STANDBY else "the Pi (the laptop's Light Sync isn't running)"
    add("Light Sync", "fail" if st in ("offline", "unavailable", "unknown") else "ok", f"{st}, driven by {driver}")
    url = ms.MAIN_INFO.get("show_url")
    if ms.STANDBY and url:
        t0 = time.time()
        try:
            ms.PLAIN.get(url.rsplit("/show", 1)[0] + "/alive", timeout=2)
            ms_ = 1000 * (time.time() - t0)
            add("Pi to laptop", "ok" if ms_ < 150 else "warn", f"answers in {ms_:.0f} ms"
                + ("" if ms_ < 150 else " (slow: the Pi's Wi-Fi is busy or weak; an Ethernet cable fixes it)"))
        except Exception as e:
            add("Pi to laptop", "fail", f"no answer ({type(e).__name__})")
    elif not ms.STANDBY:
        add("Pi to laptop", "warn", "the laptop's Light Sync isn't running: the Pi took over")

    # the lights
    for name, var in LIGHTS:
        eid = getattr(ms, var, "")
        s = _state(eid).get("state")
        add(f"Light {name}", "fail" if s in (None, "unavailable", "unknown") else "ok", f"{eid}: {s}")

    # what the driver is doing now
    try:
        body, _ = ms.show_route("/live.json")
        live = json.loads(body)
        add("Now", "info", f"mode {live.get('mode')} · sound: {live.get('audio') or '-'} · TV camera: {live.get('camera') or '-'}")
    except Exception as e:
        add("Now", "warn", f"no live data ({e})")

    # the cameras: a picture arrives (numbers only), how fast; where they point
    for which, label in (("tv", f"{cameras.LABELS['tv']} (TV colours)"), ("room", f"{cameras.LABELS['room']} (Room guard)")):
        times, ok = [], 0
        for _ in range(3):
            t0 = time.time()
            try:
                img = grab_img(which)
                ok += img is not None
            except Exception:
                pass
            times.append(time.time() - t0)
        if not ok:
            add(label, "fail", "no picture (motionEye, or the camera's stream password in Home Assistant)")
        else:
            avg = sum(times) / len(times)
            # a single picture through Home Assistant takes ~1.2 s (it opens the stream for each); Light Sync itself
            # reads the stream, several a second
            add(label, "ok" if avg < 3.0 else "warn", f"pictures arrive ({avg:.1f} s for a single one through Home Assistant)")
    try:
        cams = cameras.listing()["cams"]
        for c in cams:
            add(f"{c['label']}: position", "ok" if c["at_home"] else "warn",
                "at Home" if c["at_home"] else "moved from Home (tap ⌂, or it goes back by itself after 30 min)")
        if not cams:
            add("Camera controls", "warn", "no camera with controls found")
    except Exception as e:
        add("Camera controls", "fail", str(e))
    box = _state("input_text.light_sync_tv_box").get("state", "")
    add("TV area", "ok" if box.count(",") in (3, 7) else "warn",  # a box, or the screen's four corners (Oct 28)
        (f"the screen's four corners: {box}" if box.count(",") == 7 else box) or "not set: Auto-tune finds it")

    # the backups' copy on the USB stick (the laptop's usb_backup.py)
    u = _state("sensor.light_sync_usb_backup")
    ua = u.get("attributes", {})
    if not u or u.get("state") in (None, "unavailable", "unknown"):
        add("USB backup", "warn", "no report from the laptop yet")
    else:
        add("USB backup", "ok" if u["state"] == "ok" else "warn",
            ua.get("problem") or f"{ua.get('copies')} copies, newest {ua.get('newest')}, {ua.get('free_gb')} GB free")

    # home / away and the room
    p, dt = _state("person.ezer"), _state("device_tracker.rocky")
    age = _ago(dt.get("last_updated", ""))
    add("Phone home / away", "ok" if p.get("state") in ("home", "not_home") and age is not None and age < 7200 else "warn",
        f"{p.get('state', '?')}, last location {('%d min ago' % (age // 60)) if age is not None else '?'}")
    g, f = _state("input_boolean.room_guard").get("state"), _state("input_boolean.lights_follow_me").get("state")
    add("Room guard / Lights follow me", "info", f"Room guard {g}, Lights follow me {f}")
    return {"at": time.time(), "checks": out}
