#!/usr/bin/env python3
"""
ha_ws.py - send Home Assistant WebSocket commands from the command line (token from secrets.yaml, never printed).

  py tools/ha_ws.py '{"type": "supervisor/api", "endpoint": "/addons", "method": "get"}' [more commands...]
  ... | py tools/ha_ws.py      (one JSON command per line on stdin; easier from PowerShell, which eats quotes)

Prints each result as JSON, one per line. Long Supervisor jobs (add-on install/rebuild) can take minutes: the wait is 15 min.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import export_ha  # noqa: E402  (the small WebSocket client and token reader)


def main():
    ws = export_ha.WebSocket(os.getenv("HA_URL", "http://192.168.1.10:8123"))
    ws.sock.settimeout(900)
    ws.recv()
    ws.send({"type": "auth", "access_token": export_ha.token()})
    if ws.recv().get("type") != "auth_ok":
        sys.exit("HA login failed")
    if sys.argv[1:]:
        lines = sys.argv[1:]
    else:  # PowerShell may prefix a byte-order mark
        lines = sys.stdin.buffer.read().decode("utf-8-sig", errors="replace").replace("﻿", "").splitlines()
    for arg in (line.strip().lstrip("ï»¿?") for line in lines):
        if not arg:
            continue
        cmd = json.loads(arg)
        try:
            print(json.dumps(ws.call(**cmd), ensure_ascii=False))
        except RuntimeError as e:
            print(json.dumps({"error": str(e)}))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
