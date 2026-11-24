#!/usr/bin/env python3
"""
assistant_setup.py - the smarter "Okay Nabu" (Oct 27, owner: "I want to make an assistant"; picked a smarter voice
assistant, the Light Sync modes allowed, llama3.2 3B already on the laptop, and a guard rather than a bigger model).
Safe to run again.

  Ollama integration    the laptop's local model through Light Sync's AI doorway: Home Assistant asks the add-on on the
                        Pi (http://127.0.0.1:8090/ollama), which passes it to the laptop wherever it is now (Oct 30: the
                        laptop's address changed, the integration can't change its URL). Chat only: light_sync.py
                        ollama_forward, ollama_proxy; Ollama itself stays on 127.0.0.1 on the laptop
  Rocky                 the conversation agent (conversation.room_assistant; the owner named it): llama3.2:3b, the instructions below (short spoken answers, act only when
                        asked, the current Light Sync mode), Assist's tools, 6 messages of history, unloaded after 5 min
  What it may use       lights, media and the 8 Light Sync mode scripts (the house rule, widened by the owner); never
                        cameras, locks, plugs
  The voice pipeline    "Hey Rick" uses the Room assistant, the fixed commands first ("party on" stays instant). The
                        MeetUp's speaker is off: Rocky answers with TvD's light cue (light_sync.py voice_cue)
  The guard (laptop)    unless a sentence clearly asks for a change, the model gets only the reading tools (a question
                        about the home) or none (hello, thanks, a joke): it can't switch anything then
  Test                  py tools/tests/assistant_test.py (nothing is switched while it runs)

  py tools/ha_setup/assistant_setup.py
"""
import os
import sys

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # tools\ (export_ha)
import export_ha as ex  # noqa: E402

HA = os.getenv("HA_URL", "http://192.168.1.10:8123")
H = {"Authorization": f"Bearer {ex.token()}"}
MODEL = "llama3.2:3b"
MODES = ["script.normal_light", "script.party_mode", "script.movie_time", "script.movie_music", "script.gaming_time",
         "script.gaming_music", "script.good_night", "script.all_lights_off"]
MODE_NAMES = ("{{ {'party': 'Party (music)', 'ambient': 'Movie time (the TV colours)', 'gaming': 'Gaming', 'effects': "
              "'Effects', 'normal': 'Normal', 'paused': 'none (lights as they are)', 'standby': 'none'}"
              ".get(states('sensor.light_sync'), states('sensor.light_sync')) }}")
NAME = "Rocky"  # the owner picked it (Oct 27; was Aura). Wake word: "Hey Rick" (a community model, wake_word_test.py)
PROMPT = f"""You are {NAME}, the voice of this bedroom smart home (Light Sync). Speak in one short sentence: plain words, no lists, no emoji, no markdown, never show code or tool names.

When to act: only when the user clearly asks you to change something now: turn on or off, set, dim, brighten, change a colour, start, play, pause, stop, or a mode or an activity they are starting. Then do exactly that, once.
When not to act: greetings, thanks, goodbyes, small talk, jokes and every question (what, which, how, is, are, can you, do you) are answered in words only. To answer a question about the home you may only read it with GetLiveContext; never turn on, turn off, set, pause or start anything for a question.

Names: the TV light (the strip behind the TV) is TvD, the corner light is Corner, the top light is Top. "The TV" is the bedroom TV box (X96Q Pro plus), the only TV here. Give a light or the TV by its name only: no area, no floor. Do things by calling the tools; never write a tool call or JSON in your answer.

Modes (scripts): a movie or a show is Movie time, music or a party is Party, a game is Gaming, relaxing or reading is Normal, going to sleep is Good night, everything off is All off.
Examples:
- "hello" -> say hi, do nothing.
- "what's your name?" or "who are you?" -> answer "I'm {NAME}, the voice of your room." and do nothing.
- "thanks, that's all" -> say you're welcome, do nothing.
- "what can you do?" -> answer "I can switch, dim and colour your lights, control the TV and start the Light Sync modes." and do nothing.
- "is the TV on?" -> read with GetLiveContext, then answer.
- "how bright is the corner light?" -> read with GetLiveContext, then answer.
- "make it cozy for a movie" -> start Movie time.
- "party time" or "let's party" -> start Party (the script, not just the lights).
- "dim the top light to 30 percent" -> set Top to 30 percent.
- "make the corner blue and dim the top light" -> do both.
- "I'm bored" -> answer "Want a movie, a game or some music?"; then "a movie" -> start Movie time; just "yes" -> ask "Which one: a movie, a game or music?".

In the room nobody hears your words (the lights show that you listened and acted), so never end with "Anything else?" or another question unless you offer a choice.

Never make the lights flash or strobe, and never brighter than asked: the owner gets headaches. You can't see or control cameras, locks or plugs. It is {{{{ now().strftime('%A %H:%M') }}}}. The Light Sync mode now: {MODE_NAMES}."""
OPTIONS = {"model": MODEL, "prompt": PROMPT, "llm_hass_api": ["assist"], "num_ctx": 8192, "max_history": 6,
           "keep_alive": 300, "think": False}
