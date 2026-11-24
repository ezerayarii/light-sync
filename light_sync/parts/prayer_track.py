"""The rak'a tracker (Nov 8, owner: "check my prayer with the camera to confirm I did it right, a tracking system for
each rak'a - sometimes I forget where I was"). The room camera as numbers only, like Room guard: each picture becomes a
small grey grid in memory and is dropped; nothing is saved or sent, only the counts. (Dev mode: the owner's own live
view in the app, with the tracker's reading on top - the owner's choice, Nov 8.)

How: Start in the app -> 5 s to step out of the camera's view (the empty room, as numbers) -> pray.
AUTO (Nov 8, owner: "more auto"): once the empty room is known (the first tracked prayer, then kept up to date whenever
nobody is in view), during each prayer's time it watches by itself and starts counting when someone stands still and
then bows or prostrates - no tap, no stepping aside. Start skips stepping aside too while the empty room is fresh.
DEV (owner: "see what the camera sees, as dev mode"): the app shows the live picture with the tracker's reading on top.

Every picture, the part of the grid that differs from the empty room is the person; its height against the standing
height (learned in the first seconds, takbir) says the position:
  HIGH  standing (qiyam, i'tidal)      MID  bowing (ruku) or sitting (jalsa, tashahhud)      LOW  prostrating (sujood)
A position counts once held 0.6 s. The order tells ruku from sitting: MID after standing = ruku; MID after a sujood =
sitting. Each rak'a: standing, ruku, standing up, sujood, sitting, sujood; the next rak'a starts when standing again.
The check at the end: the rak'at counted against the prayer's (Fajr 2, Maghrib 3, the others 4), each with a ruku and
two sujood held at least ~1.5 s (calm, tuma'nina), the middle tashahhud (3 and 4 rak'a prayers) and the last one.
A camera can be wrong (a blanket, the light changing, the angle): it's a helper for remembering, not a judge."""
import threading
import time

import numpy as np

from parts import config, eyes

GRID = (96, 72)          # columns x rows of the grey grid (numbers only)
DIFF = 0.45              # a cell differs from the empty room past this (in the picture's own contrast units)
HOLD_S = 0.6             # a position counts once held this long
CALM_S = 1.5             # ruku / sujood held at least this long looks calm (tuma'nina)
QIYAM_S = 3.0            # standing this long after a rak'a's sujood part: the next rak'a has begun (Nov 8)
BLOCK_S = 5.0            # a rak'a's sujood part (sujood, sitting, sujood) is down at least this long
HIGH, MID = 0.78, 0.47   # height / standing height: above HIGH standing, above MID bowing or sitting, below prostrating
STEP_ASIDE_S = 5
END_AFTER_S = 25         # the last tashahhud done and the person gone or standing this long: finished
BG_FRESH_S = 6 * 3600    # the empty room this recent (and the light about the same): Start skips stepping aside
BG_REFRESH_S = 20        # nobody in view this long: the empty room is updated
STILL_S = 3              # standing still this long in a prayer's time: maybe takbir (auto)
CANDIDATE_S = 90         # no ruku or sujood this long after that: not a prayer, forget it
STATIC_S = 40            # a changed part that doesn't move at all this long (a prayer rug put down, a chair moved) joins
#                          the empty room (Nov 8, owner: the rug goes down before praying)
GONE_RATIO = 0.3         # under this (of the standing height) it's not the owner: their sujood measured ~0.46 (Nov 8)
MIN_STAND = 12           # a standing person is at least this many cells tall (a flat rug never counts as standing)

T = {"phase": "idle", "prayer": None, "expected": 0, "rakaa": 0, "posture": None, "msg": "", "result": None,
     "started": 0.0, "countdown": 0, "auto": False}
ENG = {"bg": None, "bg_at": 0.0, "bg_light": None, "dev_until": 0.0, "dev": {}, "done": set(), "thread": None}
STOP = {"flag": False}
LOCK = threading.Lock()


