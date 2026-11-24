"""
usb_backup.py - a copy of Home Assistant's backups on a USB stick in the laptop (laptop only; Oct 30, owner: backups off
the Pi's SD card, on USB). Home Assistant OS can't use a stick on the Pi without changing system files, so the laptop
fetches them: if the SD card dies, the copies are on another machine.

Every 30 minutes (and soon after the laptop wakes up), for each backup not on the stick yet, newest first:
  download   as a .part file, Home Assistant's stored file as it is (still encrypted), at most MAX_RATE
  verify     the size; the file read back from the stick itself, past Windows' cache, must have the same SHA-256; a
             whole tar whose backup.json has this backup's id and says "protected"
  keep       only then its real name. The last KEEP_AUTO automatic copies stay (older ones are removed); copies of
             manual backups stay until removed by hand

Only a removable drive with a "Home Assistant backups" folder at its root is ever written to, and only copies this job
named are ever removed. A backup that isn't encrypted is not copied: the stick holds every password in the house.
While a game or a film runs it waits (the download shares the Wi-Fi), unless the newest copy is older than BUSY_WAIT_S.
sensor.light_sync_usb_backup shows the result; a problem older than ALERT_AFTER_S reaches the phone, once a day.

  py light_sync/usb_backup.py              # one round now
  py light_sync/usb_backup.py --status     # what's on the stick
  py light_sync/usb_backup.py --setup E:   # first time: mark this stick (makes the folder)
"""
import datetime as dt
import hashlib
import json
import os
import re
import sys
import tarfile
import threading
import time

import requests

FOLDER = "Home Assistant backups"
AGENT = "hassio.local"
SENSOR = "sensor.light_sync_usb_backup"
KEEP_AUTO = 7
MIN_AUTO = 3                  # stick short of room: automatic copies go oldest first, down to this many
MAX_RATE = 8 * 1024 * 1024    # bytes a second
CHECK_S, RETRY_S = 1800, 600
BUSY_WAIT_S = 36 * 3600
ALERT_AFTER_S = 2 * 86400
COPY = re.compile(r"^(\d{4}-\d{2}-\d{2})_(\d{4})_(auto|manual)_([0-9A-Za-z]+)_.*\.tar$")


def find_folder():
    """The stick's folder: a removable drive (never a fixed or cloud one) with FOLDER at its root, or None."""
    import ctypes
    k = ctypes.windll.kernel32
    mask = k.GetLogicalDrives()
    for i in range(26):
        root = f"{chr(65 + i)}:\\"
        if mask >> i & 1 and k.GetDriveTypeW(root) == 2 and os.path.isdir(root + FOLDER):
            return root + FOLDER
    return None


def copy_name(b):
    kind = "auto" if b.get("with_automatic_settings") else "manual"
    date = b["date"][:16].replace("T", "_").replace(":", "")
    safe = re.sub(r"[^\w .-]", "_", b.get("name") or "backup")[:60].strip()
    return f"{date}_{kind}_{b['backup_id']}_{safe}.tar"


def copies(folder):
    """The finished copies on the stick, oldest first: [(file name, kind, backup id)]."""
    out = []
    for n in sorted(os.listdir(folder)):
        m = COPY.match(n)
        if m:
            out.append((n, m.group(3), m.group(4)))
    return out


def copy_age(name, now=None):
    """Seconds since the backup in this copy's name was made."""
    m = COPY.match(name)
    made = dt.datetime.strptime(m.group(1) + m.group(2), "%Y-%m-%d%H%M").timestamp()
    return (now or time.time()) - made


def rotate(folder, keep=KEEP_AUTO):
    """Only the newest automatic copies stay. Returns the names removed."""
    autos = [n for n, kind, _ in copies(folder) if kind == "auto"]
    gone = autos[:-keep] if len(autos) > keep else []
    for n in gone:
        os.remove(os.path.join(folder, n))
    return gone


def make_room(folder, need):
    """need bytes free: automatic copies go oldest first, down to MIN_AUTO. False if that's not enough."""
    import shutil
    while shutil.disk_usage(folder).free < need:
        autos = [n for n, kind, _ in copies(folder) if kind == "auto"]
        if len(autos) <= MIN_AUTO:
            return False
        os.remove(os.path.join(folder, autos[0]))
    return True


