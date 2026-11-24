#!/usr/bin/env python3
"""
simplify.py - Normal mode and the simple dashboard (safe to run again):

  Normal mode   input_boolean.normal_mode ("Normal light") + script.normal_light ("Normal", the quick button):
                Light Sync off, plain white light that follows the time of day (human-centric lighting):
                  07:00-18:00 neutral white 4000 K 80 %, 18:00-21:00 warm white 2700 K 60 %,
                  21:00-07:00 amber 2200 K 30 % (same night as Comfort: no blue light before sleep).
                At 07:00 / 18:00 / 21:00 it fades to the next one over 2 minutes. It ends when a Light Sync mode
                comes on or the lights are off for 15 s. sensor.light_sync then reads "normal".
  Clean up      the front page: tools/ha_setup/dashboard.py (built like the Light Sync app; the header and badges here);
                the Settings tab (was "Setup") has Music / TV / Gaming / Lights / Sound / Sleep, each
                setting once, in the Light Sync app's order, then the to-do lists. Off the screens (values kept):
                sensitivity, beat lock, latency, the presence placeholders and the daily summary.
                The two switched-off automations nobody uses ("auto light", "TV ambient (camera test)") are
                deleted (owner said yes on Oct 18; copies are in git history, ha/automations.json).

  py tools/ha_setup/simplify.py
"""
import json
import os
import sys

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # tools\ (export_ha)
import export_ha as ex  # noqa: E402

HA = os.getenv("HA_URL", "http://192.168.1.10:8123")
H = {"Authorization": f"Bearer {ex.token()}"}
LIGHTS = "light.chambre_lights"
NORMAL = "input_boolean.normal_mode"
MODES = ["input_boolean.music_sync", "input_boolean.tv_ambient", "input_boolean.gaming_mode"]
OLD_AUTOMATIONS = {"1787469458790": "auto light", "sh_tv_ambient": "Smart home · TV ambient (camera test)"}
# the time of day -> color temperature and brightness (keep in step with the docstring and the docs)
K = "{{ 4000 if 7 <= now().hour < 18 else (2700 if 18 <= now().hour < 21 else 2200) }}"
B = "{{ 80 if 7 <= now().hour < 18 else (60 if 18 <= now().hour < 21 else 30) }}"

SCRIPTS = {
    "normal_light_apply": {
        "alias": "Normal light · set by time", "icon": "mdi:theme-light-dark", "mode": "restart",
        "description": "Sets the bedroom lights to the Normal light of this time of day, at once or with a 2 minute fade.",
        "fields": {"fade": {"name": "Fade", "description": "Fade over 2 minutes instead of at once",
                            "selector": {"boolean": {}}, "default": False}},
        "sequence": [
            {"variables": {
                "k": K, "b": B,
                "k0": "{{ state_attr('" + LIGHTS + "', 'color_temp_kelvin') or (" + K[3:-3] + ") }}",
                "b0": "{{ ((state_attr('" + LIGHTS + "', 'brightness') or 0) / 2.55) | round(0) if is_state('" + LIGHTS
                      + "', 'on') else (" + B[3:-3] + ") }}",
                "steps": "{{ 12 if fade | default(false) else 1 }}"}},
            {"repeat": {"count": "{{ steps }}", "sequence": [
                {"action": "light.turn_on", "target": {"entity_id": LIGHTS},
                 "data": {"color_temp_kelvin": "{{ (k0 + (k - k0) * repeat.index / steps) | round(0) | int }}",
                          "brightness_pct": "{{ (b0 + (b - b0) * repeat.index / steps) | round(0) | int }}"}},
                {"delay": {"seconds": "{{ 10 if steps > 1 else 0 }}"}}]}},
            {"delay": {"seconds": 1}},
            # once more: Govee LAN uses UDP, a command can drop
            {"action": "light.turn_on", "target": {"entity_id": LIGHTS},
             "data": {"color_temp_kelvin": "{{ k }}", "brightness_pct": "{{ b }}"}}],
    },
    "normal_light": {
        "alias": "Normal", "icon": "mdi:lightbulb-on-outline", "mode": "restart",
        "description": "Light Sync off, plain white light like normal lamps that follows the time of day.",
        "sequence": [{"action": "script.turn_off",  # a pending All off / Good night would switch it off (Oct 21)
                      "target": {"entity_id": ["script.all_lights_off", "script.good_night", "script.fade_chambre_lights_off"]}},
                     {"if": [{"condition": "state", "entity_id": NORMAL, "state": "on"}],
                      # already on: only Home Assistant's own white when Light Sync is down (Nov 2: it put Corner and Top on, fixed,
                      # over the room Normal was showing)
                      "then": [{"if": [{"condition": "template", "value_template": ("{% set s = states.sensor.music_sync_status %}{{ s is none or s.state in ['unknown', 'unavailable', "
                                                                     "'offline'] or (now() - s.last_reported).total_seconds() > 90 }}")}],
                                "then": [{"action": "script.normal_light_apply", "data": {"fade": False}}]}],
                      "else": [{"action": "input_boolean.turn_on", "target": {"entity_id": NORMAL}}]}],
    },
}

