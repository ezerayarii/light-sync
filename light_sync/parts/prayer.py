"""Prayer (Nov 8, owner: "the app for a Muslim: the prayer times, check my prayer with the camera to confirm I did it
right, a tracker for each rak'a - sometimes I forget where I was; and Fajr and Maghrib start Normal").

TIMES      calculated here every day from the home's location in Home Assistant (no internet): ISNA (Fajr and Isha at
           15 degrees), standard Asr (shadow = 1x), Maghrib at sunset, Dhuhr just after noon. The owner's choice.
FAJR       Normal starts very dim (1 %) at Fajr and rises over 10 min (a soft wake for the prayer), if no mode is on and
           the owner isn't away. Once a day.
MAGHRIB    the evening's "dark now" is Maghrib (it was the sun 3 degrees down): Normal comes on once an evening, the first
           moment the owner is in the room with no mode on and the lights off (guard.py).
TRACKER    prayer_track.py: the room camera as numbers follows standing / bowing / prostrating / sitting and counts the
           rak'at; the app shows it live, Telegram gets a short summary after."""
import math
import threading
import time

from parts.config import HA_URL
from parts import config, ha_link, web

NAMES = ("fajr", "sunrise", "dhuhr", "asr", "maghrib", "isha")
TITLES = {"fajr": "Fajr", "sunrise": "Sunrise", "dhuhr": "Dhuhr", "asr": "Asr", "maghrib": "Maghrib", "isha": "Isha"}
RAKAAT = {"fajr": 2, "dhuhr": 4, "asr": 4, "maghrib": 3, "isha": 4}
FAJR_ANGLE, ISHA_ANGLE, ASR_FACTOR = 15.0, 15.0, 1  # ISNA, standard Asr
DHUHR_AFTER_MIN = 1
SOFT_WAKE_S = 600
PLACE = {"lat": None, "lon": None, "at": 0.0}
TODAY = {"date": "", "times": {}}
DONE = {"fajr": "", "maghrib_seen": ""}


def _sun(jd):
    """(declination degrees, equation of time hours) at this Julian date."""
    d = jd - 2451545.0
    g = math.radians((357.529 + 0.98560028 * d) % 360)
    q = (280.459 + 0.98564736 * d) % 360
    lam = math.radians((q + 1.915 * math.sin(g) + 0.020 * math.sin(2 * g)) % 360)
    e = math.radians(23.439 - 0.00000036 * d)
    ra = math.degrees(math.atan2(math.cos(e) * math.sin(lam), math.cos(lam))) / 15.0
    decl = math.degrees(math.asin(math.sin(e) * math.sin(lam)))
    eqt = q / 15.0 - (ra % 24)
    eqt = (eqt + 12) % 24 - 12
    return decl, eqt


def _hour_angle(angle, lat, decl):
    """Hours from noon until the sun is `angle` degrees below the horizon (None: it never gets there)."""
    lat, d = math.radians(lat), math.radians(decl)
    x = (-math.sin(math.radians(angle)) - math.sin(d) * math.sin(lat)) / (math.cos(d) * math.cos(lat))
    return None if abs(x) > 1 else math.degrees(math.acos(x)) / 15.0


def _asr_angle(lat, decl, factor):
    x = math.degrees(math.atan(1.0 / (factor + math.tan(abs(math.radians(lat - decl))))))
    return -x  # the sun this high above the horizon


