#!/usr/bin/env python3
"""
dashboard.py - the Smart home dashboard's front page (smart-home/overview), built like the Light Sync app (Oct 21,
owner: "I like the new Light Sync app, make the same for smart-home/overview"; picked: the dashboard rebuilt like the
app, the app unchanged). Safe to run again: this owns the front page's sections (the header and badges stay).

  Open the Light Sync app   a big neon button on top (the app: /app/local_light_sync)
  Lights      the Quick buttons (the one playing glows), the room lights, TvD / Corner / Top
  Settings    the mode switches, Comfort, the sound source; all settings (the Settings tab), the show
  Sleep       sunrise, wake-up time, late night, bedtime + reminder; the sleep calculator (in the app)
  Home        Room guard, Lights follow me, Telegram control; the Remote, Camera and System tabs, the Wi-Fi list (in the app)

Also the rest of the app's order (Oct 30, owner: "organize the theme and the display of the whole app"; run again = same):
  Room now    what the TV widgets show (light_sync/web/tv.html) and who's home: lights, weather, sun, internet, the Pi
  System tab  how the Pi, backups and notifications are doing, Start / Restart / Stop for the Pi add-on, the notification
              switches, automations, the to-do lists
  Sidebar     the Light Sync app first and opened on start (Nov 2: one app); Smart home and unused built-in pages hidden
  Theme       the colour follows the room only after it has held 5 min: the whole app repainted 123 times in a day

  py tools/ha_setup/dashboard.py
"""
import os
import sys

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # tools\ (export_ha)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import export_ha as ex  # noqa: E402

HA = os.getenv("HA_URL", "http://192.168.1.10:8123")
APP = "/app/local_light_sync"
# the app's look: its gradient button and its neon card edge (card-mod); a still glow, nothing moves
NEON_BUTTON = """ha-card {
  background: linear-gradient(90deg, rgba(255,43,214,.92), rgba(139,92,255,.92), rgba(34,228,255,.92));
  border: 0; border-radius: 16px;
  --primary-text-color: #fff; color: #fff; font-weight: 700;
}
ha-state-icon { color: #fff !important; }
"""
LINK = """ha-card { border-radius: 14px; border: 1px solid rgba(139,92,255,.28); background: rgba(255,255,255,.03); }
ha-state-icon { color: #b9a0ff !important; }
"""
DISK, HEAT = "sensor.system_monitor_utilisation_du_disque", "sensor.system_monitor_temperature_du_processeur"
USB, LAST_BACKUP = "sensor.light_sync_usb_backup", "sensor.backup_last_successful_automatic_backup"
# one line that says if anything needs you; the limits are those of the Telegram alerts (telegram_setup.py: 85 %, 176 F = 80 C)
STATUS = ("{% set p = [] %}"
          "{% set disk = states('" + DISK + "') | float(0) %}{% set heat = states('" + HEAT + "') | float(0) %}"
          "{% set age = now().timestamp() - as_timestamp(states('" + LAST_BACKUP + "'), 0) %}"
          "{% set news = states.update | selectattr('state', 'eq', 'on') | list | count %}"
          "{% if disk >= 85 %}{% set p = p + ['the Pi disk is ' ~ disk | round ~ ' % full'] %}{% endif %}"
          "{% if heat >= 176 %}{% set p = p + ['the Pi is hot'] %}{% endif %}"
          "{% if age > 172800 %}{% set p = p + ['no backup for 2 days'] %}{% endif %}"
          "{% if is_state('" + USB + "', 'problem') %}{% set p = p + ['the USB copy needs a look'] %}{% endif %}"
          "{% if p %}**Needs a look:** {{ p | join(', ') }}{% else %}**All good**{% endif %}"
          " · Pi disk {{ disk | round }} %{% if news %} · {{ news }} update{{ 's' if news > 1 }} ready{% endif %}")
