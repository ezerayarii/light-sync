"""
habits.py - Light Sync learns how the owner uses the lights (laptop only; the data never leaves the laptop).

Every minute while a mode runs, one line goes to data/usage.jsonl: the time, the mode (Music, TV, Gaming,
Normal) and the look (colours, movement, effect, brightness...). From that:

  For you      the mode and look usually used at this time of day (weekdays and weekends apart), for the app's
               "For you" card, with a plain-words summary written by the local AI (Ollama).
  Automatic    with "Switch by itself at my usual times" on: switches to the usual mode and look when a habit is
               strong (the same mode on 3 of the last 5 matching days, around this time). Never switches lights on,
               never interrupts a game or a choice made in the last 30 minutes, keeps Comfort and Flash as they are,
               never raises the brightness, at most once every 90 minutes; the app shows it with Undo.

The first start seeds the log from Home Assistant's history of sensor.light_sync (stretches of 5 minutes or more,
modes only). "Resume where I left off" lives in Home Assistant (tools/ha_setup/learn_setup.py), so it works from the Pi too.
"""
import datetime as dt
import json
import math
import os
import re
import threading
import time
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)  # the project folder (data\ is there)
DATA = os.path.join(ROOT, "data")
LOG = os.path.join(DATA, "usage.jsonl")
BOOK = os.path.join(DATA, "learn_state.json")  # automatic switching bookkeeping
NORMAL_FILE = os.path.join(DATA, "normal_prefs.json")  # Normal light: the sliders and scenes the owner used, when
KEEP_DAYS = 90
LOOK_DAYS = 28        # habits come from the last 4 weeks
WINDOW_MIN = 45       # "around this time": +-45 minutes
NAMES = {"party": "Music", "ambient": "TV", "gaming": "Gaming", "normal": "Normal light"}  # (Effects: removed Nov 6)
SWITCH = {"party": "input_boolean.music_sync", "ambient": "input_boolean.tv_ambient",
          "gaming": "input_boolean.gaming_mode",
          "normal": "input_boolean.normal_mode"}
AUTO_HELPER = "input_boolean.light_sync_auto_learn"
MOVE = {"flow": "flow →", "flow back": "flow ←", "bounce": "flow ↔", "together": "together", "each own": "each own"}


def _local(t):
    return dt.datetime.fromtimestamp(t)


def _minute_of_day(t):
    d = _local(t)
    return d.hour * 60 + d.minute


def _near(a, b, w=WINDOW_MIN):
    d = abs(a - b) % 1440
    return min(d, 1440 - d) <= w


def _weekend(t):
    return _local(t).weekday() >= 5


def look_key(mode, look):
    """What makes two looks 'the same look' for a mode (for counting favourites)."""
    if not look:
        return None
    if mode == "party":
        return (look.get("colours"), tuple(look.get("my_colours") or ()) if look.get("colours") == "mine" else (),
                look.get("movement"))
    if mode == "ambient":
        return (bool(look.get("tv_music")),)
    if mode == "gaming":
        return (bool(look.get("game_music")),)
    if mode == "normal":
        return (look.get("normal_scene") or "Follow the day",)
    return ()


def label(mode, look):
    """'Music · neon · flow →' and so on."""
    name = NAMES.get(mode, mode)
    if not look:
        return name
    if mode == "party":
        return f"{name} · {look.get('colours')} · {MOVE.get(look.get('movement'), look.get('movement'))}"
    if mode == "ambient":
        return "TV + music" if look.get("tv_music") else "TV"
    if mode == "gaming":
        return "Gaming + music" if look.get("game_music") else "Gaming"
    if mode == "normal" and look.get("normal_scene") not in (None, "Follow the day"):
        return f"{name} · {look['normal_scene']}"
    return name


