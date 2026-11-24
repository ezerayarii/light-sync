"""Camera controls (Oct 27, owner: "this camera has settings like zoom and I can move it, help me control each one";
then "put all the options of the camera, and just make me choose the camera"): every control the two MeetUps offer,
set on the camera itself (V4L2 controls, what the MeetUp's remote and Logitech's apps do): move (pan / tilt), zoom,
and the picture (brightness, contrast, white balance, exposure, focus, ...: whatever the camera lists).

Only the controls: this never reads a picture from the cameras (motionEye streams them; Light Sync reads the pictures
through Home Assistant, as numbers). Which camera is the TV one and which the room one is found once by nudging each
one's zoom and seeing which picture changes (numbers only). Kept in /data/cameras.json by USB port: which is which,
each one's Home position (where the TV colours, TV_BOX, and Room guard were set up) and the picture settings chosen
in the app (applied again when the add-on starts or motionEye restarts the camera).
"""
import errno
import fcntl
import glob
import json
import os
import re
import struct
import threading
import time

import numpy as np

# V4L2 ioctls (linux/videodev2.h)
QUERYCAP, QUERYCTRL, QUERYMENU, G_CTRL, S_CTRL = 0x80685600, 0xC0445624, 0xC02C5625, 0xC008561B, 0xC008561C
NEXT_CTRL = 0x80000000
F_DISABLED, F_READ_ONLY, F_INACTIVE, F_WRITE_ONLY = 0x1, 0x4, 0x10, 0x40
T_INTEGER, T_BOOLEAN, T_MENU, T_INTEGER_MENU = 1, 2, 3, 9
PAN, TILT, ZOOM = 0x009A0908, 0x009A0909, 0x009A090D               # absolute
PAN_REL, TILT_REL, ZOOM_REL = 0x009A0904, 0x009A0905, 0x009A090E   # relative (a step each time)
MOVE = {"pan": (PAN, PAN_REL), "tilt": (TILT, TILT_REL), "zoom": (ZOOM, ZOOM_REL)}
NOT_PICTURE = {PAN, TILT, ZOOM, PAN_REL, TILT_REL, ZOOM_REL, 0x009A0906, 0x009A0907, 0x009A090F, 0x009A0920, 0x009A0921}
# friendlier names for the usual UVC controls (the camera's own name otherwise)
NAMES = {0x00980900: "Brightness", 0x00980901: "Contrast", 0x00980902: "Saturation", 0x00980903: "Hue",
         0x0098090C: "Auto white balance", 0x00980913: "Gain", 0x00980918: "Mains flicker",
         0x0098091A: "White balance (K)", 0x0098091B: "Sharpness", 0x0098091C: "Backlight compensation",
         0x009A0901: "Exposure mode", 0x009A0902: "Exposure time", 0x009A0903: "Exposure may lower the frame rate",
         0x009A090A: "Focus", 0x009A090C: "Autofocus", 0x00980914: "Flip left-right", 0x00980915: "Flip upside down"}
WB_AUTO, WB_TEMP, EXPO_MODE, EXPO_TIME = 0x0098090C, 0x0098091A, 0x009A0901, 0x009A0902
STORE = "/data/cameras.json"
# named by where they are (owner: "the room camera is the one on the TV and the TV one is the room one"): the one in
# the room (the old spot, lower) looks at the TV screen; the one on the TV looks out at the room
# by place (Oct 28, swapped: the camera in front of the TV reads the TV colours, the one in the room watches the room)
LABELS = {"tv": "In front of the TV", "room": "In the room"}
T, R = LABELS["tv"], LABELS["room"]
LOCK = threading.Lock()
TUNE = {"running": False, "steps": [], "at": 0.0, "ok": None}  # Auto-tune's progress, for the app
LAST_CMD = {}  # bus -> when the app last moved it (Back Home waits 30 min after that)
AWAY_S = 1800  # a camera left away from Home this long goes back


