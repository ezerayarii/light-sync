"""The link with Home Assistant's switches and sliders, and the sensors sent back to it."""

import colorsys
import json
import os
import time

from parts.config import (
    CHANGE_BEATS, COLOR_SETS, FLOW_STYLES, HA_AMBIENT, HA_AUTO_DJ, HA_AUTO_GAMING, HA_CHANGE, HA_CINEMA,
    HA_COLORS, HA_COMFORT, HA_FLASH,
    HA_GAME_MUSIC, HA_GAMING, HA_HEARTBEAT, HA_LATE_NIGHT, HA_LEVEL, HA_MIC, HA_MOOD, HA_MY_COLORS, HA_NORMAL,
    HA_NORMAL_BRIGHT, HA_NORMAL_KELVIN, HA_NORMAL_SCENE, HA_NUMBERS, HA_PARTS, HA_PULSE, HA_REACTION_AUTO,
    HA_ROOM_TV_BOX, HA_SEGMENTS, HA_SENS, HA_STRIP_MAP, HA_STYLE, HA_SWITCH, HA_TOKEN, HA_TV_BOX, HA_TV_MUSIC,
    HA_URL, HA_USE, HOST, LAST_HELPER, LEVEL, LIGHT_SCREEN, NORMAL_SCENES, OWN_WRITES, PARTS,
    PART_OPTIONS, ROLE, SCREEN_SEGMENTS, SHOW_PORT, TUNE, USE, USER_PULSE,
)
from parts import (
    config, ears, ha_link, normal_light, rocky_cue, screen_map, sources, app_requests, web,
)


def set_helper(eid, value):
    domain = eid.split(".")[0]
    if domain == "input_boolean":
        on = value if isinstance(value, bool) else value == "on"
        ha_link.session.post(f"{HA_URL}/api/services/input_boolean/turn_{'on' if on else 'off'}", timeout=5, json={"entity_id": eid})
    elif domain == "input_select":
        ha_link.session.post(f"{HA_URL}/api/services/input_select/select_option", timeout=5, json={"entity_id": eid, "option": value})
    elif domain == "input_number":
        ha_link.session.post(f"{HA_URL}/api/services/input_number/set_value", timeout=5, json={"entity_id": eid, "value": float(value)})
    elif domain == "input_text":
        ha_link.session.post(f"{HA_URL}/api/services/input_text/set_value", timeout=5, json={"entity_id": eid, "value": str(value)})

STARTED_AT = time.time()  # Light Sync started (the other machine may just have moved the sliders)

def normal_yours(bright=None, kelvin=None, how=""):
    """The owner set Normal's brightness and / or warmth (its sliders, or the lights themselves): how far from the day curve,
    kept in Home Assistant until changed again or Reset, so Normal keeps following the day their way. While Normal shows the
    room as they left it: the lights that are on take it instead."""
    if normal_light.ROOM["on"]:
        for role, r in (normal_light.ROOM["lights"] or {}).items():
            if r[0]:
                normal_light.room_edit(role, bri=bright * 2.55 if bright is not None else None, kelvin=kelvin)
        return
    now = time.time()
    b, k, _ = normal_light.normal_target(now)
    b, k = bright if bright is not None else b, kelvin if kelvin is not None else k
    t = time.localtime(now)
    minute = t.tm_hour * 60 + t.tm_min
    day_b, day_k = normal_light.your_day(minute, now)  # (Nov 7) the owner's day: their times + what was learned
    db, dk = round(b - day_b), round((k - day_k) / 50) * 50
    config.NORMAL_ADJUST = (db, dk) if abs(db) >= 1 or abs(dk) >= 50 else None   # the day as it is: nothing to keep
    config.NORMAL_ADJUST_AT = now  # lasts until the next wake-up (normal_light.adjust_expired)
    try:  # the learner: what they chose, against their plain day (without the learned part), at this minute
        from parts import learner
        pb, pk = normal_light.normal_day(normal_light.your_minute(minute, now))
        learner.normal_taught(minute, b - pb, k - pk)
    except Exception:
        pass
    set_helper(config.HA_NORMAL_ADJUST, f"{db},{dk}")
    if config.NORMAL_SCENE in NORMAL_SCENES:  # changed on a fixed scene: back to the day, their way
        OWN_WRITES[HA_NORMAL_SCENE] = (0.0, now)
        set_helper(HA_NORMAL_SCENE, "Follow the day")
    if app_requests.HABITS:
        app_requests.HABITS.normal_pref(b, k)
    print(f"Normal: you set {b:.0f} % / {k:.0f} K ({how}): from now on the day {db:+d} % / {dk:+d} K", flush=True)

