"""Room guard and Lights follow me: the room camera, as numbers only."""

import numpy as np
import os
import sys
import time
from collections import deque

from parts.config import HA_TOKEN, HA_URL, ROOM_CAM_URL
from parts import approve, config, eyes, ha_link, learner, pose_link, web


# The owner changed the house rule "No camera-based presence in the bedroom" to: motion alerts only while away
# (input_boolean.room_guard on, tools/ha_setup/room_guard.py), text only, nothing recorded. motionEye's own web hook didn't fire,
# so Light Sync watches: while Room guard is on, ~5 pictures a second become 64x48 grey numbers (the TV's area left
# out, brightness evened out so the lights' colours don't count), each compared with the last one and with the empty
# room (Oct 21: frame-to-frame alone caught a walk-by only sometimes, 2-3 %); movement in 3 of 5 -> the Room guard
# automation (one 2 min gap with motionEye's web hook) -> a notification. No picture is kept, shown or sent. Paused while the lights animate (Music, TV, Gaming).
HA_GUARD = "input_boolean.room_guard"

GUARD_NOTIFY = os.getenv("GUARD_NOTIFY", "notify/mobile_app_rocky")

GUARD_PIXEL = 0.6       # a grey value counts as changed past this many standard deviations (brightness evened out)

GUARD_MOVED = 0.008     # share changed since the last picture that counts as movement (a still room: <= 0.0007)

GUARD_PRESENT = 0.02    # share different from the empty room (with a little movement) that counts too

GUARD_TOO_MUCH = 0.85   # more than this changed at once: the camera itself or a light, not a person

GUARD_HITS = (3, 5)     # movement in 3 of the last 5 pictures (~1 s at 5 a second)

GUARD_POST_S = 20       # Light Sync tells Home Assistant at most this often (the automation keeps its 2 min gap)

HA_FOLLOW = "input_boolean.lights_follow_me"   # "Lights follow me" (tools/ha_setup/room_guard.py)

OWNER = "person.ezer"   # Room guard only counts while the owner is away, even if it was left on (Nov 2: on by hand at home for 2 days)

ROOM_TV = "media_player.x96q_pro_plus"            # the TV box playing: the owner is watching (still, but here)

ROOM_ABSENT_S = 900     # no sign of the owner this long: they left the room (all off; 5 min until Oct 31, 10 until Nov 7)
# Nov 7 (the night's log review: 37 "no one in the room - all off" in a day, also in TV mode with the owner sitting
# still): the Pi drives now and had lost the laptop's keyboard / mouse as a sign of the owner. Now TV, Gaming and Party
# never go off by themselves (only Normal does), and sound in the room, a laptop game, or the laptop in use
# (its game helper reports how long since the last key / mouse, numbers only) count as someone here.
LAPTOP_IDLE = {"s": None, "at": 0.0}  # the Pi: the laptop helper's last report
# Nov 5: still too short when sitting still: Normal went off exactly 10 min after coming on 5 times in one afternoon,
# back on 4-10 min later each time. Back within ROOM_OOPS_S of an all-off = it was a mistake: wait twice as long
# (up to ROOM_ABSENT_MAX_S), back to ROOM_ABSENT_S after ROOM_OOPS_RESET_S without another mistake.
ROOM_OOPS_S = 15 * 60
ROOM_ABSENT_MAX_S = 40 * 60
ROOM_OOPS_RESET_S = 4 * 3600

# Someone sitting still, typing or on the phone stays under GUARD_PIXEL / GUARD_MOVED, which are set for alerts: in a day the lights
# went off 24 times, 7 of them exactly 5 min after coming on, some back within seconds (Oct 31). Lights follow me only needs "is someone
# there", so it takes small movements too. The empty room, lit, 150 s, 372 comparisons: 0.05 of a picture's contrast changed at most
# 0.07 % of it, so these sit well above the camera's own noise. Not used while the lights animate: those move the picture.
FOLLOW_PIXEL = 0.12     # a grey value counts as changed past this many standard deviations
FOLLOW_MOVED = 0.003    # share changed since the last picture that counts as a small movement
FOLLOW_HITS = (2, 5)    # in 2 of the last 5 pictures (~2 s at 3 a second)
FOLLOW_SETTLE_S = 12    # after Lights follow me switches the lights: their fade is not someone moving