def _load():
    try:
        with open(STORE, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = {}
    for k in ("which", "home", "picture"):
        data.setdefault(k, {})
    return data


def _save(data):
    with open(STORE, "w", encoding="utf-8") as f:
        json.dump(data, f)


def _text(buf, a, b):
    return bytes(buf[a:b]).split(b"\0")[0].decode(errors="replace")


def _controls(fd):
    """Every control of an open device: {cid: {name, type, min, max, step, default, flags, menu}}."""
    out, cid = {}, 0
    while True:
        q = bytearray(68)
        struct.pack_into("I", q, 0, cid | NEXT_CTRL)
        try:
            fcntl.ioctl(fd, QUERYCTRL, q)
        except OSError:
            break
        cid, typ = struct.unpack_from("II", q, 0)
        mn, mx, st, df, flags = struct.unpack_from("iiiiI", q, 40)
        if flags & F_DISABLED or typ not in (T_INTEGER, T_BOOLEAN, T_MENU, T_INTEGER_MENU):
            continue
        c = {"name": NAMES.get(cid, _text(q, 8, 40)), "type": typ, "min": mn, "max": mx, "step": max(1, st), "default": df,
             "flags": flags}
        if typ in (T_MENU, T_INTEGER_MENU):
            c["menu"] = []
            for i in range(mn, mx + 1):
                m = bytearray(44)
                struct.pack_into("II", m, 0, cid, i)
                try:
                    fcntl.ioctl(fd, QUERYMENU, m)
                except OSError:
                    continue
                label = _text(m, 8, 40) if typ == T_MENU else str(struct.unpack_from("q", m, 8)[0])
                c["menu"].append([i, label])
        out[cid] = c
    return out


def devices():
    """{bus: {"path", "card", "ctrls": {cid: ...}}}: the video capture devices that have controls. bus (the USB
    port) stays the same across restarts, unlike /dev/videoN."""
    out = {}
    for path in sorted(glob.glob("/dev/video*"), key=lambda p: int(re.sub(r"\D", "", p) or 0)):
        try:
            fd = os.open(path, os.O_RDWR | os.O_NONBLOCK)
        except OSError:
            continue
        try:
            cap = bytearray(104)
            fcntl.ioctl(fd, QUERYCAP, cap)
            caps, dcaps = struct.unpack_from("II", cap, 84)
            if not (dcaps if caps & 0x80000000 else caps) & 0x1:  # not a video capture node (metadata, codecs)
                continue
            bus = _text(cap, 48, 80)
            ctrls = _controls(fd)
            if ctrls and bus not in out and bus.startswith("usb"):
                out[bus] = {"path": path, "card": _text(cap, 16, 48), "ctrls": ctrls}
        except OSError:
            pass
        finally:
            os.close(fd)
    return out


def _get(path, cid):
    fd = os.open(path, os.O_RDWR | os.O_NONBLOCK)
    try:
        buf = bytearray(struct.pack("Ii", cid, 0))
        fcntl.ioctl(fd, G_CTRL, buf)
        return struct.unpack("Ii", buf)[1]
    finally:
        os.close(fd)


def _set(d, cid, value):
    c = d["ctrls"].get(cid)
    if not c or c["flags"] & F_READ_ONLY:
        raise ValueError("that setting can't be changed")
    v = int(min(c["max"], max(c["min"], c["min"] + round((float(value) - c["min"]) / c["step"]) * c["step"])))
    fd = os.open(d["path"], os.O_RDWR | os.O_NONBLOCK)
    try:
        fcntl.ioctl(fd, S_CTRL, bytearray(struct.pack("Ii", cid, v)))
    except OSError as e:
        if e.errno in (errno.EBUSY, errno.EACCES, errno.EPERM):
            raise ValueError("switch its automatic mode off first") from None
        raise
    finally:
        os.close(fd)
    return v


def _values(d):
    out = {}
    for cid, c in d["ctrls"].items():
        if c["flags"] & F_WRITE_ONLY:
            continue
        try:
            out[cid] = _get(d["path"], cid)
        except OSError:
            pass
    return out


def _position(d, vals=None):
    vals = vals if vals is not None else _values(d)
    return {n: vals[a] for n, (a, _) in MOVE.items() if a in vals}


def identify(grab):
    """Which device is the TV camera and which the room one: each one's zoom nudged for 2.5 s, and the picture that
    changed (grab(which) -> grey numbers, None if that camera isn't set) tells. Also keeps each one's Home position."""
    data, devs = _load(), devices()
    for bus, d in devs.items():
        if ZOOM not in d["ctrls"]:
            continue
        # how much each picture changes on its own first (Oct 28: the TV camera sees a playing film, which changed
        # more than the other camera's zoom, and both were taken for the TV camera)
        still0 = {w: grab(w) for w in LABELS}
        time.sleep(2.5)
        before = {w: grab(w) for w in LABELS}
        natural = {w: float(np.nanmean(np.abs(before[w] - still0[w]))) for w in LABELS
                   if before[w] is not None and still0[w] is not None}
        z0 = _get(d["path"], ZOOM)
        c = d["ctrls"][ZOOM]
        _set(d, ZOOM, z0 + (c["max"] - c["min"]) / 3 if z0 < (c["min"] + c["max"]) / 2 else z0 - (c["max"] - c["min"]) / 3)
        time.sleep(2.5)  # the room camera's stream is slow (~1-2 pictures a second)
        after = {w: grab(w) for w in LABELS}
        _set(d, ZOOM, z0)
        change = {w: float(np.nanmean(np.abs(after[w] - before[w]))) - natural.get(w, 0.0) for w in LABELS
                  if before[w] is not None and after[w] is not None}
        print(f"Cameras: zoom test on {bus}: picture change {({w: round(v, 3) for w, v in change.items()})}"
              f" (natural {({w: round(v, 3) for w, v in natural.items()})})", flush=True)
        if change:
            best = max(change, key=change.get)
            if change[best] > 0.15 and change[best] > 2 * max(0.0, min(change.values())):  # (natural taken off)
                data["which"][bus] = best
        data["home"].setdefault(bus, _position(d))
    _save(data)


def restore():
    """The picture settings chosen in the app, back on the cameras (add-on start; motionEye restarting a camera
    sets its own). Inactive ones (their automatic mode is on) wait."""
    with LOCK:
        data, devs = _load(), devices()
        for bus, d in devs.items():
            vals = _values(d)
            for cid, v in data["picture"].get(bus, {}).items():
                cid = int(cid)
                c = d["ctrls"].get(cid)
                if c and not c["flags"] & F_INACTIVE and vals.get(cid) != v:
                    try:
                        _set(d, cid, v)
                    except (OSError, ValueError):
                        pass


def listing():
    """The cameras for the app: which is which, move / zoom (absolute or step-wise), Home, and every picture setting."""
    with LOCK:
        data, out = _load(), []
        for i, (bus, d) in enumerate(sorted(devices().items())):
            vals = _values(d)
            pos = _position(d, vals)
            home = data["home"].setdefault(bus, pos)
            which = data["which"].get(bus)
            move = {}
            for n, (a, r) in MOVE.items():
                if a in d["ctrls"]:
                    c = d["ctrls"][a]
                    move[n] = {"min": c["min"], "max": c["max"], "step": c["step"], "value": vals.get(a)}
                elif r in d["ctrls"]:
                    move[n] = {"relative": True}
            picture = [{"id": cid, "name": c["name"], "kind": {T_BOOLEAN: "switch", T_MENU: "menu", T_INTEGER_MENU: "menu"}.get(c["type"], "slider"),
                        "min": c["min"], "max": c["max"], "step": c["step"], "default": c["default"], "value": vals.get(cid),
                        "menu": c.get("menu"), "off": bool(c["flags"] & F_INACTIVE), "fixed": bool(c["flags"] & F_READ_ONLY)}
                       for cid, c in d["ctrls"].items() if cid not in NOT_PICTURE]
            out.append({"id": bus, "which": which, "label": LABELS.get(which, f"Camera {i + 1}"), "move": move,
                        "home": home, "at_home": all(abs(pos[n] - home.get(n, pos[n])) <= move[n].get("step", 1)
                                                     for n in pos if n in move),
                        "picture": picture})
        _save(data)
        out.sort(key=lambda c: ({"tv": 0, "room": 1}.get(c["which"], 2), c["id"]))
        return {"cams": out, "identified": all(c["which"] for c in out)}


def _by_job(which):
    data = _load()
    return next(((bus, d) for bus, d in devices().items() if data["which"].get(bus) == which), (None, None))


def _shift(a, b):
    """How far picture b moved from a (grey cells, x and y), by phase correlation."""
    fa, fb = np.fft.fft2(a - a.mean()), np.fft.fft2(b - b.mean())
    r = fa * np.conj(fb)
    r = np.fft.ifft2(r / (np.abs(r) + 1e-9)).real
    y, x = np.unravel_index(int(np.argmax(r)), r.shape)
    h, w = r.shape
    return -(x - w if x > w // 2 else x), -(y - h if y > h // 2 else y)


def _note(text, ok=None):
    TUNE["steps"].append(text)
    if ok is not None:
        TUNE["ok"] = ok
    print("Auto-tune:", text, flush=True)


def auto_tune(grab_img):
    """Owner (Oct 27): "make the app test and learn so it can zoom out". grab_img(which) -> a picture (as numbers,
    never kept). On the TV (room): zoomed all the way out. In the room (TV): zoomed out, the screen found, how far a
    small turn moves the picture learned, turned so the screen is in the middle, zoomed in until it fills ~60 %, the
    screen found again and saved for TV mode, white balance fixed (it chased the lights' colours), the exposure
    shortened if the screen burns out to white. Both saved as Home."""
    import light_sync as ms
    TUNE.update(running=True, steps=[], at=time.time(), ok=None)
    cam = None
    try:
        with LOCK:
            data = _load()
            bus, d = _by_job("room")
            if d and ZOOM in d["ctrls"]:  # where it points stays (Oct 28: the room camera can turn now; the TV's place
                _set(d, ZOOM, d["ctrls"][ZOOM]["min"])  # in its picture, left out of Room guard, is measured there)
                data["home"][bus] = _position(d)
                _save(data)
                _note(f"{R}: zoomed all the way out, the whole room in view; saved as Home.")
            bus, d = _by_job("tv")
            if not d:
                _note(f"{T}: camera not found (tap Find which camera is which).", False)
                return
            c = d["ctrls"]
            for a in (PAN, TILT):
                if a in c:
                    _set(d, a, 0)
            zmin = c[ZOOM]["min"] if ZOOM in c else 100
            if ZOOM in c:
                _set(d, ZOOM, zmin)

        # the TV camera's live stream through Home Assistant while tuning (~4 pictures a second; single pictures took
        # ~1.2 s each, and a first run took 3 minutes)
        url = ms.SNAPSHOT_URL
        headers = {"Authorization": f"Bearer {ms.HA_TOKEN}"} if "/api/camera_proxy/" in url else {}
        cam = ms.CamStream(url.replace("/api/camera_proxy/", "/api/camera_proxy_stream/"), url,
                           None if headers else ms.stream_auth(url), headers) if url else None
        newest = {"at": 0.0}

        def frame(wait=4.0):
            t0 = time.time()
            while cam is not None and time.time() - t0 < wait:
                if cam.at > newest["at"] and cam.jpg:
                    newest["at"] = cam.at
                    img = ms.decode_bgr(cam.jpg)
                    if img is not None:
                        return img
                time.sleep(0.05)
            return grab_img("tv")

        def grey():
            img = frame()
            return None if img is None else ms.grey_small(img)

        def look(n=24, gap=0.35):
            frames = []
            for _ in range(n):
                g = grey()
                if g is not None:
                    frames.append(g)
                time.sleep(gap)
            return ms.find_screen_box(frames)

        _note(f"{T}: zoomed out; looking for the TV screen (it needs something moving on it)...")
        time.sleep(3)
        f0 = look()
        if not f0 or f0["strength"] < 3:
            _note(f"{T}: no moving screen found. Play a bright, moving video on the TV, then Auto-tune again "
                  "(the camera stays zoomed out, where the TV colours worked before).", False)
            return
        cx, cy = (f0["outer"][0] + f0["outer"][2]) / 2, (f0["outer"][1] + f0["outer"][3]) / 2
        cx0, cy0 = cx, cy
        ow, oh = f0["outer"][2] - f0["outer"][0], f0["outer"][3] - f0["outer"][1]
        _note(f"{T}: screen found ({100 * f0['share']:.0f} % of the picture, moving {f0['strength']:.0f}x more than the rest).")
        # learn how far one small turn moves the picture, then turn the screen into the middle
        for name, a, step, axis in (("left / right", PAN, 14400, 0), ("up / down", TILT, 10800, 1)):
            if a not in c:
                continue
            g1 = grey()
            with LOCK:
                _set(d, a, step)
            time.sleep(2.5)
            g2 = grey()
            if g1 is None or g2 is None:
                continue
            moved = _shift(g1, g2)[axis] / g1.shape[1 - axis]  # fraction of the picture
            if abs(moved) < 0.01:
                with LOCK:
                    _set(d, a, 0)
                _note(f"{T}: a small turn {name} didn't move the picture enough to learn from; left centred.")
                continue
            here = (cx if axis == 0 else cy) + moved          # the screen's centre after the test turn
            with LOCK:
                v = _set(d, a, step + (0.5 - here) / moved * step)
            here += moved * (v - step) / step
            cx, cy = (here, cy) if axis == 0 else (cx, here)
            _note(f"{T}: learned {name} ({100 * abs(moved) / (step / 3600):.1f} % of the picture per degree); "
                  f"turned {v / 3600:+.0f}° to centre the screen.")
        # zoom in so the screen fills up to ~60 % of the picture (more of its colours, the room's lights less in view),
        # but only as far as all of it stays in view: the zoom magnifies around the middle, and when the screen is
        # further off to the side than the camera can turn (Oct 27: ~27 deg, it turns 25), its far edge leaves first
        z, f1 = zmin, None
        sx, sy = cx - cx0, cy - cy0
        ox1, oy1, ox2, oy2 = f0["outer"][0] + sx, f0["outer"][1] + sy, f0["outer"][2] + sx, f0["outer"][3] + sy
        fit = min(0.45 / max(abs(ox1 - 0.5), abs(ox2 - 0.5), 0.01), 0.45 / max(abs(oy1 - 0.5), abs(oy2 - 0.5), 0.01))
        fill = min(0.6 / max(ow, 0.01), 0.6 / max(oh, 0.01))
        if ZOOM in c and min(fit, fill) > 1.1:
            z = int(min(c[ZOOM]["max"], zmin * min(fit, fill)))
            for _ in range(3):
                with LOCK:
                    z = _set(d, ZOOM, z)
                time.sleep(2.5)
                f1 = look()
                if f1 and f1["strength"] >= 3 and min(f1["box"][:2]) > 0.01 and max(f1["box"][2:]) < 0.99:
                    break
                f1 = None
                z = int(zmin + (z - zmin) * 0.6)  # cut off or lost: zoom out some and look again
        else:
            if ZOOM in c:
                _note(f"{T}: the screen can't get bigger without leaving the picture: no zoom.")
            time.sleep(2)
            f1 = look()
        if not f1 or f1["strength"] < 3:
            with LOCK:
                for a in (PAN, TILT):
                    if a in c:
                        _set(d, a, 0)
                if ZOOM in c:
                    _set(d, ZOOM, zmin)
            _note(f"{T}: lost the screen after turning / zooming; back to where it was (TV colours as before). "
                  "Try again with a bright, moving video.", False)
            return
        if z > zmin:
            _note(f"{T}: zoomed to {z / zmin:.1f}x: the screen fills {100 * f1['share']:.0f} % of the picture.")
        with LOCK:
            if WB_AUTO in c and WB_TEMP in c:  # the camera's white balance chased the lights' colours (they follow the TV)
                _set(d, WB_AUTO, 0)
                kelvin = _set(d, WB_TEMP, 6500)
                data = _load()
                data["picture"].setdefault(bus, {}).update({str(WB_AUTO): 0, str(WB_TEMP): kelvin})
                _save(data)
                _note(f"{T}: white balance fixed at {kelvin} K (a TV's white): it no longer chases the lights' colours.")
        box = f1["box"]

        def burnt(n=5):
            out = []
            for _ in range(n):
                img = frame()
                if img is not None:
                    h, w = img.shape[:2]
                    part = img[int(box[1] * h):int(box[3] * h), int(box[0] * w):int(box[2] * w)]
                    if part.size:
                        out.append((float((part.max(axis=2) >= 250).mean()), float(part.mean())))
                time.sleep(0.4)
            return (float(np.mean([o[0] for o in out])), float(np.mean([o[1] for o in out]))) if out else (0.0, 0.0)

        clipped, _ = burnt()
        if clipped > 0.25 and EXPO_MODE in c and EXPO_TIME in c:
            _note(f"{T}: {100 * clipped:.0f} % of the screen burns out to white: testing shorter exposures...")
            best = None
            for t in (200, 120, 80, 50, 30):  # x 0.1 ms
                with LOCK:
                    _set(d, EXPO_MODE, 1)  # manual
                    _set(d, EXPO_TIME, t)
                time.sleep(1.5)
                cl, lv = burnt(3)
                if cl < 0.08 and lv > 50:
                    best = (t, cl)
                    break
            with LOCK:
                data = _load()
                pic = data["picture"].setdefault(bus, {})
                if best:
                    _set(d, EXPO_TIME, best[0])
                    pic.update({str(EXPO_MODE): 1, str(EXPO_TIME): best[0]})
                    _note(f"{T}: exposure {best[0] / 10:g} ms: {100 * best[1]:.0f} % burnt out now.")
                else:
                    _set(d, EXPO_MODE, c[EXPO_MODE]["default"])
                    pic.pop(str(EXPO_MODE), None)
                    pic.pop(str(EXPO_TIME), None)
                    _note(f"{T}: no exposure kept the screen from burning out; back to automatic.")
                _save(data)
        else:
            _note(f"{T}: exposure fine ({100 * clipped:.0f} % of the screen burnt out).")
        # the screen's four corners (Oct 28: the camera in front of the TV sees it from below, a trapezoid; a box would
        # take in the wall the lights colour), the box if they can't be fitted
        if ms.TV_QUAD:  # measured over ~90 s (find_screen.py --quad): a short look from below is too patchy
            _note(f"{T}: TV area kept (the screen's four corners, measured with find_screen.py --quad).")
        else:
            ms.save_tv_box(box, "Auto-tune")
        with LOCK:
            data = _load()
            data["home"][bus] = _position(d)
            _save(data)
        _note(f"{T}: this position saved as Home. Done.", True)
    except Exception as e:
        _note(f"Stopped: {e}", False)
    finally:
        if cam is not None:
            cam.stop()
        TUNE["running"] = False
        TUNE["at"] = time.time()


def keep_home_loop():
    """Owner: "keep them tuned by themselves": a camera left away from Home for AWAY_S (moved with the app or its
    remote, or knocked) goes back. Save this as Home keeps a new position."""
    away = {}
    while True:
        time.sleep(60)
        if TUNE["running"]:
            continue
        try:
            cams = listing()["cams"]
        except Exception:
            continue
        now = time.time()
        for c in cams:
            if c["at_home"]:
                away.pop(c["id"], None)
                continue
            since = max(away.setdefault(c["id"], now), LAST_CMD.get(c["id"], 0))
            if now - since > AWAY_S:
                try:
                    command({"id": c["id"], "home": True})
                    print(f"Cameras: {c['label']} was away from Home for {AWAY_S // 60} min - back Home", flush=True)
                except Exception as e:
                    print(f"Cameras: couldn't put {c['label']} back Home ({e})", flush=True)
                away.pop(c["id"], None)


def command(cmd, grab=None):
    """{"identify": true} | {"id": bus, and one of: "move": {"pan" | "tilt" | "zoom": -1 / +1}, "set": {"zoom": v},
    "home": true, "save_home": true, "ctrl": {cid: value}, "reset_picture": true, "which": "tv" | "room"}"""
    if TUNE["running"]:
        raise ValueError("Auto-tune is running")
    if cmd.get("identify"):
        with LOCK:
            identify(grab)
        return listing()
    with LOCK:
        devs, data = devices(), _load()
        bus = str(cmd.get("id"))
        d = devs.get(bus)
        if not d:
            raise ValueError("no such camera")
        LAST_CMD[bus] = time.time()
        if cmd.get("which") in LABELS:  # this one is the TV (or room) camera, the other one the other (Oct 28: the
            other = "room" if cmd["which"] == "tv" else "tv"  # zoom test failed in the dark on the Pi)
            for b in devs:
                data["which"][b] = cmd["which"] if b == bus else other
        if cmd.get("save_home"):
            data["home"][bus] = _position(d)
        if cmd.get("home"):
            for n, v in data["home"].get(bus, {}).items():
                if MOVE[n][0] in d["ctrls"]:
                    _set(d, MOVE[n][0], v)
        for n, v in (cmd.get("set") or {}).items():
            if n in MOVE and MOVE[n][0] in d["ctrls"]:
                _set(d, MOVE[n][0], v)
        for n, s in (cmd.get("move") or {}).items():  # arrows: a twelfth of the range a tap (or one relative step)
            if n not in MOVE:
                continue
            a, r = MOVE[n]
            sign = 1 if float(s) > 0 else -1
            if a in d["ctrls"]:
                c = d["ctrls"][a]
                _set(d, a, _get(d["path"], a) + sign * max(c["step"], (c["max"] - c["min"]) / 12))
            elif r in d["ctrls"]:
                c = d["ctrls"][r]
                _set(d, r, sign * max(c["step"], min(c["max"], (c["max"] - c["min"]) / 24)))
        pic = data["picture"].setdefault(bus, {})
        for cid, v in (cmd.get("ctrl") or {}).items():
            cid = int(cid)
            if cid in NOT_PICTURE:
                continue
            pic[str(cid)] = _set(d, cid, v)
        if cmd.get("reset_picture"):  # automatic modes first: their manual settings may only change after
            ctrls = [(cid, c) for cid, c in d["ctrls"].items() if cid not in NOT_PICTURE and not c["flags"] & F_READ_ONLY]
            for cid, c in sorted(ctrls, key=lambda x: x[1]["type"] not in (T_BOOLEAN, T_MENU)):
                try:
                    _set(d, cid, c["default"])
                except (OSError, ValueError):
                    pass
            data["picture"][bus] = {}
        _save(data)
    return listing()