NORMAL_LIGHTS = ("light.chambre_lights", config.LIGHT_BEAT, config.LIGHT_WASH)  # Normal's white: Corner and Top (and the group)

def light_call(ev):
    """A light command through Home Assistant (Nov 2, owner: Normal "keeps going back" after the dashboard's light sliders):
    by the owner (a user, not an automation or script, not Light Sync itself), while Normal is on -> their Normal."""
    data, ctx = ev.get("data") or {}, ev.get("context") or {}   # who did it is on the event, not in its data
    sd = data.get("service_data") or {}
    if data.get("domain") != "light" or data.get("service") not in ("turn_on", "turn_off", "toggle") or config.STANDBY or not config.NORMAL_ON:
        return
    if not ctx.get("user_id") or ctx.get("parent_id") or time.time() - ha_link.OWN_LIGHT_AT[0] < 3:
        return
    ids = sd.get("entity_id") or (data.get("target") or {}).get("entity_id") or []
    ids = [ids] if isinstance(ids, str) else ids
    switched = data["service"] in ("turn_off", "toggle") or (data["service"] == "turn_on" and not any(
        k in sd for k in ("brightness", "brightness_pct", "color_temp_kelvin", "kelvin", "color_temp", "rgb_color")))
    if switched and not normal_light.ROOM["on"] and any(e in normal_light.ROLE_OF or e == "light.chambre_lights" for e in ids):
        # a light switched on or off in the standard Normal (only the Corner to read, TvD at the desk): from now on Normal is
        # the room as it is, so it stays that way, also after a film
        from parts import bulbs
        normal_light.ROOM["lights"] = normal_light.room_from(bulbs.save_states(list(normal_light.ROLE_OF))) or {}
        normal_light.ROOM["on"] = True
    if normal_light.ROOM["on"]:  # the room as they left it: each light they touch changes in it (TvD too)
        roles = [normal_light.ROLE_OF[e] for e in ids if e in normal_light.ROLE_OF]
        if "light.chambre_lights" in ids:
            roles = list(normal_light.ROLE_OF.values())
        for role in roles:
            r = (normal_light.ROOM["lights"] or {}).get(role)
            if data["service"] == "turn_off" or (data["service"] == "toggle" and r and r[0]):
                normal_light.room_edit(role, on=False)
                continue
            b = sd.get("brightness") if sd.get("brightness") is not None else (float(sd["brightness_pct"]) * 2.55 if sd.get("brightness_pct") is not None else None)
            k = sd.get("color_temp_kelvin") or sd.get("kelvin") or (1e6 / float(sd["color_temp"]) if sd.get("color_temp") else None)
            if "light.chambre_lights" in ids and not (r and r[0]):
                continue  # the group's brightness: only the lights that are on
            if not (r and r[0]) and b is None and k is None and not sd.get("rgb_color"):
                # switched on with no level: as bright and warm as Normal is now, never the light's full brightness
                nb, nk, _ = normal_light.normal_target(time.time())
                b, k = nb * 2.55, nk
            normal_light.room_edit(role, on=True, bri=b, kelvin=k, rgb=sd.get("rgb_color"))
        return
    if data["service"] != "turn_on" or not any(e in NORMAL_LIGHTS for e in ids):
        return
    b = sd.get("brightness_pct")
    if b is None and sd.get("brightness") is not None:
        b = float(sd["brightness"]) / 2.55
    k = sd.get("color_temp_kelvin") or sd.get("kelvin")
    if k is None and sd.get("color_temp"):
        k = 1e6 / float(sd["color_temp"])
    if b is not None or k is not None:
        normal_yours(float(b) if b is not None else None, float(k) if k is not None else None, "the lights")

