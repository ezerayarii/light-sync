"""The home Wi-Fi now: who is on it, in memory only, and the new-device notification."""

import json
import os
import re
import sys
import time

from parts.boot import ROOT
from parts.config import DEMO, HA_URL, ROLE
from parts import config, ha_link


# going on") ----------
# Live: the known devices are checked every 10 s, every address once a minute (a tiny packet each, then the
# neighbour table). Kept in memory only: never written to disk, never sent to Home Assistant, gone when Light Sync
# stops. Only the signed-in Light Sync app sees it (POST /wifi from the Pi's app; the home-network server refuses
# anyone else).
WIFI = {}               # mac -> {"ip", "seen", "since"}; since: when it came back after WIFI_AWAY_S away (None: here at start)

WIFI_AT = 0.0           # last scan

WIFI_AWAY_S = 600       # unseen this long: gone (a sleeping phone skips some scans)

# the owner's phone on the home Wi-Fi (Nov 3, owner: "I left the house and nothing happened": the phone's location
# stopped for hours while out and said "home" all day). input_boolean.phone_on_wifi: on = the phone answers here,
# off = not seen for PHONE_AWAY_S. Only the yes / no goes to Home Assistant, never addresses or times.
# Not "away" at night (PHONE_NIGHT): a sleeping phone may stop answering in bed.
HA_PHONE_WIFI = "input_boolean.phone_on_wifi"

PHONE_AWAY_S = 1200

PHONE_NIGHT = (0, 8)

PHONE_HERE = [None]     # what Home Assistant was last told (None: nothing yet)

WIFI_LIVE_S = 45        # answered within this: online right now (else here, but quiet: asleep, or just left)

WIFI_NAMES = {          # this home's own devices
    "02:00:00:00:00:05": "TvD light", "02:00:00:00:00:06": "Corner light", "02:00:00:00:00:07": "Top light",
    "02:00:00:00:00:08": "TV box (X96Q)", "02:00:00:00:00:04": "Laptop (Light Sync)", "02:00:00:00:00:09": "Home Assistant Pi",
    "02:00:00:00:00:0a": "eero router",
}

WIFI_MAKERS = {"58:04:4f": "TP-Link smart plug", "f8:25:51": "Epson printer", "3c:9b:d6": "Vizio TV", "6c:55:b1": "Amazon device",
               "c8:c6:fe": "eero", "5c:e7:53": "Govee light"}

# Nov 7: the Pi drives now. Its program folder is replaced on every update, so it keeps these in the add-on's own
# lasting folder (/data); the laptop handed its lists over once (POST /wifi_lists). Before that the Pi had no lists:
# no new-device alerts, and it couldn't tell the owner's phone
DATA = os.path.join(ROOT, "data") if os.path.isdir(os.path.join(ROOT, "data")) or not os.path.isdir("/data") else "/data"

WIFI_OWN = os.path.join(DATA, "wifi_names.json")  # the owner's names (gitignored)

# security (Oct 20, owner: "if someone is sneaking on me"): a device this Wi-Fi has never had -> a notification on the
# owner's phone. data/wifi_known.json (gitignored, laptop only): every device ever seen here and the day it first
# came, nothing about comings and goings.
WIFI_KNOWN = os.path.join(DATA, "wifi_known.json")

WIFI_NOTIFY = os.getenv("WIFI_NOTIFY", "script/notify_me")  # Telegram, and the app when urgent (tools/ha_setup/telegram_setup.py)

WIFI_KNOWN_CACHE = None

def wifi_label(mac, own=None):
    own = wifi_names() if own is None else own
    private = int(mac[1], 16) & 2 == 2  # a private (random) address: a phone, tablet or laptop
    return (own.get(mac) or WIFI_NAMES.get(mac) or WIFI_MAKERS.get(mac[:8])
            or ("Phone, tablet or laptop" if private else "Device") + f" ·{mac[-5:].upper()}")

def wifi_new(macs):
    """The devices never seen on this Wi-Fi before, among macs (remembered from now on). The first time, everything
    here now counts as known. Only where data/ exists (the laptop)."""
    global WIFI_KNOWN_CACHE
    if not os.path.isdir(os.path.dirname(WIFI_KNOWN)):
        return []
    if WIFI_KNOWN_CACHE is None:
        try:
            WIFI_KNOWN_CACHE = json.load(open(WIFI_KNOWN, encoding="utf-8"))
        except Exception:
            WIFI_KNOWN_CACHE = {}
    seed, own = not WIFI_KNOWN_CACHE, wifi_names()
    new = [m for m in dict.fromkeys(macs) if m not in WIFI_KNOWN_CACHE and m not in WIFI_NAMES and m not in own]
    for m in list(macs) + list(own) + list(WIFI_NAMES):
        WIFI_KNOWN_CACHE.setdefault(m, time.strftime("%Y-%m-%d") if m in new else "known")  # the day a new one came
    if new or seed:
        with open(WIFI_KNOWN, "w", encoding="utf-8") as f:
            json.dump(WIFI_KNOWN_CACHE, f, indent=0)
    return [] if seed else new

