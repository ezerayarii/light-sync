"""Light Sync learns from itself (Nov 7, owner: "make it more interesting and recursive" - learn from its own mistakes,
tune itself, and tell me; plus a weekly recap).

Every time Light Sync does something by itself it notes it (did); when the owner undoes it soon after, that's a
mistake (undone). Every night at 04:00 it looks at the last two weeks and moves its own settings a small step,
always inside safe limits, and keeps a note of what it changed. The next morning (09:30) the notes go to Telegram;
on Sundays at 11:00 a recap of the week. Numbers only: modes, times and counts - nothing about who is home.

  Auto Party wait   (auto_party_s)     a steady beat this long before Party starts: undone often -> wait longer
  Lights follow me  (follow_absent_s)  no sign of the owner this long -> all off: the lights put back on right
                                       after -> wait longer; no mistakes for a week -> a little shorter again
  Normal, your way  (normal)           Nov 7, owner: "Normal is the main one, make it well done". Each time the owner
                                       moves Normal's sliders, the light they chose and when. Nightly, per hour: how much
                                       brighter / dimmer, warmer / cooler than their day they like it (recent days count
                                       more, a single change only a little), so tomorrow's Normal starts there.
Kept in data/learn.json (the Pi: /data, which survives updates)."""
import math
import json
import os
import threading
import time

from parts import config, web

try:
    from parts.wifi import DATA  # the Pi: /data; the laptop: data/
except Exception:
    DATA = "data"
FILE = os.path.join(DATA, "learn.json")

# name: (start, lowest, highest, step up, step down)
TUNE = {"auto_party_s": (20, 20, 90, 10, 5), "follow_absent_s": (900, 900, 1800, 300, 300)}
WHY = {"auto_party_s": "Auto Party's wait (a steady beat before Party starts)",
       "follow_absent_s": "Lights follow me's wait (no one seen before all off)"}
RULE_OF = {"auto_party": "auto_party_s", "follow_off": "follow_absent_s"}
MODES = ("normal", "party", "ambient", "gaming")
NAMES = {"normal": "Normal", "party": "Party", "ambient": "TV", "gaming": "Gaming"}

LOCK = threading.Lock()
S = {"tuning": {}, "events": [], "days": {}, "notes": [], "told_at": 0, "nightly": "", "morning": "", "weekly": "",
     "taught": [], "normal": None}


def load():
    try:
        with open(FILE, encoding="utf-8") as f:
            S.update(json.load(f))
    except Exception:
        pass


def save():
    try:
        os.makedirs(DATA, exist_ok=True)
        tmp = FILE + ".tmp"
        with LOCK, open(tmp, "w", encoding="utf-8") as f:
            json.dump(S, f)
        os.replace(tmp, FILE)
    except Exception as e:
        print(f"Learner: couldn't save ({e})", flush=True)


def get(name):
    start, lo, hi = TUNE[name][:3]
    try:
        return max(lo, min(hi, S["tuning"].get(name, start)))
    except Exception:
        return start


def did(rule):
    """Light Sync did something by itself."""
    with LOCK:
        S["events"] = (S["events"] + [{"t": round(time.time()), "rule": rule, "undone": False}])[-600:]


def undone(rule, within=900):
    """The owner undid the last thing this rule did (if it was recent)."""
    now = time.time()
    with LOCK:
        for ev in reversed(S["events"]):
            if ev["rule"] == rule:
                if now - ev["t"] <= within and not ev["undone"]:
                    ev["undone"] = True
                    print(f"Learner: you undid {rule} - noted", flush=True)
                return


def note(text):
    with LOCK:
        S["notes"] = (S["notes"] + [{"t": round(time.time()), "text": text}])[-100:]


def tune_once(now=None):
    """The nightly step: per rule, the last 14 days of did / undone -> one small step (or none)."""
    now = now or time.time()
    changed = []
    for rule, name in RULE_OF.items():
        start, lo, hi, up, down = TUNE[name]
        cur = get(name)
        since = S.setdefault("changed_at", {}).get(name, 0)  # after a change only what happens from then on counts
        recent = [e for e in S["events"] if e["rule"] == rule and now - e["t"] < 14 * 86400 and e["t"] > since]
        last3 = [e for e in recent if now - e["t"] < 3 * 86400]
        n, bad = len(recent), sum(e["undone"] for e in recent)
        new = cur
        if rule == "follow_off":
            if sum(e["undone"] for e in last3) >= 2:
                new = min(hi, cur + up)
            elif n >= 4 and bad == 0 and all(now - e["t"] < 7 * 86400 for e in recent[-4:]):
                new = max(lo, cur - down)
        else:
            if n >= 3 and bad / n >= 0.34:
                new = min(hi, cur + up)
            elif n >= 5 and bad == 0:
                new = max(lo, cur - down)
        if new != cur:
            S["tuning"][name] = new
            S["changed_at"][name] = round(now)
            unit = (lambda v: f"{v // 60} min") if name == "follow_absent_s" else (lambda v: f"{v} s")
            text = (f"{WHY[name]}: {unit(cur)} -> {unit(new)} "
                    f"({bad} of {n} undone in 2 weeks{', ' + str(sum(e['undone'] for e in last3)) + ' in 3 days' if rule == 'follow_off' else ''})")
            note(text)
            changed.append(text)
    return changed