# Light Sync isn't running (laptop and Pi both off): Home Assistant's own Normal light is the fallback
LS_DOWN = ("{% set s = states.sensor.music_sync_status %}{{ s is none or s.state in ['unknown', 'unavailable', "
           "'offline'] or (now() - s.last_reported).total_seconds() > 90 }}")
NORMAL_SCENES = ["Follow the day", "Bright", "Relax", "Read", "Night"]

AUTOMATIONS = {
    "sh_normal_light_on": {
        "alias": "Smart home · Normal light on",
        "description": "Normal light switched on (Quick button, app, dashboard): the other Light Sync modes off; Light "
                       "Sync drives Normal (Oct 20: white on Corner and Top, following the day, learned). Only when "
                       "Light Sync isn't running at all (laptop and Pi off): the plain time-of-day white from here.",
        "triggers": [{"trigger": "state", "entity_id": NORMAL, "from": "off", "to": "on"}],
        "conditions": [],
        "actions": [{"action": "input_boolean.turn_off", "target": {"entity_id": MODES}},
                    {"delay": {"seconds": 3}},
                    {"condition": "state", "entity_id": NORMAL, "state": "on"},
                    {"condition": "template", "value_template": LS_DOWN},
                    {"action": "script.normal_light_apply", "data": {"fade": False}}],
        "mode": "restart",
    },
    "sh_normal_light_follows_time": {
        "alias": "Smart home · Normal light follows the time of day",
        "description": "07:00 neutral white, 18:00 warm white, 21:00 amber: a 2 minute fade, only while Normal light is "
                       "on, the lights are on and the sunrise isn't running.",
        "triggers": [{"trigger": "time", "at": t} for t in ("07:00:00", "18:00:00", "21:00:00")],
        "conditions": [{"condition": "state", "entity_id": NORMAL, "state": "on"},
                       {"condition": "state", "entity_id": LIGHTS, "state": "on"},
                       {"condition": "state", "entity_id": "script.sunrise_chambre", "state": "off"},
                       {"condition": "template", "value_template": LS_DOWN}],  # Light Sync follows the day itself
        "actions": [{"action": "script.normal_light_apply", "data": {"fade": True}}],
        "mode": "restart",
    },
    "sh_normal_light_off": {
        "alias": "Smart home · Normal light ends",
        "description": "Normal light switches itself off when a Light Sync mode comes on (Party, Movie time, Gaming, "
                       "media apps) or the bedroom lights stay off for 15 s (All off, Good night, away).",
        "triggers": [{"trigger": "state", "entity_id": MODES, "to": "on"},
                     {"trigger": "state", "entity_id": LIGHTS, "to": "off", "for": {"seconds": 15}}],
        "conditions": [{"condition": "state", "entity_id": NORMAL, "state": "on"}],
        "actions": [{"action": "input_boolean.turn_off", "target": {"entity_id": NORMAL}}],
        "mode": "single",
    },
}

# sensor.light_sync: the mode, "normal" while Normal light is on and Light Sync isn't driving, or "offline"
MODE_TEMPLATE = ("{% set s = states.sensor.music_sync_status %}{% set m = 'offline' if s is none or s.state in "
                 "['unknown', 'unavailable', 'offline'] or (now() - s.last_reported).total_seconds() > 90 else s.state %}"
                 "{{ 'normal' if m in ['paused', 'offline'] and is_state('" + NORMAL + "', 'on') else m }}")