def helper_ids():
    """Every dashboard helper Light Sync follows."""
    return (HA_SWITCH, HA_AMBIENT, HA_GAMING, HA_AUTO_GAMING, HA_GAME_MUSIC, HA_TV_MUSIC, HA_CINEMA, HA_FLASH, HA_STYLE,
            HA_SENS, HA_MIC, HA_REACTION_AUTO, HA_AUTO_DJ, *HA_PULSE.values(), *HA_LEVEL.values(),
            HA_TV_BOX, HA_ROOM_TV_BOX, HA_SEGMENTS, HA_COMFORT, HA_LATE_NIGHT, HA_MOOD, HA_COLORS, HA_MY_COLORS, HA_CHANGE,
            *HA_PARTS.values(), *HA_NUMBERS, *HA_USE.values(),
            HA_NORMAL, HA_NORMAL_BRIGHT, HA_NORMAL_KELVIN, HA_NORMAL_SCENE, config.HA_NORMAL_ADJUST, config.HA_NORMAL_ROOM, rocky_cue.HA_VOICE, LIGHT_SCREEN,
            HA_STRIP_MAP, config.HA_TV_CORNER, config.HA_FULL_VOLUME)

def poll_helpers():
    """Read the dashboard helpers once (one by one: ~2.8 s for all of them, so only at start and as a safety net;
    changes arrive at once through helper_watch_loop)."""
    for eid in helper_ids():
        try:
            st = ha_link.ha_state(eid)["state"]
        except Exception:
            continue  # helper not created yet - keep current setting
        apply_helper(eid, st)
    helpers_done()

