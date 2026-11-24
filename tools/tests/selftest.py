#!/usr/bin/env python3
"""
selftest.py - end-to-end check of the whole setup: repo and secrets, laptop Light Sync, the Pi add-on,
Home Assistant (config, automations, entities, themes, voice, camera login, backups, dashboard) and security.

  py tools/tests/selftest.py              # read-only checks (two harmless voice commands only when they change nothing)
  py tools/tests/selftest.py --features   # also every feature end to end: the app through HA, the phone-mic path
                                    # (lights pulse to a fake beat ~6 s), voice (speech -> text -> command), remote,
                                    # camera, Tailscale
  py tools/tests/selftest.py --handover   # also the real laptop -> Pi -> laptop handover (lights dance ~1 min;
                                    # switches are put back as they were)
  py tools/tests/selftest.py --all        # everything

Never prints secrets and never downloads a camera picture (status codes and sizes only).
"""
import ast
import datetime as dt
import json
import os
import re
import subprocess
import sys
import time

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # tools\ (export_ha)
import export_ha as ex  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HA = os.getenv("HA_URL", "http://192.168.1.10:8123")
PI = "192.168.1.10"
H = {"Authorization": f"Bearer {ex.token()}"}
RESULTS = []


def report(level, area, check, detail=""):
    RESULTS.append((level, area))
    mark = {"PASS": "PASS", "WARN": "WARN", "FAIL": "FAIL", "INFO": "info"}[level]
    print(f"  {mark:4}  {check}" + (f"  -  {detail}" if detail else ""), flush=True)


def ok(cond, area, check, detail="", warn=False):
    report("PASS" if cond else ("WARN" if warn else "FAIL"), area, check, detail)
    return cond


def get(url, **kw):
    try:
        return requests.get(url, timeout=kw.pop("timeout", 5), **kw)
    except requests.RequestException as e:
        return e


def code(r):
    return r.status_code if isinstance(r, requests.Response) else f"no answer ({type(r).__name__})"


def ha_get(path):
    return requests.get(f"{HA}{path}", headers=H, timeout=10)


def ha_post(path, data=None):
    return requests.post(f"{HA}{path}", headers=H, json=data or {}, timeout=20)


def state(eid):
    r = ha_get(f"/api/states/{eid}")
    return r.json() if r.ok else None


def template(t):
    return ha_post("/api/template", {"template": t}).text.strip()


def lan_ip():
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.connect((PI, 80))
    ip = s.getsockname()[0]
    s.close()
    return ip


def ws_session():
    ws = ex.WebSocket(HA)
    ws.sock.settimeout(120)
    ws.recv()
    ws.send({"type": "auth", "access_token": ex.token()})
    assert ws.recv().get("type") == "auth_ok"
    return ws


BULBS = None  # light entity -> current IP from a fresh network scan (once per run)


def current_bulb_ips():
    """Where the bulbs are right now: a Govee LAN scan, matched to the device ids in start_lights.bat."""
    global BULBS
    if BULBS is None:
        bat = open(os.path.join(ROOT, "start_lights.bat"), encoding="utf-8", errors="ignore").read()
        env = dict(re.findall(r"(?m)^\s*set\s+(\w+)=(.*?)\s*$", bat))
        sys.path.insert(0, os.path.join(ROOT, "light_sync"))
        import light_sync as ms
        found = {dev.upper(): ip for ip, (sku, dev) in ms.govee_scan().items()}
        BULBS = {env.get(f"LIGHT_{r}"): found.get(env.get(f"GOVEE_{r}", "").upper(), "") for r in ("SCREEN", "BEAT", "WASH")}
    return BULBS


# ---------------------------------------------------------------- sections
def repo():
    print("\nRepo & secrets")
    a = "repo"
    tracked = subprocess.run(["git", "-C", ROOT, "ls-files"], capture_output=True, text=True).stdout.split()
    ok(os.path.exists(os.path.join(ROOT, "secrets.yaml")), a, "secrets.yaml present")
    ok("secrets.yaml" not in tracked, a, "secrets.yaml not in git")
    acl = subprocess.run(["icacls", os.path.join(ROOT, "secrets.yaml")], capture_output=True, text=True).stdout
    broad = [w for w in ("Everyone", "BUILTIN\\Users", "Authenticated Users") if w in acl]
    ok(not broad, a, "secrets.yaml readable only by the owner", ", ".join(broad) or "ACL ok")
    pattern = r"eyJ[A-Za-z0-9_-]{20,}|" + "sk-" + "ant-"  # split so this file doesn't match its own search
    leak = subprocess.run(["git", "-C", ROOT, "grep", "-I", "-l", "-E", pattern], capture_output=True, text=True).stdout.strip()
    ok(not leak, a, "no tokens or API keys in tracked files", leak)
    dirty = subprocess.run(["git", "-C", ROOT, "status", "--porcelain"], capture_output=True, text=True).stdout.strip()
    ok(not dirty, a, "git: everything committed", f"{len(dirty.splitlines())} changed file(s)" if dirty else "", warn=True)
    bad = []
    for d, _, files in os.walk(ROOT):
        if any(p in d for p in (".git", "backup_", "__pycache__")):
            continue
        for f in files:
            if f.endswith(".py"):
                try:
                    with open(os.path.join(d, f), encoding="utf-8") as fh:
                        ast.parse(fh.read())
                except SyntaxError as e:
                    bad.append(f"{f}: {e}")
    ok(not bad, a, "all Python files parse", "; ".join(bad))
    broken = []
    for md in ["README.md"] + [os.path.join("docs", f) for f in os.listdir(os.path.join(ROOT, "docs"))]:
        text = open(os.path.join(ROOT, md), encoding="utf-8").read()
        for link in re.findall(r"\]\(([^)#:]+)(?:#[^)]*)?\)", text):
            if not os.path.exists(os.path.normpath(os.path.join(ROOT, os.path.dirname(md), link))):
                broken.append(f"{md} -> {link}")
    ok(not broken, a, "documentation links point to real files", "; ".join(broken))


