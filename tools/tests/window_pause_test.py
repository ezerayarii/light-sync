#!/usr/bin/env python3
"""
window_pause_test.py - printing must never hold up Light Sync while its window is paused (a click in the window selects
text, and Windows then stops everyone writing to it; Oct 30: the main loop stuck 24 s and 23 s, the lights frozen).
No lights or sound touched.

  py tools/tests/window_pause_test.py
"""
import os
import sys
import tempfile
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path[:0] = [os.path.join(ROOT, "light_sync"), os.path.join(ROOT, "vendor")]
os.environ["ROLE"] = "test"
import light_sync as ls  # noqa: E402


class PausedWindow:
    """A console window: every write waits while it is paused."""

    def __init__(self):
        self.go = threading.Event()
        self.lines = []

    def write(self, s):
        self.go.wait()
        self.lines.append(s)

    def flush(self):
        self.go.wait()


def main():
    failed = 0

    def check(ok, what, detail=""):
        nonlocal failed
        failed += not ok
        print(f"  {'PASS' if ok else 'FAIL'}  {what}{'  -  ' + detail if detail else ''}")

    with tempfile.TemporaryDirectory() as d:
        log = os.path.join(d, "log.txt")
        window = PausedWindow()
        tee = ls._Tee(window, log)
        worst = 0.0
        for i in range(200):
            t = time.perf_counter()
            print(f"line {i}", file=tee, flush=True)
            worst = max(worst, time.perf_counter() - t)
        check(worst < 0.1, "printing doesn't wait for a paused window", f"slowest print {worst * 1000:.1f} ms")
        with open(log, encoding="utf-8") as f:
            got = f.read().split("\n")[:-1]
        check(got == [f"line {i}" for i in range(200)], "the log has every line, while the window is paused")

        window.go.set()  # the window comes back
        end = time.time() + 3
        while "".join(window.lines).count("\n") < 200 and time.time() < end:
            time.sleep(0.02)
        check("".join(window.lines).split("\n")[:-1] == [f"line {i}" for i in range(200)],
              "the window then gets every line, in order")

        window2 = PausedWindow()
        tee2 = ls._Tee(window2, os.path.join(d, "log2.txt"))
        for i in range(tee2.waiting.maxlen * 2):
            tee2.write(f"{i}\n")
        check(len(tee2.waiting) <= tee2.waiting.maxlen, "a window paused for hours can't fill the memory",
              f"{len(tee2.waiting)} lines waiting")
        window2.go.set()
        tee2.finish()
        tee.file.close()
        tee2.file.close()
    print("All passed" if not failed else f"{failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