def wifi_alert(mac, ip):
    """A device this Wi-Fi never had: a notification on the owner's phone."""
    name = wifi_label(mac)
    print(f"Wi-Fi: new device {name} {ip}", flush=True)
    if DEMO:
        return
    try:
        ha_link.session.post(f"{HA_URL}/api/services/{WIFI_NOTIFY}", timeout=5, json={
            "title": "New device on your Wi-Fi", "level": "urgent", "topic": "security",
            "message": f"{name} just connected ({ip}, {time.strftime('%H:%M')}). Not one your Wi-Fi has had before.",
            "buttons": [f"It's mine:/mine_{mac.replace(':', '')}", f"Not mine:/notmine_{mac.replace(':', '')}"]})
    except Exception as e:
        print(f"Wi-Fi: couldn't notify ({e})", flush=True)

def _mac(macid):
    m = (macid or "").lower().replace(":", "")
    return ":".join(m[i:i + 2] for i in range(0, 12, 2)) if len(m) == 12 else ""


def label_of(macid):
    m = _mac(macid)
    return wifi_label(m) if m else None


def mark_mine(macid):
    """Telegram "It's mine": the device gets an own name (no more alerts, counted as the owner's). Its name."""
    m = _mac(macid)
    if not m:
        return None
    own = wifi_names()
    name = own.get(m) or ("Yours " + wifi_label(m, {}).split(" ·")[0].lower() + f" ·{m[-5:].upper()}")
    own[m] = name
    try:
        os.makedirs(DATA, exist_ok=True)
        with open(WIFI_OWN, "w", encoding="utf-8") as f:
            json.dump(own, f, indent=0)
    except Exception as e:
        print(f"Wi-Fi: couldn't save the name ({e})", flush=True)
    return name


def set_lists(body):
    """The Pi: the laptop's lists, once (only those it doesn't have yet are added, nothing is removed)."""
    global WIFI_KNOWN_CACHE
    d = json.loads(body or b"{}")
    os.makedirs(DATA, exist_ok=True)
    own = wifi_names()
    for k, v in (d.get("names") or {}).items():
        own.setdefault(k.lower(), str(v)[:60])
    with open(WIFI_OWN, "w", encoding="utf-8") as f:
        json.dump(own, f, indent=0)
    try:
        known = json.load(open(WIFI_KNOWN, encoding="utf-8"))
    except Exception:
        known = {}
    for k, v in (d.get("known") or {}).items():
        known.setdefault(k.lower(), str(v)[:12])
    with open(WIFI_KNOWN, "w", encoding="utf-8") as f:
        json.dump(known, f, indent=0)
    WIFI_KNOWN_CACHE = known
    print(f"Wi-Fi: lists from the laptop ({len(own)} names, {len(known)} known devices)", flush=True)
    return {"names": len(own), "known": len(known)}


def wifi_names():
    try:
        return {k.lower(): v for k, v in json.load(open(WIFI_OWN, encoding="utf-8")).items()}
    except Exception:
        return {}