def normal_taught(minute, db, dk):
    """The owner chose Normal's light: db % brighter and dk K warmer (negative: dimmer / cooler) than their day at minute."""
    with LOCK:
        S["taught"] = (S["taught"] + [{"t": round(time.time()), "m": int(minute), "db": round(db, 1), "dk": round(dk)}])[-400:]


def normal_learn(now=None):
    """Per hour, the owner's usual difference from their day: a weighted average of the slider changes within 1.5 h of
    that hour over the last 30 days (a change 10 days old counts a third as much), pulled towards 0 when there are few
    (one change: a third of it). Limits: +-40 % brightness, +-1500 K. None when nothing was taught."""
    now = now or time.time()
    taught = [x for x in S["taught"] if now - x["t"] < 30 * 86400]
    if not taught:
        S["normal"] = None
        return []
    old = S.get("normal") or [[0, 0]] * 24
    new = []
    for h in range(24):
        sw = sb = sk = 0.0
        for x in taught:
            d = abs(x["m"] - (h * 60 + 30))
            d = min(d, 1440 - d)
            if d > 90:
                continue
            w = math.exp(-(now - x["t"]) / (10 * 86400)) * math.exp(-(d / 60) ** 2)
            sw, sb, sk = sw + w, sb + w * x["db"], sk + w * x["dk"]
        if sw <= 0:
            new.append([0, 0])
            continue
        trust = sw / (sw + 2.0)
        new.append([round(max(-40, min(40, sb / sw * trust)), 1), round(max(-1500, min(1500, sk / sw * trust)) / 50) * 50])
    S["normal"] = new
    changes = []
    for h in range(24):
        db, dk = new[h][0] - old[h][0], new[h][1] - old[h][1]
        if abs(db) >= 5 or abs(dk) >= 300:
            changes.append((h, new[h]))
    if changes:  # one note, the clearest hours only
        words = []
        for h, (b, k) in changes[:3]:
            bits = [f"{abs(b):.0f} % {'brighter' if b > 0 else 'dimmer'}" if abs(b) >= 3 else "",
                    f"{'cooler' if k > 0 else 'warmer'}" if abs(k) >= 200 else ""]
            words.append(f"around {h}:00 " + " and ".join(x for x in bits if x) if any(bits) else f"around {h}:00 as your day")
        note("Normal: you like it " + "; ".join(words))
    return changes


def normal_offset(minute):
    """(brightness points, kelvin) learned for this minute of the day, smoothly between the hours."""
    cur = S.get("normal")
    if not cur:
        return 0.0, 0.0
    x = (minute - 30) / 60.0
    h0 = int(math.floor(x)) % 24
    f = x - math.floor(x)
    a, b = cur[h0], cur[(h0 + 1) % 24]
    return a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f


def add_time(mode, sec):
    day = time.strftime("%Y-%m-%d")
    with LOCK:
        d = S["days"].setdefault(day, {})
        d[mode] = d.get(mode, 0) + sec
        if len(S["days"]) > 60:  # two months is plenty
            for k in sorted(S["days"])[:-60]:
                S["days"].pop(k, None)


def hours(sec):
    return f"{sec / 3600:.1f} h" if sec >= 3600 else f"{round(sec / 60)} min"


def recap_text(days=7):
    keys = sorted(S["days"])[-days:]
    if not keys:
        return "Nothing to recap yet - I start counting from now."
    tot = {m: sum(S["days"][k].get(m, 0) for k in keys) for m in MODES}
    since = time.time() - days * 86400
    evs = [e for e in S["events"] if e["t"] >= since]
    parties = sum(1 for e in evs if e["rule"] == "auto_party")
    offs = sum(1 for e in evs if e["rule"] == "follow_off")
    fixes = sum(1 for e in evs if e["rule"] in ("fix_tvd", "tv_nudge"))
    busiest = max(keys, key=lambda k: sum(S["days"][k].get(m, 0) for m in ("party", "ambient", "gaming")))
    lines = [f"Your room, last {len(keys)} day{'s' if len(keys) != 1 else ''}:"]
    lines += [f"- {NAMES[m]}: {hours(tot[m])}" for m in MODES if tot[m] >= 60]
    lines.append(f"- Auto Party started {parties} time{'s' if parties != 1 else ''}; Lights follow me switched off {offs}")
    if fixes:
        lines.append(f"- I fixed or flagged {fixes} thing{'s' if fixes != 1 else ''} by myself (TvD, TV left on)")
    lines.append(f"- Busiest day: {time.strftime('%A', time.strptime(busiest, '%Y-%m-%d'))}")
    return "\n".join(lines)


