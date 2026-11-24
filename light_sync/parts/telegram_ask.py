"""Telegram questions that need Light Sync (Nov 7): Home Assistant's Telegram automations write the command into
input_text.light_sync_telegram_ask ("wifi|<arg>|<time>") and Light Sync answers through script.telegram_reply (the
owner's chat only, tools/ha_setup/telegram_setup.py). Only the machine driving the lights (the Pi) answers.

  wifi      who's on the Wi-Fi (the owner asked for it on Telegram; names and how long only)
  check     everything at once, problems first (health.py)
  net       the internet and the home network
  mine X    a new device is the owner's: remembered as theirs, no more alerts for it
  notmine X a new device isn't theirs: what to do
  fixtvd    switch TvD on again (the "TvD is stuck" message's button)"""
import threading
import time

from parts.config import HA_URL
from parts import config, ha_link, health, wifi

ASK = "input_text.light_sync_telegram_ask"


def reply(text, buttons=None):
    data = {"message": text[:3900]}
    if buttons:
        data["buttons"] = buttons
    try:
        ha_link.session.post(f"{HA_URL}/api/services/script/telegram_reply", json=data, timeout=5)
    except Exception as e:
        print(f"Telegram: couldn't answer ({type(e).__name__})", flush=True)


def wifi_text():
    f = wifi.wifi_frame()
    if not f["rows"]:
        return "No Wi-Fi scan yet (it starts a minute after Light Sync)."
    on = [r for r in f["rows"] if r["online"]]
    quiet = [r for r in f["rows"] if r["here"] and not r["online"]]

    def row(r):
        mark = " (NEW today)" if r["new"] else ""
        since = f", back {r['back_s'] // 60} min ago" if r.get("back_s") is not None and r["back_s"] < 7200 else ""
        return f"- {r['name']}{mark}{since}"
    out = [f"Online now ({len(on)}):"] + [row(r) for r in on]
    if quiet:
        out += ["", f"Here but quiet ({len(quiet)}, asleep or just left):"] + [row(r) for r in quiet]
    out += ["", f"Scanned {f['ago_s']} s ago."]
    return "\n".join(out)


def handle(cmd, arg):
    if cmd == "wifi":
        reply(wifi_text())
    elif cmd == "check":
        text = health.check_text()
        reply(text, buttons=["Fix TvD:/fixtvd"] if "TvD: off" in text else None)
    elif cmd == "net":
        reply(health.net_text())
    elif cmd == "mine":
        name = wifi.mark_mine(arg)
        reply(f"Got it: {name} is yours. No more alerts for it." if name else "I don't know that device any more.")
    elif cmd == "notmine":
        name = wifi.label_of(arg)
        reply(f"{name or 'That device'} isn't yours. To get it off: open the eero app, find it under Devices and block it "
              "(or pause it), then change the Wi-Fi password if you think someone knows it. Light Sync keeps an eye on new ones.")
    elif cmd in ("ok", "no", "undo"):  # Nov 12: the approval buttons
        from parts import approve
        approve.answer(cmd, arg)
    elif cmd == "ask":  # a Home Assistant rule wants to act: ask the owner first
        from parts import approve
        approve.ask_ha(arg)
    elif cmd == "tell":  # a Home Assistant rule did something (lights off): a note
        from parts import approve
        approve.tell(arg.replace("_", " "))
    elif cmd == "prayer":
        from parts import prayer
        reply(prayer.text())
    elif cmd == "recap":
        from parts import learner
        reply(learner.recap_text(7))
    elif cmd == "learned":
        from parts import learner
        reply(learner.learned_text())
    elif cmd == "fixtvd":
        health.fix_tvd()
        reply("Switching TvD on again... I'll tell you if it stays off.")
    else:
        reply("I don't know that one.")


def ask_loop():
    last = None
    while True:
        time.sleep(1.0)
        if config.STANDBY:  # the other machine answers
            continue
        try:
            v = ha_link.ha_state(ASK)["state"]
        except Exception:
            continue
        if last is None:
            last = v  # what was there before this start: already answered
            continue
        if v == last or v in ("", "unknown", "unavailable"):
            continue
        last = v
        parts = v.split("|")
        try:
            print(f"Telegram: {parts[0]}", flush=True)
            handle(parts[0], parts[1] if len(parts) > 2 else "")
        except Exception as e:
            print(f"Telegram: {parts[0]} failed ({e})", flush=True)
            reply("Something went wrong with that one, sorry.")


def start():
    threading.Thread(target=ask_loop, daemon=True, name="telegram ask").start()