DUSK_ELEVATION = 3      # the sun this low in the evening: the room gets dark (Nov 6, from "Auto mode: dusk" in HA)

ROOM_NIGHT = (23, 7)    # no lights switched on by themselves between these hours (moving in bed isn't coming in)

def mode_on():
    """The Light Sync mode switched on now ("party", "ambient", "gaming", "normal") or None."""
    for name, on in (("gaming", config.GAMING_ON), ("ambient", config.AMBIENT_ON), ("party", config.MUSIC_ON), ("normal", config.NORMAL_ON)):
        if on:
            return name
    return None

def come_back(left_mode, tv_on):
    """Back in the room (Nov 6, owner: Resume "could be auto, no need for a button"): the mode that was on when the
    lights went off comes back (Resume); a game or TV mode without the TV playing comes back as Normal (Auto gaming
    and Auto mode start those again by themselves)."""
    if left_mode == "party" or (left_mode == "ambient" and tv_on):
        room_script("script.light_sync_resume")
        return left_mode
    room_script("script.normal_light")
    return "normal"

def guard_grey(img, cv2=None, tv_box=None):
    """A picture -> 48x64 grey numbers, the TV's area (tv_box: the TV camera's TV_BOX) left out (NaN), brightness and
    contrast evened out."""
    g = img.mean(axis=2) if img.ndim == 3 else img.astype(float)
    if cv2 is not None:
        g = cv2.resize(g.astype(np.float32), (64, 48), interpolation=cv2.INTER_AREA).astype(float)
    else:
        h, w = g.shape
        g = g[:h - h % 48, :w - w % 64].reshape(48, (h - h % 48) // 48, 64, (w - w % 64) // 64).mean(axis=(1, 3))
    if tv_box:
        x1, y1, x2, y2 = tv_box
        g[int(y1 * 48):int(np.ceil(y2 * 48)), int(x1 * 64):int(np.ceil(x2 * 64))] = np.nan
    v = g[~np.isnan(g)]
    return (g - v.mean()) / (v.std() + 4.0)

def guard_hook():
    """The Room guard automation's web hook (made by tools/ha_setup/room_guard.py): Light Sync's movement goes through the
    same automation as motionEye's, so there's one 2 min gap and no double notifications. None: notify directly."""
    try:
        r = ha_link.session.get(f"{HA_URL}/api/config/automation/config/sh_room_motion", timeout=5)
        return f"{HA_URL}/api/webhook/{r.json()['triggers'][0]['webhook_id']}" if r.ok else None
    except Exception:
        return None

def laptop_idle_s():
    """Seconds since the last keyboard / mouse use on the laptop: this laptop's own, or on the Pi the game helper's
    report (POST /laptop, fresh for 90 s); None when not known."""
    if sys.platform != "win32":
        if LAPTOP_IDLE["s"] is not None and time.time() - LAPTOP_IDLE["at"] < 90:
            return LAPTOP_IDLE["s"] + (time.time() - LAPTOP_IDLE["at"])
        return None
    import ctypes

    class LastInput(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]
    li = LastInput()
    li.cbSize = ctypes.sizeof(li)
    if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(li)):
        return None
    return ((ctypes.windll.kernel32.GetTickCount() - li.dwTime) & 0xFFFFFFFF) / 1000.0

def room_script(script):
    try:
        ha_link.session.post(f"{HA_URL}/api/services/script/turn_on", timeout=5, json={"entity_id": script})
    except Exception as e:
        print(f"Lights follow me: couldn't start {script} ({e})", flush=True)