# The names people say (Oct 27, the owner on the phone: "put the TV light on blue" -> the model used the Salon's TV,
# "XBR-65X850D", and Home Assistant's own commands knew no "TV light" either). The entity's own name stays (None).
ALIASES = {"light.chambre_tvd": ["TV light", "TV strip", "strip", "light behind the TV"],
           "light.h6004_2": ["corner light"], "light.h6004": ["top light"],
           "media_player.x96q_pro_plus": ["TV", "bedroom TV", "TV box"]}
# Nov 6: the Salon's Sony TV (XBR-65X850D) isn't the owner's: removed from Home Assistant (Google Cast device, Android TV
# Remote and DLNA entries), so Rocky can't reach it.


def flow(path, payload):
    r = requests.post(f"{HA}/api/config/config_entries/{path}", headers=H, json=payload, timeout=60)
    r.raise_for_status()
    return r.json()


def main():
    url = "http://127.0.0.1:8090/ollama"  # from Home Assistant on the Pi: the add-on, which finds the laptop
    entries = requests.get(f"{HA}/api/config/config_entries/entry?domain=ollama", headers=H, timeout=10).json()
    if entries:
        entry = entries[0]["entry_id"]
        print(f"Ollama integration: there ({entries[0]['title']})")
    else:
        r = flow("flow", {"handler": "ollama"})
        r = flow(f"flow/{r['flow_id']}", {"url": url})
        if r.get("type") != "create_entry":
            sys.exit(f"Ollama integration: couldn't connect to {url} (is Light Sync running on the laptop?) {r.get('errors')}")
        entry = r["result"]["entry_id"]
        print(f"Ollama integration: added ({url})")
    ws = ex.WebSocket(HA)
    ws.sock.settimeout(60)
    ws.recv()
    ws.send({"type": "auth", "access_token": ex.token()})
    assert ws.recv().get("type") == "auth_ok"
    subs = [s for s in ws.call(type="config_entries/subentries/list", entry_id=entry) if s["subentry_type"] == "conversation"]
    if subs:
        r = flow("subentries/flow", {"handler": [entry, "conversation"], "subentry_id": subs[0]["subentry_id"], "source": "reconfigure"})
        r = flow(f"subentries/flow/{r['flow_id']}", OPTIONS)
    else:
        r = flow("subentries/flow", {"handler": [entry, "conversation"]})
        r = flow(f"subentries/flow/{r['flow_id']}", {"name": "Room assistant", **OPTIONS})
    print("Room assistant:", r.get("reason") or r.get("type"))
    ws.call(type="homeassistant/expose_entity", assistants=["conversation"], entity_ids=MODES, should_expose=True)
    print("Assist may start the Light Sync modes:", ", ".join(m.split(".")[1] for m in MODES))
    for eid, names in ALIASES.items():
        ws.call(type="config/entity_registry/update", entity_id=eid, aliases=[None, *names])
        print(f"{eid} is also: {', '.join(names)}")
    agent = next((s["entity_id"] for s in requests.get(f"{HA}/api/states", headers=H, timeout=15).json()
                  if s["entity_id"].startswith("conversation.room_assistant")), None)
    pl = ws.call(type="assist_pipeline/pipeline/list")
    p = ws.call(type="assist_pipeline/pipeline/get", pipeline_id=pl["preferred_pipeline"])
    keys = ("conversation_engine", "conversation_language", "language", "name", "stt_engine", "stt_language", "tts_engine",
            "tts_language", "tts_voice", "wake_word_entity", "wake_word_id", "prefer_local_intents")
    upd = {k: p.get(k) for k in keys}
    upd.update(conversation_engine=agent or p["conversation_engine"], prefer_local_intents=True)
    ws.call(type="assist_pipeline/pipeline/update", pipeline_id=pl["preferred_pipeline"], **upd)
    print(f"Voice pipeline \"{p['name']}\": {upd['conversation_engine']}, the fixed commands first")
    # Oct 27: updating the pipeline ends the MeetUp's wait for the wake word, and it never started a new one (deaf
    # until restarted: "no reply"). A restart starts it again.
    try:
        ws.call(type="supervisor/api", endpoint="/addons/core_assist_microphone/restart", method="post")
    except RuntimeError:
        pass  # the Supervisor answers a restart with an empty reply, which reads as an error here
    print("Assist Microphone restarted (listening for the wake word again)")
    if agent:  # the name people see in Home Assistant (the entity id stays, the tests use it)
        ws.call(type="config/entity_registry/update", entity_id=agent, name=NAME)
        print(f"{agent} is now called {NAME}")
    activities()
    quiet_for_films()


