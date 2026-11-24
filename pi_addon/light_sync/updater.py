"""updater.py - the add-on updates its own files from the laptop (Nov 7: ~12 pastes in one day was the slowest part).

The laptop's tools/pi/send_to_pi.py serves the files and a manifest.json; POST /update on the show server (from the
laptop's address only, like /game) makes this copy them into the add-on's folder (/addons/light_sync, mapped
read-write in config.yaml). Home Assistant then rebuilds the add-on (the laptop asks it through the Supervisor, as
an admin) - the new files run after that. Themes still need the old paste (they live in HA's config folder)."""
import json
import os
import tempfile

import requests

HERE = "/addons/light_sync"


def update(src):
    """Copy every file in src/manifest.json into the add-on folder; remove parts the laptop doesn't have any more."""
    if not os.path.isdir(HERE):
        raise RuntimeError(f"{HERE} isn't mapped (config.yaml: map addons:rw)")
    man = requests.get(src.rstrip("/") + "/manifest.json", timeout=10).json()
    done = []
    for rel, dest in man["files"]:
        if ".." in dest or dest.startswith("/"):
            raise RuntimeError(f"refused path {dest}")
        r = requests.get(src.rstrip("/") + "/" + rel, timeout=20)
        r.raise_for_status()
        path = os.path.join(HERE, dest)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path))
        with os.fdopen(fd, "wb") as f:
            f.write(r.content)
        os.replace(tmp, path)
        done.append(dest)
    # the pose helper add-on's files (Nov 9), next to this add-on's folder
    for rel, name in man.get("pose", []):
        if "/" in name or ".." in name:
            raise RuntimeError(f"refused path {name}")
        r = requests.get(src.rstrip("/") + "/" + rel, timeout=60)
        r.raise_for_status()
        folder = os.path.join(os.path.dirname(HERE), "light_sync_pose")
        os.makedirs(folder, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=folder)
        with os.fdopen(fd, "wb") as f:
            f.write(r.content)
        os.replace(tmp, os.path.join(folder, name))
        done.append("pose/" + name)
    keep = set(man.get("parts", []))
    removed = []
    for p in os.listdir(os.path.join(HERE, "parts")):
        if p.endswith(".py") and p not in keep:
            os.remove(os.path.join(HERE, "parts", p))
            removed.append(p)
    print(f"Update: {len(done)} files from the laptop" + (f", removed {', '.join(removed)}" if removed else ""), flush=True)
    return {"files": len(done), "removed": removed}
