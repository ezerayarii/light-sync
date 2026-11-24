"""The live data and pages: the Light Sync app's data, the live view, the show."""

import json
import os
import re
import requests
import threading
import time

from parts.boot import HERE
from parts.config import (
    HA_URL, HOST, PHONE_FROM, ROLE, SHOW_PORT, VIEW_PORT,
)
from parts import (
    assistant, colours, config, ears, eyes, ha_link, pi_backup, rocky_cue, soundtest, sources, app_requests, wifi,
)


STATE = {"lights": {}, "energy": 0.0, "bpm": 0, "beats": 0, "beat_at": 0.0,
         "source": "music palette", "audio": "", "history": [],
         "camera": "off", "screen_rgb": None}

EAR = None        # the running Ear, for the show page

SHOW_SEEN = 0.0   # last time a show page asked for a frame

SHOW_TV_SEEN = 0.0  # last time the show ON THE TV asked (opened with ?tv=1 by "Show on TV")

SHOW_CLIENTS = {}  # "ip tab" -> {"fps", "scale", "seen", "tv"}: which screens are showing it (for the laptop-only /state)

def show_open():
    """True while the show is on the TV (/show?tv=1, "Show on TV"): the lights then use the show palette, not the
    camera, which would just be filming the visuals. A show open on a phone, the dashboard or the laptop doesn't
    count: the TV still shows its own picture (before Oct 18 it did, and TV mode fell back to warm white)."""
    return time.time() - SHOW_TV_SEEN < 2

def show_frame():
    """Everything the show page needs, polled ~30 times a second."""
    e = EAR
    return {"t": time.time(), "mode": STATE.get("mode", "paused"), "running": STATE.get("running", False),
            "energy": STATE.get("energy", 0.0),
            "bands": [round(float(b), 3) for b in e.bands] if e else [0, 0, 0],
            "spectrum": [round(float(v), 3) for v in e.spectrum] if e else [],
            "last_beat": (e.anchor if e.period() else e.last_beat) if e else 0.0, "period": e.period() if e else None,
            "white_at": STATE.get("white_at", 0.0), "big_at": STATE.get("big_at", 0.0),
            "bpm": STATE.get("bpm", 0), "palette": STATE.get("palette"), "style": config.STYLE, "flash": config.FLASH,
            "comfort": config.COMFORT, "night": config.COMFORT and colours.is_night(), "analysis": STATE.get("analysis"),
            # the room as it is: each light's live color and brightness, TvD's segments (left to right), what plays
            "lights": STATE.get("lights", {}), "tvd_segments": STATE.get("tvd_segments"),
            # where each of those segments really sits on the screen's edge, [across, down], in the same order
            "tvd_places": (config.STRIP_MAP[::-1] if config.SCREEN_SEGMENTS_FROM_RIGHT else config.STRIP_MAP) if config.STRIP_MAP else None,
            "audio": STATE.get("audio"), "game": STATE.get("game") or None, "source": STATE.get("source")}

WEB_DIR = os.path.join(HERE, "web")

def web_page(name):
    """A page from web/ (live.html, show.html): read on each request, so design changes show up on reload."""
    try:
        with open(os.path.join(WEB_DIR, name), "rb") as f:
            return f.read()
    except OSError:
        return f"<h1>web/{name} is missing next to light_sync.py</h1>".encode()

def phone_allowed(client):
    """Laptop: phone sound only from the Pi's app (which only signed-in Home Assistant users reach)."""
    from urllib.parse import urlparse
    return ROLE == "main" and client == (PHONE_FROM or urlparse(HA_URL).hostname)