def apply_helper(eid, st):
    """One dashboard helper's new state into Light Sync's settings."""
    if st is None:
        return
    if eid == rocky_cue.HA_VOICE:  # not a setting: Rocky's microphone, for the light cue
        rocky_cue.voice_state(st)
        return
    if eid == LIGHT_SCREEN:  # not a setting: TvD switched on from outside (Home Assistant, the Govee app) leaves
        w = rocky_cue.VOICE["light"]   # streaming: switch it on again once (Light Sync's own power-ons restart it anyway)
        if st == "on" and LAST_HELPER.get(eid) == "off" and w is not None and w.razer and time.time() - w.razer_on_at > 60:
            w.razer = False
        LAST_HELPER[eid] = st
        return
    changed = eid in LAST_HELPER and LAST_HELPER[eid] != st  # not the first reading
    LAST_HELPER[eid] = st
    if eid in (HA_NORMAL_BRIGHT, HA_NORMAL_KELVIN):
        try:
            v = float(st)
        except ValueError:
            return
        own = OWN_WRITES.get(eid)
        if changed and not (own and abs(own[0] - v) < 1 and time.time() - own[1] < 20):
            if config.STANDBY or time.time() - STARTED_AT < 30:
                # the Pi standing by sees the laptop keep the sliders up to date, and a starting laptop the Pi's (it took over meanwhile):
                # not the owner (it froze Normal on takeover)
                return
            normal_yours(v if eid == HA_NORMAL_BRIGHT else None, v if eid == HA_NORMAL_KELVIN else None, "a slider")
        return
    if eid == config.HA_NORMAL_ROOM:
        normal_light.room_read(st)
        return
    if eid == config.HA_NORMAL_ADJUST:
        try:
            db, dk = (float(x) for x in st.split(","))
            config.NORMAL_ADJUST = (db, dk) if abs(db) >= 1 or abs(dk) >= 50 else None
        except ValueError:
            config.NORMAL_ADJUST = None
        return
    if eid == HA_NORMAL:
        config.NORMAL_ON = st == "on"
        return
    if eid == HA_NORMAL_SCENE:
        if st in ("Follow the day", *NORMAL_SCENES):
            config.NORMAL_SCENE = st
            if changed:
                if app_requests.HABITS and st != "Follow the day" and not (OWN_WRITES.get(eid) and time.time() - OWN_WRITES[eid][1] < 20):
                    app_requests.HABITS.normal_scene_used(st)
        return
    if eid == HA_SWITCH:
        config.MUSIC_ON = st != "off"
    elif eid == HA_AMBIENT:
        config.AMBIENT_ON = st == "on"
    elif eid == HA_GAMING:
        config.GAMING_ON = st == "on"
    elif eid == HA_AUTO_GAMING:
        config.AUTO_GAMING = st != "off"
    elif eid == HA_GAME_MUSIC:
        config.GAME_MUSIC = st != "off"
    elif eid == HA_TV_MUSIC:
        config.TV_MUSIC = st != "off"
    elif eid == HA_CINEMA:
        config.CINEMA = st != "off"
    elif eid == HA_REACTION_AUTO:
        config.REACTION_AUTO = st != "off"
    elif eid in HA_PULSE.values():
        USER_PULSE[next(r for r, e in HA_PULSE.items() if e == eid)] = st == "on"
    elif eid == HA_AUTO_DJ:
        config.AUTO_DJ = st == "on"
    elif eid in HA_LEVEL.values():
        try:
            LEVEL[next(r for r, e in HA_LEVEL.items() if e == eid)] = max(0.0, min(1.0, float(st) / 100))  # 0 = off
        except ValueError:
            pass
    elif eid == config.HA_FULL_VOLUME:
        try:
            config.FULL_VOLUME = max(0.0, min(1.0, float(st) / 100))
        except ValueError:
            pass
    elif eid == config.HA_TV_CORNER and st in config.TV_CORNER_RULES:
        config.TV_CORNER = st
    elif eid == HA_SEGMENTS:
        config.SEGMENTS_ON = st != "off"
    elif eid == HA_COMFORT:
        config.COMFORT = st != "off"
    elif eid == HA_MOOD:
        config.MOOD_ON = st != "off"
    elif eid in HA_PARTS.values() and st in PART_OPTIONS:
        PARTS[next(r for r, e in HA_PARTS.items() if e == eid)] = st
    elif eid in HA_NUMBERS:
        try:
            TUNE[HA_NUMBERS[eid]] = float(st)
        except ValueError:
            pass
    elif eid == HA_LATE_NIGHT:
        config.LATE_NIGHT = st == "on"
    elif eid in HA_USE.values():
        USE[next(role for role, e in HA_USE.items() if e == eid)] = st != "off"
    elif eid == HA_MIC and st not in ("unknown", "unavailable"):
        config.MIC_CHOICE = sources.OLD_LABELS.get(st, st)
    elif eid == HA_TV_BOX:
        try:
            box = tuple(float(v) for v in st.split(","))
            if len(box) == 4 and 0 <= box[0] < box[2] <= 1 and 0 <= box[1] < box[3] <= 1:
                config.TV_BOX, config.TV_QUAD = box, None
            elif len(box) == 8 and all(0 <= v <= 1 for v in box):  # the screen's four corners
                quad = tuple(zip(box[0::2], box[1::2]))
                qb = screen_map.quad_box(quad)
                if qb[2] - qb[0] > 0.02 and qb[3] - qb[1] > 0.02:
                    config.TV_QUAD, config.TV_BOX = quad, qb
        except ValueError:
            pass  # empty or not "x1,y1,x2,y2" / 8 numbers: keep the current area
    elif eid == HA_STRIP_MAP:
        try:
            pts = [tuple(float(x) for x in p.split(":")) for p in st.split(",") if p.strip()]
            config.STRIP_MAP = pts if (len(pts) == SCREEN_SEGMENTS and all(len(p) == 2 and 0 <= p[0] <= 1 and 0 <= p[1] <= 1
                                                                   for p in pts)) else None
        except ValueError:
            config.STRIP_MAP = None
    elif eid == HA_ROOM_TV_BOX:
        try:
            box = tuple(float(v) for v in st.split(","))
            config.ROOM_TV_BOX = box if len(box) == 4 and 0 <= box[0] < box[2] <= 1 and 0 <= box[1] < box[3] <= 1 else None
        except ValueError:
            config.ROOM_TV_BOX = None  # empty: the TV isn't in the room camera's view
    elif eid == HA_FLASH and st in ("off", "hits", "strobe"):
        config.FLASH = st
    elif eid == HA_STYLE and st in ("vibe", "rainbow", "each own", *FLOW_STYLES):
        config.USER_STYLE = st
    elif eid == HA_COLORS and st in ("mood", "neon", "mine", *COLOR_SETS):
        config.COLORS, config.MOOD_ON = st, st == "mood"
    elif eid == HA_MY_COLORS:
        hexes = [h.strip().lstrip("#") for h in st.split(",")]
        config.MY_HUES = [colorsys.rgb_to_hsv(*(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)))[0] for h in hexes
                   if len(h) == 6 and all(c in "0123456789abcdefABCDEF" for c in h)][:6]
    elif eid == HA_CHANGE and st in CHANGE_BEATS:
        config.BEATS_PER_COLOR = CHANGE_BEATS[st]
    elif eid == HA_SENS:
        try:
            config.SENSITIVITY = float(st)
        except ValueError:
            pass

