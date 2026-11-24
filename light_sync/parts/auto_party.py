"""Auto Party (Nov 6, owner: "when I'm playing music it's just gonna add the colours and run the Party script").

Music playing (the laptop's sound, or the room mic through the Pi) with a steady beat for AUTO_PARTY_S, the lights on
in Normal (or on with no mode), Auto mode on, not late at night -> Party (script.party_mode). Quiet for
AUTO_PARTY_QUIET_S -> back to Normal. Only a Party it started itself is ended; a mode the owner picks wins at once
(and that song doesn't start Party again). Never switches the lights on, never takes over a film or a game."""

import time

from parts import approve, config, guard, ha_link, learner, web
YES = {"at": 0.0}  # the owner said yes to Party (Nov 12: asks first)

AUTO_PARTY_S = 20        # a steady beat this long: music, not a notification sound or a video's intro
#                          (Nov 7: tuned by Light Sync itself, learner.get("auto_party_s"), 20-90 s)
AUTO_PARTY_QUIET_S = 90  # no beat this long: the music stopped (a pause between songs is shorter)


def music_now():
    e = web.EAR
    try:
        return bool(e) and e.period() is not None and e.bpm() > 0
    except Exception:
        return False


def auto_party_loop():
    heard = quiet = 0.0
    started, before, skip_until, started_at = False, None, 0.0, 0.0
    auto, lit, checked = False, False, 0.0
    while True:
        time.sleep(2)
        now = time.time()
        if config.STANDBY:  # the other machine drives
            heard = quiet = 0.0
            continue
        if now - checked > 10:
            checked = now
            try:
                auto = ha_link.ha_state("input_boolean.light_sync_auto_mode")["state"] == "on"
                lit = ha_link.ha_state("light.chambre_lights")["state"] == "on"
            except Exception:
                pass
        music, mode = music_now(), guard.mode_on()
        if started:
            if mode != "party":  # the owner picked something else: theirs
                if now - started_at < 300:  # within 5 min of Auto Party starting: it was a mistake (the learner)
                    learner.undone("auto_party", within=300)
                started, skip_until = False, now + 15 * 60
                continue
            quiet = 0.0 if music else quiet + 2
            if quiet >= AUTO_PARTY_QUIET_S:
                started, heard = False, 0.0
                print("Auto Party: the music stopped - back to Normal", flush=True)
                guard.room_script("script.normal_light")
                approve.tell("The music stopped: Party ended, back to Normal.",
                             undo=lambda: guard.room_script("script.party_mode"))
            continue
        hour = time.localtime().tm_hour
        ok = (auto and lit and mode in (None, "normal") and now > skip_until and not config.LATE_NIGHT
              and not (hour >= 23 or hour < 7))
        heard = heard + 2 if (music and ok) else 0.0
        if YES["at"] > started_at and not started:  # the owner said yes
            started, before, quiet, heard, started_at = True, mode, 0.0, 0.0, YES["at"]
            learner.did("auto_party")
            print("Auto Party: you said yes - Party", flush=True)
        if heard >= learner.get("auto_party_s"):
            heard, skip_until = 0.0, now + 10 * 60  # asked: not again for 10 min
            print("Auto Party: music playing - asking about Party", flush=True)

            def go():
                YES["at"] = time.time()
                guard.room_script("script.party_mode")
            approve.ask("autoparty", "Music with a steady beat is playing: start Party?", go, timeout=180)