# HOME AND AWAY (Nov 11, owner: "never turn the lights on when I'm not in the room ... I was outside all day, I don't want
# other people seeing my lights on"). That day: Fajr's wake at 5:09, the owner out at ~6:00, the sunrise put TvD on at
# 6:51, the lights stayed on 6.5 h, and Maghrib put Normal on at 16:51 - the phone's location had been stuck on "home"
# since Friday, and "away" needed it. Now two rules:
#   to turn lights ON: proof of home - the phone seen on the home Wi-Fi in the last HOME_S
#   to call it AWAY:   the phone gone AWAY_S (AWAY_NIGHT_S at night: a sleeping phone can drop off, Nov 5) AND nobody seen
#                      in the room for ROOM_EMPTY_S, and the location not saying "home" unless it's fresh (STALE_S)
HOME_S, AWAY_S, AWAY_NIGHT_S, ROOM_EMPTY_S, STALE_S = 900, 1200, 3600, 1200, 3 * 3600
PRESENCE = {"room_seen": time.time(), "away": None}
HA_AWAY, HA_HOME_NOW = "input_boolean.owner_away", "input_boolean.phone_home_now"


def phone_gone_s():
    """Seconds since the owner's phone answered on the home Wi-Fi (None: no Wi-Fi scan or no phone known)."""
    try:
        from parts import wifi
        macs = wifi.phone_macs()
        if not macs or not wifi.WIFI_AT:
            return None
        seen = max((wifi.WIFI[m]["seen"] for m in macs if m in wifi.WIFI), default=0.0)
        return time.time() - seen if seen else time.time() - wifi.WIFI_AT + AWAY_NIGHT_S
    except Exception:
        return None


def owner_home_now():
    """Proof of home, for anything that turns lights on: the phone on the Wi-Fi in the last 15 min."""
    s = phone_gone_s()
    if s is not None:
        return s < HOME_S
    try:  # no Wi-Fi scan here (the laptop): Home Assistant's
        return ha_link.ha_state("input_boolean.phone_on_wifi")["state"] == "on"
    except Exception:
        return True


def owner_away():
    """Away (lights off, Room guard): the phone gone long enough, nobody in the room, the location not freshly "home"."""
    s = phone_gone_s()
    if s is None:
        try:
            if ha_link.ha_state("input_boolean.phone_on_wifi")["state"] == "on":
                return False
        except Exception:
            return False
        s = AWAY_NIGHT_S
    hour = time.localtime().tm_hour
    if s < (AWAY_NIGHT_S if hour >= 23 or hour < 8 else AWAY_S):
        return False
    if time.time() - PRESENCE["room_seen"] < ROOM_EMPTY_S:
        return False
    try:
        st = ha_link.ha_state(OWNER)
        import datetime
        upd = datetime.datetime.fromisoformat(st["last_updated"]).timestamp()
        if st["state"] == "home" and time.time() - upd < STALE_S:
            return False  # a fresh location says home
    except Exception:
        pass
    return True


def presence_tick(lit):
    """Every 30 s: tell Home Assistant (phone home now; away), and on leaving: all off and a message."""
    home_now = owner_home_now()
    try:
        if ha_link.ha_state(HA_HOME_NOW)["state"] != ("on" if home_now else "off"):
            ha_link.set_switch(HA_HOME_NOW, home_now)
    except Exception:
        pass
    away = owner_away()
    if away == PRESENCE["away"]:
        return
    first = PRESENCE["away"] is None
    PRESENCE["away"] = away
    try:
        ha_link.set_switch(HA_AWAY, away)
    except Exception:
        pass
    if first:
        return
    if away:
        print("Away: your phone has left the home Wi-Fi and the room is empty - lights off, Room guard on", flush=True)
        if lit:
            room_script("script.all_lights_off")
        try:
            ha_link.session.post(f"{HA_URL}/api/services/script/notify_me", timeout=5, json={
                "title": "You left", "level": "info", "topic": "security",
                "message": ("Lights off" if lit else "The lights were already off") + ", Room guard is on: I'll tell you if "
                           "someone moves in your room. Nothing turns on until you're back in the room."})
        except Exception:
            pass
    else:
        print("Home: your phone is back on the home Wi-Fi - Room guard off; lights come on only when you're in the room", flush=True)

