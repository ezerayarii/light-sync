"""The body-pose helper (the Light Sync Pose add-on on the Pi, 127.0.0.1:8098): a JPEG in, 17 body points back.
Nov 9, owner: only the Pi and the camera, a better solution than movement -> a person is seen even sitting still.
Never needed for the lights: if the helper isn't there, everything works as before (movement only)."""
import time

import requests

URL = "http://127.0.0.1:8098"
S = requests.Session()
STATE = {"ok": None, "checked": 0.0, "ms": None, "last_person": 0.0, "seen": 0, "asked": 0, "down_told": False}
NAMES = ["nose", "l_eye", "r_eye", "l_ear", "r_ear", "l_shoulder", "r_shoulder", "l_elbow", "r_elbow", "l_wrist", "r_wrist",
         "l_hip", "r_hip", "l_knee", "r_knee", "l_ankle", "r_ankle"]


def find(jpg, cam="room"):
    """(person?, points [[y, x, score] x 17] with y, x in 0-1 of the picture) or (None, None) if the helper isn't there.
    After a failure it isn't asked again for 30 s."""
    now = time.time()
    if STATE["ok"] is False and now - STATE["checked"] < 30:
        return None, None
    try:
        t = time.time()
        r = S.post(f"{URL}/pose?cam={cam}", data=jpg, headers={"Content-Type": "image/jpeg"}, timeout=3)
        r.raise_for_status()
        d = r.json()
        if STATE["ok"] is not True:
            print("Pose helper: connected (a person is seen even sitting still)", flush=True)
        STATE.update(ok=True, checked=now, ms=round((time.time() - t) * 1000), down_told=False)
        STATE["asked"] += 1
        if d.get("person"):
            STATE["last_person"] = now
            STATE["seen"] += 1
        return bool(d.get("person")), d.get("points")
    except Exception as e:
        if not STATE["down_told"]:
            print(f"Pose helper: not reachable ({type(e).__name__}) - movement only until it's back", flush=True)
        STATE.update(ok=False, checked=now, down_told=True)
        return None, None


def frame():
    now = time.time()
    return {"ok": STATE["ok"], "ms": STATE["ms"], "asked": STATE["asked"], "seen": STATE["seen"],
            "person_s_ago": round(now - STATE["last_person"]) if STATE["last_person"] else None}