ROOM_NOTES = ("**Room guard**: movement in your room while you're away sends a notification. **Lights follow me**: all "
              "lights off 10 min after you leave the room, Normal when you're back (not 23:00-07:00). **Telegram control** "
              "off: notifications still come, the bot ignores every message and command.")
# the sidebar (per user): what's used, in order; what's hidden comes back from Profile > Sidebar
SIDEBAR = ["local_light_sync", "history", "logbook", "core_ssh", "a0d7b954_vscode"]
HIDDEN = ["smart-home", "map", "lovelace", "home", "light", "security", "climate", "maintenance", "energy", "media-browser", "todo"]
THEME_HOLD_MIN = 5


def heading(text, icon):
    return {"type": "heading", "heading": text, "icon": icon, "heading_style": "title"}


def tile(entity, name, icon=None, cols=6, toggle=True, **extra):
    c = {"type": "tile", "entity": entity, "name": name, "grid_options": {"columns": cols},
         "tap_action": {"action": "toggle" if toggle else "more-info"}}
    if icon:
        c["icon"] = icon
    c.update(extra)
    return c


def link(name, icon, path, cols=6):
    return {"type": "button", "name": name, "icon": icon, "show_state": False, "icon_height": "26px",
            "grid_options": {"columns": cols, "rows": 1}, "tap_action": {"action": "navigate", "navigation_path": path},
            "card_mod": {"style": LINK}}


def quick_buttons():
    """The Quick buttons as the app's mode buttons: each follows its mode's sensor and glows while it plays."""
    import phone_setup as ps
    import simplify as sp
    out = []
    for sid in ps.QUICK:
        c, script = ps.quick_button(sid), f"script.{sid}"
        name = sp.QUICK_STATES[script][0]
        c.update(entity="binary_sensor." + name.lower().replace(" ", "_"), state_color=True,
                 card_mod={"style": sp.GLOW_STYLE.format(c=sp.GLOW[script])})
        out.append(c)
    return out


def sections():
    hero = {"type": "grid", "column_span": 3, "cards": [
        {"type": "button", "name": "Open the Light Sync app", "icon": "mdi:equalizer", "show_state": False,
         "icon_height": "26px", "grid_options": {"columns": 12, "rows": 1},
         "tap_action": {"action": "navigate", "navigation_path": APP}, "card_mod": {"style": NEON_BUTTON}},
        {"type": "markdown", "text_only": True,
         "content": "Light Sync is **offline**. It starts by itself when you log in to the laptop, or run `start_lights.bat`.",
         "visibility": [{"condition": "state", "entity": "sensor.light_sync", "state": "offline"}]},
        {"type": "markdown", "text_only": True, "content": STATUS},
    ]}
    lights = {"type": "grid", "column_span": 3, "cards": [heading("Lights", "mdi:lightbulb-on-outline"), *quick_buttons(),
        tile("light.chambre_lights", "All bedroom lights", cols=12, toggle=False,
             features=[{"type": "light-brightness"}, {"type": "light-color-temp"}]),
        tile("light.chambre_tvd", "TvD", cols=4, toggle=False),
        tile("light.h6004_2", "Corner", cols=4, toggle=False),
        tile("light.h6004", "Top", cols=4, toggle=False)]}
    settings = {"type": "grid", "cards": [heading("Settings", "mdi:cog-outline"),
        tile("input_boolean.music_sync", "Music", "mdi:music-note", cols=3, color="pink"),
        tile("input_boolean.tv_ambient", "TV", "mdi:television", cols=3, color="cyan"),
        tile("input_boolean.gaming_mode", "Gaming", "mdi:controller", cols=6, color="lime"),
        tile("input_boolean.light_sync_comfort", "Comfort · easy on the eyes", "mdi:eye-outline", cols=12, color="purple"),
        tile("input_select.music_mic", "Sound source", "mdi:microphone", cols=12, toggle=False,
             features=[{"type": "select-options"}]),
        link("All settings", "mdi:tune-variant", "/smart-home/setup"),
        link("Open the show", "mdi:play-circle-outline", "/smart-home/show")]}
    sleep = {"type": "grid", "cards": [heading("Sleep", "mdi:weather-night"),
        tile("input_boolean.wake_up_light", "Sunrise wake-up", "mdi:weather-sunset-up", color="amber"),
        tile("input_datetime.wake_up_time", "Wake-up time", toggle=False),
        tile("input_boolean.late_night_mode", "Late night", "mdi:weather-night", color="indigo"),
        tile("input_boolean.light_sync_bedtime_reminder", "Bedtime reminder", "mdi:bell-sleep", color="purple"),
        tile("input_datetime.light_sync_bedtime", "Bedtime", toggle=False,
             visibility=[{"condition": "state", "entity": "input_boolean.light_sync_bedtime_reminder", "state": "on"}]),
        link("Sleep calculator", "mdi:sleep", APP, cols=12)]}
    home = {"type": "grid", "cards": [heading("Home", "mdi:home-outline"),
        tile("input_boolean.room_guard", "Room guard", "mdi:shield-home", color="red"),
        tile("input_boolean.lights_follow_me", "Lights follow me", "mdi:walk", color="amber"),
        tile("input_boolean.telegram_control", "Telegram control", "mdi:message-lock-outline", color="blue", cols=12),
        {"type": "markdown", "text_only": True, "content": ROOM_NOTES},
        link("Remote", "mdi:remote-tv", "/smart-home/remote"),
        link("Camera", "mdi:cctv", "/smart-home/camera"),
        link("System", "mdi:heart-pulse", "/smart-home/system"),
        link("Wi-Fi now", "mdi:wifi", APP)]}
    return [hero, lights, settings, sleep, home]