def read_back_sha(path):
    """SHA-256 of the file as it is on the stick: past Windows' cache, or a bad stick would look fine until the day
    it's needed."""
    import ctypes
    import ctypes.wintypes as wt
    import mmap
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.CreateFileW.restype = wt.HANDLE
    k.CreateFileW.argtypes = (wt.LPCWSTR, wt.DWORD, wt.DWORD, wt.LPVOID, wt.DWORD, wt.DWORD, wt.HANDLE)
    k.ReadFile.argtypes = (wt.HANDLE, wt.LPVOID, wt.DWORD, ctypes.POINTER(wt.DWORD), wt.LPVOID)
    k.CloseHandle.argtypes = (wt.HANDLE,)
    # GENERIC_READ, FILE_SHARE_READ, OPEN_EXISTING, FILE_FLAG_NO_BUFFERING | FILE_FLAG_SEQUENTIAL_SCAN
    h = k.CreateFileW(path, 0x80000000, 1, None, 3, 0x20000000 | 0x08000000, None)
    if h in (None, wt.HANDLE(-1).value):
        raise OSError(f"can't read it back ({ctypes.get_last_error()})")
    size = 1 << 20
    buf = mmap.mmap(-1, size)  # page-aligned, as unbuffered reads need
    holder = ctypes.c_char.from_buffer(buf)
    got, sha = wt.DWORD(), hashlib.sha256()
    try:
        while True:
            if not k.ReadFile(h, ctypes.addressof(holder), size, ctypes.byref(got), None):
                raise OSError(f"read error on the stick ({ctypes.get_last_error()})")
            if not got.value:
                return sha.hexdigest()
            sha.update(memoryview(buf)[:got.value])
    finally:
        k.CloseHandle(h)
        del holder
        buf.close()


def check_tar(path, backup_id):
    """A whole tar (every header walked), this backup's backup.json, and encrypted."""
    with tarfile.open(path, "r:") as t:
        meta = None
        for m in t:
            if m.name.lstrip("./") == "backup.json":
                meta = json.load(t.extractfile(m))
    if not meta or meta.get("slug") != backup_id:
        raise RuntimeError("the file isn't this backup")
    if not meta.get("protected"):
        raise RuntimeError("the copy isn't encrypted")