def helpers_done():
    """What follows from all the helpers together (after one or more changed)."""
    config.RUNNING = config.MUSIC_ON or config.AMBIENT_ON or config.GAMING_ON or config.NORMAL_ON
    # the sound settings sliders
    config.MAX_BRIGHT = int(2.55 * max(10.0, min(100.0, TUNE["bright_max"])))
    config.MIN_BRIGHT = min(config.MAX_BRIGHT - 10, int(2.55 * max(0.0, min(100.0, TUNE["bright_min"]))))
    ears.Ear.GRID_MIN = max(0.3, min(0.8, TUNE["beat_lock"]))
    if TUNE["latency"] is not None:
        config.LATENCY_MS = max(0.0, min(400.0, TUNE["latency"]))

HELPERS_LIVE = False  # helper_watch_loop is connected: changes arrive at once

def ha_control_loop():
    """Reads all dashboard helpers: at start, then every 30 s as a safety net while helper_watch_loop delivers
    changes at once (every second if it isn't connected; every 5 s while the Pi stands by)."""
    while True:
        poll_helpers()
        time.sleep(5 if config.STANDBY else 30 if HELPERS_LIVE else 1)

class HASocket:
    """Just enough of a WebSocket client for Home Assistant's API (standard library only)."""

    def __init__(self, url, token):
        import base64
        import socket
        from urllib.parse import urlparse
        u = urlparse(url)
        path = u.path.rstrip("/") + ("/websocket" if u.hostname == "supervisor" else "/api/websocket")
        self.sock = socket.create_connection((u.hostname, u.port or 80), timeout=70)
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall((f"GET {path} HTTP/1.1\r\nHost: {u.hostname}:{u.port or 80}\r\nUpgrade: websocket\r\n"
                           f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        self.buf = b""
        while b"\r\n\r\n" not in self.buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ConnectionError("refused")
            self.buf += chunk
        head, self.buf = self.buf.split(b"\r\n\r\n", 1)
        if b" 101 " not in head.split(b"\r\n")[0]:
            raise ConnectionError(head.split(b"\r\n")[0].decode(errors="replace"))
        self.recv()  # auth_required
        self.send({"type": "auth", "access_token": token})
        if self.recv().get("type") != "auth_ok":
            raise ConnectionError("not allowed")

    def _read(self, n):
        while len(self.buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise ConnectionError("closed")
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def send(self, obj, opcode=0x1):
        data = json.dumps(obj).encode() if opcode == 0x1 else obj
        n = len(data)
        head = bytearray([0x80 | opcode])
        head += bytes([0x80 | n]) if n < 126 else (bytes([0x80 | 126]) + n.to_bytes(2, "big"))
        mask = os.urandom(4)
        self.sock.sendall(bytes(head) + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    def recv(self):
        msg = b""
        while True:
            b1, b2 = self._read(2)
            n = b2 & 0x7F
            if n == 126:
                n = int.from_bytes(self._read(2), "big")
            elif n == 127:
                n = int.from_bytes(self._read(8), "big")
            payload = self._read(n)
            op = b1 & 0x0F
            if op == 0x9:  # ping
                self.send(payload, 0xA)
                continue
            if op == 0x8:
                raise ConnectionError("closed by Home Assistant")
            msg += payload
            if b1 & 0x80:
                return json.loads(msg)

def helper_watch_loop():
    """Every dashboard change at once (Oct 20, owner: "switching modes lags"): Home Assistant tells Light Sync the
    moment a helper changes, instead of Light Sync asking for ~55 helpers one by one (2.8 s, every second)."""
    global HELPERS_LIVE
    while True:
        try:
            ws = HASocket(HA_URL, HA_TOKEN)
            ws.send({"id": 1, "type": "subscribe_entities", "entity_ids": list(helper_ids())})
            ws.send({"id": 2, "type": "subscribe_events", "event_type": "call_service"})   # the owner changing the lights (light_call)
            HELPERS_LIVE = True
            msg_id = 2
            while True:
                import select
                if not ws.buf and not select.select([ws.sock], [], [], 25)[0]:
                    ws.send({"id": msg_id, "type": "ping"})  # quiet for 25 s: keep the link alive (a dead one errors)
                    msg_id += 1
                    continue
                m = ws.recv()
                ev = m.get("event") if m.get("type") == "event" else None
                if not ev:
                    continue
                if m.get("id") == 2:
                    light_call(ev)
                    continue
                for eid, v in (ev.get("a") or {}).items():  # first: every helper as it is
                    apply_helper(eid, v.get("s"))
                for eid, v in (ev.get("c") or {}).items():  # then: only what changed
                    if "s" in v.get("+", {}):
                        apply_helper(eid, v["+"]["s"])
                helpers_done()
        except Exception as e:
            if HELPERS_LIVE:
                print(f"Home Assistant live link lost ({e}) - reading every second meanwhile", flush=True)
            HELPERS_LIVE = False
            time.sleep(5)

HA_NUMBERS_S = 30  # s between BPM / loudness updates to Home Assistant

def ha_publish_loop():
    """Publishes live sensors every 2 s; on the laptop also the heartbeat the Pi watches. BPM and loudness only every
    HA_NUMBERS_S, and 0 unless the music drives the lights: at every 2 s they were 92 % of Home Assistant's history
    (Oct 30) and each update reached every open screen. The live view and the app read them from /state, at full speed."""
    numbers_at = 0.0
    while True:
        show_url = f"http://{ha_link.lan_ip()}:{SHOW_PORT}/show" if SHOW_PORT else None
        sensors = {} if config.STANDBY else {
            "sensor.music_sync_status": (web.STATE.get("mode", "paused") if config.RUNNING else "paused",
                                         {"friendly_name": "Music sync", "icon": "mdi:music", "host": HOST,
                                          "colors_from": web.STATE.get("source"), "camera": web.STATE.get("camera"),
                                          "flash": config.FLASH, "style": config.STYLE,
                                          "audio": web.STATE.get("audio"), "game": web.STATE.get("game") or None,
                                          "music_in_game": bool(web.STATE.get("music_in_game")),
                                          "show_url": show_url, "restore": web.STATE.get("restore")}),
        }
        if not config.STANDBY and time.time() - numbers_at >= HA_NUMBERS_S:
            numbers_at = time.time()
            music = web.STATE.get("mode") in ("party", "gaming")
            sensors["sensor.music_sync_bpm"] = ((web.STATE.get("bpm") or 0) if music else 0, {"friendly_name": "Music BPM", "unit_of_measurement": "BPM", "icon": "mdi:metronome"})
            sensors["sensor.music_sync_loudness"] = (int(web.STATE.get("energy", 0) * 100) if music else 0, {"friendly_name": "Music loudness", "unit_of_measurement": "%", "icon": "mdi:volume-high"})
        if ROLE == "main":
            sensors[HA_HEARTBEAT] = ("on", {"friendly_name": "Light Sync on the laptop", "icon": "mdi:laptop",
                                            "show_url": show_url, "game": web.STATE.get("game") or None,
                                            "restore": web.STATE.get("restore"), "room_mic": ears.room_wanted()})
        for eid, (state, attrs) in sensors.items():
            try:
                ha_link.session.post(f"{HA_URL}/api/states/{eid}", json={"state": state, "attributes": attrs}, timeout=3)
            except Exception:
                pass
        time.sleep(2)
