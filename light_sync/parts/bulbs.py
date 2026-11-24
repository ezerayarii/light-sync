"""The bulbs: finding them on the network, Govee LAN control, and LightWorker, which sends each light its colours."""

import numpy as np
import os
import threading
import time

from parts.config import (
    BULB_LAN_RATE, COMFORT_CAP, COMFORT_FADE, COMFORT_SWELL, DEMO, WHITE,
)
from parts import colours, config, eyes, ha_link, rocky_cue, web


class GoveeLan:
    """Talks straight to Govee lights over the local network (Govee 'LAN Control')."""

    def __init__(self):
        import socket
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.last_bri = {}  # ip -> brightness % last sent (cheap Wi-Fi bulbs choke on too many packets)

    def send(self, ip, cmd, data):
        import json
        msg = json.dumps({"msg": {"cmd": cmd, "data": data}}).encode()
        self.sock.sendto(msg, (ip, 4003))

    def color(self, ip, rgb, bri255):
        r, g, b = (int(c) for c in rgb)
        self.send(ip, "colorwc", {"color": {"r": r, "g": g, "b": b}, "colorTemInKelvin": 0})
        pct = max(1, min(100, round(bri255 / 2.55)))
        last = self.last_bri.get(ip)
        if last is None or abs(pct - last) >= 3 or (pct != last and pct in (1, 100)):  # only real changes (Govee alert)
            self.send(ip, "brightness", {"value": pct})
            self.last_bri[ip] = pct

    def power(self, ip, on):
        self.send(ip, "turn", {"value": 1 if on else 0})
        self.last_bri.pop(ip, None)

    def white(self, ip, kelvin, bri255):
        """True white (the bulbs' own white LEDs) at a colour temperature."""
        self.send(ip, "colorwc", {"color": {"r": 0, "g": 0, "b": 0}, "colorTemInKelvin": int(kelvin)})
        pct = max(1, min(100, round(bri255 / 2.55)))
        last = self.last_bri.get(ip)
        if last is None or abs(pct - last) >= 2 or (pct != last and pct in (1, 100)):
            self.send(ip, "brightness", {"value": pct})
            self.last_bri[ip] = pct

    # DreamView/Razer streaming (RGBIC strips like TvD's H612F): several colors at once, one UDP packet per frame
    def razer(self, ip, body):
        import base64
        cs = 0
        for x in body:
            cs ^= x
        self.send(ip, "razer", {"pt": base64.b64encode(bytes(body) + bytes([cs])).decode()})

    def razer_mode(self, ip, on, set_brightness=True, brightness=100):
        if on and set_brightness:  # brightness first: any normal command after "streaming on" switches streaming off
            self.send(ip, "brightness", {"value": int(brightness)})  # the strip's own dimming (the light's level)
        self.razer(ip, [0xBB, 0x00, 0x01, 0xB1, 1 if on else 0])
        self.last_bri.pop(ip, None)

    def razer_frame(self, ip, colors):
        flat = [max(0, min(255, int(v))) for c in colors for v in c]
        self.razer(ip, [0xBB, 0x00, 2 + 3 * len(colors), 0xB0, 0x00, len(colors)] + flat)

def govee_scan(seconds=3.0):
    import json
    import socket
    rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    rx.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    rx.bind(("", 4002))
    rx.settimeout(0.5)
    tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    tx.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
    # out the home network on purpose: with other networks up (VirtualBox, Bluetooth from the phone), Windows sent
    # the search out another one and no light answered (Oct 21: every light fell back to Home Assistant)
    home = ha_link.lan_ip()
    if not home.startswith("127."):
        tx.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(home))
    scan = json.dumps({"msg": {"cmd": "scan", "data": {"account_topic": "reserve"}}}).encode()
    found, end = {}, time.time() + seconds
    while time.time() < end:
        tx.sendto(scan, ("239.255.255.250", 4001))
        try:
            data, addr = rx.recvfrom(4096)
            d = json.loads(data).get("msg", {}).get("data", {})
            found[d.get("ip", addr[0])] = (d.get("sku", "?"), d.get("device", "?"))
        except Exception:
            pass
    rx.close()
    return found