def addon_button(name, icon, verb, warning):
    """Start, Restart or Stop the Pi add-on; asks first, so a stray tap does nothing."""
    return {"type": "button", "name": name, "icon": icon, "show_state": False, "icon_height": "26px",
            "grid_options": {"columns": 4, "rows": 2}, "card_mod": {"style": LINK},
            "tap_action": {"action": "perform-action", "perform_action": f"hassio.addon_{verb}",
                           "data": {"addon": "local_light_sync"}, "confirmation": {"text": warning}}}


def attr_row(entity, attribute, name, icon):
    return {"type": "attribute", "entity": entity, "attribute": attribute, "name": name, "icon": icon, "format": "time"}


def room_view():
    """Room now (Nov 2, owner: "more data, in the dashboard"): what the TV widgets show, and what's only for you (who's home)."""
    now = {"type": "grid", "cards": [heading("Now", "mdi:play-circle-outline"),
        tile("sensor.light_sync", "Lights", "mdi:lightbulb-group", toggle=False, color="pink"),
        tile("sensor.music_sync_bpm", "BPM", "mdi:metronome", toggle=False, color="cyan"),
        tile("media_player.x96q_pro_plus", "On the TV", "mdi:television-play", cols=12, toggle=False, color="blue"),
        tile("light.chambre_tvd", "TvD", cols=4, toggle=False),
        tile("light.h6004_2", "Corner", cols=4, toggle=False),
        tile("light.h6004", "Top", cols=4, toggle=False),
        link("TV widgets on the TV", "mdi:widgets-outline", "/smart-home/remote", cols=12)]}
    weather = {"type": "grid", "cards": [heading("Weather", "mdi:weather-partly-cloudy"),
        {"type": "weather-forecast", "entity": "weather.forecast_maison", "forecast_type": "daily", "show_current": True, "show_forecast": True}]}
    day = {"type": "grid", "cards": [heading("Today", "mdi:white-balance-sunny"),
        {"type": "entities", "entities": [
            attr_row("sun.sun", "next_rising", "Sunrise", "mdi:weather-sunset-up"),
            attr_row("sun.sun", "next_setting", "Sunset", "mdi:weather-sunset-down"),
            {"entity": "input_datetime.wake_up_time", "name": "Wake-up", "icon": "mdi:alarm"},
            {"entity": "input_datetime.arrival_today", "name": "Home since", "icon": "mdi:home-import-outline"},
            {"entity": "sensor.chambre_lights_on_yesterday", "name": "Lights on yesterday", "icon": "mdi:lightbulb-on-outline"}]}]}
    home = {"type": "grid", "cards": [heading("Home", "mdi:home-outline"),
        tile("person.ezer", "You", "mdi:account", toggle=False, color="green"),
        tile("input_boolean.room_guard", "Room guard", "mdi:shield-home", toggle=False, color="red"),
        tile("input_boolean.lights_follow_me", "Lights follow me", "mdi:walk", toggle=False, color="amber"),
        {"type": "markdown", "text_only": True, "content": STATUS}]}
    net = {"type": "grid", "cards": [heading("Internet", "mdi:speedometer"),
        tile("sensor.speedtest_download", "Download", "mdi:download", cols=4, toggle=False, color="cyan"),
        tile("sensor.speedtest_upload", "Upload", "mdi:upload", cols=4, toggle=False, color="purple"),
        tile("sensor.speedtest_ping", "Ping", "mdi:timer-outline", cols=4, toggle=False)]}
    pi = {"type": "grid", "cards": [heading("Pi and backups", "mdi:raspberry-pi"),
        tile(HEAT, "Pi temperature", "mdi:thermometer", toggle=False, color="amber"),
        tile(DISK, "Pi disk", "mdi:harddisk", toggle=False, color="purple"),
        tile(LAST_BACKUP, "Last backup", "mdi:check-circle-outline", toggle=False, color="green"),
        tile(USB, "USB copy", "mdi:usb-flash-drive", toggle=False, color="cyan")]}
    return {"title": "Room now", "path": "room", "icon": "mdi:view-dashboard-variant", "type": "sections", "max_columns": 3,
            "dense_section_placement": True, "sections": [now, weather, day, home, net, pi]}


