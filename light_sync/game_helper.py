"""game_helper.py - the laptop's only job now (Nov 6, owner: "the Pi is the whole project, the laptop just for testing";
then: "the laptop only sends game colours"). It never drives the lights and never makes the Pi stand by.

While a game is in front on this laptop (parts/games.foreground_game: a full-screen app or a known game folder):
  - after 3 s it switches Gaming on in Home Assistant (if Auto gaming is on), as Light Sync's own Auto gaming did
  - ~10 times a second it reads the laptop screen (64 x 36, numbers only), takes the 45 colour points
    (screen_map.sample_colors) and posts them to the Pi's Light Sync (POST /game on the show server, port 8090)
  - the game closed (or a lobby in front for 15 min): Gaming off again, if this helper switched it on
No picture leaves the laptop: only colours (numbers). Start: windows/start_game_helper.bat (the Startup shortcut)."""
import json
import os
import numpy as np
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "vendor"))
sys.argv = [sys.argv[0]]  # light_sync's settings read sys.argv
import requests  # noqa: E402

from parts import boot  # noqa: E402,F401  (secrets.yaml: the HA token)
from parts import colours, config, games, ha_link, screen_map  # noqa: E402


def game_colours(img):
    """The laptop screen (RGB, 64 x 36) -> the game's main colour (its colourful pixels, as Light Sync did) and the
    45 points (screen_map.sample_colors), all as plain numbers."""
    px = img.reshape(-1, 3).astype(np.float32)
    mx, mn = px.max(axis=1), px.min(axis=1)
    sat = np.where(mx > 0, (mx - mn) / np.maximum(mx, 1) * 255, 0)
    vivid = px[(sat > 70) & (mx > 60)]
    rgb = [float(v) for v in colours.vivid(vivid.mean(axis=0))] if len(vivid) > 20 else None
    samples = screen_map.sample_colors(np.ascontiguousarray(img[:, :, ::-1]))
    return rgb, [[float(v) for v in c] if c is not None else None for c in samples]

PI = os.getenv("PI_GAME_URL", "http://192.168.1.10:8090/game")
SEND_EVERY = 0.1
STATE = {"auto_on": False, "skip": "", "asked": ""}


