"""Light Sync app: the add-on's page in the Home Assistant sidebar (through ingress).

Only Home Assistant's ingress proxy may connect (so only signed-in HA users reach it), and it can only change
Light Sync's own helpers, never other entities, and the two cameras' controls (cameras.py). Live data comes
from Light Sync (or from the laptop while the Pi stands by); it never includes the camera picture.
"""
import json
import re
import threading
import requests
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import cameras
import checks
import light_sync as ms

INGRESS_PROXY = "172.30.32.2"  # the Supervisor's ingress proxy: the only allowed client
TOGGLES = {"input_boolean.light_sync_party_pro", "input_boolean.music_sync", "input_boolean.tv_ambient", "input_boolean.gaming_mode", "input_boolean.auto_gaming",
           "input_boolean.game_music", "input_boolean.light_sync_use_screen", "input_boolean.light_sync_use_beat",
           "input_boolean.light_sync_use_wash", "input_boolean.wake_up_light", "input_boolean.light_sync_tvd_segments",
           "input_boolean.light_sync_comfort", "input_boolean.light_sync_mood_colors", "input_boolean.normal_mode",
           "input_boolean.late_night_mode", "input_boolean.tv_music",
           "input_boolean.light_sync_reaction_auto", "input_boolean.light_sync_auto_dj", "input_boolean.light_sync_pulse_screen",
           "input_boolean.light_sync_pulse_beat", "input_boolean.light_sync_pulse_wash", "input_boolean.light_sync_auto_learn", "input_boolean.light_sync_auto_mode",
           "input_boolean.light_sync_cinema", "input_boolean.light_sync_bedtime_reminder",
           "input_boolean.room_guard", "input_boolean.lights_follow_me",  # the Home page (Oct 21)
           "input_boolean.telegram_control", "input_boolean.notify_security", "input_boolean.notify_system",  # Home, System (Nov 2)
           "input_boolean.notify_backups", "input_boolean.notify_sleep", "input_boolean.notify_updates"}
# the Room page's scene buttons (Nov 2): the same scripts as the dashboard's Quick buttons, and the sensors that say which one plays
SCENES = {"script.normal_light", "script.party_mode", "script.movie_time", "script.movie_music", "script.gaming_time",
          "script.gaming_music", "script.good_night", "script.all_lights_off"}
SCENE_ON = {"binary_sensor.light_sync_mode_" + n for n in ("normal", "party", "movie", "movie_music", "gaming", "game_music",
                                                            "good_night", "all_off")}
# the TV page (Nov 2): the box's keys and apps, by name; nothing else reaches the box from the app
TV_KEYS = {"POWER", "HOME", "BACK", "MENU", "SEARCH", "SETTINGS", "DPAD_UP", "DPAD_DOWN", "DPAD_LEFT", "DPAD_RIGHT", "DPAD_CENTER",
           "MEDIA_REWIND", "MEDIA_PLAY_PAUSE", "MEDIA_FAST_FORWARD", "VOLUME_DOWN", "VOLUME_UP", "VOLUME_MUTE", "INFO"}
TV_APPS = {"youtube": ("remote", "com.google.android.youtube.tv"), "prime": ("remote", "com.amazon.amazonvideo.livingroom"),
           "iptv": ("remote", "com.iptvav.av_iptv"), "mirroring": ("remote", "com.softwinner.miracastReceiver"),
           "widgets": ("remote", "http://192.168.1.10:8090/tv"), "show_on": ("say", "start the show"), "show_off": ("say", "close the show")}
TV_BOX = "remote.x96q_pro_plus"
ROOM_LIGHTS = {"light.chambre_tvd", "light.h6004_2", "light.h6004"}  # Normal's light buttons (Nov 2)
SYSTEM = {"sensor.system_monitor_temperature_du_processeur", "sensor.system_monitor_utilisation_du_disque",
          "sensor.backup_last_successful_automatic_backup", "sensor.light_sync_usb_backup", "media_player.x96q_pro_plus"} | ROOM_LIGHTS