def grid(img):
    """(the picture as a grid evened out, its plain brightness 0-255)."""
    g = img.mean(axis=2) if img.ndim == 3 else img.astype(float)
    h, w = g.shape
    cw, ch = GRID
    g = g[:h - h % ch, :w - w % cw].reshape(ch, (h - h % ch) // ch, cw, (w - w % cw) // cw).mean(axis=(1, 3))
    # evened out with the median and the middle half's spread (a person barely moves those; the mean and the standard
    # deviation moved with someone bright in view, and the whole picture looked changed - Nov 8 simulation)
    p25, med, p75 = np.percentile(g, (25, 50, 75))
    return (g - med) / ((p75 - p25) / 1.35 + 4.0), float(g.mean())


def changed(g, bg):
    """The cells that differ from the empty room. The whole picture's shift is taken out first (the median difference):
    evening out each picture moves every cell when someone bright walks in or the light changes (the simulation's
    whole-frame box, Nov 8)."""
    d = g - bg
    return np.abs(d - np.median(d)) > DIFF


def person_box(g, bg):
    """(top row, bottom row, left col, right col, cells) of the part that differs from the empty room, or None."""
    m = changed(g, bg)
    rows, cols = m.sum(axis=1), m.sum(axis=0)
    rr, cc = np.nonzero(rows >= 2)[0], np.nonzero(cols >= 2)[0]
    if len(rr) < 3 or len(cc) < 2 or m.sum() < 12:
        return None
    return int(rr[0]), int(rr[-1]), int(cc[0]), int(cc[-1]), int(m.sum())


def frame():
    with LOCK:
        return {k: T.get(k) for k in ("phase", "prayer", "expected", "rakaa", "posture", "msg", "result", "countdown", "auto",
                                       "events", "t0")}


def dev_frame():
    """Dev mode (the app polls it while open): what the tracker reads now. Keeps the camera on for 60 s."""
    ENG["dev_until"] = time.time() + 60
    ensure()
    with LOCK:
        d = dict(ENG["dev"])
    d["track"] = frame()
    d["bg_age_s"] = round(time.time() - ENG["bg_at"]) if ENG["bg"] is not None else None
    d["grid"] = GRID
    d["levels"] = {"high": HIGH, "mid": MID}
    return d


def say(**kw):
    with LOCK:
        T.update(**kw)


def stop():
    STOP["flag"] = True


def bg_fresh(light=None):
    return (ENG["bg"] is not None and time.time() - ENG["bg_at"] < BG_FRESH_S
            and (light is None or ENG["bg_light"] is None or abs(light - ENG["bg_light"]) < 25))


def start(prayer, expected, auto=False):
    """Start tracking (the app's Start). With a fresh empty room: no stepping aside."""
    with LOCK:
        if T["phase"] in ("aside", "tracking"):
            return False
        fresh = bg_fresh()
        T.update(events=[], t0=time.time())
        T.update(phase="tracking" if fresh else "aside", prayer=prayer, expected=int(expected), rakaa=1 if fresh else 0,
                 posture="waiting for you" if fresh else None, result=None, auto=auto,
                 msg="Pray - I'm following" if fresh else "Step out of the camera's view", started=time.time(),
                 countdown=0 if fresh else STEP_ASIDE_S)
    STOP["flag"] = False
    ensure()
    return True


def ensure():
    if ENG["thread"] is None or not ENG["thread"].is_alive():
        ENG["thread"] = threading.Thread(target=engine, daemon=True, name="prayer tracker")
        ENG["thread"].start()


def open_stream():
    url = config.ROOM_CAM_URL or config.SNAPSHOT_URL
    headers, auth, stream_url = None, None, url
    if "/api/camera_proxy/" in url:  # the Pi: straight to Home Assistant with the camera's own key (streams)
        from parts import ha_link
        eid = url.rsplit("/", 1)[-1]
        tok = ha_link.ha_state(eid)["attributes"]["access_token"]
        stream_url = url = f"http://127.0.0.1:8123/api/camera_proxy_stream/{eid}?token={tok}"
    else:
        auth = eyes.stream_auth(url)
    return eyes.CamStream(stream_url, url, auth, headers)


def prayer_window():
    """(prayer, expected rak'at) when a prayer's time has begun and it isn't tracked yet today; else (None, 0)."""
    try:
        from parts import prayer
        name = prayer.current_prayer()
        t = prayer.today()
        if not t or name not in prayer.RAKAAT or time.time() < t.get(name, 1e18):
            return None, 0
        if f"{time.strftime('%Y-%m-%d')}:{name}" in ENG["done"]:
            return None, 0
        return name, prayer.RAKAAT[name]
    except Exception:
        return None, 0


def mask_rows(g, bg):
    m = changed(g, bg)
    return ["".join("1" if v else "0" for v in row) for row in m[::2, ::2]]  # half size, for the dev view


def finish_session(sess):
    rakaat, last = sess["c"].finished()
    finish(rakaat, sess["c"].mid_tash, last)


def engine():
    """One loop for everything: Start (manual), auto in a prayer's time, dev mode. The camera only while needed."""
    st, last_at, opened_at = None, 0.0, 0.0
    empties, nobody_since = [], None
    prev_g, still, prev_t = None, None, None  # how long each changed cell hasn't moved (STATIC_S)
    sess = None            # {"c": Counter, "stand": [heights], "stand_h": float, "lost": t}
    cand = None            # auto: someone standing still in a prayer's time
    try:
        while True:
            now = time.time()
            phase = T["phase"]
            win, win_rak = prayer_window() if not config.STANDBY else (None, 0)
            needed = phase in ("aside", "tracking") or now < ENG["dev_until"] or (win and ENG["bg"] is not None)
            if not needed:
                if st is not None:
                    st.stop()
                    st = None
                cand = None
                time.sleep(2)
                continue
            if st is None:
                st = open_stream()
                last_at, opened_at = 0.0, now
            # no picture for 6 s: open again with a fresh key (Home Assistant changes the camera's key every few minutes,
            # and a stream that dropped came back with the old one: refused, no pictures, the countdown froze - Nov 8)
            if now - max(last_at, opened_at) > 6:
                print("Prayer tracker: no picture for 6 s - opening the camera again", flush=True)
                st.stop()
                st = None
                continue
            if STOP["flag"]:  # Stop in the app
                STOP["flag"] = False
                if phase == "tracking" and sess:
                    finish_session(sess)
                else:
                    say(phase="idle", msg="")
                sess = None
                continue
            if phase == "aside":  # the countdown runs on the clock, not on pictures
                left = STEP_ASIDE_S - (now - T["started"])
                if left > 0:
                    say(countdown=int(left) + 1)
            if not (st.at > last_at and st.jpg):
                time.sleep(0.03)
                continue
            last_at = st.at
            img = eyes.decode_bgr(st.jpg)
            if img is None:
                continue
            g, light = grid(img)
            # 1. Start without a fresh empty room: 5 s to step aside, then the empty room
            if phase == "aside":
                left = STEP_ASIDE_S - (now - T["started"])
                if left > 0:
                    say(countdown=int(left) + 1)
                    empties = []
                else:
                    empties.append(g)
                    if len(empties) >= 6:
                        ENG.update(bg=np.median(np.array(empties), axis=0), bg_at=now, bg_light=light)
                        say(phase="tracking", msg="Pray - I'm following", countdown=0, posture="waiting for you", rakaa=1)
                        sess = None
                continue
            bg = ENG["bg"]
            if bg is not None and phase != "tracking" and not (cand and cand.get("stand_h")):
                # still things join the empty room (the rug) - but never during a prayer: the owner standing still
                # for 40 s in qiyam became part of it, and their "ghost" read as standing for 3 rak'at (Nov 8, Isha)
                if prev_g is not None and still is not None and prev_g.shape == g.shape:
                    dt = min(1.0, now - prev_t)
                    calm = (np.abs(g - prev_g) < 0.25) & changed(g, bg)
                    still = np.where(calm, still + dt, 0.0)
                    absorb = still > STATIC_S
                    if absorb.any():
                        bg = bg.copy()
                        bg[absorb] = g[absorb]
                        ENG["bg"] = bg
                        still[absorb] = 0.0
                elif still is None or still.shape != g.shape:
                    still = np.zeros_like(g)
                prev_g, prev_t = g, now
            box = person_box(g, bg) if bg is not None else None
            size = (sess or {}).get("size") or (cand or {}).get("size")
            if box and size and box[4] < 0.25 * size:
                box = None  # much smaller than the person standing: a bit of rug just uncovered, not someone (Nov 8)
            # height = from the top of the person (head / back) down to where the feet were when standing (fixed then):
            # a prayer rug put down after the empty room was learned is "changed" along the floor and stretched the
            # box downwards (Nov 8, owner: "I put my rug on the floor before I pray"); the top never moves for a rug
            foot = (sess or {}).get("foot") or (cand or {}).get("foot")
            h = ((foot if foot is not None else box[1]) - box[0] + 1) if box else 0
            # the empty room kept up to date while nobody is in view (and not praying)
            if bg is not None and box is None and phase != "tracking":
                nobody_since = nobody_since or now
                if now - nobody_since > BG_REFRESH_S:
                    ENG.update(bg=bg * 0.8 + g * 0.2, bg_at=now, bg_light=light)
            else:
                nobody_since = None
            # 2. tracking (Start, or found by itself)
            if phase == "tracking":
                if sess is None:
                    sess = {"c": Counter(T["expected"]), "stand": [], "stand_h": None, "foot": None, "lost": None}
                c = sess["c"]
                if box is not None and sess["stand_h"] and h / sess["stand_h"] < GONE_RATIO:
                    box = None  # lower than anyone praying (sujood is ~0.46): the rug or a bag left behind, not the owner
                if box is None:
                    sess["lost"] = sess["lost"] or now
                    if (c.rakaat or c.rk["stage"] != "qiyam") and now - sess["lost"] > END_AFTER_S:
                        finish_session(sess)
                        sess = None
                elif sess["stand_h"] is None:
                    sess["lost"] = None
                    sess["stand"].append((box[0], box[1]))
                    sess.setdefault("cells", []).append(box[4])
                    if len(sess["stand"]) >= 8 and np.median([b_ - t_ + 1 for t_, b_ in sess["stand"]]) < MIN_STAND:
                        sess["stand"] = sess["stand"][-4:]  # too flat to be someone standing (a rug): wait
                    elif len(sess["stand"]) >= 8:
                        tops, feet = zip(*sess["stand"])
                        sess["foot"] = int(np.median(feet))
                        sess["size"] = float(np.median(sess["cells"])) if sess.get("cells") else None
                        sess["stand_h"] = float(sess["foot"] - np.median(tops) + 1)
                        say(posture="standing")
                else:
                    sess["lost"] = None
                    c.feed(now, h / sess["stand_h"])
                    say(rakaa=c.rakaa_now(), posture=c.posture, events=c.events[-120:])
                    done, _ = c.finished()
                    if (len(done) >= c.expected and c.stable == "HIGH" and now - c.since > END_AFTER_S) or now - T["started"] > 1800:
                        finish_session(sess)
                        sess = None
            # 3. auto: a prayer's time, the empty room known, someone stands still -> watch for ruku / sujood
            elif win and box is not None:
                if cand is None or cand.get("prayer") != win:
                    cand = {"prayer": win, "heights": [], "since": now, "stand_h": None, "c": None, "foot": None}
                if cand["stand_h"] is None:
                    cand["heights"] = (cand["heights"] + [(now, box[0], box[1])])[-60:]
                    recent = [(tp, bt) for t_, tp, bt in cand["heights"] if now - t_ <= STILL_S]
                    hs = [bt - tp + 1 for tp, bt in recent]
                    if (len(recent) >= 5 and now - cand["heights"][0][0] >= STILL_S and float(np.median(hs)) >= MIN_STAND
                            and max(hs) - min(hs) <= max(2, 0.08 * float(np.median(hs)))):
                        foot = int(np.median([bt for _, bt in recent]))
                        cand.update(stand_h=float(foot - np.median([tp for tp, _ in recent]) + 1), foot=foot,
                                    size=float(box[4]), c=Counter(win_rak), since=now)
                else:
                    c = cand["c"]
                    c.feed(now, h / cand["stand_h"])
                    if c.rk["stage"] != "qiyam" or c.rakaat:
                        # it is a prayer: tracked from here (what was counted already counts)
                        with LOCK:
                            T.update(phase="tracking", prayer=win, expected=win_rak, rakaa=c.rakaa_now(), posture=c.posture,
                                     events=c.events[-120:], t0=cand["since"],
                                     result=None, auto=True, msg="Started by itself - I'm following", started=cand["since"],
                                     countdown=0)
                        sess = {"c": c, "stand": [], "stand_h": cand["stand_h"], "foot": cand["foot"], "size": cand["size"], "lost": None}
                        print(f"Prayer tracker: {win} started by itself", flush=True)
                        cand = None
                    elif now - cand["since"] > CANDIDATE_S:
                        cand = None
            elif box is None and cand and now - cand["since"] > CANDIDATE_S:
                cand = None
            # dev mode: what it reads
            if now < ENG["dev_until"]:
                ref = (sess or {}).get("stand_h") or (cand or {}).get("stand_h")
                r = (h / ref) if (box and ref) else None
                with LOCK:
                    ENG["dev"] = {"at": now, "box": box, "height": h, "stand_h": ref, "ratio": round(r, 2) if r else None,
                                  "pos": None if r is None else ("HIGH" if r >= HIGH else "MID" if r >= MID else "LOW"),
                                  "light": round(light), "bg": bg is not None,
                                  "mask": mask_rows(g, bg) if bg is not None else None,
                                  "auto": {"window": win, "watching": bool(cand and cand.get("stand_h"))}}
    except Exception as e:
        print(f"Prayer tracker: {e}", flush=True)
        say(phase="idle", msg=f"Stopped: {e}")
    finally:
        if st is not None:
            st.stop()
        ENG["thread"] = None


class Counter:
    """Positions (HIGH / MID / LOW, held HOLD_S) -> rak'at. feed(now, ratio) with height / standing height.

    Nov 8, the owner's first real Isha (4 rak'at): from this camera standing is very clear, ruku clear, but sujood and
    sitting are nearly the same height (1-4 grid rows apart). So rak'at are counted by STANDING: a rak'a ends when the
    owner stands again for QIYAM_S after its sujood part (BLOCK_S or more down after the ruku). The short standing-up
    after ruku (i'tidal) doesn't end a rak'a, and an i'tidal the camera misses doesn't matter either. Sujood and
    sitting inside that part are counted as well as the heights allow (best effort, in the summary)."""

    def __init__(self, expected):
        self.expected = expected
        self.raw, self.stable, self.prev, self.since = None, None, None, 0.0
        self.rk = self.new_rakaa()
        self.rakaat, self.mid_tash = [], False
        self.posture = "standing"
        self.last_t = None
        self.events = []      # Nov 8 (owner: "track each movement, see the script live"): every movement, as it happens
        self._r = 1.0

    @staticmethod
    def new_rakaa():
        # stage: qiyam -> ruku -> itidal -> block (sujood, sitting, sujood); down = seconds down since the ruku ended
        return {"ruku": None, "sujood": [], "itidal": False, "sat": 0.0, "stage": "qiyam", "down": 0.0, "up": 0.0,
                "minr": None, "dip": False}

    def feed(self, now, r):
        dt = 0.0 if self.last_t is None else min(1.0, now - self.last_t)
        self.last_t = now
        self._now, self._r = now, r
        pos = "HIGH" if r >= HIGH else "MID" if r >= MID else "LOW"
        rk = self.rk
        # time down after the ruku / up again (on every picture, not only on changes)
        if rk["stage"] in ("itidal", "block") or (rk["stage"] == "ruku" and rk["ruku"] is not None):
            if pos == "HIGH":
                rk["up"] += dt
            else:
                rk["up"] = 0.0
                if rk["stage"] != "ruku":
                    rk["down"] += dt
        # sujood, adapted to this camera (best effort): a dip near the lowest point of this rak'a's sujood part,
        # a new one after rising at least 0.05 (sujood measured 0.46, sitting 0.49-0.59 on the owner's camera)
        if rk["stage"] == "block" and r < HIGH:
            rk["minr"] = r if rk["minr"] is None else min(rk["minr"], r)
            if r <= rk["minr"] + 0.025:
                if not rk["dip"]:
                    rk["dip"] = True
                    rk["sujood"].append(0.0)
                    self.move(f"sujood {len(rk['sujood'])}")
                else:
                    rk["sujood"][-1] += dt
            elif r > rk["minr"] + 0.05 and rk["dip"]:
                rk["dip"] = False
                self.move("sitting")
                if rk["sujood"] and rk["sujood"][-1] < 0.6:  # a flicker, not a sujood
                    rk["sujood"].pop()
        if pos != self.raw:
            self.raw, self.since = pos, now
            return
        held = now - self.since
        if held < HOLD_S:
            return
        if pos == self.stable:  # still there: how long it's been held
            if pos == "MID" and rk["stage"] == "ruku":
                rk["ruku"] = held
                if held > 8:  # a long "ruku" with no standing up seen: the i'tidal was missed, this is the sujood part
                    rk["stage"] = "block"
            elif pos == "MID" and rk["stage"] == "block" and rk["sujood"]:
                rk["sat"] = held
            if pos == "HIGH" and rk["stage"] == "block" and held >= QIYAM_S and rk["down"] >= BLOCK_S:
                self.next_rakaa()
            return
        self.prev, self.stable = self.stable, pos
        if pos != "HIGH" and rk["stage"] == "qiyam":
            rk["stage"], rk["ruku"] = "ruku", 0.0
            self.move("ruku")
        elif pos == "LOW" and rk["stage"] == "ruku":
            # straight from ruku down to sujood: the i'tidal wasn't seen (the camera, or quick) - the sujood part starts
            rk["stage"] = "block"
            self.move("going down")
        elif pos == "HIGH" and rk["stage"] == "ruku":
            rk["stage"], rk["itidal"] = "itidal", True
            self.move("standing up (i'tidal)")
        elif pos != "HIGH" and rk["stage"] == "itidal":
            rk["stage"] = "block"
            self.move("going down")
        elif rk["stage"] == "block" and pos == "HIGH":
            self.move("getting up")
        elif pos == "HIGH":
            self.move("standing")

    def move(self, name):
        """A movement: the posture shown, and a line in the live log (time, rak'a, movement, height ratio)."""
        if name == self.posture and self.events:
            return
        self.posture = name
        if name == "sitting" and len(self.rk["sujood"]) >= 2:
            name = "tashahhud"
            self.posture = name
        self.events = (self.events + [{"t": round(getattr(self, "_now", 0.0), 1), "rakaa": self.rakaa_now(), "move": name,
                                       "r": round(self._r, 2)}])[-300:]

    def next_rakaa(self):
        rk = self.rk
        self.rakaat.append(rk)
        if len(self.rakaat) == 2 and self.expected >= 3:
            self.mid_tash = rk["down"] >= 12  # two sujood and a tashahhud: the second rak'a's part is longer
        self.rk = self.new_rakaa()
        self.move("standing")

    def rakaa_now(self):
        """The rak'a being prayed now (1-based)."""
        return len(self.rakaat) + 1

    def finished(self):
        """All rak'at (the last one ends sitting): the list, and the last sitting's length."""
        out = list(self.rakaat)
        if self.rk["stage"] != "qiyam":
            out.append(self.rk)
        return out, self.rk["down"] if self.rk["stage"] == "block" else 0.0


def finish(rakaat, mid_tash, last_tash_s):
    exp, name = T["expected"], (T["prayer"] or "prayer").capitalize()
    notes = []
    for i, r in enumerate(rakaat, 1):
        if r["ruku"] is None:
            notes.append(f"rak'a {i}: no ruku seen")
        elif r["ruku"] < CALM_S:
            notes.append(f"rak'a {i}: ruku short ({r['ruku']:.1f} s)")
        if r["stage"] != "block" and i < len(rakaat):
            notes.append(f"rak'a {i}: no sujood part seen")
        elif r["down"] < BLOCK_S and i < len(rakaat):
            notes.append(f"rak'a {i}: the sujood part looked short ({r['down']:.0f} s)")
    if exp >= 3 and len(rakaat) >= 2 and not mid_tash:
        notes.append("the middle tashahhud looked short or wasn't seen")
    if rakaat and last_tash_s < 6:
        notes.append("the last sitting looked short or wasn't seen")
    n = len(rakaat)
    head = f"{name}: {n} rak'a{'t' if n != 1 else ''} of {exp} (sujood seen: {sum(len(r['sujood']) for r in rakaat)}, best effort)"
    ok = n == exp and not notes
    verdict = "looked complete" if ok else ("fewer rak'at than expected" if n < exp else "more rak'at than expected" if n > exp else "check these")
    auto = " (started by itself)" if T.get("auto") else ""
    text = f"{head}{auto} - {verdict}." + ("" if not notes else "\n" + "\n".join(f"- {x}" for x in notes[:6])) + \
        "\n(The camera can be wrong: a helper, not a judge.)"
    say(phase="done", result={"rakaat": n, "expected": exp, "ok": ok, "notes": notes, "text": text}, msg=verdict, posture=None)
    ENG["done"].add(f"{time.strftime('%Y-%m-%d')}:{T['prayer']}")  # tracked: auto won't start it again today
    print(f"Prayer tracker: {head}{auto} - {verdict}", flush=True)
    if not config.HA_TOKEN:  # no key (a test on the laptop): don't knock on Home Assistant's door (Nov 8: 9 failed logins)
        return
    try:
        from parts import ha_link
        from parts.config import HA_URL
        ha_link.session.post(f"{HA_URL}/api/services/script/notify_me", timeout=5,
                             json={"title": "Prayer", "message": text, "level": "info", "topic": "prayer"})
    except Exception:
        pass