def live_frame():
    """Light colors and music levels for the Light Sync app (no camera picture)."""
    e = EAR
    return {"t": time.time(), "host": HOST, "mode": STATE.get("mode", "paused"), "running": STATE.get("running", False),
            "music_in_game": bool(STATE.get("music_in_game")), "bpm": STATE.get("bpm", 0), "energy": STATE.get("energy", 0.0),
            "beats": STATE.get("beats", 0), "beat_at": STATE.get("beat_at", 0.0), "white_at": STATE.get("white_at", 0.0),
            "locked": bool(STATE.get("locked")), "lights": STATE.get("lights", {}), "source": STATE.get("source"),
            "pose": __import__("parts.pose_link", fromlist=["x"]).frame(), "camera": STATE.get("camera"), "game": STATE.get("game") or None, "audio": STATE.get("audio"),
            "screen_rgb": STATE.get("screen_rgb"),  # just the screen's color (TV section's "now"), never the picture
            "analysis": STATE.get("analysis"), "history": STATE.get("history", [])[-150:],
            "learned": STATE.get("learned"),  # an automatic switch to the usual mode: {"text", "at"} (app: Undo)
            "normal": STATE.get("normal") if STATE.get("mode") == "normal" else None,  # Normal now
            "normal_curve": STATE.get("normal_curve"),  # Normal's day, hour by hour: [brightness %, kelvin] (learned)
            "sound_test": soundtest.SOUND_TEST if time.time() - soundtest.SOUND_TEST.get("at", 0) < 600 else None,  # the Sound tab's test
            "bt": dict(sources.BT) if sources.BT.get("state") != "off" else None,  # Phone (Bluetooth): connected to which phone
            "spectrum": [round(float(v), 3) for v in e.spectrum] if e else [],
            "bands": [round(float(b), 3) for b in e.bands] if e else [0, 0, 0],
            # the app's heart: the predicted beat, so it moves on time despite polling, and TvD's parts in their colours
            "beat_anchor": (e.anchor if e.period() else e.last_beat) if e else 0.0, "period": e.period() if e else None,
            "big_at": STATE.get("big_at", 0.0), "tvd_segments": STATE.get("tvd_segments"), "comfort": config.COMFORT,
            "voice": {"cue": rocky_cue.VOICE["cue"], "age": round(time.time() - rocky_cue.VOICE["at"], 1)},  # Rocky listening (the TV widgets)
            # the new app (Nov 6): the DJ's scene, the room (Lights follow me, Room guard), and where TvD's parts sit
            # (in tvd_segments' order) so it can draw the strip around the TV
            "dj": STATE.get("dj"), "tv_party": bool(STATE.get("tv_party")), "volume_ref": STATE.get("volume_ref"), "volume_dim": STATE.get("volume_dim"),
            "room_on": STATE.get("room_on"), "blackout": STATE.get("blackout"), "tv_alive": STATE.get("tv_alive"), "room": {k: STATE.get("room", {}).get(k) for k in ("here", "follow", "guard", "absent_s")},
            "strip": ([list(p) for p in (config.STRIP_MAP[::-1] if config.SCREEN_SEGMENTS_FROM_RIGHT else config.STRIP_MAP)]
                      if config.STRIP_MAP else None)}

# The widgets on the TV (/tv, Oct 31): the time, weather, wake-up, is all well. Home network, so nothing private: no camera, no Wi-Fi
# list, not whether anyone's home. Light colours come from /live.json, which doesn't count as an open show: the lights stay on the camera.
TV_WEATHER = "weather.forecast_maison"
TV_NOW = {"at": 0.0, "busy": False, "weather": {}, "days": [], "days_at": 0.0, "wake": None, "status": "", "ok": True, "tz": None,
          "sun": {}, "box": None, "net": None, "pi": None, "updates": 0}
TV_APPS = {"com.google.android.youtube.tv": "YouTube", "com.amazon.amazonvideo.livingroom": "Prime Video", "com.iptvav.av_iptv": "IPTV",
           "com.softwinner.miracastReceiver": "Screen mirroring", "com.android.chrome": "Light Sync"}