def laptop():
    print("\nLaptop (game helper; Nov 6-7: the Pi drives the lights)")
    a = "laptop"
    count = lambda pat: subprocess.run(["powershell", "-NoProfile", "-Command",
                                        "@(Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { $_.CommandLine -match '" + pat + "' }).Count"],
                                       capture_output=True, text=True).stdout.strip()
    helpers = count("game_helper\\.py")
    ok(helpers == "1", a, "exactly one game helper running", f"{helpers} running", warn=helpers == "0")
    ai = get("http://127.0.0.1:8090/ollama/api/tags", timeout=3)
    ok(isinstance(ai, requests.Response), a, "Rocky's AI port (8090) answers", str(code(ai)), warn=True)
    procs = count("light_sync\\.py")
    if procs == "0":
        report("PASS", a, "no full Light Sync on the laptop (the Pi drives)")
        return
    report("WARN", a, "the full Light Sync runs on the laptop (a test?)", f"{procs} running - the Pi stands by meanwhile")
    r = get("http://127.0.0.1:8089/state")
    if not ok(isinstance(r, requests.Response) and r.ok, a, "live view /state answers", str(code(r))):
        return
    s = r.json()
    report("INFO", a, "mode", f"{s.get('mode')} · colors from {s.get('source')} · {s.get('audio')} · camera {s.get('camera')}")
    ub = s.get("usb_backup") or {}
    ok(ub and not ub.get("problem"), a, "backups copied to the USB stick",
       ub.get("problem") or (f"{ub.get('copies')} copies, newest {ub.get('newest')}, {ub.get('free_gb')} GB free" if ub
                             else "no report yet (the first check comes 2 minutes after Light Sync starts)"), warn=True)
    room = s.get("room") or {}
    ok(room.get("camera") == "room", a, "Room guard / Lights follow me use the room camera", f"{room.get('camera')} camera"
       + (f", {room.get('why')}" if room.get("why") else ""), warn=True)
    opts = state("input_select.music_mic").get("attributes", {}).get("options", [])
    ok("Phone (Bluetooth)" in opts, a, "Sound source list has Phone (Bluetooth)", ", ".join(opts))
    err = os.path.join(ROOT, "logs", "light_sync_errors.log")
    stuck = os.path.exists(err) and "main loop stuck" in open(err, encoding="utf-8", errors="replace").read()
    ok(not stuck, a, "main loop never stuck (light_sync_errors.log)", "a 'main loop stuck' entry: read the log" if stuck else "")
    log = os.path.join(ROOT, "data", "usage.jsonl")  # habits.py: one line a minute while a mode runs
    if os.path.exists(log):
        with open(log, encoding="utf-8") as f:
            lines = f.readlines()
        last = json.loads(lines[-1]).get("t", 0) if lines else 0
        live = s.get("mode") in ("party", "ambient", "gaming")
        ok(not live or time.time() - last < 180, a, "learning: usage log growing", f"{len(lines)} minutes logged, last "
           f"{(time.time() - last) / 60:.0f} min ago", warn=True)
    else:
        report("WARN", a, "learning: usage log growing", "no data/usage.jsonl yet")
    ctl = s.get("control", {})
    ok(ctl and all(v.startswith("Govee LAN") for v in ctl.values()), a, "all 3 bulbs on Govee LAN control",
       ", ".join(f"{k.split('.')[1]}: {v}" for k, v in ctl.items()))
    now = current_bulb_ips()
    used = {e: v.replace("Govee LAN ", "") for e, v in ctl.items() if v.startswith("Govee LAN")}
    stale = [f"{e.split('.')[1]} uses {used[e]}, bulb is at {ip}" for e, ip in now.items() if ip and e in used and used[e] != ip]
    ok(not stale, a, "laptop uses the bulbs' current addresses", "; ".join(stale) or ", ".join(f"{e.split('.')[1]} {ip or 'not answering'}" for e, ip in now.items()))
    ip = lan_ip()
    # by connecting: netstat stopped listing most listeners on this Windows build (Oct 30)
    outside = get(f"http://{ip}:8089/state", timeout=3)
    closed = isinstance(outside, Exception)
    ok(closed and not isinstance(get("http://127.0.0.1:8089/state"), Exception), a, "live view (camera preview) only on 127.0.0.1",
       "" if closed else f"{ip}:8089 answers")
    base = f"http://{ip}:8090"
    for p, want in (("/show", 200), ("/show.json", 200), ("/live.json", 200), ("/tv", 200), ("/tv.json", 200), ("/alive", 200), ("/state", 404), ("/cam.jpg", 404)):
        r = get(base + p)
        ok(code(r) == want, a, f"home network {p} -> {want}", str(code(r)))
    al = get(base + "/alive")
    ok(isinstance(al, requests.Response) and al.json().get("host") == "laptop" and not al.json().get("standby"), a, "laptop /alive says it drives the lights")
    stg = f"http://{ip}:8099"
    r = get(stg + "/install.sh", timeout=2)
    if isinstance(r, requests.Response):
        ok(code(get(stg + "/secrets.yaml", timeout=2)) == 404, a, "Pi file server (8099) does not serve secrets.yaml")
        report("WARN", a, "Pi file server (8099) still running", "only needed while installing; close send_to_pi")
    else:
        report("PASS", a, "Pi file server (8099) not running")


