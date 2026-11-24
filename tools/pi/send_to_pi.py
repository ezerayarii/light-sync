#!/usr/bin/env python3
"""
send_to_pi.py - lets the Pi download the Light Sync add-on and the Neon Club theme from this laptop.

Copies ONLY the files the Pi needs into a temporary folder (never secrets.yaml), serves that folder on
port 8099 and prints the one line to paste in Home Assistant's Terminal. Close the window (Ctrl+C) when done.
"""
import os
import shutil
import socket
import sys
import tempfile
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PORT = 8099
ADDON = ["config.yaml", "Dockerfile", "run.py", "app.py", "cameras.py", "checks.py", "updater.py"]  # from pi_addon/light_sync
WEB = ["live.html", "show.html", "app.html", "tv.html"]                # from light_sync/web/

INSTALL = """#!/bin/sh
# Light Sync: copies the add-on and the Neon Club theme from the laptop to this Pi (run in HA's Terminal).
set -e
SRC="{src}"
# the local apps folder the Supervisor reads: /apps on newer systems, /addons on older ones (must be a mount)
echo "Mounts (folder <- where it really is):"
awk '$5=="/apps"||$5=="/addons"||$5=="/addon_configs"{{print "  " $5 " <- " $4}}' /proc/self/mountinfo
A=""
for d in /apps /addons; do
  if grep -q " $d " /proc/mounts; then A="$d/light_sync"; break; fi
done
if [ -z "$A" ]; then
  echo "This terminal ($(hostname)) can't reach the add-ons folder."
  echo "Run the same line in Studio Code Server's terminal instead (sidebar: Studio Code Server, then menu > Terminal > New Terminal)."
  echo "Mounts seen:"; grep -E ' /(apps|addons|addon_configs|homeassistant|config|share) ' /proc/mounts || true
  exit 1
fi
echo "Local apps folder: ${{A%/light_sync}}"
mkdir -p "$A/web" "$A/parts"
for f in {addon} light_sync.py; do curl -fsS -o "$A/$f" "$SRC/light_sync/$f"; done
for f in {parts}; do curl -fsS -o "$A/parts/$f" "$SRC/light_sync/parts/$f"; done
# parts that are gone on the laptop go here too (Nov 6: effects_engine.py, vibes.py), and the pre-Oct-13 copy
for p in "$A"/parts/*.py; do case " {parts} " in *" $(basename "$p") "*) ;; *) echo "removed old $(basename "$p")"; rm -f "$p";; esac; done
rm -f "$A/music_sync.py"
for f in {web}; do curl -fsS -o "$A/web/$f" "$SRC/light_sync/web/$f"; done
C=/config; [ -f "$C/configuration.yaml" ] || C=/homeassistant
mkdir -p "$C/themes"
for f in {themes}; do curl -fsS -o "$C/themes/$f" "$SRC/themes/$f"; done
echo "Add-on files:"; ls -l "$A" "$A/web"
echo "Themes: $C/themes/ ({themes})"
if grep -q 'include_dir_merge_named themes' "$C/configuration.yaml"; then
  echo "configuration.yaml loads the themes folder: OK"
else
  echo "NOTE: configuration.yaml does not load the themes folder yet (see docs/home-assistant.md)"
fi
# opt-in ("| sh -s -- https"): for HA before 2026.8 only. Since then this is a UI setting and these lines are
# ignored: Settings > System > Network > HTTP server > Reverse proxy (docs/pi-addon.md). Never touches an existing http: section.
if [ "$1" = "https" ]; then
  CFG="$C/configuration.yaml"
  if grep -qE '^http:' "$CFG"; then
    echo "configuration.yaml already has an http: section (not changed):"; sed -n '/^http:/,/^[^ #]/p' "$CFG" | head -n 12
  else
    cp "$CFG" "$CFG.bak-lightsync-$(date +%Y%m%d-%H%M)"
    printf '\n# Tailscale HTTPS (share_homeassistant: serve) reaches Home Assistant through this local proxy\nhttp:\n  use_x_forwarded_for: true\n  trusted_proxies:\n    - 127.0.0.1\n' >> "$CFG"
    echo "configuration.yaml: added the http: section for Tailscale HTTPS (backup: $(basename "$CFG").bak-lightsync-*)"
  fi
fi
echo "Done - tell Claude, or install/rebuild Light Sync (Pi) under Settings > Add-ons."
"""


def lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("192.168.1.10", 80))  # the Pi; UDP connect sends nothing
        return s.getsockname()[0]
    finally:
        s.close()


def stage():
    out = os.path.join(tempfile.gettempdir(), "lightsync_to_pi")
    shutil.rmtree(out, ignore_errors=True)
    os.makedirs(os.path.join(out, "light_sync", "web"))
    os.makedirs(os.path.join(out, "light_sync", "parts"))
    os.makedirs(os.path.join(out, "themes"))
    for f in ADDON:
        shutil.copy(os.path.join(ROOT, "pi_addon", "light_sync", f), os.path.join(out, "light_sync", f))
    shutil.copy(os.path.join(ROOT, "light_sync", "light_sync.py"), os.path.join(out, "light_sync", "light_sync.py"))
    parts = sorted(p for p in os.listdir(os.path.join(ROOT, "light_sync", "parts")) if p.endswith(".py"))
    for f in parts:
        shutil.copy(os.path.join(ROOT, "light_sync", "parts", f), os.path.join(out, "light_sync", "parts", f))
    for f in WEB:
        shutil.copy(os.path.join(ROOT, "light_sync", "web", f), os.path.join(out, "light_sync", "web", f))
    themes = sorted(t for t in os.listdir(os.path.join(ROOT, "home_assistant", "themes")) if t.endswith(".yaml"))
    for t in themes:
        shutil.copy(os.path.join(ROOT, "home_assistant", "themes", t), os.path.join(out, "themes", t))
    src = f"http://{lan_ip()}:{PORT}"
    # for the add-on's own updater (updater.py): what goes where in its folder, and which parts exist
    files = [[f"light_sync/{f}", f] for f in ADDON + ["light_sync.py"]]
    files += [[f"light_sync/parts/{f}", f"parts/{f}"] for f in parts] + [[f"light_sync/web/{f}", f"web/{f}"] for f in WEB]
    import json
    pose_dir = os.path.join(ROOT, "pi_addon", "light_sync_pose")  # the pose helper add-on (Nov 9)
    pose = sorted(f for f in os.listdir(pose_dir) if not f.startswith(("_", ".")) and os.path.isfile(os.path.join(pose_dir, f)))
    os.makedirs(os.path.join(out, "light_sync_pose"), exist_ok=True)
    for f in pose:
        shutil.copy(os.path.join(pose_dir, f), os.path.join(out, "light_sync_pose", f))
    with open(os.path.join(out, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump({"files": files, "parts": parts, "pose": [[f"light_sync_pose/{f}", f] for f in pose]}, f)
    with open(os.path.join(out, "install.sh"), "w", encoding="utf-8", newline="\n") as f:
        f.write(INSTALL.format(src=src, addon=" ".join(ADDON), parts=" ".join(parts), web=" ".join(WEB), themes=" ".join(themes)))
    return out, src


def main():
    out, src = stage()
    print(f"Serving {out} (add-on files + theme only) on port {PORT}.\n")
    print("In Home Assistant open Terminal (sidebar) and paste:\n")
    print(f"  curl -fsS {src}/install.sh | sh\n")
    print("Then close this window (Ctrl+C).", flush=True)
    server = ThreadingHTTPServer(("0.0.0.0", PORT), partial(SimpleHTTPRequestHandler, directory=out))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    sys.exit(0)


if __name__ == "__main__":
    main()
