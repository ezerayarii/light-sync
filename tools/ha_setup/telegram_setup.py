#!/usr/bin/env python3
"""
telegram_setup.py - Rocky on Telegram: notifications and a chat (Oct 30, owner picked Telegram two-way over the app only,
a cloud service by their choice; the token is only in Home Assistant, pasted there by the owner). Safe to run again.

  The door       HA's Telegram bot on polling: the Pi calls out, nothing is open to the internet. Only the owner's chat
                 is allowed (the integration), and every automation checks their user id again.
  Off switch     input_boolean.telegram_control: off = notifications still come, every command and message is ignored.
  Room camera    /cam (Nov 7, the owner asked: "the CCTV of the room back", a Telegram snapshot included): one picture
                 of the room camera, sent only when the owner asks, nothing kept. It goes through Telegram's servers.
  What it can do Only what Rocky can: lights, media and the modes. Text only, apart from /cam, and notification
                 texts (a Wi-Fi device's name) never reach Rocky. /status says nothing about home or away.
  Talking        a message goes to Home Assistant's fixed commands first, then to Rocky, who keeps the conversation.
  Commands       (Nov 7 rebuild: the owner's priorities - modes, status at a glance, problems first, sleep and wake -
                 and "more networking": Wi-Fi, the internet, the lights answering)
                 modes /normal /party /tv /tvmusic /game /night /off (/movie = /tv); /status /check /net /wifi;
                 /wake [7:30 | on | off] /bedtime [0:45]; /mute [hours] /unmute /help
                 /check /net /wifi are answered by Light Sync on the Pi (input_text.light_sync_telegram_ask ->
                 script.telegram_reply): health.py, telegram_ask.py. Problems come by themselves: a light not answering,
                 TvD stuck off (Light Sync tries to fix it first), the internet down (told when it's back), a new
                 Wi-Fi device (buttons: It's mine / Not mine).
  Notifications  script.notify_me(title, message, level, topic). Levels: urgent, important, info. 23:00-08:00 they arrive
                 silently unless urgent; /mute holds back all but urgent; each topic has a switch (input_boolean.notify_*).
                 Urgent ones also go to the Home Assistant app, in case Telegram is down. "Movement in your room" stays on
                 the app only (it would tell Telegram when the owner is away).
  Good morning   At 08:00, if anything came in the night: one message listing it (nothing on a quiet night).
  Pi's health    System Monitor (local): disk 85 % full, processor 80 C for 5 min (Home Assistant reports it in F here: 176); a message when Home Assistant starts,
                 so a restart or an outage is noticed afterwards.
  Not covered    The Pi going down: it is the one that sends, so that message only comes once it's back.

  py tools/ha_setup/telegram_setup.py
"""
import os
import sys

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # tools\ (export_ha)
import export_ha as ex  # noqa: E402

HA = os.getenv("HA_URL", "http://192.168.1.10:8123")
H = {"Authorization": f"Bearer {ex.token()}"}
APP = "notify.mobile_app_rocky"
CONTROL = "input_boolean.telegram_control"
MUTE = "input_datetime.telegram_mute_until"
CONV = "input_text.telegram_conversation"
ASK = "input_text.light_sync_telegram_ask"  # questions for Light Sync (the Pi): "wifi||<time>"
WAKE, WAKE_ON, BEDTIME = "input_datetime.wake_up_time", "input_boolean.wake_up_light", "input_datetime.light_sync_bedtime"
OVERNIGHT = "input_text.telegram_overnight"  # what came in the night, for the good-morning message
DISK, HEAT = "sensor.system_monitor_utilisation_du_disque", "sensor.system_monitor_temperature_du_processeur"
TOPICS = {"security": "New Wi-Fi devices", "system": "Light Sync, lights, cameras, microphone", "backups": "Backups",
          "sleep": "Bedtime", "updates": "Updates"}