def pi_addon(ws):
    print("\nPi add-on")
    a = "pi"
    info = ws.call(type="supervisor/api", endpoint="/addons/local_light_sync/info", method="get")
    ok(info.get("state") == "started", a, "add-on running", f"v{info.get('version')} · boot {info.get('boot')} · sidebar {info.get('ingress_panel')}")
    ok(info.get("boot") == "auto", a, "starts on boot")
    ok(info.get("ingress_panel") is True, a, "Light Sync app in the sidebar")
    al = get(f"http://{PI}:8090/alive")
    j = al.json() if isinstance(al, requests.Response) and al.ok else {}
    ok(j.get("host") == "pi" and not j.get("standby"), a, "the Pi drives the lights (not on standby)", str(j))
    lj = get(f"http://{PI}:8090/live.json")
    host = lj.json().get("host") if isinstance(lj, requests.Response) and lj.ok else None
    ok(host == "pi", a, "the Pi's own live data", f"host={host}")
    for p, want in (("/show", 200), ("/state", 404), ("/cam.jpg", 404)):
        r = get(f"http://{PI}:8090{p}")
        ok(code(r) == want, a, f"Pi {p} -> {want}", str(code(r)))
    r = get(f"http://{PI}:8097/")
    ok(code(r) == 403, a, "app port refuses direct visits (403)", str(code(r)))
    log = ha_get("/api/hassio/addons/local_light_sync/logs").text
    # a Traceback that ends in KeyboardInterrupt is the add-on being stopped while it started (run.py's stop()), not a crash
    crashes = [t for t in re.split(r"(?=Traceback \(most recent call last\))", log)[1:]
               if not re.search(r"^KeyboardInterrupt\s*$", t, re.M)]
    ok(not crashes, a, "no crashes in the add-on log", f"{len(crashes)} Traceback(s)" if crashes else "")