def wifi_neighbours():
    """(ip, mac) of the devices that answered lately: in the neighbour table and not given up on (Reachable, or being
    re-checked: Stale / Delay / Probe; a device that stops answering turns Unreachable within ~10 s of a check)."""
    import subprocess
    if sys.platform == "win32":  # netsh: 0.15 s (PowerShell's Get-NetNeighbor took a second to start)
        out = subprocess.run(["netsh", "interface", "ipv4", "show", "neighbors"], capture_output=True, text=True, timeout=20,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
        pairs = [(m.group(1), m.group(2)) for m in re.finditer(
            r"^(\d+\.\d+\.\d+\.\d+)\s+([0-9a-f]{2}(?:-[0-9a-f]{2}){5})\s+(Reachable|Stale|Delay|Probe)\b", out, re.M | re.I)]
    else:  # the Pi: iproute / busybox ip
        out = subprocess.run(["ip", "-4", "neigh", "show"], capture_output=True, text=True, timeout=20).stdout
        pairs = [(p[0], p[p.index("lladdr") + 1]) for p in (ln.split() for ln in out.splitlines())
                 if "lladdr" in p and p[-1] in ("REACHABLE", "STALE", "DELAY", "PROBE")]
    return [(ip, mac.lower().replace("-", ":")) for ip, mac in pairs
            if not mac.lower().startswith(("ff", "01-00-5e", "01:00:5e", "33-33", "33:33")) and mac.strip("0:-")]

def wifi_scan(full=True):
    """full: a tiny packet to every address of the home network (finds new devices; once a minute); else only to
    the devices known (every 10 s: arrivals and departures show within seconds)."""
    global WIFI_AT
    import ipaddress
    import socket
    if full:
        targets = [str(ip) for ip in ipaddress.ip_network(f"{ha_link.lan_ip()}/22", strict=False).hosts()]
    else:
        targets = sorted({d["ip"] for d in WIFI.values()})
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    for ip in targets:  # an empty packet to an unused port: the laptop asks the network who has each address
        try:
            s.sendto(b"", (ip, 9))
        except OSError:
            pass
    s.close()
    time.sleep(9 if full else 2)  # full: devices last seen long ago are re-checked (~8 s) before reading
    now, first = time.time(), not WIFI_AT
    seen = wifi_neighbours()
    if ROLE == "main":
        for mac in wifi_new([m for _, m in seen]):
            wifi_alert(mac, next(ip for ip, m in seen if m == mac))
    for ip, mac in seen:
        d = WIFI.get(mac)
        if d is None:
            WIFI[mac] = {"ip": ip, "seen": now, "since": None if first else now}
        else:
            if now - d["seen"] > WIFI_AWAY_S:
                d["since"] = now  # back after being away
            d.update(ip=ip, seen=now)
    WIFI_AT = now

def phone_macs():
    """The owner's phone: its name in data/wifi_names.json starts with "Your iPhone" / "Your phone"."""
    return [m for m, n in wifi_names().items() if n.lower().startswith(("your iphone", "your phone"))]

def phone_check():
    """Tell Home Assistant whether the owner's phone is on the home Wi-Fi (only when it changes)."""
    macs = phone_macs()
    if not macs or not WIFI_AT or DEMO:
        return
    now = time.time()
    seen = max((WIFI[m]["seen"] for m in macs if m in WIFI), default=0.0)
    here = now - seen < PHONE_AWAY_S
    if not here and PHONE_NIGHT[0] <= time.localtime().tm_hour < PHONE_NIGHT[1]:
        return  # at night only "home" counts
    if here == PHONE_HERE[0]:
        return
    print(f"Wi-Fi: your phone {'is on the home Wi-Fi' if here else f'not seen for {PHONE_AWAY_S // 60} min (away)'}", flush=True)
    ha_link.set_switch(HA_PHONE_WIFI, here)
    PHONE_HERE[0] = here

def wifi_loop():
    n, started = 0, time.time()
    while True:
        if not config.STANDBY:  # the Pi on standby: the laptop scans
            try:
                wifi_scan(full=n % 6 == 0)  # every address once a minute, the known devices every 10 s
                if time.time() - started > PHONE_AWAY_S or any(m in WIFI for m in phone_macs()):
                    phone_check()  # just started: "away" only after a full PHONE_AWAY_S of looking
            except Exception as e:
                print(f"Wi-Fi scan: {e}", flush=True)
            n += 1
        else:
            PHONE_HERE[0] = None  # the other machine tells Home Assistant now; say it again when back
        time.sleep(8)

def wifi_frame():
    """The home network now, for the app: name, address, here or gone, since when."""
    now, own = time.time(), wifi_names()
    rows = []
    first_day, today = WIFI_KNOWN_CACHE or {}, time.strftime("%Y-%m-%d")
    for mac, d in WIFI.items():
        private = int(mac[1], 16) & 2 == 2  # a private (random) address: a phone, tablet or laptop
        name = wifi_label(mac, own)
        rows.append({"name": name, "ip": d["ip"], "here": now - d["seen"] < WIFI_AWAY_S, "online": now - d["seen"] < WIFI_LIVE_S,
                     "seen_s": round(now - d["seen"]),
                     "back_s": round(now - d["since"]) if d.get("since") else None, "personal": private or mac in own,
                     "new": first_day.get(mac) == today and mac not in own and mac not in WIFI_NAMES})  # new today
    rows.sort(key=lambda r: (not r["here"], not r["online"], not r["personal"], r["name"]))
    return {"at": round(WIFI_AT), "ago_s": round(now - WIFI_AT) if WIFI_AT else None, "away_after_min": WIFI_AWAY_S // 60,
            "rows": rows}