MODES = {"/normal": ("script.normal_light", "Normal."), "/party": ("script.party_mode", "Party."),
         "/tv": ("script.movie_time", "TV mode."), "/movie": ("script.movie_time", "TV mode."),
         "/tvmusic": ("script.movie_music", "TV + Music."), "/game": ("script.gaming_time", "Gaming."),
         "/night": ("script.good_night", "Good night."), "/off": ("script.all_lights_off", "All off.")}
ASKS = ("/check", "/net", "/wifi", "/recap", "/learned", "/prayer")  # answered by Light Sync on the Pi (Nov 7: + the learner)
ROOM_CAM = "camera.bedroom_camera"  # /cam: the room camera (motionEye Camera1)
KEYBOARD = ["/normal, /party, /tv", "/tvmusic, /game, /night", "/off, /status, /check", "/wifi, /net, /cam", "/prayer, /recap, /help"]
MODE_BUTTONS = ["Normal:/normal, Party:/party, TV:/tv", "TV + Music:/tvmusic, Gaming:/game", "Good night:/night, All off:/off"]
HELP = ("I'm Rocky. Write to me normally (my AI runs on the laptop: with it off I only know the commands), or tap:\n"
        "Modes: /normal /party /tv /tvmusic /game /night /off\n"
        "At a glance: /status (lights, TV, Pi), /check (problems first), /net (internet), /wifi (who's on the Wi-Fi)\n"
        "Sleep: /wake (see it), /wake 7:30, /wake off; /bedtime 0:45\n"
        "Room camera: /cam (one picture now, nothing saved)\n"
        "Your room: /recap (the last 7 days), /learned (what I tune by myself and why)\n"
        "Prayer: /prayer (today's times and the next one)\n"
        "Quiet: /mute 2 (2 hours, urgent ones still come), /unmute\n"
        "Problems come by themselves: a light not answering, TvD stuck, the internet down, a new Wi-Fi device.")
STATUS = ("Light Sync: {{ states('sensor.light_sync') }}\n"
          "{% set l = {'TvD': 'light.chambre_tvd', 'Corner': 'light.h6004_2', 'Top': 'light.h6004'} %}"
          "{% for n, e in l.items() %}{{ n }}: {{ states(e) }}{% if is_state(e, 'on') and state_attr(e, 'brightness') %} "
          "{{ (state_attr(e, 'brightness') / 2.55) | round }}%{% endif %}{{ ' | ' if not loop.last }}{% endfor %}\n"
          "TV box: {{ states('media_player.x96q_pro_plus') }}{% if state_attr('media_player.x96q_pro_plus', 'volume_level') is not none %}, "
          "volume {{ (state_attr('media_player.x96q_pro_plus', 'volume_level') * 100) | round }}%{% endif %}\n"
          "Wake-up: {{ states('" + WAKE + "')[:5] }} ({{ 'on' if is_state('" + WAKE_ON + "', 'on') else 'off' }}), bedtime {{ states('" + BEDTIME + "')[:5] }}\n"
          "USB backup: {{ 'no report yet' if states('sensor.light_sync_usb_backup') in ('unknown', 'unavailable') else state_attr('sensor.light_sync_usb_backup', 'problem') or (state_attr('sensor.light_sync_usb_backup', 'copies') | string) ~ ' copies, newest ' ~ state_attr('sensor.light_sync_usb_backup', 'newest') }}\n"
          "Pi: disk {{ states('" + DISK + "') }} %, processor {{ ((states('" + HEAT + "') | float(32) - 32) / 1.8) | round }} C\n"
          "Notifications: {{ 'muted until ' ~ states('" + MUTE + "')[:16] if as_timestamp(states('" + MUTE + "'), 0) > now().timestamp() else 'on' }}")


def post_auto(auto):
    requests.post(f"{HA}/api/config/automation/config/{auto['id']}", headers=H, timeout=10, json=auto).raise_for_status()
    print(f"automation {auto['id']}")


