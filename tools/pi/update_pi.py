#!/usr/bin/env python3
"""
update_pi.py - updates the Light Sync add-on on the Pi with no paste (Nov 7): serves the files like send_to_pi.py,
tells the Pi to fetch them (POST /update on its show server: only accepted from this laptop's address), then has
Home Assistant reload the add-on store and rebuild the add-on, and checks it came back.

  py tools/pi/update_pi.py            (themes still go by the old paste: they live in HA's config folder)

Replaces the Oct 20 version, which ran the paste in the Terminal add-on through ingress: that terminal later couldn't
reach the add-ons folder, so the paste moved to Studio Code Server by hand. Needs the add-on's map addons:rw (Nov 7).
"""
import hashlib
import os
import sys
import threading
import time
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))  # tools\ (export_ha)
import export_ha as ex  # noqa: E402
import send_to_pi  # noqa: E402

HA = os.getenv("HA_URL", "http://192.168.1.10:8123")
PI = "http://192.168.1.10:8090"
A = "/addons/local_light_sync"
ROOT = os.path.dirname(os.path.dirname(HERE))


class Quiet(SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


def conn():
    ws = ex.WebSocket(HA)
    ws.sock.settimeout(240)
    ws.recv()
    ws.send({"type": "auth", "access_token": ex.token()})
    assert ws.recv().get("type") == "auth_ok", "HA login failed"
    return ws


def main():
    out, src = send_to_pi.stage()
    server = ThreadingHTTPServer(("0.0.0.0", send_to_pi.PORT), partial(Quiet, directory=out))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        r = requests.post(PI + "/update", json={"src": src}, timeout=120)
        print("Pi fetched the files:", r.status_code, r.text[:200])
        if r.status_code != 200:
            sys.exit("The Pi couldn't update itself - use the paste (send_to_pi.py) this time.")
    finally:
        server.shutdown()
    ws = conn()
    for ep in ("/store/reload", A + "/rebuild"):  # the store reload reads a changed config.yaml
        try:
            ws.call(type="supervisor/api", endpoint=ep, method="post")
        except Exception as e:
            print(f"{ep}: {str(e)[:80]}")
    info = {}
    for _ in range(50):
        time.sleep(8)
        try:
            info = conn().call(type="supervisor/api", endpoint=A + "/info", method="get")
            if info.get("state") == "started":
                break
        except Exception:
            pass
    print("add-on", info.get("state"), info.get("version"))
    time.sleep(25)
    H = {"Authorization": "Bearer " + ex.token()}
    log = requests.get(HA + "/api/hassio/addons/local_light_sync/logs", headers=H, timeout=30).text.splitlines()
    bad = [line[:160] for line in log if "Traceback" in line or "Error" in line]
    print(f"log: {len(bad)} error lines" + ("".join("\n  " + b for b in bad[-5:]) if bad else ""))
    ws = conn()
    info = ws.call(type="supervisor/api", endpoint=A + "/info", method="get")
    sess = ws.call(type="supervisor/api", endpoint="/ingress/session", method="post")
    base = HA + info["ingress_url"]
    page = requests.get(base, cookies={"ingress_session": sess["session"]}, timeout=20)
    same = hashlib.sha256(page.content).hexdigest() == hashlib.sha256(open(os.path.join(ROOT, "light_sync", "web", "app.html"), "rb").read()).hexdigest()
    print("app page", page.status_code, "same as the laptop's:", same)
    live = requests.get(PI + "/live.json", timeout=10).json()
    print("Light Sync:", live.get("mode"), "on", live.get("host"))


if __name__ == "__main__":
    main()
