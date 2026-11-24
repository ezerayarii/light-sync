#!/usr/bin/env python3
"""
remote_view.py - adds (or rebuilds) the "Remote" tab of the Smart home dashboard: a TV-box remote, app
shortcuts, and the lights and Light Sync modes on one page. Run again after changing it here.

  py tools/ha_setup/remote_view.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # tools\ (export_ha)
import export_ha as ex  # noqa: E402

DASH = "smart-home"
BOX = "remote.x96q_pro_plus"
PLAYER = "media_player.x96q_pro_plus"


def key(icon, name, command, cols=4, rows=2, hold_repeats=0):
    """One remote button: sends an Android TV key to the box."""
    c = {"type": "button", "icon": icon, "name": name, "show_name": True, "show_state": False, "icon_height": "34px",
         "tap_action": {"action": "perform-action", "perform_action": "remote.send_command",
                        "target": {"entity_id": BOX}, "data": {"command": command}},
         "grid_options": {"columns": cols, "rows": rows}}
    if hold_repeats:  # hold = repeat (volume)
        c["hold_action"] = {"action": "perform-action", "perform_action": "remote.send_command",
                            "target": {"entity_id": BOX}, "data": {"command": command, "num_repeats": hold_repeats, "delay_secs": 0.12}}
    return c


def app(icon, name, package):
    return {"type": "button", "icon": icon, "name": name, "show_state": False, "icon_height": "34px",
            "tap_action": {"action": "perform-action", "perform_action": "remote.turn_on",
                           "target": {"entity_id": BOX}, "data": {"activity": package}},
            "grid_options": {"columns": 6, "rows": 2}}


def say(icon, name, sentence):
    """Runs one of the local voice commands (same logic as saying it)."""
    return {"type": "button", "icon": icon, "name": name, "show_state": False, "icon_height": "34px",
            "tap_action": {"action": "perform-action", "perform_action": "conversation.process",
                           "data": {"text": sentence, "language": "en"}},
            "grid_options": {"columns": 6, "rows": 2}}


def tile(entity, name, icon, color, cols=4):
    return {"type": "tile", "entity": entity, "name": name, "icon": icon, "color": color, "vertical": True,
            "tap_action": {"action": "toggle"}, "grid_options": {"columns": cols}}


remote = {"type": "grid", "cards": [
    {"type": "heading", "heading": "TV box", "icon": "mdi:remote-tv", "heading_style": "title",
     "badges": [{"type": "entity", "entity": PLAYER, "show_state": True, "show_icon": True, "state_content": ["state", "app_name"]}]},
    {"type": "markdown", "text_only": True, "visibility": [{"condition": "state", "entity": BOX, "state": "unavailable"}],
     "content": "<small>The box isn't answering (off at the wall, or no network). Turn it on with its own remote once.</small>"},
    key("mdi:power", "Power", "POWER"), key("mdi:home-outline", "Home", "HOME"), key("mdi:arrow-u-left-top", "Back", "BACK"),
    key("mdi:menu", "Menu", "MENU"), key("mdi:chevron-up", "", "DPAD_UP"), key("mdi:cog-outline", "Settings", "SETTINGS"),
    key("mdi:chevron-left", "", "DPAD_LEFT"), key("mdi:circle-outline", "OK", "DPAD_CENTER"), key("mdi:chevron-right", "", "DPAD_RIGHT"),
    key("mdi:magnify", "Search", "SEARCH"), key("mdi:chevron-down", "", "DPAD_DOWN"), key("mdi:information-outline", "Info", "INFO"),
    key("mdi:rewind", "Rewind", "MEDIA_REWIND"), key("mdi:play-pause", "Play/Pause", "MEDIA_PLAY_PAUSE"), key("mdi:fast-forward", "Forward", "MEDIA_FAST_FORWARD"),
    key("mdi:volume-minus", "Vol −", "VOLUME_DOWN", hold_repeats=5), key("mdi:volume-off", "Mute", "VOLUME_MUTE"),
    key("mdi:volume-plus", "Vol +", "VOLUME_UP", hold_repeats=5),
    {"type": "markdown", "text_only": True, "content": "<small>Hold Vol − / Vol + to change it faster. Power and volume reach the TV through HDMI-CEC.</small>"},
]}

apps = {"type": "grid", "cards": [
    {"type": "heading", "heading": "Apps", "icon": "mdi:apps", "heading_style": "title"},
    app("mdi:youtube", "YouTube", "com.google.android.youtube.tv"),
    app("mdi:movie-open-play-outline", "Prime Video", "com.amazon.amazonvideo.livingroom"),
    app("mdi:television-classic", "IPTV", "com.iptvav.av_iptv"),
    app("mdi:cast", "Screen mirroring", "com.softwinner.miracastReceiver"),
    say("mdi:television-play", "Show on TV", "start the show"),
    say("mdi:television-off", "Close the show", "close the show"),
    app("mdi:widgets-outline", "TV widgets", "http://192.168.1.10:8090/tv"),   # clock, weather, the lights (light_sync/web/tv.html)
    {"type": "markdown", "text_only": True, "content": "<small>Opening YouTube, Prime Video, IPTV or mirroring also turns Music sync on (the media-apps automation).</small>"},
]}

lights = {"type": "grid", "cards": [
    {"type": "heading", "heading": "Lights & Light Sync", "icon": "mdi:lightbulb-group", "heading_style": "title"},
    {"type": "tile", "entity": "light.chambre_lights", "name": "All bedroom lights", "grid_options": {"columns": 12},
     "features": [{"type": "light-brightness"}], "features_position": "bottom"},
    tile("input_boolean.music_sync", "Music", "mdi:music-note", "pink"),
    tile("input_boolean.tv_ambient", "TV", "mdi:television", "cyan"),
    tile("input_boolean.gaming_mode", "Gaming", "mdi:controller", "lime"),
    say("mdi:weather-night", "Good night", "good night"),
    {"type": "button", "name": "Light Sync app", "icon": "mdi:equalizer", "show_state": False, "icon_height": "34px",
     "tap_action": {"action": "navigate", "navigation_path": "/app/local_light_sync"}, "grid_options": {"columns": 6, "rows": 2}},
]}

# no view theme: the dashboard follows HA's theme, which "Dashboard follows the room lights" sets
view = {"title": "Remote", "path": "remote", "icon": "mdi:remote-tv", "type": "sections", "max_columns": 3,
        "dense_section_placement": True, "sections": [remote, apps, lights]}


def main():
    ws = ex.WebSocket(os.getenv("HA_URL", "http://192.168.1.10:8123"))
    ws.recv()
    ws.send({"type": "auth", "access_token": ex.token()})
    assert ws.recv().get("type") == "auth_ok", "HA login failed"
    cfg = ws.call(type="lovelace/config", url_path=DASH)
    views = [v for v in cfg["views"] if v.get("path") != "remote"]
    at = next((i + 1 for i, v in enumerate(views) if v.get("path") == "overview"), 0)
    views.insert(at, view)  # right after the main tab
    cfg["views"] = views
    ws.call(type="lovelace/config/save", url_path=DASH, config=cfg)
    print("Remote tab saved:", [v.get("title") for v in cfg["views"]])


if __name__ == "__main__":
    main()