HEADER = ("## Light Sync\n{% set m = states('sensor.light_sync') %}{% set host = state_attr('sensor.music_sync_status', "
          "'host') %}{% set bpm = states('sensor.music_sync_bpm') | int(0) %}{%- if m == 'normal' -%}**NORMAL LIGHT** · "
          "plain white that follows the time of day{%- elif m == 'offline' -%}Offline · starts with the laptop, or the Pi "
          "add-on takes over{%- elif m == 'paused' -%}Paused · tap Normal, Party, Movie time or Gaming{%- else -%}"
          "**{{ m | upper }}**{% if bpm > 0 %} · {{ bpm }} BPM{% endif %} · running on the {{ host or 'laptop' }}{%- endif %}")


FLASH_NOTE = "Flash is paused while Comfort is on (no flashes)."


def _head(text, icon):
    return {"type": "heading", "heading": text, "icon": icon, "heading_style": "title"}


def _toggle(eid, name, icon, cols=6, color="pink"):
    return {"type": "tile", "entity": eid, "name": name, "icon": icon, "color": color, "vertical": False,
            "tap_action": {"action": "toggle"}, "grid_options": {"columns": cols}}


def _select(eid, name, icon, color="purple"):
    return {"type": "tile", "entity": eid, "name": name, "icon": icon, "color": color, "vertical": False,
            "tap_action": {"action": "more-info"}, "features": [{"type": "select-options"}],
            "features_position": "bottom", "grid_options": {"columns": 12}}


def _slider(eid, name, icon, cols=12, color="cyan"):
    return {"type": "tile", "entity": eid, "name": name, "icon": icon, "color": color, "vertical": False,
            "tap_action": {"action": "more-info"}, "features": [{"type": "numeric-input", "style": "slider"}],
            "features_position": "bottom", "grid_options": {"columns": cols}}


def _note(text, **extra):
    return {"type": "markdown", "text_only": True, "content": f"<small>{text}</small>", **extra}


SETTINGS_ORDER = ["Normal", "Music", "TV", "Gaming", "Lights", "Sound", "Sleep", "For you"]