def tv_refresh():
    def state(eid):
        try:
            return ha_link.ha_state(eid)
        except Exception:
            return {}
    try:
        try:  # Home Assistant's time zone: the Pi's own clock runs on UTC (Nov 2: the TV said 5 PM at 9 AM)
            TV_NOW["tz"] = float(ha_link.session.post(f"{HA_URL}/api/template", timeout=5,
                                                      json={"template": "{{ now().utcoffset().total_seconds() }}"}).text)
        except (requests.RequestException, ValueError):
            pass
        w = state(TV_WEATHER)
        a = w.get("attributes", {})
        if w.get("state") not in (None, "unavailable", "unknown"):
            TV_NOW["weather"] = {"condition": w["state"], "temperature": a.get("temperature"), "humidity": a.get("humidity"),
                                 "wind": a.get("wind_speed"), "wind_unit": a.get("wind_speed_unit")}
        if time.time() - TV_NOW["days_at"] > 1800:
            r = ha_link.session.post(f"{HA_URL}/api/services/weather/get_forecasts?return_response", timeout=10,
                                     json={"entity_id": TV_WEATHER, "type": "daily"})
            if r.ok:
                from datetime import datetime
                TV_NOW["days_at"] = time.time()
                TV_NOW["days"] = [{"at": datetime.fromisoformat(f["datetime"]).timestamp(), "condition": f.get("condition"),
                                   "high": f.get("temperature"), "low": f.get("templow")}
                                  for f in r.json()["service_response"][TV_WEATHER]["forecast"]][1:5]
        sun = state("sun.sun").get("attributes", {})
        from datetime import datetime as _dt
        TV_NOW["sun"] = {k: _dt.fromisoformat(sun[a]).timestamp() for k, a in (("rise", "next_rising"), ("set", "next_setting")) if sun.get(a)}
        box = state("media_player.x96q_pro_plus")
        app = box.get("attributes", {}).get("app_name")
        TV_NOW["box"] = None if box.get("state") in (None, "off", "unavailable") else (TV_APPS.get(app) or (app or "").split(".")[-1].capitalize() or None)
        def num(eid):
            try:
                return round(float(state(eid).get("state")))
            except (TypeError, ValueError):
                return None
        TV_NOW["net"] = {"down": num("sensor.speedtest_download"), "up": num("sensor.speedtest_upload"), "ping": num("sensor.speedtest_ping")}
        TV_NOW["pi"] = {"heat": num("sensor.system_monitor_temperature_du_processeur"), "disk": num("sensor.system_monitor_utilisation_du_disque")}
        try:
            TV_NOW["updates"] = sum(1 for s in ha_link.session.get(f"{HA_URL}/api/states", timeout=10).json()
                                    if s["entity_id"].startswith("update.") and s["state"] == "on")
        except (requests.RequestException, ValueError):
            pass
        TV_NOW["wake"] = state("input_datetime.wake_up_time").get("state", "")[:5] if state("input_boolean.wake_up_light").get("state") == "on" else None
        # the same limits as the dashboard's status line (tools/ha_setup/dashboard.py)
        problems = []
        try:
            if float(state("sensor.system_monitor_utilisation_du_disque").get("state", 0)) >= 85:
                problems.append("Pi disk nearly full")
            if float(state("sensor.system_monitor_temperature_du_processeur").get("state", 0)) >= 176:
                problems.append("Pi is hot")
        except ValueError:
            pass
        if state("sensor.light_sync_usb_backup").get("state") == "problem":
            problems.append("USB backup needs a look")
        from datetime import datetime
        last = state("sensor.backup_last_successful_automatic_backup").get("state", "")
        try:
            if time.time() - datetime.fromisoformat(last).timestamp() > 172800:
                problems.append("no backup for 2 days")
        except ValueError:
            pass
        TV_NOW["ok"], TV_NOW["status"] = not problems, ", ".join(problems) or "All good"
    finally:
        TV_NOW["at"], TV_NOW["busy"] = time.time(), False