def helpers(ws):
    have = {x["id"] for x in ws.call(type="input_boolean/list")}
    made = []
    for key, name in [("telegram_control", "Telegram control")] + [(f"notify_{t}", f"Notify: {n}") for t, n in TOPICS.items()]:
        if key not in have:
            # the id comes from the name: made by its id's words first, then shown with the friendly name
            r = ws.call(type="input_boolean/create", name=key.replace("_", " "), icon="mdi:bell-outline")
            ws.call(type="input_boolean/update", input_boolean_id=r["id"], name=name, icon="mdi:bell-outline")
            made.append(f"input_boolean.{r['id']}")
    for eid in made:
        requests.post(f"{HA}/api/services/input_boolean/turn_on", headers=H, json={"entity_id": eid}, timeout=10)
    if not any(x["id"] == "telegram_mute_until" for x in ws.call(type="input_datetime/list")):
        ws.call(type="input_datetime/create", name="telegram mute until", has_date=True, has_time=True)
    if not any(x["id"] == "telegram_conversation" for x in ws.call(type="input_text/list")):
        ws.call(type="input_text/create", name="telegram conversation", min=0, max=64)
    if not any(x["id"] == "light_sync_telegram_ask" for x in ws.call(type="input_text/list")):
        ws.call(type="input_text/create", name="light sync telegram ask", min=0, max=100)
    if not any(x["id"] == "telegram_overnight" for x in ws.call(type="input_text/list")):
        ws.call(type="input_text/create", name="telegram overnight", min=0, max=255)
    print("helpers ready; newly switched on:", made or "none")


def pi_health(ws):
    """System Monitor, only the two sensors in use switched on (its others stay off)."""
    if not requests.get(f"{HA}/api/config/config_entries/entry?domain=systemmonitor", headers=H, timeout=10).json():
        f = requests.post(f"{HA}/api/config/config_entries/flow", headers=H, json={"handler": "systemmonitor"}, timeout=20).json()
        requests.post(f"{HA}/api/config/config_entries/flow/{f['flow_id']}", headers=H, json={}, timeout=30).raise_for_status()
    for eid in (DISK, HEAT):
        ws.call(type="config/entity_registry/update", entity_id=eid, disabled_by=None)
    ws.call(type="homeassistant/expose_entity", assistants=["conversation"], entity_ids=[DISK, HEAT], should_expose=False)
    print("System Monitor: disk and processor temperature (values appear about a minute after a reload)")


