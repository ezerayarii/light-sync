"""Approvals (Nov 12, owner: "for any light on or off or any changes I want to approve or see a notice of it"; chose: lights
ON -> ask first, lights OFF -> do it then tell (Undo), other changes -> ask first, silent at night).

ask()   a question on Telegram with Yes / No; the action runs only on Yes. No answer in time = nothing happens (at night
        the messages come silently, so an unanswered night question changes nothing).
tell()  something already done, with an Undo button when it can be undone.
Home Assistant's own automatic rules ask through here too (input_text.light_sync_telegram_ask "ask|<key>|<time>",
tools/ha_setup/approvals_setup.py), and the buttons come back the same way ("ok|<id>", "no|<id>", "undo|<id>").
Anything the owner does themselves (app, Telegram, voice, the switch) never asks."""
import secrets
import threading
import time

from parts.config import HA_URL
from parts import config, ha_link

PENDING = {}   # id -> {"key", "text", "do", "until"}
UNDO = {}      # id -> {"text", "do", "until"}
LOCK = threading.Lock()

# Home Assistant's automatic rules: what they ask, and what they do on Yes (domain, service, data)
HA_ASKS = {
    "sunrise": ("Sunrise wake-up: start the 20-minute sunrise in your room?",
                [("input_boolean", "turn_off", {"entity_id": "input_boolean.late_night_mode"}),
                 ("script", "sunrise_chambre", {"duration_min": 20})]),
    "latenight": ("It's late: switch to late-night mode (dimmer, warmer, softer)?",
                  [("input_boolean", "turn_on", {"entity_id": "input_boolean.late_night_mode"})]),
    "tvfilm": ("A film app opened on the TV box: turn on TV mode?",
               [("script", "turn_on", {"entity_id": "script.movie_time"}),
                ("input_boolean", "turn_on", {"entity_id": "input_boolean.light_sync_film_by_auto"})]),
    "tvmusic": ("YouTube or mirroring opened on the TV box: turn on TV + Music?",
                [("script", "turn_on", {"entity_id": "script.movie_music"}),
                 ("input_boolean", "turn_on", {"entity_id": "input_boolean.light_sync_film_by_auto"})]),
    "tvend": ("The TV app closed: back to Normal?",
              [("script", "turn_on", {"entity_id": "script.normal_light"})]),
    "appmusic": ("A media app is playing on the TV box: turn on Music sync?",
                 [("input_boolean", "turn_on", {"entity_id": "input_boolean.music_sync"}),
                  ("input_boolean", "turn_on", {"entity_id": "input_boolean.light_sync_music_by_apps"})]),
}


def _send(title, text, buttons=None):
    if not config.HA_TOKEN:
        return
    data = {"title": title, "message": text, "level": "info", "topic": "approvals"}
    if buttons:
        data["buttons"] = buttons
    try:
        ha_link.session.post(f"{HA_URL}/api/services/script/notify_me", json=data, timeout=5)
    except Exception as e:
        print(f"Approvals: couldn't send ({type(e).__name__})", flush=True)


def _reply(text):
    if not config.HA_TOKEN:
        return
    try:
        ha_link.session.post(f"{HA_URL}/api/services/script/telegram_reply", json={"message": text}, timeout=5)
    except Exception:
        pass


def ha_do(steps):
    def run():
        for domain, service, data in steps:
            ha_link.session.post(f"{HA_URL}/api/services/{domain}/{service}", json=data, timeout=10).raise_for_status()
    return run


def ask(key, text, do, timeout=300):
    """Ask before doing. One question per key at a time. Returns the id (or None if that key is already waiting)."""
    now = time.time()
    with LOCK:
        if any(p["key"] == key and p["until"] > now for p in PENDING.values()):
            return None
        qid = secrets.token_hex(3)
        PENDING[qid] = {"key": key, "text": text, "do": do, "until": now + timeout}
    mins = max(1, round(timeout / 60))
    _send("Light Sync asks", f"{text}\n(no answer in {mins} min = nothing happens)", [f"Yes:/ok_{qid}, No:/no_{qid}"])
    print(f"Asked ({key}): {text}", flush=True)
    return qid


def ask_ha(key):
    """A Home Assistant rule wants to act: ask with its words, run its steps on Yes."""
    if key not in HA_ASKS:
        print(f"Approvals: unknown question {key}", flush=True)
        return
    text, steps = HA_ASKS[key]
    ask(key, text, ha_do(steps), timeout=(12 * 3600 if key == "latenight" else 600))


def tell(text, undo=None, title="Light Sync"):
    """Already done: a note, with Undo if it can be undone (for 2 hours)."""
    buttons = None
    if undo:
        uid = secrets.token_hex(3)
        with LOCK:
            UNDO[uid] = {"text": text, "do": undo, "until": time.time() + 2 * 3600}
        buttons = [f"Undo:/undo_{uid}"]
    _send(title, text, buttons)
    print(f"Told: {text}", flush=True)


def answer(kind, qid):
    """The owner tapped Yes / No / Undo."""
    with LOCK:
        if kind == "undo":
            item = UNDO.pop(qid, None)
        else:
            item = PENDING.pop(qid, None)
    if not item or item["until"] < time.time():
        _reply("That one has expired - nothing was changed.")
        return
    if kind == "no":
        print(f"Approvals: no - {item['text']}", flush=True)
        _reply("OK, left as it is.")
        return
    try:
        item["do"]()
        _reply("Undone." if kind == "undo" else "Done.")
        print(f"Approvals: {'undone' if kind == 'undo' else 'yes'} - {item['text']}", flush=True)
    except Exception as e:
        _reply(f"Couldn't do it: {e}")
        print(f"Approvals: failed - {e}", flush=True)


def expire_loop():
    while True:
        time.sleep(15)
        now = time.time()
        with LOCK:
            gone = [(k, p) for k, p in PENDING.items() if p["until"] < now]
            for k, _ in gone:
                PENDING.pop(k, None)
            for k in [k for k, u in UNDO.items() if u["until"] < now]:
                UNDO.pop(k, None)
        for _, p in gone:
            print(f"Approvals: no answer - left as it is ({p['key']})", flush=True)


def frame():
    now = time.time()
    with LOCK:
        return {"waiting": [{"key": p["key"], "text": p["text"], "left_s": round(p["until"] - now)} for p in PENDING.values()]}


def start():
    threading.Thread(target=expire_loop, daemon=True, name="approvals").start()