def settings_sections():
    """The Settings tab, in the Light Sync app's order (Oct 19 redesign): each mode's own settings (Music, TV,
    Gaming), then what counts in every mode (Lights, Sound, Sleep). Each setting once."""
    normal = {"type": "grid", "cards": [
        _head("Normal", "mdi:lightbulb-on-outline"),
        _note("Everyday white on Corner and Top (TvD off). It follows the day: bright by day, warm in the evening, "
              "dim at night. What you set at a time of day, it learns and does by itself the next days."),
        _select("input_select.light_sync_normal_scene", "Scene", "mdi:palette-swatch", "amber"),
        _slider("input_number.light_sync_normal_bright", "Brightness now", "mdi:brightness-6", 6, "amber"),
        _slider("input_number.light_sync_normal_kelvin", "Warmth now (K)", "mdi:thermometer", 6, "amber")]}
    music = {"type": "grid", "cards": [
        _head("Music", "mdi:music"),
        _note("How the lights dance to the music. What each light listens to: Lights, below."),
        _select("input_select.light_sync_colors", "Colours", "mdi:palette", "pink"),
        {"type": "tile", "entity": "input_text.light_sync_my_colors", "name": "My colours (#rrggbb, comma separated)",
         "icon": "mdi:eyedropper", "color": "pink", "tap_action": {"action": "more-info"}, "grid_options": {"columns": 12},
         "visibility": [{"condition": "state", "entity": "input_select.light_sync_colors", "state": "mine"}]},
        _select("input_select.music_style", "Movement", "mdi:swap-horizontal", "pink"),
        _note("Flow: colours travel TvD → Corner → Top · Flow back: Top → TvD · Bounce: one colour goes up, the next "
              "comes back · Together: one colour · Each own: every light its own colour."),
        _toggle("input_boolean.light_sync_auto_dj", "Auto DJ · movement and pulses follow the song's parts",
                "mdi:robot-happy-outline", 12),
        _toggle("input_boolean.light_sync_reaction_auto", "Reaction follows the rhythm", "mdi:metronome", 12),
        _slider("input_number.light_sync_reaction", "Reaction speed (when not following the rhythm)", "mdi:speedometer"),
        _slider("input_number.light_sync_bright_min", "Lowest brightness", "mdi:brightness-4", 6),
        _slider("input_number.light_sync_bright_max", "Highest brightness", "mdi:brightness-7", 6),
        _note(f"⚠ {FLASH_NOTE} Turn Comfort off to use it.",
              visibility=[{"condition": "state", "entity": "input_boolean.light_sync_comfort", "state": "on"}]),
        _select("input_select.music_flash", "Flash (never on TvD)", "mdi:flash", "amber")]}
    tv = {"type": "grid", "cards": [
        _head("TV", "mdi:television"),
        _note("The camera watches the screen and the lights take its colours: only the TV's colours, soft and slow. "
              "Brighter scenes fade in slowly (no flashes); black bars and subtitles don't count."),
        _toggle("input_boolean.light_sync_cinema", "Cinema look · Corner and Top a low, slow glow", "mdi:movie-open-outline",
                12, "cyan"),
        _toggle("input_boolean.tv_music", "Music in TV mode · moving with the music's beat", "mdi:television-play", 12, "cyan")]}
    gaming = {"type": "grid", "cards": [
        _head("Gaming", "mdi:controller"),
        _toggle("input_boolean.auto_gaming", "Auto gaming", "mdi:controller-classic", color="lime"),
        _toggle("input_boolean.game_music", "Music in games", "mdi:music-note", color="lime")]}
    lights = {"type": "grid", "cards": [
        _head("Lights", "mdi:lightbulb-group-outline"),
        _note("Each light on its own. In Light Sync off = use it by hand. Listens to: all = the whole song, bass = kick "
              "and bass, mids = voices and instruments, highs = hi-hats and cymbals, mix = its colour from the sound "
              "(bass red, mids green, highs blue). The brightness counts in every mode; 0 = that light off."),
        *[c for role, name, icon in (("screen", "TvD", "mdi:monitor-shimmer"), ("beat", "Corner", "mdi:lamp"),
                                     ("wash", "Top", "mdi:ceiling-light"))
          for c in (_toggle(f"input_boolean.light_sync_use_{role}", f"{name} in Light Sync", icon, 12, "deep-purple"),
                    _select(f"input_select.light_sync_part_{role}", f"{name} listens to", icon),
                    _toggle(f"input_boolean.light_sync_pulse_{role}", f"{name} pulses on the beat", "mdi:pulse", 6),
                    _slider(f"input_number.light_sync_level_{role}", f"{name} brightness (0 = off)", "mdi:brightness-6", 6),
                    *([_toggle("input_boolean.light_sync_tvd_segments", "TvD segments · the screen left to right",
                               "mdi:led-strip-variant", 12)] if role == "screen" else []))]]}
    sound = {"type": "grid", "cards": [
        _head("Sound", "mdi:waveform"),
        _select("input_select.music_mic", "Sound source", "mdi:microphone"),
        _note("Auto: the laptop's own sound while it plays something, otherwise the room microphone (camera). "
              "The phone's mic: in the Light Sync app.")]}
    sleep = {"type": "grid", "cards": [
        _head("Sleep", "mdi:weather-night"),
        _toggle("input_boolean.late_night_mode", "Late night · dim, warm light (23:00-06:00 by itself)", "mdi:weather-night",
                12, "deep-purple"),
        _note("Sunrise wake-up: on the front page.")]}
    foryou = {"type": "grid", "cards": [
        _head("For you", "mdi:brain"),
        _toggle("input_boolean.light_sync_auto_learn", "Switch by itself at my usual times", "mdi:brain", 12, "purple"),
        {"type": "tile", "entity": "input_text.light_sync_last_mode", "name": "Last mode", "icon": "mdi:history",
         "color": "purple", "vertical": False, "grid_options": {"columns": 6}},
        # Nov 6: no Resume button (owner: "could be auto"): coming back into the room or switching the lights on
        # brings the last mode back by itself
        _note("Light Sync learns what you use and when (on the laptop only). Coming back into the room or switching the "
              "lights back on brings back the last mode by itself. Suggestions: the Light Sync app's For you card.")]}
    return [normal, music, tv, gaming, lights, sound, sleep, foryou]


def heading(sec):
    return (sec.get("cards") or [{}])[0].get("heading")


def ws_connect():
    ws = ex.WebSocket(HA)
    ws.recv()
    ws.send({"type": "auth", "access_token": ex.token()})
    assert ws.recv().get("type") == "auth_ok"
    return ws


