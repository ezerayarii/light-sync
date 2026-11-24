"""The last few hundred lines Light Sync printed, kept in memory for the app's Logs (Nov 6, owner: "I want logs in
the app"). Everything printed still goes where it went (the console, logs/light_sync.log); this only keeps a copy.
Anything that looks like a secret (a long token, a password or key value) is masked before it is kept."""

import re
import sys
import threading
import time
from collections import deque

LINES = deque(maxlen=400)  # (unix time, text)
_lock = threading.Lock()
_SECRET = [re.compile(r"[0-9]{6,}:[A-Za-z0-9_-]{20,}"),           # Telegram-style bot tokens
           re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_.-]{10,}"),  # JWTs (Home Assistant tokens)
           re.compile(r"(?i)(token|password|passwd|secret|api[_-]?key)(\s*[=:]\s*)\S+")]


def clean(text):
    for rx in _SECRET[:2]:
        text = rx.sub("[hidden]", text)
    return _SECRET[2].sub(r"\1\2[hidden]", text)


class _Tee:
    def __init__(self, stream):
        self.stream, self.buf = stream, ""

    def write(self, s):
        n = self.stream.write(s)
        with _lock:
            self.buf += s
            while "\n" in self.buf:
                line, self.buf = self.buf.split("\n", 1)
                if line.strip():
                    LINES.append((time.time(), clean(line.rstrip())))
        return n

    def flush(self):
        self.stream.flush()

    def __getattr__(self, name):
        return getattr(self.stream, name)


def install():
    if not isinstance(sys.stdout, _Tee):
        sys.stdout = _Tee(sys.stdout)
    if not isinstance(sys.stderr, _Tee):
        sys.stderr = _Tee(sys.stderr)


def recent(n=200):
    with _lock:
        return [{"t": round(t, 1), "text": x} for t, x in list(LINES)[-n:]]
