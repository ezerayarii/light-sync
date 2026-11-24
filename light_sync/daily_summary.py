#!/usr/bin/env python3
"""
daily_summary.py - writes an AI summary of your day to your Home Assistant dashboard.

It pulls the day's history from Home Assistant (music sessions, TV ambient, Android box,
lights, presence toggles, phone charging) and your existing daily numbers, then asks Claude
for a short summary like:
  "Desk 4h · Music 9-11pm · Bed ~12:40am"

The "day" runs 4:00 AM -> 4:00 AM so late nights count as the same day.
Result goes to sensor.ai_daily_summary (shown by the "AI summary" card) and summaries/<date>.md.

Run:
  python daily_summary.py            # summarize yesterday (the 5:00 AM task does this)
  python daily_summary.py --today    # summarize today so far (to test)
  python daily_summary.py --dry-run  # show the facts it would send, no AI, nothing posted
  python daily_summary.py --repost   # put the latest summary back on the dashboard

Settings (HA_URL, HA_MEDIA) are read from start_lights.bat; secrets (ha_token,
anthropic_api_key) from secrets.yaml. Camera images and camera notes are never sent to Claude.
"""
import datetime as dt
import json
import os
import re
import sys
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent.parent  # the project folder


def load_bat_settings():
    bat = HERE / "start_lights.bat"
    if not bat.exists():
        return
    for line in bat.read_text(errors="ignore").splitlines():
        m = re.match(r"\s*set\s+([A-Za-z_][A-Za-z0-9_]*)=(.*)$", line)
        if m and not os.getenv(m.group(1)):
            os.environ[m.group(1)] = m.group(2).strip()


def load_secrets():
    """Read secrets.yaml (next to this script) into env vars that aren't already set."""
    path = HERE / "secrets.yaml"
    if not path.exists():
        return
    names = {"ha_token": "HA_TOKEN", "anthropic_api_key": "ANTHROPIC_API_KEY"}
    for line in path.read_text(encoding="utf-8-sig", errors="ignore").splitlines():
        key, sep, value = line.partition(":")
        env = names.get(key.strip())
        if sep and env and not os.getenv(env):
            os.environ[env] = value.strip().strip('"').strip("'")


load_bat_settings()
load_secrets()
HA_URL = os.getenv("HA_URL", "http://homeassistant.local:8123").rstrip("/")
HA_TOKEN = os.getenv("HA_TOKEN", "")
API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
MODEL = os.getenv("SUMMARY_MODEL", "claude-sonnet-5-5")
HA_MEDIA = os.getenv("HA_MEDIA", "media_player.x96q_pro_plus")
DAY_START_HOUR = 4
OUT = HERE / "summaries"
SENSOR = "sensor.ai_daily_summary"

# entity -> {state: activity label}. Same label = merged.
TRACK = {
    # sensor.light_sync = music_sync_status, but "offline" when Light Sync stops reporting (no stale "party")
    "sensor.light_sync": {"party": "music (party lights)", "ambient": "watching (TV ambient)",
                          "gaming": "gaming (screen lights)"},
    HA_MEDIA: {"playing": "Android box playing"},
    "light.chambre_lights": {"on": "bedroom lights on"},
    "input_boolean.late_night_mode": {"on": "late-night mode"},
    "sensor.rocky_battery_state": {"Charging": "phone charging", "Full": "phone charging"},
}

S = requests.Session()
S.headers.update({"Authorization": f"Bearer {HA_TOKEN}"})


def parse_ts(s):
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))


def fmt_time(t):
    return t.astimezone().strftime("%I:%M%p").lstrip("0").lower()


def fmt_dur(sec):
    m = int(round(sec / 60))
    return f"{m // 60}h{m % 60:02d}" if m >= 60 else f"{m} min"


def fetch_history(start, end):
    r = S.get(f"{HA_URL}/api/history/period/{start.isoformat()}",
              params={"end_time": end.isoformat(), "filter_entity_id": ",".join(TRACK),
                      "no_attributes": ""}, timeout=60)
    r.raise_for_status()
    return r.json()


def intervals(changes, wanted, start, end, merge_gap=300):
    """[(state, t)] sorted -> list of (from, to) where state is in `wanted`, clipped, merged."""
    out = []
    for i, (state, t) in enumerate(changes):
        if state not in wanted:
            continue
        t0 = max(t, start)
        t1 = changes[i + 1][1] if i + 1 < len(changes) else end
        t1 = min(t1, end)
        if t1 > t0:
            if out and (t0 - out[-1][1]).total_seconds() <= merge_gap:
                out[-1] = (out[-1][0], t1)
            else:
                out.append((t0, t1))
    return out