def main():
    ws = ex.WebSocket(HA)
    ws.sock.settimeout(60)
    ws.recv()
    ws.send({"type": "auth", "access_token": ex.token()})
    assert ws.recv().get("type") == "auth_ok"
    entry = requests.get(f"{HA}/api/config/config_entries/entry?domain=telegram_bot", headers=H, timeout=10).json()[0]
    subs = ws.call(type="config_entries/subentries/list", entry_id=entry["entry_id"])
    if len(subs) != 1:
        sys.exit(f"expected exactly one allowed chat, found {len(subs)}: fix that first")
    owner = int(subs[0]["unique_id"])
    ents = [x["entity_id"] for x in ws.call(type="config/entity_registry/list") if x.get("platform") == "telegram_bot"]
    bot = next(e for e in ents if e.startswith("event."))
    chat = next(e for e in ents if e.startswith("notify."))
    helpers(ws)
    pi_health(ws)

    # Rocky sees only what was chosen: nothing new by itself, and none of this
    ws.call(type="homeassistant/expose_new_entities/set", assistant="conversation", expose_new=False)
    hide = [CONTROL, MUTE, CONV, OVERNIGHT, ASK, "script.notify_me", *(f"input_boolean.notify_{t}" for t in TOPICS)]
    ws.call(type="homeassistant/expose_entity", assistants=["conversation"], entity_ids=[e for e in hide if e != "script.notify_me"], should_expose=False)

    requests.post(f"{HA}/api/config/script/config/notify_me", headers=H, timeout=10, json={
        "alias": "Notify me",
        "description": "Every notification: on Telegram (tools/ha_setup/telegram_setup.py).",
        "fields": {"title": {"description": "Title"}, "message": {"description": "Text"},
                   "level": {"description": "urgent, important or info"}, "topic": {"description": "|".join(TOPICS)},
                   "buttons": {"description": "Telegram buttons, e.g. Text:/command"}},
        "mode": "queued", "max": 20,
        "sequence": [
            {"variables": {"urgent": "{{ (level | default('important')) == 'urgent' }}",
                           "quiet": "{{ now().hour >= 23 or now().hour < 8 }}",
                           "the_topic": "{{ topic | default('system') }}"}},
            {"condition": "template", "value_template": "{{ states('input_boolean.notify_' ~ the_topic) != 'off' }}"},
            {"condition": "template", "value_template": "{{ urgent or as_timestamp(states('" + MUTE + "'), 0) <= now().timestamp() }}"},
            {"action": "telegram_bot.send_message", "continue_on_error": True,
             "data": {"entity_id": chat, "parse_mode": "plain_text",
                      "message": "{{ title | default('Rocky') }}: {{ message }}",
                      "disable_notification": "{{ quiet and not urgent }}",
                      "inline_keyboard": "{{ (buttons | default([], true) | list) + ['Mute 1 hour:/mute1'] }}"}},
            {"if": [{"condition": "template", "value_template": "{{ quiet and (title | default('Rocky')) not in states('" + OVERNIGHT + "') }}"}],
             "then": [{"action": "input_text.set_value", "target": {"entity_id": OVERNIGHT},
                       "data": {"value": "{{ ((states('" + OVERNIGHT + "') ~ '; ' if states('" + OVERNIGHT + "') not in ('', 'unknown', 'unavailable') else '') ~ (title | default('Rocky')))[:250] }}"}}]},
            {"if": [{"condition": "template", "value_template": "{{ urgent }}"}],
             "then": [{"action": APP, "data": {"title": "{{ title | default('Rocky') }}", "message": "{{ message }}"}}]},
        ]}).raise_for_status()
    print("script.notify_me")
    requests.post(f"{HA}/api/config/script/config/telegram_reply", headers=H, timeout=10, json={
        "alias": "Telegram reply",
        "description": "Light Sync's answers to /check /net /wifi and its buttons (tools/ha_setup/telegram_setup.py).",
        "fields": {"message": {"description": "Text"}, "buttons": {"description": "Telegram buttons"}},
        "mode": "queued", "max": 10,
        "sequence": [{"action": "telegram_bot.send_message", "continue_on_error": True,
                      "data": {"entity_id": chat, "parse_mode": "plain_text", "message": "{{ message }}",
                               "inline_keyboard": "{{ buttons | default([], true) | list }}"}}]}).raise_for_status()
    print("script.telegram_reply")
    ws.call(type="homeassistant/expose_entity", assistants=["conversation"], entity_ids=["script.notify_me", "script.telegram_reply"], should_expose=False)

    allowed = [{"condition": "template", "value_template": "{{ trigger.to_state.attributes.user_id == " + str(owner) + " }}"},
               {"condition": "state", "entity_id": CONTROL, "state": "on"}]
    is_type = lambda t: {"condition": "template", "value_template": "{{ trigger.to_state.attributes.event_type == '" + t + "' }}"}
    say = lambda text, **more: {"action": "telegram_bot.send_message",
                                "data": {"entity_id": chat, "parse_mode": "plain_text", "message": text, **more}}
    post_auto({
        "id": "sh_telegram_chat", "alias": "Smart home · Telegram: chat with Rocky",
        "description": "A message from the owner: the fixed commands first, then Rocky (tools/ha_setup/telegram_setup.py).",
        "triggers": [{"trigger": "state", "entity_id": bot}],
        "conditions": [is_type("telegram_text")] + allowed,
        "actions": [
            {"variables": {"said": "{{ (trigger.to_state.attributes.text | default(''))[:500] }}", "msg": "{{ trigger.to_state.attributes.id }}"}},
            {"action": "telegram_bot.send_chat_action", "continue_on_error": True, "data": {"entity_id": chat, "chat_action": "typing"}},
            {"action": "conversation.process", "response_variable": "fixed", "continue_on_error": True,
             "data": {"text": "{{ said }}", "language": "en", "agent_id": "conversation.home_assistant"}},
            {"if": [{"condition": "template", "value_template": "{{ fixed is defined and fixed.response.response_type != 'error' }}"}],
             "then": [{"action": "telegram_bot.set_message_reaction", "continue_on_error": True,
                       "data": {"chat_id": owner, "message_id": "{{ msg }}", "reaction": "\U0001F44D", "is_big": False}},
                      say("{{ fixed.response.speech.plain.speech | default('Done.') }}")],
             "else": [{"action": "telegram_bot.send_chat_action", "continue_on_error": True, "data": {"entity_id": chat, "chat_action": "typing"}},
                      {"if": [{"condition": "template", "value_template": "{{ states('" + CONV + "') not in ('', 'unknown', 'unavailable') }}"}],
                       # the conversation so far; Home Assistant refuses an empty id, so the first message goes without
                       "then": [{"action": "conversation.process", "response_variable": "ai", "continue_on_error": True,
                                 "data": {"text": "{{ said }}", "language": "en", "agent_id": "conversation.room_assistant",
                                          "conversation_id": "{{ states('" + CONV + "') }}"}}],
                       "else": [{"action": "conversation.process", "response_variable": "ai", "continue_on_error": True,
                                 "data": {"text": "{{ said }}", "language": "en", "agent_id": "conversation.room_assistant"}}]},
                      {"if": [{"condition": "template", "value_template": "{{ ai is defined and ai.conversation_id is defined and ai.conversation_id }}"}],
                       "then": [{"action": "input_text.set_value", "target": {"entity_id": CONV}, "data": {"value": "{{ ai.conversation_id[:64] }}"}}]},
                      say("{{ ai.response.speech.plain.speech if ai is defined and ai.response is defined else "
                          "'My AI is on the laptop, and it is off or asleep. The commands still work: /help.' }}")]},
        ],
        "mode": "parallel", "max": 5, "max_exceeded": "silent"})

    run_mode = [{"conditions": [{"condition": "template", "value_template": "{{ cmd == '" + c + "' }}"}],
                 "sequence": [{"action": "script.turn_on", "target": {"entity_id": s}}, say(words)]}
                for c, (s, words) in MODES.items()]
    asks = [{"conditions": [{"condition": "template", "value_template": "{{ cmd == '" + c + "' }}"}],
             "sequence": [{"action": "input_text.set_value", "target": {"entity_id": ASK},
                           "data": {"value": "{{ '" + c[1:] + "||' ~ (now().timestamp() | int | string) }}"}}]} for c in ASKS]
    t_arg = "(trigger.to_state.attributes.args | default([], true) | first | default(''))"
    hhmm = "a | regex_match('^[0-2]?[0-9]:[0-5][0-9]$') and a.split(':')[0] | int < 24"
    as_time = "{{ '%02d' % (a.split(':')[0] | int) }}:{{ a.split(':')[1] }}"
    wake = {"conditions": [{"condition": "template", "value_template": "{{ cmd == '/wake' }}"}],
            "sequence": [{"variables": {"a": "{{ " + t_arg + " | lower }}"}},
                         {"choose": [
                             {"conditions": [{"condition": "template", "value_template": "{{ a == 'off' }}"}],
                              "sequence": [{"action": "input_boolean.turn_off", "target": {"entity_id": WAKE_ON}},
                                           say("Wake-up light off. /wake on brings it back.")]},
                             {"conditions": [{"condition": "template", "value_template": "{{ a == 'on' }}"}],
                              "sequence": [{"action": "input_boolean.turn_on", "target": {"entity_id": WAKE_ON}},
                                           say("Wake-up light on, at {{ states('" + WAKE + "')[:5] }}.")]},
                             {"conditions": [{"condition": "template", "value_template": "{{ " + hhmm + " }}"}],
                              "sequence": [{"action": "input_datetime.set_datetime", "target": {"entity_id": WAKE},
                                            "data": {"time": as_time + ":00"}},
                                           {"action": "input_boolean.turn_on", "target": {"entity_id": WAKE_ON}},
                                           say("Wake-up light at " + as_time + ". Sleep well.")]}],
                          "default": [say("Wake-up light: {{ states('" + WAKE + "')[:5] }}, {{ 'on' if is_state('" + WAKE_ON + "', 'on') else 'off' }}. "
                                          "Change it: /wake 7:30, or /wake off.")]}]}
    bedtime = {"conditions": [{"condition": "template", "value_template": "{{ cmd == '/bedtime' }}"}],
               "sequence": [{"variables": {"a": "{{ " + t_arg + " }}"}},
                            {"if": [{"condition": "template", "value_template": "{{ " + hhmm + " }}"}],
                             "then": [{"action": "input_datetime.set_datetime", "target": {"entity_id": BEDTIME},
                                       "data": {"time": as_time + ":00"}},
                                      say("Bedtime reminder at " + as_time + ".")],
                             "else": [say("Bedtime: {{ states('" + BEDTIME + "')[:5] }}. Change it: /bedtime 0:45.")]}]}
    mute_for = lambda hours: {"action": "input_datetime.set_datetime", "target": {"entity_id": MUTE},
                              "data": {"timestamp": "{{ now().timestamp() + " + hours + " * 3600 }}"}}
    post_auto({
        "id": "sh_telegram_commands", "alias": "Smart home · Telegram: commands",
        "description": "/status, the modes, /mute (tools/ha_setup/telegram_setup.py).",
        "triggers": [{"trigger": "state", "entity_id": bot}],
        "conditions": [is_type("telegram_command")] + allowed,
        "actions": [
            {"variables": {"cmd": "{{ (trigger.to_state.attributes.command | default('')).split('@')[0] | lower }}",
                           "hours": "{{ [[((trigger.to_state.attributes.args | default([], true)) | first | default(1)) | float(1), 24] | min, 0.1] | max }}"}},
            {"choose": [
                {"conditions": [{"condition": "template", "value_template": "{{ cmd in ('/start', '/help') }}"}],
                 "sequence": [say(HELP, keyboard=KEYBOARD)]},
                {"conditions": [{"condition": "template", "value_template": "{{ cmd == '/status' }}"}],
                 "sequence": [say(STATUS, inline_keyboard=MODE_BUTTONS)]},
                *asks, wake, bedtime,
                {"conditions": [{"condition": "template", "value_template": "{{ cmd == '/cam' }}"}],
                 "sequence": [{"action": "telegram_bot.send_chat_action", "continue_on_error": True,
                               "data": {"entity_id": chat, "chat_action": "upload_photo"}},
                              {"action": "telegram_bot.send_photo", "continue_on_error": True,
                               "data": {"entity_id": chat, "caption": "Room camera, {{ now().strftime('%H:%M') }}",
                                        "url": "http://127.0.0.1:8123/api/camera_proxy/" + ROOM_CAM + "?token={{ state_attr('" + ROOM_CAM + "', 'access_token') }}"}}]},
                {"conditions": [{"condition": "template", "value_template": "{{ cmd == '/mute' }}"}],
                 "sequence": [mute_for("hours"), say("Quiet for {{ hours }} hours. Urgent ones still come. /unmute ends it.")]},
                {"conditions": [{"condition": "template", "value_template": "{{ cmd == '/unmute' }}"}],
                 "sequence": [mute_for("0"), say("Notifications are back on.")]},
                *run_mode],
             "default": [say("I don't know that one. /help lists what I can do.")]},
        ],
        "mode": "parallel", "max": 5, "max_exceeded": "silent"})
    post_auto({
        "id": "sh_telegram_buttons", "alias": "Smart home · Telegram: buttons",
        "description": "Mute, the mode buttons, It's mine / Not mine, Fix TvD (tools/ha_setup/telegram_setup.py).",
        "triggers": [{"trigger": "state", "entity_id": bot}],
        "conditions": [is_type("telegram_callback")] + allowed,
        "actions": [
            {"variables": {"cb": "{{ (trigger.to_state.attributes.command | default('')) | lower }}"}},
            {"choose": [
                {"conditions": [{"condition": "template", "value_template": "{{ cb == '/mute1' }}"}],
                 "sequence": [mute_for("1"), {"action": "telegram_bot.answer_callback_query",
                              "data": {"callback_query_id": "{{ trigger.to_state.attributes.id }}", "message": "Muted for an hour."}}]},
                {"conditions": [{"condition": "template", "value_template": "{{ cb.startswith('/mine_') or cb.startswith('/notmine_') or cb == '/fixtvd' or cb.startswith('/ok_') or cb.startswith('/no_') or cb.startswith('/undo_') }}"}],
                 "sequence": [{"action": "input_text.set_value", "target": {"entity_id": ASK},
                               "data": {"value": "{{ cb[1:].split('_')[0] ~ '|' ~ (cb.split('_')[1] if '_' in cb else '') ~ '|' ~ (now().timestamp() | int | string) }}"}},
                              {"action": "telegram_bot.answer_callback_query",
                               "data": {"callback_query_id": "{{ trigger.to_state.attributes.id }}", "message": "OK"}}]},
                *[{"conditions": [{"condition": "template", "value_template": "{{ cb == '" + c + "' }}"}],
                   "sequence": [{"action": "script.turn_on", "target": {"entity_id": s}},
                                {"action": "telegram_bot.answer_callback_query",
                                 "data": {"callback_query_id": "{{ trigger.to_state.attributes.id }}", "message": words}}]}
                  for c, (s, words) in MODES.items()]]}],
        "mode": "parallel", "max": 5, "max_exceeded": "silent"})

    notify = lambda title, message, level, topic: {"action": "script.notify_me", "data": {"title": title, "message": message, "level": level, "topic": topic}}
    post_auto({
        "id": "sh_notify_system", "alias": "Smart home · Notify: something is offline",
        "description": "Lights, cameras, the microphone offline 10 min, Light Sync 5 min (tools/ha_setup/telegram_setup.py).",
        "triggers": [{"trigger": "state", "entity_id": ["light.chambre_tvd", "light.h6004", "light.h6004_2", "camera.bedroom_camera",
                                                        "camera.room_camera", "assist_satellite.assist_microphone"],
                      "to": "unavailable", "for": {"minutes": 10}},
                     {"trigger": "state", "entity_id": "sensor.light_sync", "to": ["offline", "unavailable"], "for": {"minutes": 5}}],
        "conditions": [],
        "actions": [notify("Offline", "{{ trigger.to_state.name }} isn't answering.", "important", "system")],
        "mode": "queued", "max": 10, "max_exceeded": "silent"})
    post_auto({
        "id": "sh_notify_backups", "alias": "Smart home · Notify: backups",
        "description": "A backup failed, or none for 2 days (tools/ha_setup/telegram_setup.py).",
        "triggers": [{"trigger": "state", "entity_id": "event.backup_automatic_backup", "id": "event"},
                     {"trigger": "time", "at": "09:30:00", "id": "daily"}],
        "conditions": [{"condition": "template", "value_template":
                        "{{ (trigger.id == 'event' and trigger.to_state.attributes.event_type == 'failed') or (trigger.id == 'daily' and "
                        "as_timestamp(states('sensor.backup_last_successful_automatic_backup'), 0) < now().timestamp() - 172800) }}"}],
        "actions": [notify("Backup problem", "{{ 'The automatic backup failed.' if trigger.id == 'event' else 'No successful backup for over 2 days.' }}",
                           "important", "backups")],
        "mode": "single"})
    post_auto({
        "id": "sh_notify_updates", "alias": "Smart home · Notify: updates waiting",
        "description": "Once a day, if any (tools/ha_setup/telegram_setup.py).",
        "triggers": [{"trigger": "time", "at": "10:00:00"}],
        "conditions": [{"condition": "template", "value_template": "{{ states.update | selectattr('state', 'eq', 'on') | list | count > 0 }}"}],
        "actions": [notify("Updates waiting", "{{ states.update | selectattr('state', 'eq', 'on') | map(attribute='name') | join(', ') }}", "info", "updates")],
        "mode": "single"})

    post_auto({
        "id": "sh_notify_pi_health", "alias": "Smart home · Notify: the Pi's health",
        "description": "Disk 85 % full, processor 80 C for 5 min, Home Assistant started (tools/ha_setup/telegram_setup.py).",
        "triggers": [{"trigger": "numeric_state", "entity_id": DISK, "above": 85, "for": {"minutes": 10}, "id": "disk"},
                     {"trigger": "numeric_state", "entity_id": HEAT, "above": 176, "for": {"minutes": 5}, "id": "heat"},
                     {"trigger": "homeassistant", "event": "start", "id": "start"}],
        "conditions": [],
        "actions": [{"if": [{"condition": "trigger", "id": "start"}], "then": [{"delay": {"seconds": 60}}]},
                    notify("The Pi", "{% if trigger.id == 'disk' %}Its disk is {{ states('" + DISK + "') }} % full."
                           "{% elif trigger.id == 'heat' %}It's running hot: {{ ((states('" + HEAT + "') | float(32) - 32) / 1.8) | round }} C."
                           "{% else %}Home Assistant just started. If you didn't restart it, the Pi was down or lost power.{% endif %}",
                           "important", "system")],
        "mode": "queued", "max": 5, "max_exceeded": "silent"})
    post_auto({
        "id": "sh_telegram_good_morning", "alias": "Smart home · Telegram: good morning",
        "description": "08:00: what came in the night, if anything (tools/ha_setup/telegram_setup.py).",
        "triggers": [{"trigger": "time", "at": "08:00:00"}],
        "conditions": [{"condition": "template", "value_template": "{{ states('" + OVERNIGHT + "') not in ('', 'unknown', 'unavailable') }}"},
                       {"condition": "template", "value_template": "{{ as_timestamp(states('" + MUTE + "'), 0) <= now().timestamp() }}"}],
        "actions": [say("Good morning. While you slept: {{ states('" + OVERNIGHT + "') }}."),
                    {"action": "input_text.set_value", "target": {"entity_id": OVERNIGHT}, "data": {"value": ""}}],
        "mode": "single"})

    # the bedtime reminder through the same door (sleep_setup.py writes it the same way)
    bed = requests.get(f"{HA}/api/config/automation/config/sh_bedtime_reminder", headers=H, timeout=10)
    if bed.ok:
        cfg = bed.json()
        for a in cfg["actions"]:
            if a.get("action") == APP:
                a["action"] = "script.notify_me"
                a["data"].update(level="info", topic="sleep")
        post_auto({**cfg, "id": "sh_bedtime_reminder"})
    print(f"Done. Bot events: {bot}; chat: {chat}")


if __name__ == "__main__":
    main()