# Activities as fixed commands (Oct 27): the 3B model answered "a movie" (after its own "Want a movie, a game or some
# music?") and "party time" with "turn the lights on". Fixed commands are matched before Aura: instant and always right,
# alone or as the answer to her question.
ACTIVITIES = [
    ("movie", "script.movie_time", "Movie time.",
     ["[a] movie [please]", "movie (time|night)", "[(let's|let us)] watch a movie", "(i'm|i am) (going to|gonna) watch a movie"]),
    ("game", "script.gaming_time", "Gaming.",
     ["[a] game [please]", "game time", "[(let's|let us)] play a game", "(i'm|i am) (going to|gonna) play [a game]"]),
    ("music", "script.party_mode", "Party mode on.",
     ["[some] music [please]", "(let's|let us) party", "play some music"]),
    ("normal", "script.normal_light", "Normal light.",
     ["normal [(light|lights|mode)] [please]", "(regular|everyday) light[s] [please]"]),
    # Oct 27: from the phone (no room) "turn all the lights on" matched none of HA's own commands and went to the
    # model, which made up a script. All the lights = the room's everyday light; off = Light Sync off too
    ("lights_on", "script.normal_light", "Lights on.",
     ["(turn|switch) [all] [the] lights on [please]", "(turn|switch) on [all] [the] lights [please]",
      "[all] [the] lights on [please]"]),
    ("lights_off", "script.all_lights_off", "All off.",
     ["(turn|switch) [all] [the] lights off [please]", "(turn|switch) off [all] [the] lights [please]",
      "[all] [the] lights off [please]", "all off"]),
]


def activities():
    auto = {"id": "sh_voice_activities", "alias": "Smart home · Voice: activities",
            "description": "A movie, a game, some music: the Light Sync mode, before the AI (tools/ha_setup/assistant_setup.py).",
            "triggers": [{"trigger": "conversation", "command": sentences, "id": key} for key, _, _, sentences in ACTIVITIES],
            "conditions": [],
            "actions": [{"choose": [{"conditions": [{"condition": "trigger", "id": key}],
                                     "sequence": [{"action": "script.turn_on", "target": {"entity_id": script}},
                                                  {"set_conversation_response": said}]}
                                    for key, script, said, _ in ACTIVITIES]}],
            "mode": "queued"}
    requests.post(f"{HA}/api/config/automation/config/sh_voice_activities", headers=H, timeout=10, json=auto).raise_for_status()
    print("Fixed commands for activities:", ", ".join(s[0] for _, _, _, s in ACTIVITIES))


def quiet_for_films():
    """Rocky doesn't listen in TV mode (Oct 30, owner: yes; a film woke him 8 times in about 90 minutes, each a cue on
    TvD and film lines sent to him). The phone still reaches him. After a film the microphone is restarted too: it has
    gone deaf without a word after changes before (docs/home-assistant.md)."""
    auto = {"id": "sh_voice_quiet_for_films", "alias": "Smart home · Voice: quiet during films",
            "description": "TV mode on: the MeetUp's microphone muted for Rocky. Off: unmuted and restarted (tools/ha_setup/assistant_setup.py).",
            "triggers": [{"trigger": "state", "entity_id": "input_boolean.tv_ambient", "to": ["on", "off"]},
                         {"trigger": "homeassistant", "event": "start"}],
            "conditions": [],
            "actions": [{"choose": [{"conditions": [{"condition": "state", "entity_id": "input_boolean.tv_ambient", "state": "on"}],
                                     "sequence": [{"action": "switch.turn_on", "target": {"entity_id": "switch.assist_microphone_muet"}}]}],
                         "default": [{"if": [{"condition": "state", "entity_id": "switch.assist_microphone_muet", "state": "on"}],
                                      "then": [{"action": "switch.turn_off", "target": {"entity_id": "switch.assist_microphone_muet"}},
                                               {"action": "hassio.addon_restart", "data": {"addon": "core_assist_microphone"}}]}]}],
            "mode": "restart"}
    requests.post(f"{HA}/api/config/automation/config/sh_voice_quiet_for_films", headers=H, timeout=10, json=auto).raise_for_status()
    print("Rocky doesn't listen in TV mode (sh_voice_quiet_for_films)")


if __name__ == "__main__":
    main()