def learned_text():
    t = ["What I tune by myself now:"]
    cur = S.get("normal")
    if cur and any(abs(b) >= 3 or abs(k) >= 200 for b, k in cur):
        parts = []
        for h in (8, 12, 18, 21, 23):
            b, k = cur[h]
            if abs(b) >= 3 or abs(k) >= 200:
                parts.append(f"{h}:00 {'+' if b >= 0 else ''}{b:.0f} %{', warmer' if k <= -200 else ', cooler' if k >= 200 else ''}")
        t.append("- Normal, your way: " + ("; ".join(parts) if parts else "small changes"))
    else:
        t.append("- Normal, your way: nothing learned yet (move Normal's sliders when you want it different)")
    t += [f"- {WHY[n]}: {get(n) // 60} min" if n == "follow_absent_s" else f"- {WHY[n]}: {get(n)} s" for n in TUNE]
    recent = S["notes"][-5:]
    t += ["", "Last changes:"] + [f"- {time.strftime('%a %d %b', time.localtime(x['t']))}: {x['text']}" for x in recent] if recent \
        else ["", "No changes yet: I change a setting only when you undo me a few times (or never do for a while)."]
    return "\n".join(t)


def tell(title, text):
    if not config.HA_TOKEN:  # no key (a test): nothing to send (Nov 8)
        return
    try:
        from parts import ha_link
        from parts.config import HA_URL
        ha_link.session.post(f"{HA_URL}/api/services/script/notify_me", timeout=5,
                             json={"title": title, "message": text, "level": "info", "topic": "system"})
    except Exception as e:
        print(f"Learner: couldn't send ({type(e).__name__})", flush=True)


def learner_loop():
    last_save = time.time()
    last = time.time()
    while True:
        time.sleep(5)
        now = time.time()
        if config.STANDBY:
            last = now
            continue
        mode = web.STATE.get("mode")
        if config.RUNNING and mode in MODES:
            add_time(mode, min(30.0, now - last))
        last = now
        lt = time.localtime()
        today = time.strftime("%Y-%m-%d")
        if lt.tm_hour == 4 and S["nightly"] != today:
            S["nightly"] = today
            # Nov 12 (owner: other changes -> ask first): work the changes out, put everything back, ask for each
            import copy
            from parts import approve
            keys = ("tuning", "changed_at", "notes", "normal")
            before = copy.deepcopy({k: S.get(k) for k in keys})
            tune_once()
            normal_learn()
            after = copy.deepcopy({k: S.get(k) for k in keys})
            S.update(copy.deepcopy(before))
            new_notes = (after["notes"] or [])[len(before["notes"] or []):]
            for i, note_ in enumerate(new_notes):
                def apply(a=after, n=note_):
                    S["tuning"], S["changed_at"], S["normal"] = a["tuning"], a["changed_at"], a["normal"]
                    S["notes"] = (S["notes"] + [n])[-100:]
                    save()
                approve.ask(f"learn{i}", f"I'd like to change: {note_['text']}. OK?", apply, timeout=14 * 3600)
            print("Learner: nightly - " + (f"{len(new_notes)} change(s) proposed" if new_notes else "nothing to change"), flush=True)
            save()
        if lt.tm_hour == 9 and lt.tm_min >= 30 and S["morning"] != today:
            S["morning"] = today
            new = [x for x in S["notes"] if x["t"] > S.get("told_at", 0)]
            if new:
                tell("Light Sync learned", "Overnight I changed:\n" + "\n".join(f"- {x['text']}" for x in new)
                     + "\n\n/learned shows what I tune; tell me if you want it back.")
            S["told_at"] = round(now)
            save()
        if lt.tm_wday == 6 and lt.tm_hour == 11 and S["weekly"] != today:
            S["weekly"] = today
            tell("Your week", recap_text(7))
            save()
        if now - last_save > 600:
            last_save = now
            save()


def start():
    load()
    threading.Thread(target=learner_loop, daemon=True, name="learner").start()