def times_for(date_tuple, lat, lon):
    """{name: epoch seconds} for this local date (year, month, day)."""
    y, m, d = date_tuple
    noon = time.mktime((y, m, d, 12, 0, 0, 0, 0, -1))
    tz = time.localtime(noon).tm_gmtoff / 3600.0
    jd0 = 367 * y - (7 * (y + (m + 9) // 12)) // 4 + (275 * m) // 9 + d + 1721013.5  # 0h UT of the date
    out = {}
    guess = {"fajr": 5, "sunrise": 6, "dhuhr": 12, "asr": 15, "maghrib": 18, "isha": 19}
    for name in NAMES:
        t = guess[name]
        for _ in range(3):  # the sun moves during the day: work it out again at the time found
            decl, eqt = _sun(jd0 + (t - tz) / 24.0)
            dhuhr = 12 + tz - lon / 15.0 - eqt
            if name == "dhuhr":
                t = dhuhr
            elif name == "fajr":
                h = _hour_angle(FAJR_ANGLE, lat, decl)
                t = dhuhr - h if h is not None else t
            elif name == "sunrise":
                t = dhuhr - _hour_angle(0.833, lat, decl)
            elif name == "maghrib":
                t = dhuhr + _hour_angle(0.833, lat, decl)
            elif name == "isha":
                h = _hour_angle(ISHA_ANGLE, lat, decl)
                t = dhuhr + h if h is not None else t
            elif name == "asr":
                t = dhuhr + _hour_angle(_asr_angle(lat, decl, ASR_FACTOR), lat, decl)
        if name == "dhuhr":
            t += DHUHR_AFTER_MIN / 60.0
        out[name] = time.mktime((y, m, d, 0, 0, 0, 0, 0, -1)) + t * 3600
    return out


def place():
    if PLACE["lat"] is None or time.time() - PLACE["at"] > 86400:
        try:
            c = ha_link.session.get(f"{HA_URL}/api/config", timeout=5).json()
            PLACE.update(lat=float(c["latitude"]), lon=float(c["longitude"]), at=time.time())
        except Exception:
            pass
    return PLACE["lat"], PLACE["lon"]


def today(now=None):
    now = now or time.time()
    lt = time.localtime(now)
    key = time.strftime("%Y-%m-%d", lt)
    if TODAY["date"] != key or not TODAY["times"]:
        lat, lon = place()
        if lat is None:
            return {}
        TODAY.update(date=key, times=times_for((lt.tm_year, lt.tm_mon, lt.tm_mday), lat, lon))
    return TODAY["times"]


def next_prayer(now=None):
    """(name, epoch) of the next prayer (sunrise isn't one); after Isha: tomorrow's Fajr."""
    now = now or time.time()
    t = today(now)
    for n in ("fajr", "dhuhr", "asr", "maghrib", "isha"):
        if t.get(n, 0) > now:
            return n, t[n]
    lat, lon = place()
    if lat is None:
        return None, None
    tm = time.localtime(now + 86400)
    return "fajr", times_for((tm.tm_year, tm.tm_mon, tm.tm_mday), lat, lon)["fajr"]


def current_prayer(now=None):
    """The prayer whose time it is now (the last one that began), for the tracker's default; Fajr until sunrise."""
    now = now or time.time()
    t = today(now)
    cur = "isha"
    for n in ("fajr", "sunrise", "dhuhr", "asr", "maghrib", "isha"):
        if t.get(n, 1e18) <= now:
            cur = n
    return "dhuhr" if cur == "sunrise" else cur


def after_maghrib(now=None):
    """Evening dark for the room (guard.py's once-an-evening Normal): from Maghrib, or before Fajr."""
    now = now or time.time()
    t = today(now)
    if not t:
        return None
    return now >= t["maghrib"] or now < t["fajr"]


def frame(now=None):
    """For the app: today's times, the next one, and the tracker."""
    now = now or time.time()
    t = today(now)
    nxt, at = next_prayer(now)
    from parts import prayer_track
    return {"times": {n: time.strftime("%H:%M", time.localtime(v)) for n, v in t.items()},
            "next": nxt, "next_at": at, "now": now, "current": current_prayer(now), "rakaat": RAKAAT,
            "method": "ISNA (15 degrees), standard Asr", "track": prayer_track.frame()}


def text(now=None):
    now = now or time.time()
    t = today(now)
    if not t:
        return "Prayer times: I don't know the home's location yet."
    nxt, at = next_prayer(now)
    lines = [f"Prayer times today (ISNA, standard Asr):"]
    for n in NAMES:
        mark = "  <- next" if n == nxt and abs(t[n] - at) < 60 else ""
        lines.append(f"{TITLES[n]:8s} {time.strftime('%H:%M', time.localtime(t[n]))}{mark}")
    if nxt:
        mins = round((at - now) / 60)
        lines.append(f"\nNext: {TITLES[nxt]} in {mins // 60} h {mins % 60} min" if mins >= 60 else f"\nNext: {TITLES[nxt]} in {mins} min")
    return "\n".join(lines)


def prayer_loop():
    """Fajr's soft wake (once a day)."""
    from parts import guard
    while True:
        time.sleep(20)
        if config.STANDBY:
            continue
        now = time.time()
        t = today(now)
        if not t:
            continue
        day = time.strftime("%Y-%m-%d")
        if DONE["fajr"] != day and 0 <= now - t["fajr"] < 300:
            DONE["fajr"] = day
            try:
                away = not guard.owner_home_now()  # (Nov 11: the phone on the home Wi-Fi, not the stuck location)
            except Exception:
                away = False
            if guard.mode_on() is None and not away:
                from parts import approve

                def wake():
                    config.NORMAL_RAMP = (time.time(), SOFT_WAKE_S)
                    guard.room_script("script.normal_light")
                print("Fajr: asking about the soft wake", flush=True)
                approve.ask("fajr", f"Fajr ({time.strftime('%H:%M', time.localtime(t['fajr']))}): wake gently with Normal "
                            "(1 % rising over 10 min)?", wake, timeout=1200)
            else:
                print("Fajr: " + ("you're away" if away else "a mode is on") + " - lights left as they are", flush=True)


def start():
    threading.Thread(target=prayer_loop, daemon=True, name="prayer").start()
    from parts import prayer_track
    prayer_track.ensure()  # the tracker's engine: auto in each prayer's time (the camera only while needed)