def normal_mode(ws):
    if requests.get(f"{HA}/api/states/{NORMAL}", headers=H, timeout=10).status_code == 404:
        ws.call(type="input_boolean/create", name="normal mode", icon="mdi:lightbulb-on-outline")
    ws.call(type="input_boolean/update", input_boolean_id="normal_mode", name="Normal light", icon="mdi:lightbulb-on-outline")
    # Normal's controls (Oct 20): the brightness and warmth now (moving them teaches Light Sync), and a scene
    for oid, name, lo, hi, step, unit, first, icon in (
            ("light_sync_normal_bright", "Normal brightness", 5, 100, 5, "%", 85, "mdi:brightness-6"),
            ("light_sync_normal_kelvin", "Normal warmth", 2000, 6500, 100, "K", 4000, "mdi:thermometer")):
        if requests.get(f"{HA}/api/states/input_number.{oid}", headers=H, timeout=10).status_code == 404:
            ws.call(type="input_number/create", name=oid.replace("_", " "), min=lo, max=hi, step=step, initial=first,
                    mode="slider", unit_of_measurement=unit, icon=icon)
        ws.call(type="input_number/update", input_number_id=oid, name=name, min=lo, max=hi, step=step, mode="slider",
                unit_of_measurement=unit, icon=icon)
    if requests.get(f"{HA}/api/states/input_select.light_sync_normal_scene", headers=H, timeout=10).status_code == 404:
        ws.call(type="input_select/create", name="light sync normal scene", options=NORMAL_SCENES, icon="mdi:palette-swatch")
    ws.call(type="input_select/update", input_select_id="light_sync_normal_scene", name="Normal scene",
            options=NORMAL_SCENES, icon="mdi:palette-swatch")
    for sid, cfg in SCRIPTS.items():
        r = requests.post(f"{HA}/api/config/script/config/{sid}", headers=H, timeout=10, json=cfg)
        print(f"script.{sid:20} {cfg['alias']:28} {r.status_code}")
    for aid, cfg in AUTOMATIONS.items():
        r = requests.post(f"{HA}/api/config/automation/config/{aid}", headers=H, timeout=10, json={"id": aid, **cfg})
        print(f"automation {aid:30} {r.status_code}")
    # house rule: Assist sees lights and media only
    ws.call(type="homeassistant/expose_entity", assistants=["conversation"], should_expose=False,
            entity_ids=[NORMAL, "input_number.light_sync_normal_bright", "input_number.light_sync_normal_kelvin",
                        "input_select.light_sync_normal_scene"] + [f"script.{s}" for s in SCRIPTS])
    # the mode sensor learns "normal"
    entry = ws.call(type="config/entity_registry/get", entity_id="sensor.light_sync")["config_entry_id"]
    flow = requests.post(f"{HA}/api/config/config_entries/options/flow", headers=H, timeout=10, json={"handler": entry}).json()
    r = requests.post(f"{HA}/api/config/config_entries/options/flow/{flow['flow_id']}", headers=H, timeout=10,
                      json={"state": MODE_TEMPLATE})
    print(f"sensor.light_sync knows 'normal': {r.json().get('type')}")


def tv_music(ws):
    """input_boolean.tv_music "Music in TV mode" (on at creation): in TV mode the TV's colors move with the beat."""
    if requests.get(f"{HA}/api/states/input_boolean.tv_music", headers=H, timeout=10).status_code == 404:
        ws.call(type="input_boolean/create", name="tv music", icon="mdi:television-play")
        requests.post(f"{HA}/api/services/input_boolean/turn_on", headers=H, timeout=10,
                      json={"entity_id": "input_boolean.tv_music"})
    ws.call(type="input_boolean/update", input_boolean_id="tv_music", name="Music in TV mode", icon="mdi:television-play")
    # "Cinema look" (on at creation, Oct 20): in TV mode Corner and Top are a low, slow glow, TvD follows the film
    if requests.get(f"{HA}/api/states/input_boolean.light_sync_cinema", headers=H, timeout=10).status_code == 404:
        ws.call(type="input_boolean/create", name="light sync cinema", icon="mdi:movie-open-outline")
        requests.post(f"{HA}/api/services/input_boolean/turn_on", headers=H, timeout=10,
                      json={"entity_id": "input_boolean.light_sync_cinema"})
    ws.call(type="input_boolean/update", input_boolean_id="light_sync_cinema", name="Cinema look",
            icon="mdi:movie-open-outline")
    ws.call(type="homeassistant/expose_entity", assistants=["conversation"], should_expose=False,
            entity_ids=["input_boolean.tv_music", "input_boolean.light_sync_cinema"])
    print("input_boolean.tv_music (Music in TV mode):",
          requests.get(f"{HA}/api/states/input_boolean.tv_music", headers=H, timeout=10).json()["state"])



