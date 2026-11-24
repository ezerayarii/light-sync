#!/usr/bin/env python3
"""
export_ha.py - save the Home Assistant side of this project into home_assistant/ (for the docs and for git history).

Exports (read-only, nothing is changed in HA):
  ha/automations.json   every automation's config
  ha/scripts.json       every script's config
  ha/helpers.json       input_boolean / input_select / input_number / input_datetime / input_text helpers
  ha/dashboard_smart_home.json   the "Smart home" dashboard
  ha/assist_exposed.json         which entities Assist (and any AI agent) can see

Run:  py tools/export_ha.py
Uses ha_token from secrets.yaml and HA_URL (default http://192.168.1.10:8123). No extra packages.
"""
import base64
import json
import os
import re
import socket
import sys
from urllib.parse import urlparse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "home_assistant")
DASHBOARD = "Smart home"  # dashboard title
HELPERS = ("input_boolean", "input_select", "input_number", "input_datetime", "input_text")
SENSITIVE = ("password", "token", "api_key", "apikey", "secret")


def token():
    with open(os.path.join(ROOT, "secrets.yaml"), encoding="utf-8-sig") as f:
        for line in f:
            key, sep, value = line.partition(":")
            if sep and key.strip() == "ha_token":
                return value.strip().strip('"').strip("'")
    sys.exit("ha_token not found in secrets.yaml")


class WebSocket:
    """Just enough of a WebSocket client (RFC 6455) for Home Assistant's API, standard library only."""

    def __init__(self, url):
        u = urlparse(url)
        self.sock = socket.create_connection((u.hostname, u.port or 80), timeout=20)
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall((f"GET /api/websocket HTTP/1.1\r\nHost: {u.hostname}:{u.port or 80}\r\n"
                           f"Upgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n"
                           "Sec-WebSocket-Version: 13\r\n\r\n").encode())
        self.buf = b""
        while b"\r\n\r\n" not in self.buf:
            self.buf += self.sock.recv(4096)
        head, self.buf = self.buf.split(b"\r\n\r\n", 1)
        if b" 101 " not in head.split(b"\r\n")[0]:
            sys.exit(f"WebSocket refused: {head.splitlines()[0].decode()}")
        self.next_id = 1

    def _read(self, n):
        while len(self.buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise ConnectionError("connection closed")
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def _send_frame(self, opcode, data):
        n = len(data)
        head = bytearray([0x80 | opcode])
        if n < 126:
            head.append(0x80 | n)
        elif n < 65536:
            head.append(0x80 | 126)
            head += n.to_bytes(2, "big")
        else:
            head.append(0x80 | 127)
            head += n.to_bytes(8, "big")
        mask = os.urandom(4)
        self.sock.sendall(bytes(head) + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    def send(self, obj):
        self._send_frame(0x1, json.dumps(obj).encode())

    def send_bytes(self, data):
        self._send_frame(0x2, data)

    def recv(self):
        msg = b""
        while True:
            b1, b2 = self._read(2)
            n = b2 & 0x7F
            if n == 126:
                n = int.from_bytes(self._read(2), "big")
            elif n == 127:
                n = int.from_bytes(self._read(8), "big")
            mask = self._read(4) if b2 & 0x80 else None
            payload = self._read(n)
            if mask:
                payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
            op = b1 & 0x0F
            if op == 0x9:  # ping
                self._send_frame(0xA, payload)
                continue
            if op == 0x8:
                raise ConnectionError("server closed the connection")
            msg += payload
            if b1 & 0x80:
                return json.loads(msg)

    def call(self, **cmd):
        cmd["id"] = self.next_id
        self.next_id += 1
        self.send(cmd)
        while True:
            m = self.recv()
            if m.get("id") == cmd["id"] and m.get("type") == "result":
                if not m.get("success"):
                    raise RuntimeError(f"{cmd['type']}: {m.get('error')}")
                return m["result"]


def check(name, data):
    """Warn (don't write) if something that looks like a secret ended up in an export."""
    text = json.dumps(data).lower()
    hits = [w for w in SENSITIVE if f'"{w}' in text or f'_{w}"' in text]
    if hits:
        print(f"  ! {name}: contains keys like {hits} - review before committing")


REDACT = ("webhook_id", "chat_id")  # the webhook's works like a password, the Telegram one is private: keep them in HA only
USER_ID = re.compile(r"(user_id == )\d+")  # the Telegram automations' check of who wrote


def redact(data):
    if isinstance(data, dict):
        return {k: "<redacted - see Home Assistant>" if k in REDACT else redact(v) for k, v in data.items()}
    if isinstance(data, list):
        return [redact(v) for v in data]
    if isinstance(data, str):
        return USER_ID.sub(r"\1<redacted - see Home Assistant>", data)
    return data


def save(name, data):
    data = redact(data)
    check(name, data)
    with open(os.path.join(OUT, name), "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")
    print(f"  {name}")


def main():
    url = os.getenv("HA_URL", "http://192.168.1.10:8123")
    ws = WebSocket(url)
    ws.recv()  # auth_required
    ws.send({"type": "auth", "access_token": token()})
    if ws.recv().get("type") != "auth_ok":
        sys.exit("HA login failed - check ha_token in secrets.yaml")
    os.makedirs(OUT, exist_ok=True)
    print(f"Exporting from {url} into home_assistant/")

    states = ws.call(type="get_states")
    ids = sorted(s["entity_id"] for s in states)

    autos = []
    for eid in (e for e in ids if e.startswith("automation.")):
        cfg = ws.call(type="automation/config", entity_id=eid).get("config", {})
        autos.append({"entity_id": eid, **cfg})
    save("automations.json", autos)

    scripts = []
    for eid in (e for e in ids if e.startswith("script.")):
        cfg = ws.call(type="script/config", entity_id=eid).get("config", {})
        scripts.append({"entity_id": eid, **cfg})
    save("scripts.json", scripts)

    save("helpers.json", {d: ws.call(type=f"{d}/list") for d in HELPERS})

    boards = {b.get("title"): b["url_path"] for b in ws.call(type="lovelace/dashboards/list")}
    if DASHBOARD in boards:
        save("dashboard_smart_home.json", ws.call(type="lovelace/config", url_path=boards[DASHBOARD]))
    else:
        print(f"  ! no dashboard titled '{DASHBOARD}' (found: {', '.join(map(str, boards))})")

    exposed = ws.call(type="homeassistant/expose_entity/list").get("exposed_entities", {})
    save("assist_exposed.json", sorted(e for e, a in exposed.items() if a.get("conversation")))


if __name__ == "__main__":
    main()