def room_guard_loop():
    """The bedroom camera as numbers (guard_grey), for two things:
    Room guard (owner away): movement -> the Room guard automation -> "Movement in your room".
    Lights follow me (Oct 21, owner: "when I leave my room turn all the lights off, when I come in turn them back on
    in Normal"; the camera rule changed again to allow it while home, numbers only): no sign of the owner for
    ROOM_ABSENT_S -> All off; back -> Normal (not at night, not while Room guard is on, not after the owner's own
    All off until they've been out). Signs: movement on the camera, the laptop in use, the TV box playing.
    The camera: the room camera (ROOM_CAM_URL, Oct 27) when there is one, else the TV camera with the TV's area left out."""
    try:
        import cv2
    except ImportError:
        cv2 = None
    url = ROOM_CAM_URL or config.SNAPSHOT_URL
    auth = eyes.stream_auth(url)
    headers = {"Authorization": f"Bearer {HA_TOKEN}"} if "/api/camera_proxy/" in url else {}
    stream_url = url.replace("/api/camera_proxy/", "/api/camera_proxy_stream/")
    stream, prev, bg, last_at, checked, posted, hook = None, None, None, 0.0, 0.0, 0.0, None
    guard = follow = tv_on = False
    recent, peak = deque(maxlen=GUARD_HITS[1]), deque(maxlen=300)
    small, switched_at = deque(maxlen=FOLLOW_HITS[1]), 0.0
    here, seen_at, left_at = True, time.time(), 0.0  # the owner in the room: assumed at start
    absent_s, oops_at, off_by_me = learner.get("follow_absent_s"), 0.0, False
    pose_at = pose_seen = 0.0  # the pose helper: last asked, last time it saw a person
    pose_anchor, pose_still_since, last_stir, presence_at = None, 0.0, time.time(), 0.0  # how long "gone" takes now (longer after a mistake)
    left_mode, dark, auto_mode, dusk_day = None, False, False, ""  # the mode the lights went off in; dusk -> Normal
    while True:
        now = time.time()
        if now - presence_at > 30:
            presence_at = now
            try:
                lit = mode_on() is not None or ha_link.ha_state("light.chambre_lights")["state"] == "on"
                presence_tick(lit)
            except Exception as e:
                print(f"Presence: {e}", flush=True)
        if now - checked > 5:
            checked = now
            try:
                guard = (not config.STANDBY and bool(url) and ha_link.ha_state(HA_GUARD)["state"] == "on"
                         and owner_away())
                follow = not config.STANDBY and bool(url) and ha_link.ha_state(HA_FOLLOW)["state"] == "on"
                tv_on = ha_link.ha_state(ROOM_TV)["state"] in ("playing", "paused")
                # Nov 8 (owner: "use Fajr and Maghrib for Normal's auto start"): dark = from Maghrib (until Fajr);
                # the sun's 3 degrees only if the prayer times aren't known
                from parts import prayer
                after = prayer.after_maghrib()
                if after is None:
                    sun = ha_link.ha_state("sun.sun")["attributes"]
                    after = sun.get("elevation", 90) < DUSK_ELEVATION
                dark = after
                auto_mode = ha_link.ha_state("input_boolean.light_sync_auto_mode")["state"] == "on"
            except Exception:
                pass
        animating = config.RUNNING and web.STATE.get("mode") in ("party", "ambient", "gaming")
        watch = (guard and not animating) or (follow and not guard)
        if not watch:
            if stream is not None:
                stream.stop()
                stream, prev, bg = None, None, None
                recent.clear()
                small.clear()
            # not watching (the other machine drives, Room guard on, Lights follow me off): when watching starts again
            # the owner counts as here from then (Oct 27: the Pi taking over from the laptop counted the owner gone
            # since the Pi started, hours, and switched all lights off at once)
            here, seen_at = True, time.time()
            web.STATE["room"] = {"guard": guard, "follow": follow, "watching": False, "camera": "room" if ROOM_CAM_URL else "tv",
                             "why": "lights animating" if guard and animating else "off"}
            time.sleep(1)
            continue
        if stream is None:
            stream, last_at, hook = eyes.CamStream(stream_url, url, auth, headers), 0.0, guard_hook()
        if stream.at <= last_at:
            time.sleep(0.05)
            continue
        last_at = stream.at
        data = stream.jpg
        img = (cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR) if cv2 else eyes.decode_bgr(data)) if data else None
        if img is None:
            time.sleep(0.2)
            continue
        # Nov 9: the pose helper - a person's body is seen even sitting completely still (movement missed that: 17 false
        # "no one in the room" in two days). Asked every 3 s while Lights follow me watches; without it, as before
        if follow and not guard and now - pose_at > 3:
            pose_at = now
            person, pts = pose_link.find(data)
            if person and pts:
                seen_pts = [p for p in pts if p[2] >= 0.3]
                cy = sum(p[0] for p in seen_pts) / len(seen_pts)
                cx = sum(p[1] for p in seen_pts) / len(seen_pts)
                if pose_anchor is None or abs(cy - pose_anchor[0]) > 0.012 or abs(cx - pose_anchor[1]) > 0.012:
                    pose_anchor, pose_still_since = (cy, cx), now
                # Nov 11: a "person" that hasn't moved at all for 10 min, with no movement in the picture either, is an
                # object the model mistakes for one (the lights stayed on 6.5 h in an empty room)
                if now - pose_still_since < 600 or now - last_stir < 600:
                    pose_seen = now
        g = guard_grey(img, cv2, config.ROOM_TV_BOX if ROOM_CAM_URL else config.TV_BOX)
        if prev is None:
            prev, bg = g, g.copy()
            continue
        moved = float(np.nanmean(np.abs(g - prev) > GUARD_PIXEL))     # changed since the last picture
        present = float(np.nanmean(np.abs(g - bg) > GUARD_PIXEL))     # different from the empty room
        if animating:  # the lights' colours move the picture too: only clear movement counts, the empty room doesn't
            motion = GUARD_MOVED * 4 < moved < GUARD_TOO_MUCH
        else:
            motion = (moved < GUARD_TOO_MUCH and present < GUARD_TOO_MUCH
                      and (moved > GUARD_MOVED or (present > GUARD_PRESENT and moved > GUARD_MOVED / 4)))
        recent.append(motion)
        slight = float(np.nanmean(np.abs(g - prev) > FOLLOW_PIXEL))
        small.append(not animating and FOLLOW_MOVED < slight < GUARD_TOO_MUCH)
        if not motion:  # the empty room, kept up to date (~20 s) only while nothing moves
            bg += (g - bg) * 0.01
        prev = g
        peak.append((moved, present))
        moving = sum(recent) >= GUARD_HITS[0]
        if guard and moving and now - posted > GUARD_POST_S:
            posted = now
            print(f"Room guard: movement ({moved:.1%} moving, {present:.1%} different from the empty room)", flush=True)
            try:
                if hook:  # through the automation (Room guard check, 2 min gap, the notification)
                    ha_link.session.post(hook, timeout=5)
                else:
                    ha_link.session.post(f"{HA_URL}/api/services/{GUARD_NOTIFY}", timeout=5, json={
                        "title": "Movement in your room",
                        "message": f"The bedroom camera saw movement at {time.strftime('%H:%M')} while you're away."})
            except Exception as e:
                print(f"Room guard: couldn't tell Home Assistant ({e})", flush=True)
        if follow and not guard:
            idle = laptop_idle_s()
            stirring = moving or sum(small) >= FOLLOW_HITS[0]
            busy_mode = mode_on() in ("party", "ambient", "gaming")  # a mode the owner chose: never off by itself
            sound = config.RUNNING and (web.STATE.get("energy") or 0) > 0.15  # music / a film / talking in the room
            try:
                from parts import games
                gaming_here = bool(games.GAME_ON_LAPTOP)
            except Exception:
                gaming_here = False
            body = now - pose_seen < 10  # the pose helper saw a person in the last 10 s
            if stirring and now - switched_at > FOLLOW_SETTLE_S:
                last_stir = now
            if ((stirring and now - switched_at > FOLLOW_SETTLE_S) or tv_on or (idle is not None and idle < 120)
                    or busy_mode or sound or gaming_here or body):
                seen_at = now
                PRESENCE["room_seen"] = now
            hour = time.localtime().tm_hour
            night = config.LATE_NIGHT or hour >= ROOM_NIGHT[0] or hour < ROOM_NIGHT[1]
            base = learner.get("follow_absent_s")  # Nov 7: Light Sync tunes this itself (learner.py)
            if (absent_s > base and now - oops_at > ROOM_OOPS_RESET_S) or absent_s < base:
                absent_s = base
            if here and now - seen_at > absent_s:
                here, left_at = False, now
                lit = config.MUSIC_ON or config.AMBIENT_ON or config.GAMING_ON or config.NORMAL_ON
                try:
                    lit = lit or ha_link.ha_state("light.chambre_lights")["state"] == "on"
                except Exception:
                    pass
                print(f"Lights follow me: no one in the room for {absent_s // 60} min"
                      + (" - all off" if lit else " (already off)"), flush=True)
                off_by_me = lit
                left_mode = mode_on() if lit else None
                if lit:
                    switched_at = now
                    learner.did("follow_off")
                    try:  # Auto mode (Nov 3): at night a gentle fade (Good night) instead of off at once
                        gentle = night and ha_link.ha_state("input_boolean.light_sync_auto_mode")["state"] == "on"
                    except Exception:
                        gentle = False
                    room_script("script.good_night" if gentle else "script.all_lights_off")
                    gone_mode = left_mode
                    approve.tell(f"Nobody seen in your room for {absent_s // 60} min: lights off.",  # (Nov 12: tell + Undo)
                                 undo=lambda m=gone_mode: come_back(m, False) and None)
            elif not here and seen_at > left_at:
                here = True
                if off_by_me and now - left_at < ROOM_OOPS_S and not night:  # still here after all: wait longer from now on
                    learner.undone("follow_off", within=ROOM_OOPS_S + 60)
                    absent_s, oops_at = min(absent_s * 2, ROOM_ABSENT_MAX_S), now
                    print(f"Lights follow me: back {round((now - left_at) / 60)} min after the lights went off - "
                          f"waiting {absent_s // 60} min from now on", flush=True)
                off_by_me_was, off_by_me = off_by_me, False
                calm = not (config.MUSIC_ON or config.AMBIENT_ON or config.GAMING_ON or config.NORMAL_ON)
                back = None
                if calm and not night and owner_home_now():  # (Nov 11: only with the phone home)
                    switched_at = now
                    # Nov 12 (owner: lights on by themselves -> ask first)
                    name = {"party": "Party", "ambient": "TV mode"}.get(left_mode, "Normal")
                    approve.ask("back", f"You're back in your room: turn the lights back on ({name})?",
                                lambda m=left_mode, tv=tv_on: come_back(m, tv) and None)
                    back = "asked you"
                    if dark:
                        dusk_day = time.strftime("%Y-%m-%d")
                print("Lights follow me: back in the room" + (f" - {back}" if back else
                                                               " (night: lights left as they are)" if night else ""), flush=True)
                left_mode = None
            # dusk (Nov 6, owner: "it's getting dark and the lights are off -> Normal: not working"): it ran in HA at the
            # sun's 3 degrees, often with nobody in the room, and Lights follow me switched it off again. Now: once an
            # evening, the first moment it is dark AND the owner is in the room AND no mode is on AND the lights are off.
            today = time.strftime("%Y-%m-%d")
            if dark and auto_mode and here and owner_home_now() and not night and dusk_day != today and now - seen_at < 120                     and time.localtime().tm_hour >= 12:
                dusk_day = today  # once an evening, whatever happens next (lights on already, a mode: nothing to do)
                lit = mode_on() is not None
                try:
                    lit = lit or ha_link.ha_state("light.chambre_lights")["state"] == "on"
                except Exception:
                    lit = True
                if not lit:
                    switched_at = now
                    print("Maghrib: it's evening and you're in the room - asking about Normal", flush=True)
                    approve.ask("maghrib", "Maghrib: it's evening and you're in your room. Turn on Normal?",
                                lambda: room_script("script.normal_light"), timeout=600)
        web.STATE["room"] = {"guard": guard, "follow": follow, "watching": True, "camera": "room" if ROOM_CAM_URL else "tv",
                         "moved": round(moved, 4),
                         "present": round(present, 4), "hits": sum(recent), "small": sum(small), "here": here,
                         "unseen_s": round(now - seen_at), "absent_s": absent_s, "peak_moved_60s": round(max(p[0] for p in peak), 4),
                         "peak_present_60s": round(max(p[1] for p in peak), 4), "told_at": round(posted)}
        time.sleep(0.15 if guard else 0.3)  # ~5 pictures a second for Room guard, ~3 for Lights follow me