def arp_table():
    """MAC -> the IPs it had recently (Linux). On the Pi, Home Assistant's Govee integration polls the bulbs,
    so they're in it; after the router hands out new addresses the old ones can linger for a while."""
    out = {}
    try:
        with open("/proc/net/arp") as f:
            next(f)
            for line in f:
                p = line.split()
                if len(p) >= 4 and p[2] != "0x0":  # 0x0 = incomplete entry
                    out.setdefault(p[3].upper(), []).append(p[0])
    except OSError:
        pass
    return out

PING_WORKS = None  # checked once: can this machine ping at all (container permissions)?

def answers_ping(ip):
    """True/False, or None when ping can't be used here (then the ARP table is trusted as it is)."""
    import subprocess
    global PING_WORKS

    def ping(host):
        try:
            return subprocess.run(["ping", "-c", "1", "-W", "1", host], capture_output=True, timeout=3).returncode == 0
        except Exception:
            return False
    if PING_WORKS is None:
        PING_WORKS = ping("127.0.0.1")
    return ping(ip) if PING_WORKS else None

def arp_pick(ips):
    """The address that's alive now: a stale entry from before the router changed addresses doesn't answer."""
    for ip in ips:
        alive = answers_ping(ip)
        if alive or alive is None:
            return ip
    return ""

def resolve_govee(values, quiet=False):
    """GOVEE_* can be an IP or a device id (like 11:2F:5C:...). Device ids are looked up on the network (at
    startup and every GOVEE_RECHECK seconds), so a bulb that got a new IP from the router is still found; one
    that doesn't answer goes through Home Assistant instead."""
    if not any(v and ":" in v for v in values):
        return values
    if os.getenv("GOVEE_FIND") == "arp":
        # Pi: HA's Govee integration owns UDP port 4002 (scan replies), so find the bulbs by MAC instead.
        # A value can be the bulb's MAC, or a device id ending with it (most Govee models)
        table = arp_table()
        found = {v.upper(): arp_pick(table.get(v.upper()[-17:], [])) for v in values if v and ":" in v}
    else:
        found = {dev.upper(): ip for ip, (sku, dev) in govee_scan().items()}
    out = []
    for v in values:
        if v and ":" in v:
            ip = found.get(v.upper(), "")
            if not quiet:
                print(f"Govee {v}: " + (f"found at {ip}" if ip else "not answering - using Home Assistant for it"), flush=True)
            out.append(ip)
        else:
            out.append(v)
    return out

GOVEE_RECHECK = float(os.getenv("GOVEE_RECHECK", "120"))  # s between bulb look-ups (0 = only at start)

def govee_recheck_loop(workers, ids):
    """The router can give the bulbs new addresses (a router restart, a move): look them up again every
    GOVEE_RECHECK seconds. A new address is used at once; a bulb missing twice in a row goes through Home
    Assistant until it's back."""
    misses = [0] * len(workers)
    while True:
        time.sleep(GOVEE_RECHECK)
        if config.STANDBY:
            continue
        try:
            ips = resolve_govee(ids, quiet=True)
        except Exception as e:
            print(f"Govee look-up failed: {e}", flush=True)
            continue
        for i, (w, ip) in enumerate(zip(workers, ips)):
            if not ids[i] or ":" not in ids[i]:
                continue
            misses[i] = 0 if ip else misses[i] + 1
            new = ip if ip else ("" if misses[i] >= 2 else w.lan_ip)
            if new != w.lan_ip:
                print(f"{w.entity_id}: " + (f"now at {new} (Govee LAN)" if new else "not answering - via Home Assistant"), flush=True)
                w.set_lan(new)
                w.power_on = bool(new) and w.enabled
                web.STATE.setdefault("control", {})[w.entity_id] = f"Govee LAN {new}" if new else "via Home Assistant"

