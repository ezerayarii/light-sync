"""The cameras: getting pictures from motionEye, as numbers. No picture is kept."""

import numpy as np
import os
import requests
import threading
import time
from requests.auth import HTTPDigestAuth

from parts.config import SNAP_PASS, SNAP_USER
from parts import config


screen_rgb = None

SAMPLES = None        # TV mode: the 45 colour points (9 boxes x middle + 4 sides) as [(u, v, rgb or None)], or None
SCREEN_LUM = None     # TV mode: the picture's brightness now (0-255, numbers only), or None
SCREEN_SIDES = None   # TV mode: the screen's left, middle and right thirds' colours (raw), or None
SCREEN_SECOND = None  # TV mode: the screen's second colour family (the Cinema look's Corner), or None

def fetch_frame(url, auth, headers):
    """Get one JPEG from a snapshot URL or an MJPEG stream (e.g. motionEye port 8081)."""
    r = requests.get(url, auth=auth, headers=headers, timeout=5, stream=True)
    ctype = r.headers.get("Content-Type", "?")
    if "multipart" in ctype:
        buf = b""
        try:
            for chunk in r.iter_content(16384):
                buf += chunk
                start = buf.find(b"\xff\xd8")
                end = buf.find(b"\xff\xd9", start + 2) if start != -1 else -1
                if start != -1 and end != -1:
                    return r.status_code, "image/jpeg", buf[start:end + 2]
                if len(buf) > 8_000_000:
                    break
        finally:
            r.close()
        return r.status_code, ctype, b""
    return r.status_code, ctype, r.content

CAM_JPG = None  # latest camera frame with the TV box drawn, for the live view

def stream_auth(url=None):
    """Camera login: Digest when the stream asks for it (password never sent in clear), else Basic. Both cameras use
    the same streaming login (motionEye)."""
    if not SNAP_USER:
        return None
    try:
        r = requests.get(url or config.SNAPSHOT_URL, timeout=5, stream=True)
        challenge = r.headers.get("WWW-Authenticate", "")
        r.close()
    except requests.RequestException:
        challenge = ""
    if challenge.lower().startswith("digest"):
        return HTTPDigestAuth(SNAP_USER, SNAP_PASS)
    return (SNAP_USER, SNAP_PASS)