def tv_frame():
    """What the TV's widgets need. Answers at once from what it knows, and looks again in the background every minute."""
    now = time.time()
    if now - TV_NOW["at"] > 60 and not TV_NOW["busy"]:
        TV_NOW["busy"] = True
        threading.Thread(target=tv_refresh, daemon=True, name="tv widgets").start()
    tz = TV_NOW["tz"] if TV_NOW["tz"] is not None else (time.localtime(now).tm_gmtoff or 0)
    wake = TV_NOW["wake"]
    if wake:
        h, m = int(wake[:2]), wake[3:]
        wake = f"{h % 12 or 12}:{m} {'AM' if h < 12 else 'PM'}"
    hm = lambda t: time.strftime("%I:%M %p", time.gmtime(t + tz)).lstrip("0") if t else None
    today = time.strftime("%Y-%m-%d")
    first = wifi.WIFI_KNOWN_CACHE or {}
    return {"local_t": now + tz, "tz_s": tz, "weather": TV_NOW["weather"], "days": TV_NOW["days"], "wake": wake,
            "status": TV_NOW["status"], "ok": TV_NOW["ok"], "sunrise": hm(TV_NOW["sun"].get("rise")), "sunset": hm(TV_NOW["sun"].get("set")),
            "box": TV_NOW["box"], "net": TV_NOW["net"], "pi": TV_NOW["pi"], "updates": TV_NOW["updates"], "host": HOST if not config.STANDBY else "laptop",
            # the Wi-Fi as counts only: how many devices now, how many first seen today (who they are stays in the app)
            "wifi": {"now": len(wifi.WIFI), "new_today": sum(1 for m in wifi.WIFI if str(first.get(m, ""))[:10] == today)} if wifi.WIFI else None}

def show_route(path, client=""):
    """The show endpoints, shared by every server (show port, live view, Pi app). No camera, no live view.
    Returns (body, content type) or None."""
    global SHOW_SEEN, SHOW_TV_SEEN
    from urllib.parse import parse_qs, urlencode, urlparse
    if path == "/alive":  # the Pi asks the laptop this every second
        return json.dumps({"host": HOST, "standby": config.STANDBY}).encode(), "application/json"
    if path.startswith("/tv.json"):
        if config.STANDBY and config.MAIN_INFO.get("show_url"):  # the Pi standing by: the laptop knows the Wi-Fi
            try:
                return pi_backup.PLAIN.get(config.MAIN_INFO["show_url"].rsplit("/show", 1)[0] + "/tv.json", timeout=1).content, "application/json"
            except requests.RequestException:
                pass
        return json.dumps(tv_frame()).encode(), "application/json"
    if path == "/tv" or path.startswith("/tv?"):
        return web_page("tv.html"), "text/html; charset=utf-8"
    if path.startswith("/show.json") or path.startswith("/live.json"):
        name = "/live.json" if path.startswith("/live.json") else "/show.json"
        q = parse_qs(urlparse(path).query)
        if config.STANDBY and config.MAIN_INFO.get("show_url"):
            # Pi standing by: the laptop drives the lights, so pass its data on (one show address that always works,
            # whichever machine is running), with the show's own fps / q / tv, so the laptop knows it's on the TV
            fwd = urlencode({k: q[k][0][:8] for k in ("fps", "q", "tv", "id") if k in q})
            try:
                return (pi_backup.PLAIN.get(config.MAIN_INFO["show_url"].rsplit("/show", 1)[0] + name + (f"?{fwd}" if fwd else ""),
                                  timeout=0.5).content, "application/json")
            except requests.RequestException:
                pass
        if name == "/live.json":
            return json.dumps(live_frame()).encode(), "application/json"
        SHOW_SEEN = time.time()
        on_tv = q.get("tv", ["0"])[0] == "1"
        if on_tv:
            SHOW_TV_SEEN = SHOW_SEEN
        tab = re.sub(r"[^a-z0-9]", "", q.get("id", [""])[0][:8])  # each show tab apart (a TV box keeps old tabs open)
        SHOW_CLIENTS[f"{client} {tab}".strip()] = {"fps": q.get("fps", ["?"])[0][:4], "scale": q.get("q", ["?"])[0][:4], "seen": round(SHOW_SEEN),
                                "tv": on_tv}
        return json.dumps(show_frame()).encode(), "application/json"
    if path.startswith("/show"):
        return web_page("show.html"), "text/html; charset=utf-8"
    return None

