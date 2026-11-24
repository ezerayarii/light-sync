"""First things at startup: the folders, secrets.yaml, the crash log, the log file. Imported before everything else."""

import atexit
import os
import sys
import threading
import time
from collections import deque


# packages kept with the project (soundcard for "Laptop sound"): found however Light Sync is started
# where things are: the program (C:\lightsync\light_sync on the laptop, /app on the Pi), the project folder above it
# on the laptop (secrets.yaml, vendor\, data\, logs\), and the logs
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # light_sync\, one up from parts\

ROOT = os.path.dirname(HERE) if os.path.isfile(os.path.join(os.path.dirname(HERE), "start_lights.bat")) else HERE

LOGS = os.path.join(ROOT, "logs") if ROOT != HERE else HERE

VENDOR = os.path.join(ROOT, "vendor")

if os.path.isdir(VENDOR):
    sys.path.insert(1, VENDOR)

def load_secrets():
    """Read secrets.yaml (next to this script) into env vars that aren't already set."""
    path = os.path.join(ROOT, "secrets.yaml")
    if not os.path.exists(path):
        return
    names = {"ha_token": "HA_TOKEN", "snap_pass": "SNAP_PASS", "anthropic_api_key": "ANTHROPIC_API_KEY"}
    with open(path, encoding="utf-8-sig") as f:
        for line in f:
            key, sep, value = line.partition(":")
            env = names.get(key.strip())
            if sep and env and not os.getenv(env):
                os.environ[env] = value.strip().strip('"').strip("'")

load_secrets()

def log_crashes():
    """Unexpected errors (main loop or any thread) also go to light_sync_errors.log next to this script, with the
    time, so a Light Sync that stopped can be explained afterwards (the window may be closed by then)."""
    import traceback
    path = os.path.join(LOGS, "light_sync_errors.log")

    def write(kind, exc_type, exc, tb):
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} {kind}\n")
                f.write("".join(traceback.format_exception(exc_type, exc, tb)))
        except OSError:
            pass

    def main_hook(exc_type, exc, tb):
        if not issubclass(exc_type, KeyboardInterrupt):
            write("Light Sync stopped on an error", exc_type, exc, tb)
        sys.__excepthook__(exc_type, exc, tb)

    def thread_hook(args):
        write(f"error in thread {args.thread.name if args.thread else '?'}", args.exc_type, args.exc_value, args.exc_traceback)
        sys.__excepthook__(args.exc_type, args.exc_value, args.exc_traceback)

    sys.excepthook = main_hook
    threading.excepthook = thread_hook

log_crashes()

LOOP = {"at": 0.0}  # the main loop's last turn

def watch_for_stall():
    """The main loop stuck (no turn for 20 s): every thread's stack goes to light_sync_errors.log, once per stall, so
    the cause can be found afterwards (Oct 20: a spin in the mic-beat code froze the lights ~13 min, unnoticed while
    the live view and the status kept answering)."""
    import faulthandler
    path = os.path.join(LOGS, "light_sync_errors.log")
    told = False
    while True:
        time.sleep(5)
        late = time.time() - LOOP["at"] if LOOP["at"] else 0.0
        if late > 20 and not told:
            told = True
            print(f"Main loop stuck for {late:.0f} s - every thread's stack is in light_sync_errors.log", flush=True)
            try:
                with open(path, "a", encoding="utf-8") as f:
                    f.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} main loop stuck for {late:.0f} s\n")
                    f.flush()
                    faulthandler.dump_traceback(file=f, all_threads=True)
            except (OSError, ValueError):
                pass
        elif late <= 20 and told:
            told = False
            print("Main loop running again", flush=True)

class _Tee:
    """Everything printed also goes to light_sync.log (this run only), so messages from a minimized or closed
    window can be read afterwards. The window gets it from its own thread: while a window is paused (a click in it
    selects text) Windows holds up everyone writing to it, and the main loop froze the lights at its next print
    (Oct 30: 24 s and 23 s; a window paused 8 s in a real console paused the loop 8.0 s)."""

    def __init__(self, stream, path):
        self.stream = stream
        self.file = open(path, "a", encoding="utf-8", buffering=1)
        self.waiting = deque(maxlen=2000)  # a window paused for long misses the oldest lines; the log has them all
        self.busy = False
        self.wake = threading.Event()
        threading.Thread(target=self.to_window, daemon=True, name="window output").start()
        atexit.register(self.finish)

    def to_window(self):
        while True:
            self.wake.wait()
            self.wake.clear()
            self.busy = True
            while self.waiting:
                try:
                    self.stream.write(self.waiting.popleft())
                except Exception:
                    pass
            try:
                self.stream.flush()
            except Exception:
                pass
            self.busy = False

    def finish(self):
        """Stopping: the last lines still reach the window, unless it is paused."""
        end = time.time() + 1
        while (self.waiting or self.busy) and time.time() < end:
            time.sleep(0.02)

    def write(self, s):
        self.waiting.append(s)
        self.wake.set()
        try:
            self.file.write(s)
        except Exception:
            pass
        return len(s)

    def flush(self):
        try:
            self.file.flush()
        except Exception:
            pass

    def __getattr__(self, name):
        return getattr(self.stream, name)