SELECTS = {"input_select.music_flash", "input_select.music_style", "input_select.music_mic",
           "input_select.light_sync_part_screen", "input_select.light_sync_part_beat", "input_select.light_sync_part_wash",
           "input_select.light_sync_colors", "input_select.light_sync_color_change",
           "input_select.light_sync_normal_scene",
           "input_select.light_sync_tv_corner"}
TEXTS = {"input_text.light_sync_my_colors": r"#[0-9a-fA-F]{6}(,#[0-9a-fA-F]{6}){0,5}",  # value must match fully
         "input_text.light_sync_normal_adjust": r"-?\d{1,3},-?\d{1,4}",  # Normal's Reset: "0,0"
         "input_text.light_sync_tv_box": r"[01](\.\d{1,2})?(,[01](\.\d{1,2})?){7}",  # the corner editor (Nov 6)
         "input_text.light_sync_normal_room": r""}  # Normal's Reset: empty (the room as left, forgotten)
NUMBERS = {"input_number.music_sensitivity", "input_number.light_sync_reaction", "input_number.light_sync_bright_min",
           "input_number.light_sync_bright_max", "input_number.light_sync_beat_lock", "input_number.light_sync_latency",
           "input_number.light_sync_level_screen", "input_number.light_sync_full_volume",
           "input_number.light_sync_level_beat", "input_number.light_sync_level_wash",
           "input_number.light_sync_normal_bright", "input_number.light_sync_normal_kelvin"}
TIMES = {"input_datetime.wake_up_time", "input_datetime.light_sync_bedtime"}  # + the sleep calculator's bedtime
WATCH = TOGGLES | SELECTS | NUMBERS | TIMES | set(TEXTS) | {"sensor.light_sync", "sensor.music_sync_status", "sensor.light_sync_laptop",
                                                            "input_text.light_sync_last_mode"} | SCENE_ON | SYSTEM  # read only


def ha_states():
    r = ms.session.get(f"{ms.HA_URL}/api/states", timeout=5)
    r.raise_for_status()
    return {s["entity_id"]: {"state": s["state"], "attributes": s["attributes"]} for s in r.json() if s["entity_id"] in WATCH}


def call(domain, service, data):
    r = ms.session.post(f"{ms.HA_URL}/api/services/{domain}/{service}", json=data, timeout=5)
    r.raise_for_status()


def grab_img(which):
    """One picture of the TV ("tv") or room camera (through Home Assistant), as numbers for the cameras' tests;
    never kept or sent anywhere."""
    url = ms.SNAPSHOT_URL if which == "tv" else ms.ROOM_CAM_URL
    if not url:
        return None
    headers = {"Authorization": f"Bearer {ms.HA_TOKEN}"} if "/api/camera_proxy/" in url else {}
    status, ctype, data = ms.fetch_frame(url, ms.stream_auth(url) if not headers else None, headers)
    return ms.decode_bgr(data) if data else None


def grab_grey(which):
    """One picture as 48x64 grey numbers, for cameras.identify."""
    img = grab_img(which)
    return None if img is None else ms.guard_grey(img, None, None)


def apply(eid, value):
    """Change one Light Sync helper; anything else is refused."""
    if eid in TOGGLES:
        call("input_boolean", "turn_on" if value else "turn_off", {"entity_id": eid})
    elif eid in SELECTS:
        if value not in ms.ha_state(eid)["attributes"].get("options", []):
            raise ValueError("not one of the options")
        call("input_select", "select_option", {"entity_id": eid, "option": value})
    elif eid in NUMBERS:
        a = ms.ha_state(eid)["attributes"]
        call("input_number", "set_value", {"entity_id": eid, "value": min(max(float(value), a["min"]), a["max"])})
    elif eid in TEXTS:
        if not re.fullmatch(TEXTS[eid], str(value)):
            raise ValueError("colors must be #rrggbb (comma separated where several are allowed)")
        call("input_text", "set_value", {"entity_id": eid, "value": str(value)})
    elif eid in TIMES:
        if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", str(value)):
            raise ValueError("time must be HH:MM")
        call("input_datetime", "set_datetime", {"entity_id": eid, "time": f"{value}:00"})
    else:
        raise PermissionError(f"{eid} can't be changed from the app")