SHOW_URL = "{{ state_attr('sensor.music_sync_status', 'show_url') }}"
SHOW_URL_TV = "{{ state_attr('sensor.music_sync_status', 'show_url') ~ '?tv=1' }}"


def show_on_tv(ws):
    """"Show on TV" (voice, the Remote tab's button) opens the show with ?tv=1: only then do the lights take the
    show's colors instead of the TV's (a show open on a phone or the dashboard leaves TV mode alone)."""
    for s in requests.get(f"{HA}/api/states", headers=H, timeout=10).json():
        aid = s["attributes"].get("id") if s["entity_id"].startswith("automation.") else None
        if not aid:
            continue
        r = requests.get(f"{HA}/api/config/automation/config/{aid}", headers=H, timeout=10)
        if r.ok and SHOW_URL in json.dumps(r.json()):
            cfg = json.loads(json.dumps(r.json()).replace(json.dumps(SHOW_URL)[1:-1] + '"', json.dumps(SHOW_URL_TV)[1:-1] + '"'))
            requests.post(f"{HA}/api/config/automation/config/{aid}", headers=H, timeout=10, json=cfg).raise_for_status()
            print(f"Show on TV opens the show with ?tv=1 ({s['attributes'].get('friendly_name')})")


# Quick buttons light up for the mode that's on (Oct 20, owner: "highlight what is working, I want to see what is
# playing"): each button follows a template binary sensor of its mode (the scripts are only "on" while running).
# The combos (Movie + Music, Game + Music) light instead of the plain ones while their music part is on.
NO_GAME = "and is_state('input_boolean.gaming_mode', 'off')"  # (Effects: removed Nov 6)
QUICK_STATES = {
    "script.normal_light": ("Light Sync mode Normal", "{{ is_state('input_boolean.normal_mode', 'on') }}"),
    "script.party_mode": ("Light Sync mode Party",
                          "{{ is_state('input_boolean.music_sync', 'on') and is_state('input_boolean.tv_ambient', 'off') "
                          + NO_GAME + " }}"),
    "script.movie_time": ("Light Sync mode Movie",
                          "{{ is_state('input_boolean.tv_ambient', 'on') and is_state('input_boolean.tv_music', 'off') "
                          + NO_GAME + " }}"),
    "script.movie_music": ("Light Sync mode Movie music",
                           "{{ is_state('input_boolean.tv_ambient', 'on') and is_state('input_boolean.tv_music', 'on') "
                           + NO_GAME + " }}"),
    "script.gaming_time": ("Light Sync mode Gaming",
                           "{{ is_state('input_boolean.gaming_mode', 'on') and is_state('input_boolean.game_music', 'off') }}"),
    "script.gaming_music": ("Light Sync mode Game music",
                            "{{ is_state('input_boolean.gaming_mode', 'on') and is_state('input_boolean.game_music', 'on') }}"),
    "script.good_night": ("Light Sync mode Good night",
                          "{{ is_state('script.good_night', 'on') or is_state('script.fade_chambre_lights_off', 'on') }}"),
    "script.all_lights_off": ("Light Sync mode All off",
                              "{{ is_state('light.chambre_lights', 'off') and is_state('input_boolean.normal_mode', 'off') "
                              "and is_state('input_boolean.music_sync', 'off') and is_state('input_boolean.tv_ambient', 'off') "
                              "and is_state('input_boolean.gaming_mode', 'off') "
                              "and is_state('script.fade_chambre_lights_off', 'off') }}"),
}
# With card-mod (a dashboard add-on, installed by hand from GitHub on Oct 20 with the owner's OK: card_mod_resource)
# the whole button glows in its mode's color; without card-mod the card_mod key is simply ignored. A still glow, no
# animation (the owner gets headaches from flashing).
CARD_MOD = "/local/card-mod.js?v=4.2.1"
GLOW = {"script.normal_light": "#ffc46b", "script.party_mode": "#e040fb", "script.movie_time": "#5b8cff",
        "script.movie_music": "#22d3ee", "script.gaming_time": "#34d399", "script.gaming_music": "#a3e635",
        "script.good_night": "#9d8cff", "script.all_lights_off": "#9aa4b2"}