class Habits:
    def __init__(self, ms):
        self.ms = ms
        self.entries = []          # [(t, mode, look)]
        self.lock = threading.Lock()
        self.last_mode, self.changed_at, self.changed_by_auto = None, 0.0, False
        self.pending_auto = None   # the mode an automatic switch just turned on
        self.book = {"auto_at": 0.0}
        self.ai = {"text": "", "at": 0.0}
        os.makedirs(DATA, exist_ok=True)
        try:
            self.book.update(json.load(open(BOOK, encoding="utf-8")))
        except Exception:
            pass

    # ---------- the log ----------
    def load(self):
        cut = time.time() - KEEP_DAYS * 86400
        keep = []
        if os.path.exists(LOG):
            with open(LOG, encoding="utf-8") as f:
                for line in f:
                    try:
                        e = json.loads(line)
                    except ValueError:
                        continue
                    if e.get("t", 0) >= cut and e.get("mode") in NAMES:
                        keep.append(e)
            with open(LOG, "w", encoding="utf-8") as f:  # drop what's older than KEEP_DAYS
                f.writelines(json.dumps(e) + "\n" for e in keep)
        with self.lock:
            self.entries = [(e["t"], e["mode"], e.get("look")) for e in keep]

    def append(self, t, mode, look, seed=False):
        e = {"t": int(t), "mode": mode, "look": look}
        if seed:
            e["seed"] = 1
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(e) + "\n")
        with self.lock:
            self.entries.append((int(t), mode, look))

    def seed(self):
        """First start: Home Assistant's history of sensor.light_sync (it keeps ~10 days), stretches >= 5 min."""
        if self.book.get("seeded"):
            return
        ms = self.ms
        first = None  # minutes already logged live are not seeded twice
        if os.path.exists(LOG):
            with open(LOG, encoding="utf-8") as f:
                ts = [json.loads(line).get("t", 0) for line in f if line.strip()]
            first = min(ts) if ts else None
        try:
            now = dt.datetime.now(dt.timezone.utc)
            fmt = "%Y-%m-%dT%H:%M:%S+00:00"
            r = ms.session.get(f"{ms.HA_URL}/api/history/period/{(now - dt.timedelta(days=30)).strftime(fmt)}", timeout=60,
                               params={"filter_entity_id": "sensor.light_sync", "minimal_response": "", "no_attributes": "",
                                       "end_time": now.strftime(fmt)})  # without it: one day only
            rows = (r.json() or [[]])[0]
        except Exception as e:
            print(f"Habits: couldn't read Home Assistant's history ({e}) - trying again at the next start", flush=True)
            return
        changes = [(dt.datetime.fromisoformat(x["last_changed"]).timestamp(), x["state"]) for x in rows]
        changes.append((first or time.time(), None))
        n = 0
        for (t0, mode), (t1, _) in zip(changes, changes[1:]):
            t1 = min(t1, first or t1)
            if mode in NAMES and t1 - t0 >= 300:  # 5 minutes or more: real use, not a quick try
                for t in range(int(t0), int(t1), 60):
                    self.append(t, mode, None, seed=True)
                    n += 1
        self.book["seeded"] = True
        json.dump(self.book, open(BOOK, "w", encoding="utf-8"))
        print(f"Habits: learned {n} minutes of use from Home Assistant's history", flush=True)

    # ---------- what's on now ----------
    def mode_now(self):
        ms = self.ms
        if getattr(ms, "STANDBY", False):
            return None
        if ms.RUNNING:
            m = ms.STATE.get("mode")
            return m if m in NAMES else None
        try:
            return "normal" if ms.ha_state("input_boolean.normal_mode")["state"] == "on" else "off"
        except Exception:
            return None

    def look_now(self):
        ms = self.ms
        try:
            mine = [c for c in ms.ha_state(ms.HA_MY_COLORS)["state"].split(",") if len(c) == 7]
        except Exception:
            mine = []
        nm = ms.STATE.get("normal") or {}
        return {"normal_scene": ms.NORMAL_SCENE, "normal_bri": nm.get("bri"), "normal_k": nm.get("k"), "colours": ms.COLORS, "my_colours": mine, "movement": ms.USER_STYLE,
                "bright_min": int(ms.TUNE["bright_min"]),
                "bright_max": int(ms.TUNE["bright_max"]), "tv_music": bool(ms.TV_MUSIC),
                "game_music": bool(ms.GAME_MUSIC), "pulse_tvd": bool(ms.USER_PULSE["screen"]),
                "pulse_corner": bool(ms.USER_PULSE["beat"]), "pulse_top": bool(ms.USER_PULSE["wash"]),
                "reaction_auto": bool(ms.REACTION_AUTO), "auto_dj": bool(ms.AUTO_DJ)}

    # ---------- learning ----------
    def _recent(self, days):
        cut = time.time() - days * 86400
        with self.lock:
            return [e for e in self.entries if e[0] >= cut]

    def suggestion(self, now=None):
        """The mode (and look) usually on around this time on this kind of day, or None."""
        now = now or time.time()
        mod, wk = _minute_of_day(now), _weekend(now)
        ents = [e for e in self._recent(LOOK_DAYS) if _near(_minute_of_day(e[0]), mod)]
        same = [e for e in ents if _weekend(e[0]) == wk]
        use_same = len({_local(e[0]).date() for e in same}) >= 3  # enough weekdays (or weekends) to tell apart
        kind = ("weekend" if wk else "weekday") if use_same else "day"
        per_day = defaultdict(Counter)
        for t, m, _ in (same if use_same else ents):
            per_day[_local(t).date()][m] += 1
        dominant = {d: c.most_common(1)[0][0] for d, c in per_day.items() if c.most_common(1)[0][1] >= 10}
        if len(dominant) < 2:
            return None
        mode, n = Counter(dominant.values()).most_common(1)[0]
        if n < 2 or n / len(dominant) < 0.5:
            return None  # no clear habit around this time (yet)
        # the last 5 days of this kind (used or not): how many had this mode around now
        today = _local(now).date()
        recent, d = [], today - dt.timedelta(days=1)
        while len(recent) < 5 and (today - d).days <= 35:
            if kind == "day" or (d.weekday() >= 5) == wk:
                recent.append(d)
            d -= dt.timedelta(days=1)
        hits = sum(1 for d in recent if dominant.get(d) == mode)
        looks = Counter()
        latest = {}
        for t, m, lk in ents:
            k = look_key(m, lk)
            if m == mode and k is not None:
                looks[k] += 1
                latest[k] = lk
        if not looks:  # nothing around this time: the mode's favourite look overall
            for t, m, lk in self._recent(LOOK_DAYS):
                k = look_key(m, lk)
                if m == mode and k is not None:
                    looks[k] += 1
                    latest[k] = lk
        look = latest[looks.most_common(1)[0][0]] if looks else None
        when = _local(now).strftime("%H:%M")
        return {"mode": mode, "look": look, "label": label(mode, look), "days": n, "of": len(dominant),
                "recent": hits, "strength": round(n / len(dominant), 2),
                "why": f"on {n} of the {len(dominant)} {kind}s you used the lights around {when}"}

    def stats(self, days=30):
        ents = self._recent(days)
        minutes = Counter(m for _, m, _ in ents)
        total = sum(minutes.values()) or 1
        part = defaultdict(Counter)
        for t, m, _ in ents:
            h = _local(t).hour
            part[m]["morning" if 5 <= h < 12 else "afternoon" if 12 <= h < 17 else "evening" if 17 <= h < 22
                    else "night"] += 1
        fav = {}
        for mode in ("party", "ambient"):
            c = Counter(look_key(mode, lk) for t, m, lk in ents if m == mode and lk)
            if c:
                k = c.most_common(1)[0][0]
                fav[mode] = label(mode, next(lk for t, m, lk in reversed(ents) if m == mode and look_key(mode, lk) == k))
        bmax = sorted(lk["bright_max"] for _, _, lk in ents if lk and "bright_max" in lk)
        return {"hours": round(total / 60, 1) if minutes else 0,
                "modes": [(NAMES[m], round(100 * n / total)) for m, n in minutes.most_common()],
                "when": {NAMES[m]: c.most_common(1)[0][0] for m, c in part.items()},
                "favourites": {NAMES[m]: v for m, v in fav.items()},
                "brightness_max": bmax[len(bmax) // 2] if bmax else None}

    def favourite_look(self, mode, days=LOOK_DAYS):
        """The look used most (in minutes) for a mode, or None."""
        c, latest = Counter(), {}
        for t, m, lk in self._recent(days):
            k = look_key(m, lk)
            if m == mode and k:
                c[k] += 1
                latest[k] = lk
        return latest[c.most_common(1)[0][0]] if c else None

    def profile_text(self):
        """The owner's habits in one line, for the local AI's typed vibes."""
        s = self.stats()
        if not s["modes"]:
            return ""
        parts = ["most used: " + ", ".join(f"{n} {p}% (mostly {s['when'].get(n, '')})" for n, p in s["modes"][:3])]
        if s["favourites"]:
            parts.append("favourite looks: " + "; ".join(s["favourites"].values()))
        if s["brightness_max"]:
            parts.append(f"usual highest brightness {s['brightness_max']} %")
        return ". ".join(parts)

    def ai_summary(self):
        """Two friendly sentences about the habits, by the local AI (cached 30 min); a plain one without it."""
        if time.time() - self.ai["at"] < 1800 and self.ai["text"]:
            return self.ai["text"]
        s = self.stats()
        if not s["modes"]:
            return "Nothing learned yet: use the lights for a few days and I'll learn your habits."
        used = [(n, p) for n, p in s["modes"][:3] if p >= 5]
        facts = ["Most used: " + ", then ".join(f"{n} ({p}%)" for n, p in used) + "."]
        facts += [f"{n} is mostly used in the {s['when'].get(n, 'evening')}." for n, _ in used]
        facts += [f"Favourite {n} look: {v}." for n, v in s["favourites"].items()]
        plain = (f"In the last 30 days the lights ran {s['hours']} hours: "
                 + ", ".join(f"{n} {p}%" for n, p in s["modes"][:3]) + ".")
        text = plain
        try:
            if self.ms.local_ai_ready():
                r = self.ms.requests.post(f"{self.ms.OLLAMA_URL}/api/chat", timeout=60, json={
                    "model": self.ms.OLLAMA_MODEL, "stream": False, "keep_alive": "10m", "options": {"temperature": 0.2},
                    "messages": [{"role": "system", "content":
                                  "Write two short, friendly sentences telling the owner how they use their bedroom "
                                  "lights. Say 'you'. Use ONLY the facts given, in plain words; invent nothing (no "
                                  "feelings, no reasons, no brightness, no sleep). Music, TV, Gaming and "
                                  "Normal light are the modes' names."},
                                 {"role": "user", "content": "Facts:\n- " + "\n- ".join(facts)}]})
                out = r.json()["message"]["content"].strip().replace("\n", " ")
                out = re.sub(r"^(here (are|is)|sure)[^:]*:\s*", "", out, flags=re.I).strip()  # "Here are two sentences:"
                # a small model can mix things up: keep its words only if they name the top mode and no unused one
                unused = [n for n in NAMES.values() if n not in {u for u, _ in used}]
                if 20 <= len(out) <= 300 and used and used[0][0] in out and not any(n in out for n in unused):
                    text = out
        except Exception:
            pass
        self.ai = {"text": text, "at": time.time()}
        return text

    # ---------- Normal light: learned from the sliders and the scenes (Oct 20) ----------
    def _normal_load(self):
        try:
            return json.load(open(NORMAL_FILE, encoding="utf-8"))
        except Exception:
            return {"prefs": [], "scenes": []}

    def _normal_save(self, d):
        cut = time.time() - 90 * 86400
        d["prefs"] = [p for p in d["prefs"] if p["t"] >= cut][-300:]
        d["scenes"] = [s for s in d["scenes"] if s["t"] >= cut][-300:]
        json.dump(d, open(NORMAL_FILE, "w", encoding="utf-8"))
        self._normal = d

    def normal_pref(self, bri, kelvin):
        """The owner moved Normal's Brightness or Warmth slider: remember it for this time of day."""
        d = self._normal_load()
        now = time.time()
        d["prefs"].append({"t": int(now), "m": _minute_of_day(now), "b": round(float(bri)), "k": int(kelvin)})
        self._normal_save(d)

    def normal_scene_used(self, scene):
        d = self._normal_load()
        now = time.time()
        d["scenes"].append({"t": int(now), "m": _minute_of_day(now), "s": scene})
        self._normal_save(d)

    def normal_curve(self, minute, b0, k0):
        """Normal's brightness and warmth at this minute of the day: the built-in day (b0, k0) pulled toward what the
        owner set around this time (each setting counts less the further away in the day it was and the older it
        is: half after 14 days). Returns (brightness, kelvin, mostly learned?)."""
        d = getattr(self, "_normal", None) or self._normal_load()
        self._normal = d
        now, wsum, bs, ks = time.time(), 0.0, 0.0, 0.0
        for p in d["prefs"]:
            dm = abs(p["m"] - minute) % 1440
            dm = min(dm, 1440 - dm)
            if dm > 180:
                continue
            w = math.exp(-0.5 * (dm / 60) ** 2) * 0.5 ** ((now - p["t"]) / (14 * 86400))
            wsum, bs, ks = wsum + w, bs + w * p["b"], ks + w * p["k"]
        if wsum < 0.05:
            return b0, k0, False
        a = min(1.0, wsum)  # one recent setting right at this time: fully learned
        return b0 + (bs / wsum - b0) * a, k0 + (ks / wsum - k0) * a, a > 0.3

    def usual_scene(self, now):
        """The Normal scene picked at least twice around now (+-1 h) in the last 2 weeks, or None."""
        d = getattr(self, "_normal", None) or self._normal_load()
        mod = _minute_of_day(now)
        c = Counter(s["s"] for s in d["scenes"] if now - s["t"] < 14 * 86400 and _near(s["m"], mod, 60))
        best = c.most_common(1)
        return best[0][0] if best and best[0][1] >= 2 else None

    def normal_forget(self):
        self._normal_save({"prefs": [], "scenes": []})

    # ---------- doing ----------
    def apply(self, mode, look, auto=False):
        """Switch to a learned mode and look through Home Assistant, remembering the previous values for Undo.
        Comfort and Flash are never touched; automatic switching never raises the brightness."""
        ms = self.ms
        look = look or {}
        changes = []
        if mode == "party":
            changes += [("input_select.light_sync_colors", look.get("colours")), ("input_select.music_style", look.get("movement"))]
            if look.get("colours") == "mine" and len(look.get("my_colours") or []) >= 2:
                changes.append(("input_text.light_sync_my_colors", ",".join(look["my_colours"])))
            for k, eid in (("pulse_tvd", "input_boolean.light_sync_pulse_screen"), ("pulse_corner", "input_boolean.light_sync_pulse_beat"),
                           ("pulse_top", "input_boolean.light_sync_pulse_wash"), ("reaction_auto", "input_boolean.light_sync_reaction_auto"),
                           ("auto_dj", "input_boolean.light_sync_auto_dj")):
                if k in look:
                    changes.append((eid, look[k]))
        elif mode == "ambient":
            changes.append(("input_boolean.tv_music", look.get("tv_music")))
        elif mode == "normal" and look.get("normal_scene"):
            changes.append(("input_select.light_sync_normal_scene", look.get("normal_scene")))
        if mode == "party" and look.get("bright_max"):
            bmax, bmin = int(look["bright_max"]), int(look.get("bright_min", 10))
            if auto:
                bmax = min(bmax, int(ms.TUNE["bright_max"]))
                bmin = min(bmin, max(5, bmax - 5))
            changes += [("input_number.light_sync_bright_min", bmin), ("input_number.light_sync_bright_max", bmax)]
        changes = [(e, v) for e, v in changes if v is not None]
        changes += ([("input_boolean.tv_ambient", False)] if mode == "party" else []) + [(SWITCH[mode], True)]
        undo = []
        for eid in dict.fromkeys([e for e, _ in changes] + list(ms.MODE_SWITCHES)):
            try:
                undo.append((eid, ms.ha_state(eid)["state"]))
            except Exception:
                pass
        known = {e for e, _ in undo}
        for eid, value in changes:
            if eid in known:
                ms.set_helper(eid, value)
        ms.UNDO = [u for u in undo if u[0] not in ms.MODE_SWITCHES] + [u for u in undo if u[0] in ms.MODE_SWITCHES]

    def request(self, data):
        """POST /vibe {"foryou": "get" | "use"} from the app."""
        if data.get("foryou") == "use":
            s = self.suggestion()
            if not s:
                return {"error": "nothing learned for this time yet"}
            self.apply(s["mode"], s["look"])
            print(f"For you: {s['label']} ({s['why']})", flush=True)
            return {"ok": True, "summary": f"{s['label']}: your usual now."}
        s = self.suggestion()
        st = self.stats()
        return {"ok": True, "suggestion": s, "most": st["modes"][:3], "hours": st["hours"], "summary": self.ai_summary(),
                "auto_at": self.book.get("auto_at", 0.0)}

    def maybe_auto(self, mode, now):
        ms = self.ms
        try:
            if ms.ha_state(AUTO_HELPER)["state"] != "on":
                return
            s = self.suggestion(now)
            if not s or s["recent"] < 3 or s["strength"] < 0.6 or s["mode"] == mode or s["mode"] == "gaming":
                return
            if mode == "gaming" or now - self.book.get("auto_at", 0.0) < 90 * 60:
                return
            if now - self.changed_at < 30 * 60 and not self.changed_by_auto:
                return  # a choice made in the last 30 minutes wins
            if ms.ha_state("light.chambre_lights")["state"] != "on":
                return  # never switches the lights on
            if s["mode"] == "ambient" and ms.ha_state("media_player.x96q_pro_plus")["state"] in ("off", "unavailable", "standby"):
                return  # TV mode only with the TV box on
        except Exception:
            return
        self.apply(s["mode"], s["look"], auto=True)
        self.book["auto_at"] = now
        self.pending_auto = s["mode"]
        json.dump(self.book, open(BOOK, "w", encoding="utf-8"))
        ms.STATE["learned"] = {"text": f"Switched to {s['label']} by itself: your usual at this time ({s['why']}).",
                               "at": now}
        print(f"Habits: switched to {s['label']} by itself ({s['why']})", flush=True)

    # ---------- every minute ----------
    def tick(self):
        now = time.time()
        mode = self.mode_now()
        if mode is None:
            return
        if mode != self.last_mode:  # a new mode: by the owner, or by an automatic switch
            self.changed_at, self.changed_by_auto = now, mode == self.pending_auto
            self.last_mode, self.pending_auto = mode, None
        if mode != "off":
            self.append(now, mode, self.look_now())
        self.maybe_auto(mode, now)

    def loop(self):
        self.seed()
        self.load()
        while True:
            time.sleep(60 - time.time() % 60 + 1)
            try:
                self.tick()
            except Exception as e:
                print(f"Habits: {e}", flush=True)


def start(ms):
    h = Habits(ms)
    threading.Thread(target=h.loop, daemon=True).start()
    return h