def build_facts(start, end):
    facts = []
    try:
        hist = fetch_history(start, end)
    except Exception as e:
        hist = []
        facts.append(f"(Home Assistant history unavailable: {e})")

    by_label = {}
    for series in hist:
        if not series:
            continue
        eid = series[0].get("entity_id")
        wanted = TRACK.get(eid)
        if not wanted:
            continue
        changes = [(x["state"], parse_ts(x["last_changed"])) for x in series]
        changes.sort(key=lambda c: c[1])
        for state in set(wanted):
            label = wanted[state]
            by_label.setdefault(label, []).extend(intervals(changes, {state}, start, end))

    for label, iv in by_label.items():
        iv = sorted(i for i in iv if (i[1] - i[0]).total_seconds() >= 120)
        if not iv:
            continue
        total = sum((b - a).total_seconds() for a, b in iv)
        spans = ", ".join(f"{fmt_time(a)}-{fmt_time(b)}" for a, b in iv[:8])
        more = f" (+{len(iv) - 8} more)" if len(iv) > 8 else ""
        facts.append(f"- {label}: total {fmt_dur(total)}; {spans}{more}")

    # the existing nightly numbers (input_text.daily_summary JSON), if they're for this day
    try:
        raw = S.get(f"{HA_URL}/api/states/input_text.daily_summary", timeout=10).json().get("state", "")
        d = json.loads(raw) if raw.startswith("{") else {}
        if d.get("date") == start.astimezone().date().isoformat():
            keep = {k: d[k] for k in ("arrival", "desk_min", "lights_on_min", "last_lights_off",
                                      "camera_events", "placeholder_sensors") if k in d}
            facts.append(f"- dashboard daily numbers: {json.dumps(keep)}")
    except Exception:
        pass
    return facts


def ask_claude(day_label, facts):
    prompt = (
        f"You write the daily summary card on a smart-home dashboard for {day_label}.\n"
        "The day runs 4am to 4am. Facts from the home (times are local):\n"
        + "\n".join(facts) + "\n\n"
        "Write:\n"
        "1. One stat line with the key numbers joined by ' · ' (like: Desk 4h · Music 9-11pm · Bed ~12:40am).\n"
        "2. Then 2-3 short bullet points: what the day looked like, anything notable.\n"
        "Rules: use only these facts, never invent. Use 12-hour times like 9:40pm. "
        "Bedtime is an estimate (use ~) from the last lights-off, phone starting to charge late, "
        "or the in-bed toggle; skip it if there's no evidence. "
        "Presence toggles may be set by hand, so don't over-trust them. "
        "Plain markdown, under 450 characters, no heading."
    )
    r = requests.post("https://api.anthropic.com/v1/messages", timeout=90,
                      headers={"x-api-key": API_KEY, "anthropic-version": "2023-06-01",
                               "content-type": "application/json"},
                      json={"model": MODEL, "max_tokens": 400,
                            "messages": [{"role": "user", "content": prompt}]})
    r.raise_for_status()
    return "".join(b.get("text", "") for b in r.json()["content"]).strip()


def post_sensor(day_iso, text):
    r = S.post(f"{HA_URL}/api/states/{SENSOR}", timeout=10, json={
        "state": day_iso,
        "attributes": {"summary": text, "friendly_name": "AI daily summary", "icon": "mdi:robot-happy"}})
    r.raise_for_status()


def main():
    if not HA_TOKEN:
        sys.exit("HA_TOKEN missing - put ha_token in secrets.yaml")
    OUT.mkdir(exist_ok=True)

    if "--repost" in sys.argv:
        files = sorted(OUT.glob("????-??-??.md"))
        if files:
            post_sensor(files[-1].stem, files[-1].read_text(encoding="utf-8").strip())
            print(f"Reposted summary for {files[-1].stem}")
        return

    now = dt.datetime.now().astimezone()
    today_start = now.replace(hour=DAY_START_HOUR, minute=0, second=0, microsecond=0)
    if now < today_start:
        today_start -= dt.timedelta(days=1)
    if "--today" in sys.argv:
        start, end = today_start, now
    else:
        start, end = today_start - dt.timedelta(days=1), today_start
    day = start.date()
    day_label = day.strftime("%A %B %d, %Y")

    facts = build_facts(start, end)
    print(f"Facts for {day_label}:")
    print("\n".join(facts) if facts else "(no activity recorded)")
    if "--dry-run" in sys.argv:
        return
    if not facts:
        text = "_Quiet day: nothing was recorded._"
    else:
        if not API_KEY or API_KEY.startswith("PASTE"):
            sys.exit("ANTHROPIC_API_KEY missing - put anthropic_api_key in secrets.yaml")
        text = ask_claude(day_label, facts)
    (OUT / f"{day.isoformat()}.md").write_text(text + "\n", encoding="utf-8")
    post_sensor(day.isoformat(), text)
    print("\nSummary posted to Home Assistant:\n" + text)


if __name__ == "__main__":
    main()