def system_view(tail):
    """The System tab: is everything alright, what may notify you, and the housekeeping that had been under Settings."""
    health = {"type": "grid", "cards": [heading("Health", "mdi:heart-pulse"),
        {"type": "markdown", "text_only": True, "content": STATUS},
        tile("sensor.light_sync", "Light Sync", "mdi:equalizer", toggle=False, color="pink"),
        tile("sensor.light_sync_laptop", "Laptop", "mdi:laptop", toggle=False, color="cyan"),
        tile(DISK, "Pi disk", "mdi:harddisk", toggle=False, color="purple"),
        tile(HEAT, "Pi temperature", "mdi:thermometer", toggle=False, color="amber")]}
    backups = {"type": "grid", "cards": [heading("Backups", "mdi:backup-restore"),
        tile(LAST_BACKUP, "Last backup", "mdi:check-circle-outline", toggle=False, color="green"),
        tile("sensor.backup_next_scheduled_automatic_backup", "Next backup", "mdi:calendar-clock", toggle=False),
        tile(USB, "Copy on the USB stick", "mdi:usb-flash-drive", cols=12, toggle=False, color="cyan")]}
    notify = {"type": "grid", "cards": [heading("Notifications", "mdi:bell-outline"),
        tile("input_boolean.telegram_control", "Telegram control", "mdi:message-lock-outline", color="blue", cols=12),
        tile("input_boolean.notify_security", "New Wi-Fi devices", "mdi:wifi-alert", color="red"),
        tile("input_boolean.notify_system", "Something offline", "mdi:lan-disconnect", color="amber"),
        tile("input_boolean.notify_backups", "Backups", "mdi:backup-restore", color="green"),
        tile("input_boolean.notify_sleep", "Bedtime", "mdi:bell-sleep", color="purple"),
        tile("input_boolean.notify_updates", "Updates", "mdi:update", color="cyan", cols=12)]}
    pi = {"type": "grid", "cards": [heading("Light Sync on the Pi", "mdi:raspberry-pi"),
        addon_button("Start", "mdi:play", "start", "Start Light Sync on the Pi?"),
        addon_button("Restart", "mdi:restart", "restart", "Restart Light Sync on the Pi? The app closes for about 30 seconds."),
        addon_button("Stop", "mdi:stop", "stop", "Stop Light Sync on the Pi? No backup for the lights and no Light Sync app until you press Start."),
        {"type": "markdown", "text_only": True,
         "content": "Light Sync on the laptop: the **Light Sync Control** shortcut on its desktop."}]}
    return {"title": "System", "path": "system", "icon": "mdi:heart-pulse", "type": "sections", "max_columns": 3,
            "dense_section_placement": True, "sections": [health, pi, backups, notify, *tail]}