GLOW_STYLE = """ha-card {{ transition: box-shadow 0.4s ease, border-color 0.4s ease, background 0.4s ease; }}
{{% if is_state(config.entity, 'on') %}}
ha-card {{
  border: 2px solid {c};
  box-shadow: 0 0 16px 2px {c}99;
  background: color-mix(in srgb, {c} 14%, var(--ha-card-background, var(--card-background-color, #fff)));
}}
ha-state-icon {{ color: {c} !important; }}
{{% endif %}}
"""


def template_sensor(entries, name, tmpl):
    """A template binary sensor made as a UI helper; its template is updated when it changed."""
    if name not in entries:
        f = requests.post(f"{HA}/api/config/config_entries/flow", headers=H, timeout=10, json={"handler": "template"}).json()
        f = requests.post(f"{HA}/api/config/config_entries/flow/{f['flow_id']}", headers=H, timeout=10,
                          json={"next_step_id": "binary_sensor"}).json()
        r = requests.post(f"{HA}/api/config/config_entries/flow/{f['flow_id']}", headers=H, timeout=10,
                          json={"name": name, "state": tmpl}).json()
        return f"made ({r.get('type')})"
    f = requests.post(f"{HA}/api/config/config_entries/options/flow", headers=H, timeout=10,
                      json={"handler": entries[name]}).json()
    now = next((d.get("description", {}).get("suggested_value") for d in f.get("data_schema", []) if d.get("name") == "state"), None)
    if now == tmpl:
        requests.delete(f"{HA}/api/config/config_entries/options/flow/{f['flow_id']}", headers=H, timeout=10)
        return "same"
    r = requests.post(f"{HA}/api/config/config_entries/options/flow/{f['flow_id']}", headers=H, timeout=10,
                      json={"state": tmpl}).json()
    return f"updated ({r.get('type')})"


def quick_highlight(ws):
    """The Quick buttons (phone_setup's scripts, two rows of four): each button follows its mode's template binary
    sensor, so its icon lights up while that mode is on (and with card-mod the whole button glows in the mode's color).
    Tapping still runs the script. The buttons themselves: dashboard.quick_buttons() (the front page's Lights)."""
    import phone_setup as ps
    ps.post_scripts(["movie_time", "movie_music", "gaming_time", "gaming_music"])
    entries = {e["title"]: e["entry_id"] for e in
               requests.get(f"{HA}/api/config/config_entries/entry?domain=template", headers=H, timeout=10).json()}
    made = {}
    for script, (name, tmpl) in QUICK_STATES.items():
        eid = "binary_sensor." + name.lower().replace(" ", "_")
        print(f"{eid}: {template_sensor(entries, name, tmpl)}")
        made[script] = eid
    ws.call(type="homeassistant/expose_entity", assistants=["conversation"], should_expose=False,
            entity_ids=list(made.values()) + list(made))  # house rule: Assist gets lights and media only
    import dashboard
    dashboard.build_overview(ws)


def card_mod_resource(ws):
    """card-mod as a dashboard resource, once its file is in /config/www (the paste in docs/home-assistant.md)."""
    if requests.get(f"{HA}{CARD_MOD}", timeout=10).status_code != 200:
        print(f"card-mod: {CARD_MOD.split('?')[0]} not there yet (paste the line in docs/home-assistant.md; if the "
              "file is in /config/www and this still says so, restart Home Assistant once so it serves /local)")
        return False
    have = ws.call(type="lovelace/resources")
    old = [r for r in have if "card-mod.js" in r["url"]]
    if any(r["url"] == CARD_MOD for r in old):
        print("card-mod: dashboard resource already there")
    elif old:
        ws.call(type="lovelace/resources/update", resource_id=old[0]["id"], res_type="module", url=CARD_MOD)
        print(f"card-mod: dashboard resource updated to {CARD_MOD}")
    else:
        ws.call(type="lovelace/resources/create", res_type="module", url=CARD_MOD)
        print(f"card-mod: dashboard resource added ({CARD_MOD})")
    return True


