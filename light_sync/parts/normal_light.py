"""Normal mode: everyday white that follows the time of day."""

import json
import threading
import time

from parts.config import (
    DEMO, HA_NORMAL_BRIGHT, HA_NORMAL_KELVIN, HA_NORMAL_ROOM, LAST_HELPER, LIGHT_BEAT, LIGHT_SCREEN, LIGHT_WASH, NORMAL_DAY,
    NORMAL_ROOM_S, NORMAL_SCENES, OWN_WRITES,
)
from parts import config, helpers, app_requests


ROOM = {"lights": None, "at": 0.0, "on": False}  # on: Normal is showing the room right now (the main loop sets it)
ROLE_OF = {LIGHT_SCREEN: "screen", LIGHT_BEAT: "beat", LIGHT_WASH: "wash"}

def room_from(saved):
    """Light states (bulbs.save_states / brief) -> the room: each light on or off, brightness, warmth or colour. None if all off."""
    room = {}
    for eid, st in (saved or {}).items():
        role = ROLE_OF.get(eid)
        if not role:
            continue
        a = st.get("attributes", {}) or {}
        on = st.get("state") == "on"
        white = a.get("color_mode") == "color_temp" and a.get("color_temp_kelvin")
        room[role] = [on, int(a.get("brightness") or 255), int(a["color_temp_kelvin"]) if white else None,
                      None if white or not a.get("rgb_color") else [int(v) for v in a["rgb_color"]]]
    return room if any(v[0] for v in room.values()) else None

def room_store(room):
    """Keep the room (here and in Home Assistant, where the other machine reads it)."""
    ROOM["lights"], ROOM["at"] = room, time.time()
    try:
        helpers.set_helper(HA_NORMAL_ROOM, json.dumps({"t": int(ROOM["at"]), **room}, separators=(",", ":")) if room else "")
    except Exception as e:
        print(f"couldn't keep the room: {e}", flush=True)

def room_read(text):
    """The helper changed (the other machine, or the app's Reset: empty)."""
    try:
        d = json.loads(text) if text and text not in ("unknown", "unavailable") else {}
    except ValueError:
        d = {}
    t = d.pop("t", 0)
    ROOM["lights"], ROOM["at"] = (d or None), float(t)
    if not d:
        ROOM["on"] = False

def room_ready(now):
    return bool(ROOM["lights"]) and now - ROOM["at"] < NORMAL_ROOM_S

def room_edit(role, on=None, bri=None, kelvin=None, rgb=None):
    """The owner changed a light while Normal shows the room: the room changes with it."""
    r = (ROOM["lights"] or {}).get(role) or [False, 200, 3000, None]
    if on is not None:
        r[0] = on
    if bri is not None:
        r[1], r[0] = int(max(1, min(255, bri))), True
    if kelvin is not None:
        r[2], r[3], r[0] = int(kelvin), None, True
    if rgb is not None:
        r[2], r[3], r[0] = None, [int(v) for v in rgb], True
    room = dict(ROOM["lights"] or {})
    room[role] = r
    room_store(room)
    print(f"Normal: the room now {role} {'on' if r[0] else 'off'}", flush=True)

# YOUR DAY (Nov 7, owner: "Normal is the main one, make it well done"): the built-in day curve is stretched to the owner's
# own wake-up time and bedtime (Home Assistant's Wake-up time and Bedtime): the morning rise starts at waking up, the
# evening wind-down ends at bedtime (1 %, 2000 K: the lowest, Nov 7), and the night runs from there to the next morning. Changing either
# time moves the whole curve. Read from Home Assistant every 10 min.
HA_WAKE, HA_BED = "input_datetime.wake_up_time", "input_datetime.light_sync_bedtime"
TIMES = {"wake": 420, "bed": 1485, "at": 0.0}  # minutes of the day; bed past midnight = + 1440


def your_times(now):
    if now - TIMES["at"] > 600:
        TIMES["at"] = now
        try:
            from parts import ha_link

            def minute(eid):
                h, m = ha_link.ha_state(eid)["state"].split(":")[:2]
                return int(h) * 60 + int(m)
            w, b = minute(HA_WAKE), minute(HA_BED)
            if b < w:  # bedtime after midnight
                b += 1440
            if 300 <= w <= 720 and 1260 <= b <= 1440 + 240:  # sane: wake 5:00-12:00, bed 21:00-4:00
                TIMES["wake"], TIMES["bed"] = w, b
        except Exception:
            pass
    return TIMES["wake"], TIMES["bed"]


def your_minute(minute, now=None):
    """This clock minute on the built-in curve, stretched to the owner's wake-up and bedtime."""
    w, b = your_times(now or time.time())
    s0 = w - 120
    clock = [s0, w, w + 120, 1020, b - 210, b - 120, b, s0 + 1440]
    base = [300, 420, 540, 1020, 1260, 1350, 1440, 1740]
    if any(c1 <= c0 for c0, c1 in zip(clock, clock[1:])):
        return minute  # times that don't fit: the plain curve
    m = minute if minute >= s0 else minute + 1440
    for (c0, b0), (c1, b1) in zip(zip(clock, base), zip(clock[1:], base[1:])):
        if c0 <= m <= c1:
            return (b0 + (b1 - b0) * (m - c0) / (c1 - c0)) % 1440
    return minute