LAN = None

class LightWorker(threading.Thread):
    """Sends only the newest wanted state, rate-limited, so slow bulbs never lag behind."""

    def __init__(self, entity_id, max_per_sec, lan_ip=""):
        super().__init__(daemon=True)
        self.entity_id = entity_id
        self.rate = max_per_sec
        self.set_lan(lan_ip)
        self.want = None
        self.sent = None
        self.lock = threading.Lock()
        self.wake = threading.Event()
        self.enabled = True     # False = taken out of Light Sync on the dashboard
        self.power_on = 1 if lan_ip else False   # LAN: switch the bulb on before the next color (at start too, Nov 7)
        self.segments = 0       # >0: an RGBIC strip driven per segment (DreamView/Razer streaming over LAN)
        self.seg_from_right = False  # the strip's first segment is on the right of the screen
        self.razer = False      # streaming mode switched on in the strip
        self.razer_at = 0.0     # last frame sent
        self.razer_on_at = 0.0  # last "streaming on" (repeated every 30 s, in case something switched it off)
        self.hw_bri = None      # the strip's own brightness (%) set when streaming started: the light's level
        self.c_cols, self.c_bri = None, 0.0  # comfort filter: the softened colors / brightness shown now
        self.flash_pending = False  # a white flash waiting for the next send slot
        self.level, self.sent_level = 1.0, 1.0  # this light's own brightness level (0-1), applied when sending; 0 = off
        self.sent_at = 0.0  # last command sent (bulbs: small changes wait, see worth_sending)
        self.dark_at = 0.0  # level 0: when the light was last switched off (0 = it's not off)
        self.forced_dark = False  # off for the mode (Normal: TvD off), whatever its level
        self.cue_on, self.cue_saved = False, None  # Rocky's light cue showing; the strip as it was (Light Sync paused)

    def set_level(self, level):
        if level != self.level:
            self.level = level
            self.wake.set()

    def segmented(self):
        return bool(self.segments and self.lan_ip and config.SEGMENTS_ON and not DEMO)

    def set_white(self, kelvin, brightness):
        """True white at a colour temperature (Normal light; brightness 0-255)."""
        if not self.enabled:
            return
        with self.lock:
            self.want = ("white", int(kelvin), int(max(1, min(255, brightness))))
        self.wake.set()

    def set_dark(self, dark):
        """Off for the current mode (Normal: TvD off), back on when the mode changes."""
        if dark != self.forced_dark:
            self.forced_dark = dark
            self.wake.set()

    def set_segments(self, colors, brightness):
        """Per-segment colors, left to right as seen on the screen (scaled by brightness 0-255)."""
        if not self.enabled:
            return
        cols = list(colors)[::-1] if self.seg_from_right else list(colors)
        cmd = ("seg", tuple(tuple(int(v) for v in c) for c in cols), int(max(1, min(255, brightness))))
        with self.lock:
            self.want = cmd
        self.wake.set()

    def comfort(self, dt, night):
        """Comfort filter over what the modes asked for: soft (warm at night) colors that crossfade slowly, and a
        brightness that swells with the music over seconds inside a narrow band - no flashes, no pulsing."""
        w = self.want
        if not w or not self.enabled or w[0] == "white":  # Normal's white: already calm
            self.c_cols = None
            return
        seg = w[0] == "seg"
        cols = np.array([colours.soft_color(c, night) for c in (w[1] if seg else [w[0]])], float)
        bri = w[2] if seg else w[1]
        cap = min(config.MAX_BRIGHT, COMFORT_CAP[1]) if night else config.MAX_BRIGHT
        floor = max(0.5, min(1.0, config.MIN_BRIGHT / max(1, config.MAX_BRIGHT)))  # a narrow band: never deep swings
        target = cap * (floor + (1 - floor) * bri / 255)
        speed = max(1.0, min(10.0, config.REACTION_NOW)) / 5          # reaction slider; comfort keeps it slow
        fade, swell = max(1.2, COMFORT_FADE / speed), max(1.5, COMFORT_SWELL / speed)
        c = getattr(self, "c_cols", None)
        if c is None:
            c, self.c_bri = cols.copy(), target
        elif len(c) != len(cols):  # one color <-> segments: carry the current look over, no jump
            c = np.repeat(c.mean(axis=0, keepdims=True), len(cols), axis=0)
        c += (cols - c) * (1 - np.exp(-dt / fade))
        self.c_cols = c
        self.c_bri += (target - self.c_bri) * (1 - np.exp(-dt / swell))
        q = np.clip(np.round(c / 3) * 3, 0, 255)  # fewer tiny updates
        new = (("seg", tuple(tuple(int(v) for v in x) for x in q), int(self.c_bri)) if seg
               else (tuple(int(v) for v in q[0]), int(self.c_bri), 0))
        with self.lock:
            self.want = new

    def shown(self):
        """(rgb, brightness) this light is asked to show, for the live view (segments: their average)."""
        w = self.want
        if not w:
            return [0, 0, 0], 0
        if self.forced_dark:
            return [0, 0, 0], 0
        if w[0] == "seg":
            return [int(v) for v in np.mean(w[1], axis=0)], int(w[2] * self.level)
        if w[0] == "white":
            return [int(v) for v in colours.kelvin_rgb(w[1])], int(w[2] * self.level)
        return list(w[0]), int(w[1] * self.level)

    def razer_stop(self):
        """Back to normal commands (pausing, taken out, handing over, exit): the strip leaves streaming mode."""
        if self.razer and self.lan_ip and not DEMO:
            try:
                LAN.razer_mode(self.lan_ip, False)
            except Exception:
                pass
        self.razer = False

    def set_lan(self, lan_ip):
        # Nov 7: the Pi restarted, gave the lights back as they were before (TvD: off), and the new start sent TvD
        # colours over the LAN to a strip that was powered off - it stayed dark. A light found on the LAN is switched
        # on before its first colour (as take() does)
        if lan_ip and lan_ip != getattr(self, "lan_ip", None):
            self.power_on = 1
        self.lan_ip = lan_ip
        rate = max(self.rate, 10) if lan_ip else self.rate  # LAN is faster, but cheap bulbs freeze if flooded
        self.gap = 1.0 / rate

    def send_gap(self):
        """Time between commands. A strip in DreamView streaming takes ~10 frames/s; a bulb over LAN at most
        BULB_LAN_RATE a second (Oct 19: Govee emailed a "Device Abnormal Alert" for high-frequency local access)."""
        if self.lan_ip and not self.segmented():
            return max(self.gap, 1.0 / BULB_LAN_RATE)
        return self.gap

    def worth_sending(self, cmd):
        """A bulb: skip changes too small to see (unless 1.5 s passed), so a steady look sends almost nothing."""
        old = self.sent
        if not old or cmd[0] == "seg" or old[0] == "seg" or self.segmented() or time.time() - self.sent_at > 1.5:
            return True
        if cmd[0] == "white" or old[0] == "white":  # Normal's white: 50 K or a few % make a difference
            return old[0] != cmd[0] or abs(cmd[1] - old[1]) >= 50 or abs(cmd[2] - old[2]) >= 4
        step = 4 if web.STATE.get("mode") == "ambient" else 10  # TV mode's slow glides: small steps (Oct 27), same rate
        return max(abs(a - b) for a, b in zip(cmd[0], old[0])) >= step or abs(cmd[1] - old[1]) >= 6

    def set(self, rgb, brightness, transition=0.0):
        if not self.enabled:
            return
        cmd = (tuple(int(c) for c in rgb), int(max(1, min(255, brightness))), transition)
        with self.lock:
            self.want = cmd
        self.wake.set()

    def flash(self):
        """One white flash, sent for sure at the next send slot (a short white window could fall between two
        rate-limited sends and be skipped: strobe missed 1 beat in 5 per light that way)."""
        if not self.enabled:
            return
        with self.lock:
            self.flash_pending = True
        self.wake.set()

    def release(self):
        """Taken out of Light Sync: stop driving this light and switch it off."""
        with self.lock:
            self.enabled, self.want, self.sent = False, None, None
        time.sleep(self.gap + 0.05)  # let a command already on its way go out first
        if DEMO:
            return
        self.razer_stop()
        try:
            if self.lan_ip:
                LAN.power(self.lan_ip, False)
            else:
                ha_link.ha_call("turn_off", {"entity_id": self.entity_id})
        except Exception as e:
            print(f"{self.entity_id}: couldn't switch off ({e})", flush=True)

    def take(self):
        """Back in Light Sync."""
        with self.lock:
            self.enabled, self.power_on = True, bool(self.lan_ip)

    def go_dark(self):
        """Its brightness at 0 (app / dashboard): the light switches off but stays in Light Sync; raising the
        brightness switches it back on (owner, Oct 19: "the last value: off, not less bright"). Sent again every
        10 s: a UDP command can drop."""
        first = not self.dark_at  # the first "off" (it's re-sent every 10 s while dark)
        self.dark_at, self.sent = time.time(), None
        if DEMO:
            return
        self.razer_stop()
        try:
            if self.lan_ip:
                if first and not self.segmented():  # (only while it's still on: never wakes a bulb that's off)
                    # Nov 7 (owner: TV mode's quiet, then the lights "jump from 0 to 33 and 36"): a Govee bulb comes
                    # back on at the brightness it had when switched off, before the next colour arrives. Down to
                    # the lowest level first (2 %: the Nov 7 camera test found the colours hold down to 1 %), so it
                    # comes back there and fades up
                    LAN.send(self.lan_ip, "brightness", {"value": 1})  # the lowest (1 %, Nov 7)
                    LAN.last_bri[self.lan_ip] = 1
                    time.sleep(0.15)
                LAN.power(self.lan_ip, False)
                self.power_on = 2  # switch it on again (twice: a UDP command can drop) before the next color
            else:
                ha_link.ha_call("turn_off", {"entity_id": self.entity_id})
        except Exception as e:
            print(f"{self.entity_id}: couldn't switch off ({e})", flush=True)

    def quiet(self, wait=True):
        """Light Sync stops driving this light (pausing): nothing left queued, then the strip leaves streaming mode.
        Oct 20: a frame already on its way went out after the stop and put TvD back in streaming mode; the 30 s
        keep-alive then switched it back on every time the owner switched it off. wait=False: the caller waits once
        for all the lights (quiet_all)."""
        with self.lock:
            self.want, self.sent, self.flash_pending = None, None, False
        if wait:
            time.sleep(self.gap + 0.6)  # a command on its way goes out first (power-on waits up to ~0.7 s)
            self.razer_stop()

    def reset(self, enabled):
        """Starting or taking over: follow the dashboard switch as it is now, without switching anything."""
        with self.lock:
            self.enabled, self.want, self.sent, self.power_on, self.dark_at = enabled, None, None, False, 0.0

    def voice(self, cmd):
        """Rocky's light cue (TvD only): the frame to send instead of cmd, or None. With nobody driving the strip
        (Light Sync paused, Normal's TvD off) it is Home Assistant's: switched on for the cue, and put back after."""
        cue = (rocky_cue.voice_cue(time.time()) if self is rocky_cue.VOICE["light"] and self.enabled and self.segmented() and self.level > 0
               else None)
        if cue and not self.cue_on:
            self.cue_on, self.cue_saved = True, None
            if self.want is None or self.forced_dark:
                try:
                    self.cue_saved = brief(save_states([self.entity_id]))
                    if self.cue_saved[self.entity_id]["state"] != "on":
                        self.power_on = 1
                except Exception as e:
                    print(f"Voice cue: couldn't read {self.entity_id} ({e})", flush=True)
        elif not cue and self.cue_on:
            self.cue_on, self.sent = False, None  # the mode's own frame goes out again
            if self.want is None:  # nobody drives it (now): back to how it was, or how Home Assistant says it is
                saved, self.cue_saved = self.cue_saved, None
                self.razer_stop()
                try:
                    restore_states(saved or brief(save_states([self.entity_id])))
                except Exception as e:
                    print(f"Voice cue: couldn't put {self.entity_id} back ({e})", flush=True)
        if not cue:
            return None
        rgb, s = cue
        n = self.segments
        if cmd is None or self.forced_dark:
            base = np.zeros((n, 3))
        elif cmd[0] == "seg":
            base = np.array(cmd[1], float) * cmd[2] / 255
        else:
            one = colours.kelvin_rgb(cmd[1]) if cmd[0] == "white" else cmd[0]
            base = np.tile(np.array(one, float) * (cmd[2] if cmd[0] == "white" else cmd[1]) / 255, (n, 1))
        w = s * (1 - 0.5 * np.abs(np.linspace(-1, 1, n)))[:, None]  # strongest in the middle, half at the ends
        out = base * (1 - w) + np.array(rgb) * rocky_cue.VOICE_BRIGHT[colours.is_night()] / 255 * w
        return "seg", tuple(tuple(int(round(v)) for v in c) for c in np.clip(out, 0, 255)), 255

    def run(self):
        while True:
            self.wake.wait(self.send_gap() if self.cue_on else 0.5 if self.razer else None)
            self.wake.clear()
            with self.lock:
                cmd = self.want if self.enabled else None
                flash, self.flash_pending = self.flash_pending and self.enabled, False
            if flash:
                cmd = WHITE
            cue = self.voice(cmd)
            if cue:
                cmd, flash = cue, False
            if self.razer and cmd and time.time() - self.razer_at > 0.45:
                # keep the strip in streaming mode: a frame at least twice a second, also while the main loop keeps
                # asking for the same one (Oct 27, strip_log.py: a calm film went 0.8-2.2 s without a frame; the
                # resend only ran when nothing woke this thread, and the main loop wakes it every turn)
                self.sent = None
            if self.enabled and not cue and (self.forced_dark or (cmd and self.level <= 0)):  # off: level 0, or the mode's (TvD in Normal)
                if time.time() - self.dark_at > 10:
                    self.go_dark()
                self.sent_level = 0.0
                time.sleep(self.send_gap())
                continue
            self.dark_at = 0.0
            if cmd and (cmd != self.sent or flash or self.level != self.sent_level) and \
                    (flash or self.level != self.sent_level or self.worth_sending(cmd)):
                self.sent_level = self.level
                self.sent_at = time.time()
                if DEMO:
                    self.sent = cmd
                    time.sleep(self.send_gap())
                    continue
                seg, white = cmd[0] == "seg", cmd[0] == "white"
                if seg:
                    cols, bri, tr = cmd[1], cmd[2], 0
                    rgb = tuple(int(v) for v in np.mean(cols, axis=0))
                elif white:
                    kel, bri, tr = cmd[1], cmd[2], 0
                    rgb = tuple(int(v) for v in colours.kelvin_rgb(kel))
                else:
                    rgb, bri, tr = cmd
                bri_mode = bri  # what the mode asked for, before the light's own level
                bri = max(1, int(bri * self.level))  # the light's own brightness level (dashboard / app; 0 = off, above)
                if self.lan_ip:
                    try:
                        if self.power_on:  # was switched off (taken out of Light Sync, or its brightness at 0)
                            for _ in range(int(self.power_on)):  # 2 after brightness 0: a UDP command can drop
                                LAN.power(self.lan_ip, True)
                                time.sleep(0.15)
                            self.power_on = 0
                            if self.segmented():  # the strip powers up first, then streaming goes on again
                                time.sleep(0.4)
                                self.razer = False
                        if self.want is None and not cue:  # stopped meanwhile (pause, taken out): don't wake it up again
                            time.sleep(self.send_gap())
                            continue
                        if self.segmented() and not white:
                            # stay in streaming mode: one color = every segment the same (no mode flip-flop).
                            # The light's level dims the strip itself (its brightness setting), the frames keep full
                            # colours: at 5 % the frames were 0-7 of 255 and a channel rounding to 0 blinked TvD off
                            # (owner's video, Oct 20)
                            hw = max(1, min(100, round(self.level * 100)))
                            if not self.razer or hw != self.hw_bri:
                                # streaming on (with the brightness command) only when it starts or the level changes:
                                # the brightness command is a normal one, sent every 5 s it switched streaming off for
                                # an instant (Oct 18). Not "on" again every 30 s either: on a strip already streaming it
                                # restarts the stream, dark for ~0.1 s (Oct 28, the owner's video: the wall around the
                                # TV 20 % darker for two frames, on the dot of a 30 s resend). Frames every 0.45 s keep
                                # it streaming; switched on from outside: apply_helper (LIGHT_SCREEN)
                                LAN.razer_mode(self.lan_ip, True, set_brightness=True, brightness=hw)
                                self.razer, self.razer_on_at, self.hw_bri = True, time.time(), hw
                            k = bri_mode / 255
                            frame = cols if seg else [rgb] * self.segments
                            LAN.razer_frame(self.lan_ip, [tuple(v * k for v in colours.strip_correct(c)) for c in frame])  # (Nov 6)
                            self.razer_at = time.time()
                            eyes.DIAG["tvd"] = (eyes.DIAG.get("tvd", []) + [(round(self.razer_at, 2), "seg" if seg else "one",
                                                                   int(k * float(np.mean(frame))))])[-60:]
                        else:
                            if self.razer:
                                self.razer_stop()
                            if white:
                                LAN.white(self.lan_ip, kel, bri)
                            else:
                                LAN.color(self.lan_ip, rgb, bri)
                        self.sent = cmd
                    except Exception as e:
                        print(f"{self.entity_id} (LAN): {e}", flush=True)
                    time.sleep(self.send_gap())
                    continue
                try:
                    ha_link.ha_call("turn_on", {"entity_id": self.entity_id, "brightness": bri, "transition": tr,
                                        **({"color_temp_kelvin": kel} if white else {"rgb_color": list(rgb)})})
                    self.sent = cmd
                except Exception as e:
                    print(f"{self.entity_id}: {e}", flush=True)
            time.sleep(self.send_gap())