def fetch(ha_url, token, b, folder, say=print):
    """One backup onto the stick. Its real name only after every check passed."""
    size = b["agents"][AGENT]["size"]
    name = copy_name(b)
    part = os.path.join(folder, name + ".part")
    sha, got, t0 = hashlib.sha256(), 0, time.time()
    with requests.get(f"{ha_url}/api/backup/download/{b['backup_id']}", params={"agent_id": AGENT}, stream=True,
                      headers={"Authorization": f"Bearer {token}"}, timeout=(10, 120)) as r:
        r.raise_for_status()
        with open(part, "wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)
                sha.update(chunk)
                got += len(chunk)
                wait = got / MAX_RATE - (time.time() - t0)
                if wait > 0:
                    time.sleep(wait)
            f.flush()
            os.fsync(f.fileno())
    took = time.time() - t0
    if got != size:
        raise RuntimeError(f"incomplete download ({got} of {size} bytes)")
    t1 = time.time()
    if read_back_sha(part) != sha.hexdigest():
        raise RuntimeError("the stick gave back different data: the stick may be failing")
    check_tar(part, b["backup_id"])
    os.replace(part, os.path.join(folder, name))
    say(f"USB backup: {name}: {got / 1e6:.0f} MB in {took:.0f} s, read back and checked in {time.time() - t1:.0f} s")
    return name


def ha_backups(ms):
    """backup/info from Home Assistant."""
    ws = ms.HASocket(ms.HA_URL, ms.HA_TOKEN)
    try:
        ws.send({"id": 1, "type": "backup/info"})
        while True:
            m = ws.recv()
            if m.get("id") == 1 and m.get("type") == "result":
                if not m.get("success"):
                    raise RuntimeError(str(m.get("error"))[:100])
                return m["result"]
    finally:
        ws.sock.close()


def run_round(ms, force=False, say=print):
    """One check. Returns the status: newest, copies, free_gb, problem, waiting, unprotected, checked."""
    import shutil
    st = {"checked": round(time.time()), "newest": None, "copies": 0, "free_gb": None, "problem": None}
    folder = find_folder()
    if not folder:
        st["problem"] = "the USB stick isn't plugged in"
        return st
    try:
        for n in os.listdir(folder):  # what a stopped copy left
            if n.endswith(".part") and COPY.match(n[:-5]):
                os.remove(os.path.join(folder, n))
        have = {bid for _, _, bid in copies(folder)}
        backups = sorted(ha_backups(ms)["backups"], key=lambda b: b["date"], reverse=True)
        local = [b for b in backups if AGENT in (b.get("agents") or {})]
        st["unprotected"] = sum(not b["agents"][AGENT].get("protected") for b in local)
        todo = [b for b in local if b["agents"][AGENT].get("protected") and b["backup_id"] not in have]
        for b in todo:
            names = [n for n, _, _ in copies(folder)]
            overdue = not names or min(copy_age(n) for n in names) > BUSY_WAIT_S
            if not force and not overdue and ms.STATE.get("mode") in ("gaming", "ambient"):
                st["waiting"] = "a game or a film is on"
                break
            if not make_room(folder, b["agents"][AGENT]["size"] * 1.02 + 100e6):
                st["problem"] = "the USB stick is full"
                break
            fetch(ms.HA_URL, ms.HA_TOKEN, b, folder, say)
            for n in rotate(folder):
                say(f"USB backup: removed the old copy {n}")
    except Exception as e:  # never in Light Sync's way: reported, tried again soon
        st["problem"] = f"{type(e).__name__}: {e}"[:160]
    try:
        names = [n for n, _, _ in copies(folder)]
        st["copies"] = len(names)
        st["free_gb"] = round(shutil.disk_usage(folder).free / 1e9, 1)
        if names:
            newest = max(names)
            st["newest"] = newest[:15]
            st["age_s"] = round(copy_age(newest))
            if not st["problem"] and st["age_s"] > ALERT_AFTER_S:
                st["problem"] = f"the newest backup is {st['age_s'] // 86400} days old: Home Assistant isn't making new ones"
    except OSError as e:  # pulled out during the round
        st["problem"] = st["problem"] or f"the USB stick went away ({e})"[:160]
    return st


def report(ms, st):
    """The status for the live view and Home Assistant."""
    ms.STATE["usb_backup"] = st
    attrs = {"friendly_name": "USB backup", "icon": "mdi:usb-flash-drive", **{k: v for k, v in st.items() if v is not None}}
    try:
        ms.session.post(f"{ms.HA_URL}/api/states/{SENSOR}", timeout=5,
                        json={"state": "problem" if st["problem"] else "ok", "attributes": attrs})
    except Exception as e:
        print(f"USB backup: couldn't report ({e})", flush=True)


def loop(ms):
    time.sleep(120)  # Light Sync settles first
    last, wait, last_good, last_alert = 0.0, 0, time.time(), 0.0
    while True:
        now = time.time()  # by the clock: after the laptop slept, the check comes within a minute
        if now - last >= wait:
            last = now
            try:
                st = run_round(ms, say=lambda text: print(text, flush=True))
            except Exception as e:
                st = {"checked": round(now), "newest": None, "copies": 0, "free_gb": None, "problem": str(e)[:160]}
            report(ms, st)
            wait = RETRY_S if st["problem"] else CHECK_S
            age = st.get("age_s")
            if age is not None:
                last_good = now - age
            if st["problem"] and now - last_good > ALERT_AFTER_S and now - last_alert > 86400:
                last_alert = now
                try:
                    ms.session.post(f"{ms.HA_URL}/api/services/{ms.WIFI_NOTIFY}", timeout=5, json={
                        "title": "USB backup problem", "level": "important", "topic": "backups",
                        "message": f"No new backup copy for over 2 days: {st['problem']}."})
                except Exception as e:
                    print(f"USB backup: couldn't notify ({e})", flush=True)
        time.sleep(60)


def start(ms):
    threading.Thread(target=loop, args=(ms,), daemon=True, name="usb backup").start()


def main():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for line in open(os.path.join(root, "start_lights.bat"), encoding="utf-8"):
        if line.lower().startswith("set ") and "=" in line:
            k, v = line[4:].strip().split("=", 1)
            if k == "HA_URL":
                os.environ.setdefault(k, v)
    sys.path.insert(0, os.path.join(root, "vendor"))
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import light_sync as ms
    if "--setup" in sys.argv:
        import ctypes
        letter = sys.argv[sys.argv.index("--setup") + 1].rstrip(":\\/").upper()
        if ctypes.windll.kernel32.GetDriveTypeW(letter + ":\\") != 2:
            sys.exit(f"{letter}: isn't a removable drive")
        os.makedirs(f"{letter}:\\{FOLDER}", exist_ok=True)
        print(f"{letter}:\\{FOLDER} is ready")
    elif "--status" in sys.argv:
        folder = find_folder()
        print(folder or "no stick with the folder plugged in")
        for n, kind, _ in copies(folder) if folder else []:
            print(f"  {os.path.getsize(os.path.join(folder, n)) / 1e6:6.0f} MB  {n}")
    else:
        st = run_round(ms, force=True)
        report(ms, st)
        print(st)


if __name__ == "__main__":
    main()