def voice_after_boot():
    """After a Pi restart Assist Microphone can start before openWakeWord is ready ("No wake-word-detection
    provider") and then never listens for the wake word: restart it once a minute after Home Assistant starts."""
    auto = {"id": "sh_voice_after_boot", "alias": "Smart home · Wake word ready after a restart",
            "description": "Assist Microphone restarted 60 s after Home Assistant starts, when openWakeWord is up.",
            "triggers": [{"trigger": "homeassistant", "event": "start"}],
            "conditions": [],
            "actions": [{"delay": {"seconds": 60}},
                        {"action": "hassio.addon_restart", "data": {"addon": "core_assist_microphone"}}],
            "mode": "single"}
    requests.post(f"{HA}/api/config/automation/config/sh_voice_after_boot", headers=H, timeout=10, json=auto).raise_for_status()
    print("Wake word: Assist Microphone restarts 60 s after Home Assistant starts")
    # Oct 27: after a wake-up with nothing said ("No text recognized") it stopped listening for ~10 h. Listening ->
    # idle without processing anything is exactly that case: restart it (~5 s), at most once every 2 minutes.
    auto = {"id": "sh_voice_after_error", "alias": "Smart home · Wake word back after a missed request",
            "description": "Assist Microphone restarted when a request ended without being processed (no text "
                           "recognized), so it keeps listening for the wake word.",
            "triggers": [{"trigger": "state", "entity_id": "assist_satellite.assist_microphone", "from": "listening",
                          "to": "idle", "for": {"seconds": 3}}],
            "conditions": [],
            "actions": [{"action": "hassio.addon_restart", "data": {"addon": "core_assist_microphone"}},
                        {"delay": {"minutes": 2}}],
            "mode": "single", "max_exceeded": "silent"}
    requests.post(f"{HA}/api/config/automation/config/sh_voice_after_error", headers=H, timeout=10, json=auto).raise_for_status()
    print("Wake word: Assist Microphone restarts after a request that ended without text")


def delete_old():
    for aid, name in OLD_AUTOMATIONS.items():
        r = requests.delete(f"{HA}/api/config/automation/config/{aid}", headers=H, timeout=10)
        print(f"deleted automation {name!r}: {r.status_code}" if r.ok else f"automation {name!r}: already gone")


def clean_dashboard(ws):
    cfg = ws.call(type="lovelace/config", url_path="smart-home")
    ov = next(v for v in cfg["views"] if v.get("path") == "overview")
    st = next(v for v in cfg["views"] if v.get("path") == "setup")
    st.update(title="Settings", icon="mdi:cog")
    # the front page's sections: tools/ha_setup/dashboard.py (Open the app, Lights, Settings, Sleep, Home)
    import dashboard
    ov["sections"] = dashboard.sections()
    ov["max_columns"] = 3
    ov["header"]["card"]["content"] = HEADER
    ov["badges"] = [b for b in ov.get("badges", []) if b.get("entity") in
                    ("sensor.light_sync", "sensor.music_sync_bpm", "input_datetime.wake_up_time")]

    # Settings tab: SETTINGS_ORDER, each setting once (the front page's Comfort and Wake-up aren't
    # repeated). Off the screens (values kept): sensitivity, beat lock and latency
    # (fine-tuning that broke the sync when pushed to the extremes), the presence placeholders (until the mmWave
    # sensors arrive), the daily summary, BPM / loudness (on the front page's badges).
    # Show tab: the address to open on the TV box carries ?tv=1 (then the lights take the show's colors)
    cfg = json.loads(json.dumps(cfg).replace("open this address in its browser: `{{ u }}`",
                                             "open this address in its browser: `{{ u }}?tv=1`"))
    ov = next(v for v in cfg["views"] if v.get("path") == "overview")
    st = next(v for v in cfg["views"] if v.get("path") == "setup")
    st["sections"] = settings_sections()  # automations and the lists: the System tab (dashboard.py)
    ws.call(type="lovelace/config/save", url_path="smart-home", config=cfg)
    print("front page:", " | ".join(str(heading(s)) for s in ov["sections"]))
    print("settings tab:", " | ".join(str(heading(s)) for s in st["sections"]))


def main():
    ws = ws_connect()
    normal_mode(ws)
    tv_music(ws)
    show_on_tv(ws)
    voice_after_boot()
    delete_old()
    clean_dashboard(ws)
    quick_highlight(ws)
    card_mod_resource(ws)


if __name__ == "__main__":
    main()