def home_assistant(ws):
    print("\nHome Assistant")
    a = "ha"
    chk = ha_post("/api/config/core/check_config").json()
    ok(chk.get("result") == "valid", a, "configuration valid", str(chk.get("errors") or ""))
    issues = [i for i in ws.call(type="repairs/list_issues").get("issues", []) if not i.get("ignored")]
    ok(not issues, a, "no repair issues", ", ".join(f"{i['domain']}:{i['issue_id']}" for i in issues), warn=True)
    states = {s["entity_id"]: s for s in ha_get("/api/states").json()}
    autos = {e: s for e, s in states.items() if e.startswith("automation.")}
    broken = [e for e, s in autos.items() if s["state"] not in ("on", "off")]
    ok(not broken, a, f"all {len(autos)} automations loaded", ", ".join(broken))
    off = [s["attributes"].get("friendly_name", e) for e, s in autos.items() if s["state"] == "off"]
    report("INFO", a, "automations switched off", ", ".join(off) or "none")
    need = ["input_boolean.music_sync", "input_boolean.tv_ambient", "input_boolean.gaming_mode", "input_boolean.auto_gaming",
            "input_boolean.game_music", "input_boolean.light_sync_use_screen", "input_boolean.light_sync_use_beat",
            "input_boolean.light_sync_use_wash", "input_select.music_flash", "input_select.music_style", "input_select.music_mic",
            "input_number.music_sensitivity", "input_boolean.wake_up_light", "input_datetime.wake_up_time",
            "sensor.music_sync_status", "sensor.music_sync_bpm", "sensor.music_sync_loudness", "sensor.light_sync",
            "sensor.light_sync_laptop", "sensor.room_light_theme", "script.sunrise_chambre", "script.fade_chambre_lights_off",
            "remote.x96q_pro_plus", "camera.bedroom_camera",
            # memory and learning (tools/ha_setup/learn_setup.py)
            "input_text.light_sync_last_mode", "input_boolean.light_sync_auto_learn", "script.light_sync_resume",
            "automation.smart_home_remember_the_last_mode", "automation.smart_home_resume_when_the_lights_come_back_on"]
    missing = [e for e in need if e not in states]
    ok(not missing, a, f"all {len(need)} project entities exist", ", ".join(missing))
    for e in ("light.chambre_tvd", "light.h6004_2", "light.h6004"):
        st = states.get(e, {}).get("state")
        ok(st in ("on", "off"), a, f"{e} reachable", st)
    ls = states.get("sensor.light_sync", {}).get("state")
    ok(ls not in (None, "offline", "unknown"), a, "Light Sync online on the dashboard", f"{ls} on the {states.get('sensor.music_sync_status', {}).get('attributes', {}).get('host')}")
    # (no laptop heartbeat any more: the laptop only runs the game helper since Nov 6)
    th = ws.call(type="frontend/get_themes")
    names = list(th.get("themes", {}))
    ok(len([n for n in names if n.startswith("Neon")]) == 14, a, "14 Neon themes loaded", f"{len(names)} themes")
    want = states.get("sensor.room_light_theme", {}).get("state")
    # the theme waits until the colour has held 5 min (tools/ha_setup/dashboard.py), so it may be behind for that long
    held = float(template("{{ (now() - states.sensor.room_light_theme.last_changed).total_seconds() | round }}"))
    ok(th.get("default_theme") == want or held < 330, a, "HA theme follows the room lights", f"lights -> {want}, theme -> {th.get('default_theme')}, held {held:.0f} s")
    exposed = ws.call(type="homeassistant/expose_entity/list").get("exposed_entities", {})
    # the house rule, widened by the owner on Oct 27 (the AI voice assistant): lights, media and the Light Sync modes
    modes = {"script.normal_light", "script.party_mode", "script.movie_time", "script.movie_music", "script.gaming_time",
             "script.gaming_music", "script.good_night", "script.all_lights_off"}
    extra = sorted(e for e, v in exposed.items() if v.get("conversation") and e.split(".")[0] not in ("light", "media_player")
                   and e not in modes)
    ok(not extra, a, "Assist sees only lights, media and the Light Sync modes", ", ".join(extra), warn=True)
    banned = [e for e, v in exposed.items() if v.get("conversation") and e.split(".")[0] in ("lock", "camera")]
    # Telegram (telegram_setup.py): text only, one chat, nothing new reaches Rocky by itself
    autos = ha_get("/api/config/automation/config/sh_telegram_chat")
    if autos.ok:
        ok(not ws.call(type="homeassistant/expose_new_entities/get", assistant="conversation").get("expose_new"), a,
           "new devices aren't exposed to Rocky by themselves")
        tg = [x for x in ha_get("/api/config/config_entries/entry?domain=telegram_bot").json()]
        ok(len(tg) == 1 and len(ws.call(type="config_entries/subentries/list", entry_id=tg[0]["entry_id"])) == 1, a,
           "Telegram: one bot, one allowed chat")
        ids = [s["attributes"].get("id") for s in ha_get("/api/states").json() if s["entity_id"].startswith("automation.")]
        sent = ""
        for i in ids:
            r = ha_get(f"/api/config/automation/config/{i}") if i else None
            sent += r.text if r is not None and r.ok else ""
        sent += ha_get("/api/config/script/config/notify_me").text
        # Nov 7: the owner asked for /cam (one picture of the room on request, through Telegram). Only that one is allowed:
        # one send_photo in the commands automation, nothing else sends pictures and nothing saves them
        photos = sent.count("telegram_bot.send_photo")
        bad = [w for w in ("telegram_bot.send_video", "telegram_bot.send_animation", "telegram_bot.send_document", "camera.snapshot",
                           "camera.record") if w in sent] + ([f"send_photo x{photos}"] if photos > 1 else [])
        ok(not bad, a, "camera pictures only by /cam (nothing else sends or saves one)", ", ".join(bad) or f"/cam: {photos}")
    ok(not banned, a, "no locks or cameras exposed to Assist", ", ".join(banned))
    # two cameras since Oct 27: 8081 the TV one (TV colors), 8082 the room one, low (Room guard, Lights follow me)
    for cam, what in (("camera.bedroom_camera", "TV camera (Pi TV colors)"), ("camera.room_camera", "room camera (Pi Room guard)")):
        r = ha_get(f"/api/camera_proxy/{cam}")  # status only; the picture is never looked at
        ok(r.status_code == 200, a, f"HA can read the {what}", f"HTTP {r.status_code}" + ("" if r.ok else " - re-enter the stream password in HA"), warn=True)
    for port, what in ((8081, "TV"), (8082, "room")):
        r = get(f"http://{PI}:{port}/", stream=True)
        c = code(r)
        if isinstance(r, requests.Response):
            r.close()
        ok(c == 401, a, f"{what} camera stream needs a login", f"HTTP {c}")
    box = states.get("input_text.light_sync_room_tv_box", {}).get("state", "")
    ok(box.count(",") == 3, a, "the TV is left out of the room camera's picture", box or "not measured: find_screen.py --room --apply", warn=True)
    backs = ws.call(type="supervisor/api", endpoint="/backups", method="get").get("backups", [])
    if backs:
        newest = max(backs, key=lambda b: b["date"])
        days = (dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(newest["date"])).days
        ok(days <= 8, a, "recent backup", f"newest {newest['date'][:10]} ({newest['name']}), {len(backs)} total", warn=True)
    else:
        report("FAIL", a, "backups exist", "none")
    cfg = ws.call(type="lovelace/config", url_path="smart-home")
    titles = [v.get("title") for v in cfg["views"]]
    ok(all(t in titles for t in ("Smart home", "Remote", "Show", "Settings", "System", "Room camera")), a, "dashboard tabs", " / ".join(titles))
    front = next(v for v in cfg["views"] if v.get("path") == "overview")
    heads = [(s.get("cards") or [{}])[0].get("heading") for s in front.get("sections", [])]
    ok(heads == [None, "Lights", "Settings", "Sleep", "Home"], a, "front page like the app: Lights / Settings / Sleep / Home",
       " / ".join(map(str, heads)))  # tools/ha_setup/dashboard.py
    cards = [c for sec in front.get("sections", []) for c in sec.get("cards", [])]
    app = [c for c in cards if c.get("tap_action", {}).get("navigation_path") == "/app/local_light_sync"]
    ok(app and app[0] is front["sections"][0]["cards"][0], a, "front page opens with the Light Sync app button",
       f"{len(app)} links to the app")
    gone = sorted({c["entity"] for c in cards if c.get("entity") and c["entity"] not in states})
    ok(not gone, a, "front page: every card's entity exists", ", ".join(gone))
    sysv = next((v for v in cfg["views"] if v.get("path") == "system"), {})
    gone = sorted({c["entity"] for sec in sysv.get("sections", []) for c in sec.get("cards", []) if c.get("entity") and c["entity"] not in states})
    ok(not gone, a, "System tab: every card's entity exists", ", ".join(gone))
    stv = next(v for v in cfg["views"] if v.get("path") == "setup")
    sheads = [(s.get("cards") or [{}])[0].get("heading") for s in stv.get("sections", [])]
    order = ["Normal", "Music", "TV", "Gaming", "Lights", "Sound", "Sleep", "For you"]  # the app's tabs + For you
    ok(sheads[:len(order)] == order, a, "Settings tab: " + " / ".join(order), " / ".join(map(str, sheads)))
    ok(not any(v.get("theme") for v in cfg["views"]), a, "dashboard follows HA's theme (no pinned view theme)")
    ok("192.168.1.10:8090/show" in str(cfg), a, "Show tab uses the Pi's fixed address")
    box = states.get("media_player.x96q_pro_plus", {}).get("state")
    ok(box not in (None, "unavailable"), a, "TV box reachable (Remote tab)", box, warn=True)
    for sentence, eid in (("cancel the sunrise", "input_boolean.wake_up_light"), ("tv ambient off", "input_boolean.tv_ambient")):
        if states.get(eid, {}).get("state") == "off":  # only when it changes nothing
            r = ha_post("/api/conversation/process", {"text": sentence, "language": "en"}).json()
            speech = r.get("response", {}).get("speech", {}).get("plain", {}).get("speech", "")
            ok(r.get("response", {}).get("response_type") == "action_done", a, f"voice: \"{sentence}\"", speech)
            ok(state(eid)["state"] == "off", a, f"  ... {eid} still off")
        else:
            report("INFO", a, f"voice: \"{sentence}\" skipped", f"{eid} is on, the test would change it")
    quick = ["script.normal_light", "script.party_mode", "script.movie_time", "script.movie_music", "script.gaming_time",
             "script.gaming_music", "script.good_night", "script.all_lights_off"]
    missing = [q for q in quick if q not in states]
    ok(not missing, a, "phone quick buttons exist (8 scripts)", ", ".join(missing))
    qrow = [c for c in cards if c.get("type") == "button" and c.get("tap_action", {}).get("action") == "perform-action"]
    lit = [c["entity"] for c in qrow if not c.get("entity", "").startswith("binary_sensor.light_sync_mode_")
           or states.get(c["entity"], {}).get("state") not in ("on", "off")]
    ok(len(qrow) == 8 and not lit, a, "Quick buttons light up for their mode (8, combos too)", ", ".join(lit) or f"{len(qrow)} buttons")
    on_now = [c["name"] for c in qrow if states.get(c.get("entity"), {}).get("state") == "on"]
    ok(len(on_now) <= 1, a, "at most one Quick button lit", ", ".join(on_now) or "none")
    res = [r["url"] for r in ws.call(type="lovelace/resources") if "card-mod" in r["url"]]
    ok(len(res) == 1 and requests.get(HA + res[0], timeout=10).ok, a, "card-mod loaded (the Quick buttons glow)",
       ", ".join(res) or "not installed", warn=True)
    normal = [e for e in ("input_boolean.normal_mode", "automation.smart_home_normal_light_on",
                          "automation.smart_home_normal_light_follows_the_time_of_day", "automation.smart_home_normal_light_ends")
              if states.get(e, {}).get("state") not in ("on", "off") or (e.startswith("automation") and states[e]["state"] != "on")]
    ok(not normal, a, "Normal mode: helper and 3 automations on", ", ".join(normal))
    away = [e for e in ("automation.smart_home_away_lights_off_when_i_leave", "automation.smart_home_welcome_home_after_dark")
            if states.get(e, {}).get("state") != "on"]
    ok(not away, a, "home / away automations on", ", ".join(away))
    # the phone's location arriving (Oct 27: the owner turned the phone's other sensors off, the location permission
    # one too, so the location's own age is what tells)
    perm = states.get("sensor.rocky_location_permission", {}).get("state")
    who = states.get("person.ezer", {}).get("state")
    seen = states.get("device_tracker.rocky", {}).get("last_updated")
    age = (dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(seen)).total_seconds() / 60 if seen else None
    ok(who in ("home", "not_home") and age is not None and age < 120 and perm in (None, "Authorized Always"), a,
       "phone tells HA if you're home (home / away)",
       f"person.ezer={who}, last location {age:.0f} min ago" + (f", location permission: {perm}" if perm else "")
       if age is not None else f"person.ezer={who}, no location yet", warn=True)
    r = subprocess.run([sys.executable, os.path.join(ROOT, "light_sync", "daily_summary.py"), "--dry-run"], capture_output=True, text=True, cwd=ROOT, timeout=120)
    ok(r.returncode == 0, a, "daily summary dry run (nothing sent, nothing posted)", (r.stderr or "").strip().splitlines()[-1] if r.returncode else "")