class AppHandler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body if isinstance(body, bytes) else json.dumps(body).encode())

    def _allowed(self):
        if self.client_address[0] != INGRESS_PROXY:
            self._send(403, {"error": "open Light Sync from the Home Assistant sidebar"})
            return False
        return True

    def do_GET(self):
        if not self._allowed():
            return
        path = self.path.split("?", 1)[0]
        try:
            if path in ("/", "/index.html"):
                self._send(200, ms.web_page("app.html"), "text/html; charset=utf-8")
            elif path == "/live":
                body, ctype = ms.show_route("/live.json")
                self._send(200, body, ctype)
            elif path == "/state":
                self._send(200, {"entities": ha_states(), "standby": ms.STANDBY, "host": "laptop" if ms.STANDBY else ms.HOST})
            elif path == "/wifi":  # the home Wi-Fi now (whichever machine drives the lights scans; memory only)
                if ms.STANDBY and ms.MAIN_INFO.get("show_url"):
                    r = ms.PLAIN.post(ms.MAIN_INFO["show_url"].rsplit("/show", 1)[0] + "/wifi", timeout=5)
                    self._send(r.status_code, r.content)
                else:
                    self._send(200, ms.wifi_frame())
            elif path == "/cams":  # the cameras' controls (cameras.py)
                self._send(200, cameras.listing())
            elif path == "/cams/tune":  # Auto-tune's progress
                self._send(200, cameras.TUNE)
            elif path == "/roomcam.mjpg":  # the same, as live video (one picture through HA took ~1.3 s: choppy)
                # the Supervisor's way to Home Assistant can't pass a stream on (it waits for the end: 502 after 10 s),
                # so straight to Home Assistant on this Pi, with the camera's own short-lived access key
                url, headers = "", {}
                if "/api/camera_proxy/" in (ms.ROOM_CAM_URL or ""):
                    eid = ms.ROOM_CAM_URL.rsplit("/", 1)[-1]
                    try:
                        tok = ms.ha_state(eid)["attributes"]["access_token"]
                        url = f"http://127.0.0.1:8123/api/camera_proxy_stream/{eid}?token={tok}"
                    except Exception:
                        url = ""
                elif ms.ROOM_CAM_URL:
                    url = ms.ROOM_CAM_URL
                try:
                    r = requests.get(url, headers=headers, auth=None if "/api/camera_proxy_stream/" in url else ms.stream_auth(url),
                                     stream=True, timeout=10)
                    r.raise_for_status()
                except Exception:
                    self._send(502, {"error": "the room camera gave no picture"})
                    return
                started = time.time()
                try:
                    self.send_response(r.status_code)
                    self.send_header("Content-Type", r.headers.get("Content-Type", "multipart/x-mixed-replace"))
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    for chunk in r.iter_content(65536):  # passed through as it comes, never kept
                        self.wfile.write(chunk)
                        if time.time() - started > 1800:  # 30 min at most; the app asks again if still watching
                            break
                except (BrokenPipeError, ConnectionResetError):
                    pass  # the app stopped watching
                finally:
                    r.close()
            elif path == "/roomcam":  # Nov 7, owner: "the CCTV of the room back" - live only, nothing saved
                url = ms.ROOM_CAM_URL
                headers = {"Authorization": f"Bearer {ms.HA_TOKEN}"} if url and "/api/camera_proxy/" in url else {}
                try:
                    status, ctype, data = ms.fetch_frame(url, ms.stream_auth(url) if not headers else None, headers) if url else (0, "", b"")
                except Exception:
                    status, data = 0, b""
                if data and status == 200:
                    self._send(200, data, "image/jpeg")
                else:
                    self._send(502, {"error": "the room camera gave no picture"})
            elif path == "/tvpic":  # the corner editor (Nov 6): one picture of the TV area only, never kept
                import base64, io
                from PIL import Image
                img = grab_img("tv")
                if img is None:
                    self._send(502, {"error": "the TV camera gave no picture"})
                    return
                h, w = img.shape[:2]
                quad = ms.TV_QUAD or ((ms.TV_BOX[0], ms.TV_BOX[1]), (ms.TV_BOX[2], ms.TV_BOX[1]),
                                      (ms.TV_BOX[2], ms.TV_BOX[3]), (ms.TV_BOX[0], ms.TV_BOX[3]))
                xs, ys = [p[0] for p in quad], [p[1] for p in quad]
                mx, my = 0.15 * (max(xs) - min(xs)) + 0.03, 0.15 * (max(ys) - min(ys)) + 0.03
                crop = [max(0.0, min(xs) - mx), max(0.0, min(ys) - my), min(1.0, max(xs) + mx), min(1.0, max(ys) + my)]
                part = img[int(crop[1] * h):int(crop[3] * h), int(crop[0] * w):int(crop[2] * w), ::-1]  # RGB
                buf = io.BytesIO()
                Image.fromarray(part).save(buf, "JPEG", quality=70)
                self._send(200, {"crop": crop, "quad": [list(p) for p in quad],
                                 "jpg": base64.b64encode(buf.getvalue()).decode()})
            elif path == "/prayer/dev":  # Nov 8: dev mode - what the tracker reads (box, ratio, position, the mask)
                from parts import prayer_track
                self._send(200, prayer_track.dev_frame())
            elif path == "/prayer":  # Nov 8: the prayer times and the rak'a tracker
                from parts import prayer
                self._send(200, prayer.frame())
            elif path == "/logs":  # the app's Logs (Nov 6): whichever machine drives the lights
                from parts import logring
                body = None
                if ms.STANDBY and ms.MAIN_INFO.get("show_url"):
                    try:
                        r = ms.PLAIN.get(ms.MAIN_INFO["show_url"].rsplit("/show", 1)[0] + "/logs.json", timeout=3)
                        if r.ok:
                            body = r.json()
                    except Exception:
                        body = None
                self._send(200, body or {"host": "pi", "lines": logring.recent()})
            elif path == "/check":  # the system check (checks.py): read only
                self._send(200, checks.run(grab_img))
            elif path.startswith("/show"):  # the full show, opened from the app
                show = ms.show_route(self.path, "app")
                if show:
                    self._send(200, show[0], show[1])
                else:
                    self._send(404, {"error": "not found"})
            else:
                self._send(404, {"error": "not found"})
        except Exception as e:
            self._send(502, {"error": str(e)})

    def do_POST(self):
        if not self._allowed():
            return
        path = self.path.split("?", 1)[0]
        if path == "/phone":  # the phone's mic ("Listen"): to whichever machine drives the lights
            body = self.rfile.read(min(int(self.headers.get("Content-Length", 0)), 262144))
            try:
                if ms.STANDBY and ms.MAIN_INFO.get("show_url"):
                    r = ms.PLAIN.post(ms.MAIN_INFO["show_url"].rsplit("/show", 1)[0] + "/phone", data=body, timeout=1)
                    self._send(r.status_code, r.content)
                else:
                    self._send(200, {"t": ms.phone_feed(body)})
            except Exception as e:
                self._send(502, {"error": str(e)})
            return
        if path == "/prayer":  # Nov 8: start / stop the rak'a tracker
            from parts import prayer, prayer_track
            try:
                d = json.loads(self.rfile.read(min(int(self.headers.get("Content-Length", 0)), 4096)) or b"{}")
                if d.get("action") == "stop":
                    prayer_track.stop()
                else:
                    name = d.get("prayer") if d.get("prayer") in prayer.RAKAAT else prayer.current_prayer()
                    rak = int(d.get("rakaat") or prayer.RAKAAT[name])
                    if not 1 <= rak <= 8:
                        raise ValueError("rak'at 1-8")
                    prayer_track.start(name, rak)
                self._send(200, prayer.frame())
            except Exception as e:
                self._send(400, {"error": str(e)})
            return
        if path == "/vibe":  # the app's requests (Undo, For you, ...): by whichever machine drives the lights
            body = self.rfile.read(min(int(self.headers.get("Content-Length", 0)), 4096))
            if not (ms.STANDBY and ms.MAIN_INFO.get("show_url")):  # the Pi drives: Undo here
                self._send(200, ms.app_request(body))
                return
            try:
                r = ms.PLAIN.post(ms.MAIN_INFO["show_url"].rsplit("/show", 1)[0] + "/vibe", data=body, timeout=90)
                self._send(r.status_code, r.content)
            except Exception as e:
                self._send(502, {"error": f"laptop not reachable ({type(e).__name__})"})
            return
        if path == "/cams":  # move / zoom a camera, Home, Save as Home, which is which, Auto-tune
            try:
                data = json.loads(self.rfile.read(min(int(self.headers.get("Content-Length", 0)), 4096)) or b"{}")
                if data.get("tune"):
                    if not cameras.TUNE["running"]:
                        cameras.TUNE.update(running=True, steps=[], ok=None)  # at once, so a second tap can't start another
                        threading.Thread(target=cameras.auto_tune, args=(grab_img,), daemon=True).start()
                    self._send(200, cameras.TUNE)
                    return
                self._send(200, cameras.command(data, grab_grey))
            except Exception as e:
                self._send(400, {"error": str(e)})
            return
        if path == "/light":  # Normal's light buttons: one of the three on or off (Light Sync takes it into the room)
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0)) or 0) or b"{}")
            except ValueError:
                body = {}
            if body.get("entity_id") not in ROOM_LIGHTS:
                self._send(400, {"error": "not a bedroom light"})
                return
            try:
                call("light", "turn_on" if body.get("on") else "turn_off", {"entity_id": body["entity_id"]})
                self._send(200, {"ok": True})
            except Exception as e:
                self._send(502, {"error": str(e)})
            return
        if path in ("/remote", "/tvapp"):
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0)) or 0) or b"{}")
            except ValueError:
                body = {}
            try:
                if path == "/remote" and body.get("key") in TV_KEYS:
                    call("remote", "send_command", {"entity_id": TV_BOX, "command": body["key"]})
                elif path == "/tvapp" and body.get("app") in TV_APPS:
                    how, what = TV_APPS[body["app"]]
                    if how == "remote":
                        call("remote", "turn_on", {"entity_id": TV_BOX, "activity": what})
                    else:  # the same as saying it: the voice commands open / close the show on the box
                        call("conversation", "process", {"text": what, "language": "en"})
                else:
                    self._send(400, {"error": "not a TV button"})
                    return
                self._send(200, {"ok": True})
            except Exception as e:
                self._send(502, {"error": str(e)})
            return
        if path == "/scene":  # a scene button: its script, as the dashboard's Quick button
            try:
                eid = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0)) or 0) or b"{}").get("entity_id")
            except ValueError:
                eid = None
            if eid not in SCENES:
                self._send(400, {"error": "not a scene"})
                return
            try:
                call("script", "turn_on", {"entity_id": eid})
                self._send(200, {"ok": True})
            except Exception as e:
                self._send(502, {"error": str(e)})
            return
        if path != "/set":
            self._send(404, {"error": "not found"})
            return
        try:
            n = min(int(self.headers.get("Content-Length", 0)), 4096)
            data = json.loads(self.rfile.read(n) or b"{}")
            apply(str(data.get("entity_id", "")), data.get("value"))
            self._send(200, {"ok": True})
        except PermissionError as e:
            self._send(403, {"error": str(e)})
        except Exception as e:
            self._send(400, {"error": str(e)})

    def log_message(self, *a):
        pass


def start(port):
    server = ThreadingHTTPServer(("0.0.0.0", port), AppHandler)  # host network: other clients get 403
    threading.Thread(target=server.serve_forever, daemon=True).start()
    threading.Thread(target=cameras.restore, daemon=True).start()  # the picture settings chosen in the app
    threading.Thread(target=cameras.keep_home_loop, daemon=True).start()  # a camera left moved 30 min goes back Home
    print(f"Light Sync app on ingress port {port}", flush=True)