def ai_server():
    """Nov 7: Rocky's AI (Ollama on this laptop) for the Pi's Home Assistant, as the full Light Sync used to serve it:
    POST/GET /ollama/... on port 8090, only from the Pi, through the same guard and repair (parts/assistant.py)."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from parts import assistant

    class AI(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path.startswith("/ollama/"):
                assistant.ollama_proxy(self, "GET")
            else:
                self.send_response(404)
                self.end_headers()

        def do_POST(self):
            if self.path.startswith("/ollama/"):
                n = min(int(self.headers.get("Content-Length", 0)), 4 << 20)
                assistant.ollama_proxy(self, "POST", self.rfile.read(n))
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, *a):
            pass
    try:
        ThreadingHTTPServer(("0.0.0.0", 8090), AI).serve_forever()
    except OSError as e:
        print(f"Game helper: Rocky's AI not served ({e})", flush=True)


def presence_loop():
    """Nov 7: tell the Pi how long since the last key / mouse on this laptop (a number only), every 30 s, so Lights
    follow me knows the owner is here while they sit at the laptop (the Pi can't see this laptop's keyboard)."""
    from parts import guard
    sess = requests.Session()
    url = PI.rsplit("/", 1)[0] + "/laptop"
    while True:
        try:
            idle = guard.laptop_idle_s()
            if idle is not None:
                sess.post(url, data=json.dumps({"idle": round(idle)}), headers={"Content-Type": "application/json"}, timeout=3)
        except Exception:
            pass
        time.sleep(30)


def ha_on(eid):
    try:
        return ha_link.ha_state(eid)["state"] == "on"
    except Exception:
        return False


def main():
    # one helper only (Nov 9: five were running at once, started by the launcher, the control and by hand)
    import socket
    global _ONE
    _ONE = socket.socket()
    try:
        _ONE.bind(("127.0.0.1", 47811))
    except OSError:
        print("Game helper: already running - this copy stops", flush=True)
        sys.exit(3)
    print(f"Game helper: watching for games, colours to {PI}", flush=True)
    threading.Thread(target=ai_server, daemon=True, name="rocky ai").start()
    threading.Thread(target=presence_loop, daemon=True, name="presence").start()
    import ctypes
    ctypes.windll.user32.SetProcessDPIAware()
    seen = gone = 0.0
    playing, pid = "", 0
    gaming = auto = False
    checked = 0.0
    sess = requests.Session()
    last = None  # the last colours sent: tabbed away from the game, the Pi keeps them (as Light Sync did)
    fails = 0
    while True:
        now = time.time()
        if now - checked > 2:
            checked = now
            gaming, auto = ha_on(config.HA_GAMING), ha_on(config.HA_AUTO_GAMING)
        try:
            front, fpid = games.foreground_game()
        except Exception:
            front, fpid = "", 0
        if front:
            playing, pid = front, fpid
        elif playing:
            try:
                if not games.process_alive(pid):
                    playing, pid = "", 0
            except Exception:
                playing, pid = "", 0
        seen, gone = (seen or now, 0.0) if playing else (0.0, gone or now)
        if not playing:
            STATE["skip"] = STATE["asked"] = ""
        if auto:
            if playing and gaming and not STATE["auto_on"] and not STATE["skip"]:  # Gaming already on for this game: ours
                STATE["auto_on"] = True
            if STATE["auto_on"] and not gaming and playing:  # switched off by hand during the game: leave it
                STATE["auto_on"], STATE["skip"] = False, playing
            if playing and playing != STATE["skip"] and playing != STATE.get("asked") and not gaming and now - seen > 3:
                # Nov 12 (owner: lights on by themselves -> ask first): the Pi asks on Telegram; on Yes it switches Gaming
                # on and this helper takes it as its own (it switches it off again when the game closes)
                print(f"Game: {playing} - asking about Gaming", flush=True)
                STATE["asked"] = playing
                try:
                    sess.post(PI.rsplit("/", 1)[0] + "/laptop_ask", data=json.dumps({"game": playing}),
                              headers={"Content-Type": "application/json"}, timeout=3)
                except Exception as e:
                    print(f"Game helper: couldn't ask ({type(e).__name__})", flush=True)
            elif STATE["auto_on"] and not playing and now - gone > (games.LOBBY_WAIT if games.FRONT_EXE in games.LOBBIES else 10):
                print("Game closed - Gaming off", flush=True)
                ha_link.set_switch(config.HA_GAMING, False)
                STATE["auto_on"], gaming = False, False
                try:  # (Nov 12: lights off by themselves -> a note with Undo)
                    sess.post(PI.rsplit("/", 1)[0] + "/laptop_tell", data=json.dumps({"text": "The game closed: Gaming off.",
                              "undo": "gaming"}), headers={"Content-Type": "application/json"}, timeout=3)
                except Exception:
                    pass
        if playing and gaming:  # the game's colours to the Pi (~10 a second; tabbed away: the last ones, 2 a second)
            try:
                if front:
                    rgb, samples = game_colours(games.grab_laptop_screen())
                    last = {"game": playing, "rgb": rgb, "samples": samples}
                if last:
                    sess.post(PI, data=json.dumps(last), headers={"Content-Type": "application/json"}, timeout=0.5).raise_for_status()
                if fails:
                    print("Game helper: the Pi gets the colours again", flush=True)
                fails = 0
            except Exception as e:
                fails += 1
                if fails in (1, 30) or fails % 600 == 0:
                    print(f"Game helper: couldn't send the colours to the Pi ({type(e).__name__})", flush=True)
                time.sleep(1)
            time.sleep(SEND_EVERY if front else 0.5)
        else:
            last = None
            time.sleep(0.5 if playing else 1.0)


if __name__ == "__main__":
    main()