def build_overview(ws):
    cfg = ws.call(type="lovelace/config", url_path="smart-home")
    ov = next(v for v in cfg["views"] if v.get("path") == "overview")
    ov["sections"] = sections()
    ov["max_columns"] = 3
    import simplify as sp
    ov["header"]["card"]["content"] = sp.HEADER
    # housekeeping moves from Settings to System, as it is; run again: it's already there
    st = next(v for v in cfg["views"] if v.get("path") == "setup")
    old = next((v for v in cfg["views"] if v.get("path") == "system"), None)
    moved = ("Automations", "Phase 2 shopping", "Smart home tasks")
    title = lambda s: (s.get("cards") or [{}])[0].get("heading")
    tail = [s for s in st["sections"] if title(s) in moved] or [s for s in (old or {}).get("sections", []) if title(s) in moved]
    st["sections"] = [s for s in st["sections"] if title(s) not in moved]
    views = [v for v in cfg["views"] if v.get("path") not in ("system", "room")]
    views.insert(next(i for i, v in enumerate(views) if v.get("path") == "setup") + 1, system_view(tail))
    views.insert(next(i for i, v in enumerate(views) if v.get("path") == "overview") + 1, room_view())
    cfg["views"] = views
    ws.call(type="lovelace/config/save", url_path="smart-home", config=cfg)
    print("tabs:", " | ".join(v.get("title") for v in views))


def organize_sidebar(ws):
    ws.call(type="frontend/set_user_data", key="sidebar", value={"panelOrder": SIDEBAR, "hiddenPanels": HIDDEN})
    # the Light Sync app is the one app (Nov 2, owner: "two inside one app"): Home Assistant opens on it
    ws.call(type="frontend/set_user_data", key="core", value={"default_panel": "local_light_sync"})
    print("sidebar:", " | ".join(SIDEBAR), "(opens on the Light Sync app)")


def calm_theme():
    url = f"{HA}/api/config/automation/config/sh_dashboard_follows_lights"
    head = {"Authorization": "Bearer " + ex.token()}
    auto = requests.get(url, headers=head, timeout=10).json()
    auto["triggers"][0]["for"] = {"minutes": THEME_HOLD_MIN}
    auto["description"] = ("Switches the Home Assistant theme to the Neon theme matching the brightest bedroom light "
                           "(sensor.room_light_theme: 12 hues, warm white, Neon Club when the lights are off), once that colour "
                           f"has held for {THEME_HOLD_MIN} min: every switch repaints all open screens. Themes: home_assistant/themes/neon_room.yaml.")
    requests.post(url, headers=head, json=auto, timeout=10).raise_for_status()
    print(f"theme: follows the room after {THEME_HOLD_MIN} min of the same colour")


def main():
    ws = ex.WebSocket(HA)
    ws.sock.settimeout(60)
    ws.recv()
    ws.send({"type": "auth", "access_token": ex.token()})
    assert ws.recv().get("type") == "auth_ok"
    build_overview(ws)
    organize_sidebar(ws)
    calm_theme()


if __name__ == "__main__":
    main()
