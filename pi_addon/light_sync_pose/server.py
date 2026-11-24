"""server.py - Light Sync Pose: POST a JPEG to http://127.0.0.1:8098/pose -> {"person": bool, "points": 17 x [y, x,
score], "ms": time}. 127.0.0.1 only; the picture is decoded in memory and dropped. GET /health -> speed and counts."""
import io
import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading

import numpy as np
from PIL import Image

import pose

POSES = {}  # one tracker (its own crop memory) per caller's "cam" name
LOCK = threading.Lock()
STATS = {"n": 0, "ms": 0.0, "started": time.time()}


class H(BaseHTTPRequestHandler):
    def _send(self, code, body):
        b = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if self.path.startswith("/health"):
            n = max(1, STATS["n"])
            self._send(200, {"ok": True, "pictures": STATS["n"], "avg_ms": round(STATS["ms"] / n, 1),
                             "up_s": round(time.time() - STATS["started"])})
        else:
            self._send(404, {})

    def do_POST(self):
        if not self.path.startswith("/pose"):
            self._send(404, {})
            return
        try:
            data = self.rfile.read(min(int(self.headers.get("Content-Length", 0)), 8 << 20))
            cam = self.path.split("cam=")[1][:20] if "cam=" in self.path else "room"
            im = Image.open(io.BytesIO(data))
            if im.format == "JPEG" and im.width > 1400:
                im.draft("RGB", (im.width // 2, im.height // 2))  # half size is plenty for the model, decodes faster
            img = np.asarray(im.convert("RGB"))
            t = time.time()
            with LOCK:
                p = POSES.get(cam) or POSES.setdefault(cam, pose.Pose("/app/movenet.onnx"))
                ok, pts = p.find(img)
            ms = (time.time() - t) * 1000
            STATS["n"] += 1
            STATS["ms"] += ms
            self._send(200, {"person": bool(ok), "points": [[round(float(v), 4) for v in row] for row in pts], "ms": round(ms, 1)})
        except Exception as e:
            self._send(400, {"error": str(e)[:200]})

    def log_message(self, *a):
        pass


print("Light Sync Pose: listening on 127.0.0.1:8098", flush=True)
ThreadingHTTPServer(("127.0.0.1", 8098), H).serve_forever()