def features(ws):
    """The features end to end: the app through HA's ingress (as the phone uses it), the phone-mic path, voice
    (speech -> text -> command), the TV box remote and apps automation, the camera, Tailscale."""
    print("\nFeatures end to end")
    a = "features"
    st = {s["entity_id"]: s for s in ha_get("/api/states").json()}

    # --- the Light Sync app, through Home Assistant's ingress (signed-in route, like the phone)
    info = ws.call(type="supervisor/api", endpoint="/addons/local_light_sync/info", method="get")
    sess = ws.call(type="supervisor/api", endpoint="/ingress/session", method="post")["session"]
    base = HA + info["ingress_url"].rstrip("/") + "/"
    jar = {"ingress_session": sess}
    r = requests.get(base, cookies=jar, timeout=10)
    ok(r.ok and "Listen with this phone" in r.text, a, "app opens through Home Assistant", f"HTTP {r.status_code}")
    r = requests.get(base + "state", cookies=jar, timeout=10)
    ok(r.ok and len(r.json().get("entities", {})) >= 15, a, "app reads the switches", f"{len(r.json().get('entities', {})) if r.ok else r.status_code} entities")
    r = requests.get(base + "live", cookies=jar, timeout=10)
    host = r.json().get("host") if r.ok else None
    ok(host in ("laptop", "pi"), a, "app shows live lights", f"driven by the {host}")
    style = st["input_select.music_style"]["state"]
    r = requests.post(base + "set", cookies=jar, json={"entity_id": "input_select.music_style", "value": style}, timeout=10)
    ok(r.ok, a, "app can change a Light Sync setting", f"style set to its current value ({style}): HTTP {r.status_code}")
    r = requests.post(base + "set", cookies=jar, json={"entity_id": "lock.front_door", "value": True}, timeout=10)
    ok(r.status_code == 403, a, "app refuses anything else (a lock)", f"HTTP {r.status_code}")
    r = requests.get(base + "cams", cookies=jar, timeout=15)  # the cameras' pan / tilt / zoom (Oct 27; controls only)
    cams = r.json().get("cams", []) if r.ok else []
    ok(sorted(c.get("which") or "?" for c in cams) == ["room", "tv"], a, "app controls both cameras",
       "; ".join(f"{c['label']}: {', '.join(c['move'])} + {len(c['picture'])} picture settings" for c in cams) or f"HTTP {r.status_code}")
    r = requests.get(base + "check", cookies=jar, timeout=60)  # the app's System check (read only)
    chk = r.json().get("checks", []) if r.ok else []
    failed = [c["name"] for c in chk if c["status"] == "fail"]
    ok(chk and not failed, a, "app's System check runs, nothing failing", ", ".join(failed) or f"{len(chk)} checks")
    away = [c["label"] for c in cams if not c.get("at_home")]
    ok(not away, a, "  ... both at Home (where the TV colours and Room guard were set up)",
       ", ".join(away) + " moved: tap ⌂ in the app" if away else "", warn=True)

    # --- phone mic path: a fake 124 BPM phone through HA -> Pi app -> the machine driving the lights
    import struct
    import numpy as np
    music_was = st["input_boolean.music_sync"]["state"]
    if music_was != "on":  # like the real Listen button: Music sync on while listening
        ha_post("/api/services/input_boolean/turn_on", {"entity_id": "input_boolean.music_sync"})
        time.sleep(3)
    offs = []
    for _ in range(4):
        t1 = time.time()
        j = requests.post(base + "phone", cookies=jar, data=bytes(8), timeout=5).json()
        t2 = time.time()
        offs.append((t2 - t1, j.get("t", t2) - (t1 + t2) / 2))
    offset = min(offs)[1]
    rate, block, period = 22050, 512, 60 / 124
    chunk = (rate // 10) // block * block
    rng = np.random.default_rng(3)
    start, sent = time.time(), 0
    while time.time() - start < 7:
        tt = sent / rate + np.arange(chunk) / rate
        since = np.mod(tt, period)
        x = 0.6 * np.sin(2 * np.pi * 60 * since) * np.exp(-since * 18) + 0.02 * rng.standard_normal(chunk)
        body = struct.pack("<d", start + sent / rate + offset) + (np.clip(x, -1, 1) * 32767).astype("<i2").tobytes()
        requests.post(base + "phone", cookies=jar, data=body, timeout=5)
        sent += chunk
        time.sleep(max(0, start + sent / rate - time.time()))
    driver = "http://127.0.0.1:8089/state" if host == "laptop" else f"http://{PI}:8090/live.json"
    phone_label = "phone mic (Light Sync app)"
    s = get(driver).json()
    ok(s.get("audio") == phone_label, a, "phone sound reaches the lights", f"audio source: {s.get('audio')}")
    ok(115 <= (s.get("bpm") or 0) <= 133, a, "lights lock onto the phone's beat", f"{s.get('bpm')} BPM (fake track 124)")
    time.sleep(2.5)
    s = get(driver).json()
    ok(s.get("audio") != phone_label, a, "back to the normal mic when the phone stops", str(s.get("audio")))
    if music_was != "on":
        ha_post("/api/services/input_boolean/turn_off", {"entity_id": "input_boolean.music_sync"})

    # --- voice: HA's own voice says a command, Whisper turns it into text, the command runs
    if st["input_boolean.wake_up_light"]["state"] == "off":
        sentence, expect = "cancel the sunrise", "sunrise"
    else:
        sentence, expect = "what time is it", "time"
    r = ha_post("/api/tts_get_url", {"engine_id": "tts.piper", "message": sentence,
                                     "options": {"preferred_format": "wav", "preferred_sample_rate": 16000, "preferred_sample_channels": 1}})
    wav = requests.get(r.json()["url"], timeout=20).content if r.ok else b""
    import io
    import wave
    try:
        with wave.open(io.BytesIO(wav)) as w:
            pcm, sr, ch = w.readframes(w.getnframes()), w.getframerate(), w.getnchannels()
        ok(sr == 16000 and ch == 1, a, "voice: HA speaks the test sentence", f"\"{sentence}\", {len(pcm) // 32000:.0f} s")
    except Exception as e:
        report("FAIL", a, "voice: HA speaks the test sentence", str(e))
        pcm = b""
    if pcm:
        pl = ws.call(type="assist_pipeline/pipeline/list")
        rid = ws.next_id
        ws.next_id += 1
        ws.send({"id": rid, "type": "assist_pipeline/run", "start_stage": "stt", "end_stage": "intent",
                 "input": {"sample_rate": 16000}, "pipeline": pl["preferred_pipeline"]})
        heard, said, err, end = "", "", "", time.time() + 40
        while time.time() < end:
            m = ws.recv()
            if m.get("id") != rid:
                continue
            ev = m.get("event") or {}
            if ev.get("type") == "run-start":
                h = bytes([ev["data"]["runner_data"]["stt_binary_handler_id"]])
                for i in range(0, len(pcm), 3200):  # 100 ms frames, then silence, then "end of audio"
                    ws.send_bytes(h + pcm[i:i + 3200])
                for _ in range(15):
                    ws.send_bytes(h + bytes(3200))
                ws.send_bytes(h)
            elif ev.get("type") == "stt-end":
                heard = ev["data"]["stt_output"]["text"]
            elif ev.get("type") == "intent-end":
                said = ev["data"]["intent_output"]["response"]["speech"].get("plain", {}).get("speech", "")
            elif ev.get("type") == "error":
                err = f"{ev['data'].get('code')}: {ev['data'].get('message')}"
                break
            elif ev.get("type") == "run-end":
                break
        ok(expect in heard.lower(), a, "voice: Whisper understood it", f"heard \"{heard}\"" + (f" ({err})" if err else ""))
        ok(bool(said), a, "voice: the command ran", f"answer \"{said}\"")
    pl = ws.call(type="assist_pipeline/pipeline/list")
    pref = next(p for p in pl["pipelines"] if p["id"] == pl["preferred_pipeline"])
    ok(bool(pref.get("wake_word_id")), a, "voice: wake word set", f"{pref.get('wake_word_id')} on '{pref.get('name')}'")
    words = [w["id"] for w in ws.call(type="wake_word/info", entity_id=pref.get("wake_word_entity")).get("wake_words", [])]
    ok(pref.get("wake_word_id") in words, a, "voice: openWakeWord has that word",
       "" if pref.get("wake_word_id") in words else f"missing: is it still in /share/openwakeword? ({', '.join(words)})")
    def mic_waiting():  # any run still open at the wake word: the last run can be this test's own typed question
        runs = ws.call(type="assist_pipeline/pipeline_debug/list", pipeline_id=pref["id"])["pipeline_runs"]
        for run in runs[-6:]:
            ev = ws.call(type="assist_pipeline/pipeline_debug/get", pipeline_id=pref["id"],
                         pipeline_run_id=run["pipeline_run_id"])["events"]
            if any(e["type"] == "wake_word-start" for e in ev) and not any(e["type"] == "run-end" for e in ev):
                return True
        return False
    ended = not mic_waiting()
    for _ in range(3):  # between two runs for a moment (after a command): look again
        if not ended:
            break
        time.sleep(3)
        ended = not mic_waiting()
    ok(not ended, a, "voice: the MeetUp is waiting for the wake word",
       "" if not ended else "no open run: restart Assist Microphone (a pipeline change leaves it deaf)")
    mic = ws.call(type="supervisor/api", endpoint="/addons/core_assist_microphone/info", method="get")["options"]
    ok(not mic.get("sound_enabled"), a, "voice: Rocky answers with the lights (MeetUp speaker off)",
       "" if not mic.get("sound_enabled") else "the speaker is on: Assist Microphone's sound_enabled", warn=True)
    log = ha_get("/api/hassio/addons/core_assist_microphone/logs").text.splitlines()[-30:]
    bad = [l for l in log if "provider-missing" in l or "Traceback" in l]
    ok("Streaming audio" in "\n".join(log) and not bad, a, "voice: MeetUp mic streaming to HA without errors", bad[-1][:80] if bad else "")

    # --- TV box: remote reachable, the apps automation agrees with what's open
    box = st.get("media_player.x96q_pro_plus", {})
    ok(st.get("remote.x96q_pro_plus", {}).get("state") not in (None, "unavailable"), a, "Remote tab: TV box reachable",
       box.get("state") + (" (switched off or unplugged?)" if box.get("state") == "unavailable" else ""), warn=True)
    apps = ["com.google.android.youtube.tv", "com.amazon.amazonvideo.livingroom", "com.iptvav.av_iptv", "com.softwinner.miracastReceiver"]
    app = box.get("attributes", {}).get("app_id")
    if box.get("state") == "on" and app in apps:
        ok(st["input_boolean.music_sync"]["state"] == "on", a, "media app open -> Music sync on", app)
    else:
        report("INFO", a, "media-apps automation", f"box {box.get('state')}, app {app}: nothing to compare")

    # --- camera on the laptop, Tailscale
    s = get("http://127.0.0.1:8089/state")
    sj = s.json() if isinstance(s, requests.Response) and s.ok else {}
    cam = sj.get("camera")
    if not sj:
        report("WARN", a, "camera follows the screen (laptop)", "laptop Light Sync not running")
    elif sj.get("mode") == "paused" or not sj.get("running", True):
        report("INFO", a, "camera follows the screen (laptop)", "Light Sync paused: camera not read")
    elif str(cam).startswith("idle"):
        report("INFO", a, "camera follows the screen (laptop)", f"{sj.get('mode')} mode: camera only read in TV mode and gaming")
    else:
        ok(cam == "following screen", a, "camera follows the screen (laptop)", str(cam), warn=cam == "connected, screen dark/gray")
    box_val = st.get("input_text.light_sync_tv_box", {}).get("state")
    ok(bool(box_val and box_val.count(",") in (3, 7)), a, "screen area saved in Home Assistant", str(box_val))  # a box, or four corners
    ts = ws.call(type="supervisor/api", endpoint="/addons/a0d7b954_tailscale/info", method="get")
    ok(ts.get("state") == "started", a, "Tailscale running (remote access)", ts.get("state"))
    ok(ts.get("boot") == "auto", a, "Tailscale starts after a Pi restart", f"boot {ts.get('boot')}", warn=True)


def handover(ws):
    print("\nHandover (real)")
    a = "handover"
    uses = ["input_boolean.light_sync_use_screen", "input_boolean.light_sync_use_beat", "input_boolean.light_sync_use_wash"]
    keep = ["input_boolean.music_sync", "input_boolean.tv_ambient", "input_boolean.gaming_mode", "input_boolean.auto_gaming"] + uses
    before = {e: state(e)["state"] for e in keep}
    report("INFO", a, "switches before", ", ".join(f"{e.split('.')[1]}={v}" for e, v in before.items()))

    def switch(e, on):
        ha_post(f"/api/services/input_boolean/turn_{'on' if on else 'off'}", {"entity_id": e})

    def alive():
        r = get(f"http://{PI}:8090/alive", timeout=2)
        return r.json() if isinstance(r, requests.Response) and r.ok else {}

    def light_sync_pids():
        out = subprocess.run(["powershell", "-NoProfile", "-Command",
                              "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { $_.CommandLine -match 'light_sync\\.py' } | "
                              "ForEach-Object { $_.ProcessId; $_.ParentProcessId }"], capture_output=True, text=True).stdout.split()
        return out

    try:
        switch("input_boolean.auto_gaming", False)  # so a running game doesn't turn Gaming back on mid-test
        switch("input_boolean.gaming_mode", False)
        for e in uses:  # all lights in the show for the test (put back afterwards)
            switch(e, True)
        switch("input_boolean.music_sync", True)
        time.sleep(4)
        for pid in light_sync_pids():  # the python process and its start_lights.bat window
            subprocess.run(["taskkill", "/F", "/PID", pid], capture_output=True)
        t0 = time.time()
        took = None
        while time.time() - t0 < 30:
            if alive().get("standby") is False:
                took = time.time() - t0
                break
            time.sleep(0.5)
        ok(took is not None and took < 15, a, "laptop silent -> Pi takes over", f"{took:.1f} s" if took else "not within 30 s")
        if took:
            time.sleep(6)
            st = state("sensor.music_sync_status")
            ok(st["attributes"].get("host") == "pi", a, "dashboard says the Pi drives", f"{st['state']} on the {st['attributes'].get('host')}")
            report("INFO", a, "Pi audio", str(st["attributes"].get("audio")))
            levels, sent = [], []
            for _ in range(12):
                lj = get(f"http://{PI}:8090/live.json").json()
                levels.append(lj.get("energy", 0))
                sent.append(max((l.get("bri", 0) for l in lj.get("lights", {}).values()), default=0))
                time.sleep(0.5)
            ok(max(sent) > 0, a, "Pi is sending colors to the bulbs", f"brightest {max(sent)}/255")
            switch("input_boolean.tv_ambient", True)  # the camera is only read in TV mode (put back afterwards)
            seen = ("following screen", "connected, screen dark/gray", "connected, screen off")
            for _ in range(20):
                time.sleep(1)
                cam = get(f"http://{PI}:8090/live.json").json().get("camera")
                if cam in seen:
                    break
            ok(cam in seen, a, "Pi reads the camera in TV mode (through HA)", str(cam))
            ok(max(levels) > 0.05, a, "MeetUp mic hears the room", f"loudness peak {max(levels):.2f}" + ("" if max(levels) > 0.05 else " - quiet room?"), warn=True)
            log = ha_get("/api/hassio/addons/local_light_sync/logs").text
            lan = dict(re.findall(r"(light\.\S+): Govee LAN (\S+)", log)[-3:])
            ok(len(lan) == 3, a, "Pi found all 3 bulbs on the network", ", ".join(f"{e.split('.')[1]} {ip}" for e, ip in lan.items()))
            now = current_bulb_ips()
            stale = [f"{e.split('.')[1]} {lan.get(e)} (bulb at {ip})" for e, ip in now.items() if ip and lan.get(e) and lan[e] != ip]
            ok(not stale, a, "Pi uses the bulbs' current addresses", "; ".join(stale))
    finally:
        # through Windows' own shell (the login shortcut): independent of this test and of whatever started it
        lnk = os.path.join(os.environ["APPDATA"], r"Microsoft\Windows\Start Menu\Programs\Startup\Light Sync.lnk")
        subprocess.run(["explorer.exe", lnk])
        t1 = time.time()
        back = None
        while time.time() - t1 < 45:
            if alive().get("standby") is True:
                back = time.time() - t1
                break
            time.sleep(1)
        ok(back is not None, a, "laptop back -> Pi hands over", f"{back:.1f} s (laptop startup included)" if back else "not within 45 s")
        for e, v in before.items():  # modes first, the per-light switches last
            switch(e, v == "on")
        time.sleep(4)
        now = {e: state(e)["state"] for e in keep}
        ok(now == before, a, "switches put back as they were", ", ".join(f"{e.split('.')[1]}={v}" for e, v in now.items()))


def main():
    print(f"Light Sync self-test · {dt.datetime.now():%Y-%m-%d %H:%M}")
    ws = ws_session()
    if "--only-handover" not in sys.argv:
        repo()
        laptop()
        pi_addon(ws)
        home_assistant(ws)
    was_normal = state("sensor.light_sync")["state"] == "normal"
    if "--all" in sys.argv or "--features" in sys.argv:
        features(ws)
    if "--all" in sys.argv or "--handover" in sys.argv or "--only-handover" in sys.argv:
        handover(ws)
    if was_normal and state("sensor.light_sync")["state"] != "normal":  # the feature tests end with the modes off
        ha_post("/api/services/script/normal_light", {})
    n = {k: sum(1 for l, _ in RESULTS if l == k) for k in ("PASS", "WARN", "FAIL")}
    print(f"\n{n['PASS']} passed · {n['WARN']} warnings · {n['FAIL']} failed")
    sys.exit(1 if n["FAIL"] else 0)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