def save_states(ids):
    saved = {}
    for eid in ids:
        try:
            saved[eid] = ha_link.ha_state(eid)
        except Exception as e:
            print(f"Couldn't read {eid}: {e}")
    return saved

def brief(saved):
    """Saved light states, trimmed to what restoring needs (handed between laptop and Pi through HA)."""
    keep = ("brightness", "color_mode", "color_temp_kelvin", "rgb_color")
    return {e: {"state": s.get("state"),
                "attributes": {k: s.get("attributes", {}).get(k) for k in keep if s.get("attributes", {}).get(k) is not None}}
            for e, s in saved.items()}

def restore_states(saved):
    for eid, st in saved.items():
        try:
            if st["state"] != "on":
                ha_link.ha_call("turn_off", {"entity_id": eid})
                continue
            a = st.get("attributes", {})
            data = {"entity_id": eid, "transition": 1}
            if a.get("brightness"):
                data["brightness"] = a["brightness"]
            if a.get("color_mode") == "color_temp" and a.get("color_temp_kelvin"):
                data["color_temp_kelvin"] = a["color_temp_kelvin"]
            elif a.get("rgb_color"):
                data["rgb_color"] = a["rgb_color"]
            ha_link.ha_call("turn_on", data)
        except Exception as e:
            print(f"Couldn't restore {eid}: {e}")