def your_day(minute, now=None):
    """(brightness %, kelvin) of the owner's day: the curve fitted to their times, plus what Light Sync learned they like
    at this hour (learner.py)."""
    b, k = normal_day(your_minute(minute, now))
    try:
        from parts import learner
        db, dk = learner.normal_offset(minute)
    except Exception:
        db = dk = 0.0
    return b + db, k + dk


def normal_day(minute):
    """(brightness %, kelvin) of the built-in day curve at this minute of the day."""
    for (m0, b0, k0), (m1, b1, k1) in zip(NORMAL_DAY, NORMAL_DAY[1:]):
        if m0 <= minute <= m1:
            f = (minute - m0) / max(1, m1 - m0)
            return b0 + (b1 - b0) * f, k0 + (k1 - k0) * f
    return NORMAL_DAY[0][1], NORMAL_DAY[0][2]

def normal_target(now):
    """Normal light now: (brightness %, kelvin, where from). A scene wins, then the day curve your way (the sliders: how far
    from it you like it, kept until changed), then the day curve learned from you (habits.py). Late night: never above 25 % / 2400 K."""
    t = time.localtime(now)
    minute = t.tm_hour * 60 + t.tm_min
    if config.NORMAL_SCENE in NORMAL_SCENES:
        (b, k), src = NORMAL_SCENES[config.NORMAL_SCENE], config.NORMAL_SCENE.lower()
    elif config.NORMAL_ADJUST and not adjust_expired(now):
        db, dk = config.NORMAL_ADJUST
        b, k = your_day(minute, now)
        b, k = b + db, k + dk
        src = "the day, your way (" + ", ".join(x for x in (
            f"{abs(db):.0f} % {'brighter' if db > 0 else 'dimmer'}" if abs(db) >= 1 else "",
            f"{'cooler' if dk > 0 else 'warmer'}" if abs(dk) >= 50 else "") if x) + ")"
    else:
        b, k = your_day(minute, now)
        try:
            from parts import learner
            ob, ok_ = learner.normal_offset(minute)
            src = "your day, learned from you" if abs(ob) >= 3 or abs(ok_) >= 200 else "your day"
        except Exception:
            src = "your day"
        if app_requests.HABITS:
            b, k, learned = app_requests.HABITS.normal_curve(minute, b, k)
            if learned:
                src = "learned from you"
    if config.LATE_NIGHT:
        b, k = min(b, 25), min(k, 2400)
    ramp = getattr(config, "NORMAL_RAMP", None)
    if ramp:  # Fajr's soft wake (Nov 8): from 1 % up to the day's level, slowly at first
        u = (now - ramp[0]) / ramp[1]
        if 0 <= u < 1:
            b = config.LOWEST_PCT + (b - config.LOWEST_PCT) * u * u * (3 - 2 * u)
            src = "Fajr, waking up gently"
        else:
            config.NORMAL_RAMP = None
    return max(float(config.LOWEST_PCT), min(100.0, b)), max(2000.0, min(6500.0, k)), src

def adjust_expired(now):
    """A slider change lasts until the next wake-up (Nov 7): then the learned day takes over (it learned from that change)."""
    at = getattr(config, "NORMAL_ADJUST_AT", 0.0)
    if not at:
        return False
    w = your_times(now)[0]
    t = time.localtime(now)
    wake_today = time.mktime((t.tm_year, t.tm_mon, t.tm_mday, w // 60, w % 60, 0, 0, 0, -1))
    if at < wake_today <= now:
        config.NORMAL_ADJUST, config.NORMAL_ADJUST_AT = None, 0.0
        try:
            set_own(config.HA_NORMAL_ADJUST, "0,0")
        except Exception:
            pass
        print("Normal: a new day - yesterday's slider change is in the learned day now", flush=True)
        return True
    return False


def set_own(eid, value):
    """Light Sync sets a helper itself (not the owner: it isn't learned)."""
    try:
        OWN_WRITES[eid] = (float(value) if eid.startswith("input_number.") else 0.0, time.time())
        helpers.set_helper(eid, value)
    except Exception as e:
        print(f"couldn't set {eid}: {e}", flush=True)

def sync_normal_sliders(b, k):
    """Normal's Brightness and Warmth sliders show the light as it is now (the day moves them), in the background."""
    def go():
        # (Nov 7: the slider goes down to 1 %: under 10 % it shows the exact value, above that in steps of 5)
        bv = max(1, round(b)) if b < 10 else round(b / 5) * 5
        for eid, v, tol in ((HA_NORMAL_BRIGHT, bv, 1 if b < 10 else 5), (HA_NORMAL_KELVIN, round(k / 100) * 100, 100)):
            try:
                cur = float(LAST_HELPER.get(eid, "nan"))
            except ValueError:
                cur = float("nan")
            if not abs(cur - v) < tol:
                set_own(eid, v)
    if not DEMO:
        threading.Thread(target=go, daemon=True).start()