def start_view():
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    show_page = show_route

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            show = show_page(self.path, self.client_address[0])
            if show:
                body, ctype = show
            elif self.path.startswith("/state"):
                now = time.time()
                e = EAR
                body = json.dumps({**STATE, "show_clients": {ip: c for ip, c in SHOW_CLIENTS.items() if now - c["seen"] < 10},
                                   "spectrum": [round(float(v), 3) for v in e.spectrum] if e else [],
                                   "bands": [round(float(b), 3) for b in e.bands] if e else []}).encode()
                ctype = "application/json"
            elif self.path.startswith("/cam.jpg"):
                if not eyes.CAM_JPG:
                    self.send_response(404)
                    self.end_headers()
                    return
                body = eyes.CAM_JPG
                ctype = "image/jpeg"
            else:
                body = web_page("live.html")
                ctype = "text/html; charset=utf-8"
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

        def do_POST(self):
            """The live view's corner editor (this laptop only: the live view answers on 127.0.0.1 / ::1)."""
            body = self.rfile.read(min(int(self.headers.get("Content-Length", 0)), 65536))
            if self.path == "/tvbox" and self.client_address[0] in ("127.0.0.1", "::1", "::ffff:127.0.0.1"):
                # Nov 6 (owner: "let me edit the points myself"): the screen's 4 corners dragged in the live view
                # (this laptop only: the live view answers on 127.0.0.1 / ::1)
                try:
                    q = json.loads(body or b"{}")["quad"]
                    quad = [(float(x), float(y)) for x, y in q]
                    assert len(quad) == 4 and all(0 <= v <= 1 for p in quad for v in p)
                    from parts import screen_map  # (imported here: screen_map imports web)
                    screen_map.save_tv_box(None, "corners set by hand in the live view", quad)
                    out, code = json.dumps({"ok": True}).encode(), 200
                except Exception as e:
                    out, code = json.dumps({"error": str(e)}).encode(), 400
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(out)
                return
            self.send_response(404)
            self.end_headers()

    class ShowHandler(BaseHTTPRequestHandler):
        """Home-network server: only the show visuals and their music data (no camera, no live view)."""
        def do_GET(self):
            if self.path.startswith("/ollama/"):
                assistant.ollama_proxy(self, "GET")
                return
            if self.path.startswith("/logs.json"):  # the app's Logs, through the Pi (Nov 6): only the Pi may ask
                if not phone_allowed(self.client_address[0]):
                    self.send_response(404)
                    self.end_headers()
                    return
                from parts import logring
                body = json.dumps({"host": HOST, "lines": logring.recent()}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
                return
            show = show_page("/show" if self.path == "/" else self.path, self.client_address[0])
            if not show:
                self.send_response(404)
                self.end_headers()
                return
            body, ctype = show
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            """Phone sound passed on by the Pi's app, or the room's sound from the Pi (nobody else may send them)."""
            if self.path.startswith("/ollama/"):  # Home Assistant's voice assistant (the prompt and its tools: bigger)
                assistant.ollama_proxy(self, "POST", self.rfile.read(min(int(self.headers.get("Content-Length", 0)), 4 << 20)))
                return
            body = self.rfile.read(min(int(self.headers.get("Content-Length", 0)), 262144))
            if self.path in ("/laptop_ask", "/laptop_tell"):  # Nov 12: the game helper asks / tells through here
                if not config.from_laptop(self.client_address[0]):
                    self.send_response(404)
                    self.end_headers()
                    return
                from parts import approve
                try:
                    d = json.loads(body or b"{}")
                    on = lambda: ha_link.set_switch(config.HA_GAMING, True)  # noqa: E731
                    if self.path == "/laptop_ask":
                        game = str(d.get("game") or "a game")[:60].replace(".exe", "")
                        approve.ask("gaming", f"{game} is running on the laptop: turn on Gaming?", on, timeout=300)
                    else:
                        approve.tell(str(d.get("text") or "")[:200], undo=on if d.get("undo") == "gaming" else None)
                    out, code = b'{"ok": true}', 200
                except Exception:
                    out, code = b'{"ok": false}', 400
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(out)
                return
            if self.path == "/laptop":  # Nov 7: the laptop helper's "seconds since a key / the mouse" (Lights follow me)
                if not config.from_laptop(self.client_address[0]):
                    self.send_response(404)
                    self.end_headers()
                    return
                from parts import guard
                try:
                    guard.LAPTOP_IDLE.update(s=max(0.0, float(json.loads(body or b"{}").get("idle"))), at=time.time())
                    out, code = b'{"ok": true}', 200
                except Exception:
                    out, code = b'{"ok": false}', 400
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(out)
                return
            if self.path == "/update":  # Nov 7: the add-on updates its own files from the laptop (no more pasting)
                if not config.from_laptop(self.client_address[0]):
                    self.send_response(404)
                    self.end_headers()
                    return
                try:
                    import updater  # the Pi add-on's own (pi_addon/light_sync/updater.py)
                    src = json.loads(body or b"{}").get("src") or f"http://{config.laptop_ip()}:8099"
                    out, code = json.dumps(updater.update(src)).encode(), 200
                except Exception as e:
                    out, code = json.dumps({"error": str(e)}).encode(), 500
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(out)
                return
            if self.path == "/wifi_lists":  # Nov 7: the laptop's Wi-Fi lists, handed to the Pi once (the laptop only)
                if not config.from_laptop(self.client_address[0]):
                    self.send_response(404)
                    self.end_headers()
                    return
                out = json.dumps(wifi.set_lists(body)).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(out)
                return
            if self.path == "/game":  # the laptop's game helper (Nov 6): a game's colours, numbers only, from the laptop only
                from parts import games
                if not config.from_laptop(self.client_address[0]):
                    self.send_response(404)
                    self.end_headers()
                    return
                try:
                    out, code = json.dumps(games.take_remote(body)).encode(), 200
                except Exception:
                    out, code = b'{"ok": false}', 400
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(out)
                return
            if self.path not in ("/phone", "/room", "/vibe", "/wifi") or not phone_allowed(self.client_address[0]):
                self.send_response(404)
                self.end_headers()
                return
            if self.path == "/vibe":  # "Describe a vibe" from the Pi's app (the API key lives on the laptop)
                out = json.dumps(app_requests.app_request(body)).encode()
            elif self.path == "/wifi":  # the home Wi-Fi now, for the Pi's app (signed-in users only)
                out = json.dumps(wifi.wifi_frame()).encode()
            else:
                out = json.dumps({"t": ears.phone_feed(body, room=self.path == "/room")}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(out)

        def log_message(self, *a):
            pass

    url = None
    if VIEW_PORT:  # 0 = no live view (Pi add-on)
        # 127.0.0.1 = only this laptop can open it (the page includes the bedroom camera preview)
        server = ThreadingHTTPServer((os.getenv("VIEW_HOST", "127.0.0.1"), VIEW_PORT), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        # Nov 6 (owner: "it's not smooth", a video of the camera preview): "localhost" tries IPv6 (::1) first, and with
        # nothing there Windows waits 2 s before 127.0.0.1, so every preview picture took 2.05 s (0.016 s on
        # 127.0.0.1). Also answer on ::1, still this laptop only, and open the page by its number.
        try:
            import socket

            class Server6(ThreadingHTTPServer):
                address_family = socket.AF_INET6
            server6 = Server6(("::1", VIEW_PORT), Handler)
            threading.Thread(target=server6.serve_forever, daemon=True).start()
        except OSError as e:
            print(f"Live view on ::1 not started ({e}): use http://127.0.0.1:{VIEW_PORT}", flush=True)
        url = f"http://127.0.0.1:{VIEW_PORT}"
        print(f"Live view: {url}")
    if SHOW_PORT:
        try:
            show_server = ThreadingHTTPServer((os.getenv("SHOW_HOST", "0.0.0.0"), SHOW_PORT), ShowHandler)
            threading.Thread(target=show_server.serve_forever, daemon=True).start()
            print(f"Show for the home network: http://{ha_link.lan_ip()}:{SHOW_PORT}/show")
        except OSError as e:
            print(f"Show server not started: {e}", flush=True)
    return url