def decode_bgr(data):
    """JPEG -> BGR pixels without OpenCV (the Pi add-on has Pillow only)."""
    import io
    from PIL import Image
    try:
        im = Image.open(io.BytesIO(data))
        # Nov 6 (the Pi drives now): the moved camera's big pictures took long to decode, TV mode ran ~3.6 s behind
        # the screen. JPEG can decode straight at 1/2, 1/4 or 1/8 size: about 640 px wide is plenty for colours
        if im.format == "JPEG" and im.width > 800:
            im.draft("RGB", (im.width // 4, im.height // 4))
        return np.asarray(im.convert("RGB"))[:, :, ::-1]
    except Exception:
        return None

class CamStream:
    """The camera's newest picture, as often as it comes, while TV mode or Gaming needs the screen.
    Keeps the MJPEG stream open (motionEye: ~20 pictures/s); opening a new connection per picture took ~1.3 s, so
    TV mode only saw a picture every ~2.3 s (Oct 18). A route that can't stream (a snapshot URL, or the Pi's way in
    through the Supervisor's proxy, which times out on streams) falls back to POLLERS staggered single-picture
    fetchers (~1.1 s each, so ~2.7 pictures/s with 3)."""

    POLLERS = 3

    def __init__(self, url, snap_url, auth, headers):
        self.url, self.snap_url, self.auth, self.headers = url, snap_url, auth, headers
        self.jpg, self.at, self.status, self.ctype, self.err = None, 0.0, 0, "?", None
        self.running, self.mode, self.started = True, "stream", time.time()
        threading.Thread(target=self.run, daemon=True).start()

    def stop(self):
        self.running = False

    def run(self):
        while self.running:
            try:
                r = requests.get(self.url, auth=self.auth, headers=self.headers, timeout=5, stream=True)
                self.status, self.ctype = r.status_code, r.headers.get("Content-Type", "?")
                if "multipart" not in self.ctype:
                    r.close()
                    break  # not a stream: poll
                # Nov 7: the TV-mode test on the Pi found the lights 12.5 s behind the screen. Each new piece of data
                # copied the whole buffer and searched it again from the start, which the Pi couldn't do as fast as
                # the moved camera's big pictures arrive, so they piled up in the connection. Now only the new
                # bytes are searched, the buffer is trimmed in place, and only the newest picture is kept.
                buf, scan = bytearray(), 0
                for chunk in r.iter_content(65536):
                    if not self.running:
                        break
                    buf += chunk
                    while True:
                        s = buf.find(b"\xff\xd8")
                        if s == -1:
                            del buf[:max(0, len(buf) - 1)]
                            scan = 0
                            break
                        e = buf.find(b"\xff\xd9", max(s + 2, scan))
                        if e == -1:
                            if s:
                                del buf[:s]
                            scan = max(0, len(buf) - 1)
                            break
                        self.jpg, self.at = bytes(buf[s:e + 2]), time.time()
                        del buf[:e + 2]
                        scan = 0
                    if len(buf) > 8_000_000:
                        buf, scan = bytearray(), 0
                r.close()
            except Exception as e:
                self.err = str(e)
                if self.jpg is None:
                    break  # never gave a picture: this route can't stream, poll instead
            if self.running:
                time.sleep(1)
        if self.running:
            self.mode = "poll"
            for i in range(self.POLLERS):
                threading.Thread(target=self.poll, args=(i,), daemon=True).start()

    def poll(self, i):
        time.sleep(i * 1.1 / self.POLLERS)  # staggered, so the pictures arrive evenly
        while self.running:
            try:
                status, ctype, data = fetch_frame(self.snap_url, self.auth, self.headers)
                self.status, self.ctype = status, ctype
                if data and status == 200:
                    self.jpg, self.at = data, time.time()
                else:
                    time.sleep(1)
            except Exception as e:
                self.err = str(e)
                time.sleep(1)

CAM_OK = False  # the last camera read gave a picture

CAM_IDLE = True  # not reading the camera (only TV mode and gaming need the screen)

DIAG = {}  # diagnostics on /state: the wall's brightness around the screen (TvD's glow), numbers only

SCREEN_OFF = False  # the screen looks switched off (much darker than the wall around it)

CAM_FPS = float(os.getenv("CAM_FPS", "0"))  # screen reads per second in TV mode / gaming (0 = 10 laptop, 5 Pi)

screen_zones = None  # TvD segments: the screen's colors left to right (camera), None = not known

laptop_zones = None  # same, from the laptop screen while a game runs

class PictureArea:
    """Where the picture is inside the screen box (Oct 20, owner: "ignore bars and subtitles"): the rows of a film's
    letterbox bars stay dark, so rows whose brightness hasn't come up for ~10 s are left out (a dark scene doesn't
    count, it lights the rows again within the 10 s)."""

    def __init__(self):
        self.row_max = None

    def bounds(self, box):
        """(top, bottom) rows of the picture in this box (the whole box until there are bars)."""
        rows = np.percentile(box.max(axis=2), 90, axis=1).astype(np.float32)  # each row's bright end (not one hot pixel)
        if self.row_max is None or len(self.row_max) != len(rows):
            self.row_max = rows
        self.row_max = np.maximum(rows, self.row_max * 0.993)  # ~10 s at 10 readings/s to forget a bright row
        dark = max(40.0, 0.3 * float(np.percentile(self.row_max, 75)))
        lit = np.nonzero(self.row_max >= dark)[0]
        if not len(lit) or lit[-1] + 1 - lit[0] < 0.4 * len(rows):
            return 0, len(rows)
        return int(lit[0]), int(lit[-1]) + 1
