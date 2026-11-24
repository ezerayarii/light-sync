"""
light_sync.py - make your bedroom lights dance to whatever plays on the Android box.

  Laptop mic   -> hears the music, detects beats + loudness
  Room camera  -> (optional) looks at the monitor and picks the screen's main color
  Home Assistant API -> drives your 3 RGB lights:
      TvD    = screen color (ambilight), brightness follows loudness
      Corner = flashes on every beat (complementary color)
      Top    = slow color wash that speeds up with the energy

Run:
  py light_sync/light_sync.py           # Ctrl+C stops and restores your lights (start_lights.bat does this)
  py light_sync/light_sync.py --list    # list mics, if the default one is wrong
  py light_sync/light_sync.py --demo    # fake music, no lights touched - just to see the live view
  py light_sync/light_sync.py --scan    # find Govee lights on your network for super-fast LAN control

Live view: opens http://localhost:8089 in your browser - shows each light's color,
beat flashes, loudness, BPM and where the colors come from.

Audio source: laptop mic by default. To use the MeetUp mic on the Pi instead,
stream it with go2rtc and set AUDIO_URL (needs ffmpeg installed on this machine).

Setup:
  pip install numpy requests sounddevice opencv-python-headless
  set HA_URL=http://<pi-tailscale-or-local-ip>:8123
  set SNAPSHOT_URL=http://<pi>:8081/   (optional, for screen colors)
  (use "export" instead of "set" on Mac/Linux)
  Secrets (ha_token, snap_pass) go in secrets.yaml next to this script, not in the .bat.
"""

import numpy as np
import os
import sys
import threading
import time

from parts import boot  # first: the folders and secrets.yaml, before the settings read them
from parts.boot import (
    LOGS, LOOP, _Tee, watch_for_stall,
)
from parts.config import (
    AMBIENT_BRIGHT, AUDIO, AUDIO_URL, BASE_PALETTE, COLOR_SETS, DEMO, DEMO_RAW, DJ_LOOKS, FLOW_STYLES, GAMING_BRIGHT,
    GOVEE_BEAT, GOVEE_SCREEN, GOVEE_WASH, HA_GAMING, HA_HEARTBEAT, HA_MIC, HA_NORMAL_SCENE, HA_TOKEN, HA_URL, LEVEL,
    LIGHT_BEAT, LIGHT_SCREEN, LIGHT_WASH, MIC_DEVICE, MIN_ENERGY, MOODS, PARTS, PAUSE_GRACE_S, PULSE, ROLE,
    SCREEN_SEGMENTS, SCREEN_SEGMENTS_FROM_RIGHT, SMOOTH, TRANS_S, TUNE, TV_ACTION, TV_DARK, TV_DARK_TVD_MAX,
    TV_GLIDE_S, USE, USER_PULSE, USING_LAN,
)
from parts import (
    bulbs, colours, config, ears, flow, eyes, games, guard, ha_link, helpers, normal_light, pi_backup,
    party_dj, rocky_cue, screen_map, songs, sources, app_requests, tvd_scenes, web, wifi,
)


if __name__ == "__main__" and sys.platform == "win32" and os.getenv("ROLE", "main") == "main":
    os.makedirs(LOGS, exist_ok=True)
    _log = os.path.join(LOGS, "light_sync.log")
    open(_log, "w", encoding="utf-8").close()  # one run per file
    sys.stdout = _Tee(sys.stdout, _log)
    sys.stderr = _Tee(sys.stderr, _log)
    print(f"=== Light Sync started {time.strftime('%Y-%m-%d %H:%M:%S')}", flush=True)

def main():
    from parts import logring
    logring.install()  # the app's Logs (Nov 6): a copy of what's printed, secrets masked
    if "--list" in sys.argv:
        import sounddevice as sd
        print(sd.query_devices())
        return
    if "--scan" in sys.argv:
        print("Looking for Govee lights with LAN Control on...")
        found = bulbs.govee_scan()
        if not found:
            print("None found. In the Govee app: open each light -> settings -> turn on 'LAN Control',"
                  "\nthen allow Python through Windows Firewall and try again.")
        for ip, (sku, dev) in found.items():
            print(f"  {ip}   model {sku}   id {dev}  (use the id in start_lights.bat: it survives IP changes)")
        return
    if not HA_TOKEN and not DEMO:
        sys.exit("Set HA_TOKEN first (HA profile -> Security -> Long-lived access tokens).")

    lights = [LIGHT_SCREEN, LIGHT_BEAT, LIGHT_WASH]
    backup = ROLE == "backup" and not DEMO
    config.STANDBY = backup  # until we know the laptop isn't running (before any thread reads the camera)
    if not DEMO:
        helpers.poll_helpers()  # know the dashboard state before touching any light
    saved = {}
    if config.RUNNING and not DEMO and not backup:
        saved = pi_backup.adopt_restore() or bulbs.save_states(lights)  # the Pi was driving them: its saved states
        web.STATE["restore"] = bulbs.brief(saved)
    govee_ids = [GOVEE_SCREEN, GOVEE_BEAT, GOVEE_WASH]
    lan_ips = ["", "", ""]
    if USING_LAN:
        bulbs.LAN = bulbs.GoveeLan()
        if not backup:  # the Pi looks them up each time it takes over
            lan_ips = bulbs.resolve_govee(govee_ids)
    screen = bulbs.LightWorker(LIGHT_SCREEN, 5, lan_ips[0])
    screen.segments, screen.seg_from_right = SCREEN_SEGMENTS, SCREEN_SEGMENTS_FROM_RIGHT
    rocky_cue.VOICE["light"] = screen  # Rocky's light cue shows on TvD
    beat = bulbs.LightWorker(LIGHT_BEAT, 8, lan_ips[1])
    wash = bulbs.LightWorker(LIGHT_WASH, 4, lan_ips[2])
    roles = (("screen", screen), ("beat", beat), ("wash", wash))

    def show_control():
        web.STATE["control"] = {w.entity_id: f"Govee LAN {w.lan_ip}" if w.lan_ip else "via Home Assistant"
                            for w in (screen, beat, wash)}
        for eid, how in web.STATE["control"].items():
            print(f"{eid}: {how}", flush=True)

    if not backup:
        show_control()
    for role, w in roles:
        w.reset(USE[role])  # lights taken out of Light Sync stay as they are
        w.start()
    if USING_LAN and bulbs.GOVEE_RECHECK and not DEMO:
        threading.Thread(target=bulbs.govee_recheck_loop, args=((screen, beat, wash), govee_ids), daemon=True).start()

    def let_go():
        """Lights taken out of Light Sync on the dashboard: left alone, also when restoring."""
        return {w.entity_id for w in (screen, beat, wash) if not w.enabled}

    if config.SNAPSHOT_URL:
        threading.Thread(target=screen_map.screen_color_loop, daemon=True).start()
        print("Screen colors: ON (camera)")
    else:
        print("Screen colors: OFF (music palette only)")

    ear = web.EAR = ears.Ear()
    device = int(MIC_DEVICE) if MIC_DEVICE and MIC_DEVICE.isdigit() else MIC_DEVICE
    hue, flash_at, beat_rgb, beats = 0.0, 0.0, colours.hue_rgb(0.5), 0
    pal_i, e_s, next_fired = 0, 0.0, 0.0
    seg_cur = None  # TvD segments: faded colors
    fol_cur, fol_t = {}, time.time()  # Corner / Top following TvD (Nov 6): their glided colours
    fol_bri = {}  # ... and their glided brightness (Nov 6 camera test: it jumped 79 / 34 times a minute)
    scene_cur = None  # TvD's DJ scene, lightly smoothed
    tv_lum, tv_lo, tv_hi = None, None, None  # TV mode: the picture's brightness, smoothed, and its recent range
    BULB_LOW = 3  # Corner / Top's lowest brightness (of 255, 1 %): colours hold down to 1 % (Nov 7 camera test)
    quiet_env, room_on = 0.0, 1.0  # TV / TV + Music: the sound over ~1.5 s, and how much Corner / Top are on (Nov 6)
    vol_mult = 1.0  # TV / TV + Music: the TV box's volume as the brightness knob, glided (Nov 6)
    vol_peak = 0.0  # the loudest volume lately (Auto "full brightness at volume"), fading over ~3 h
    black_since, dark_on, quiet_dark = None, False, False  # TV / TV + Music: a black screen since when; all lights off now
    tv_snd, tv_slow = 0.0, 0.0  # TV mode: the sound's loudness, quick and slow (TvD rides it and its hits, Nov 6)
    mix_c = np.array([140.0, 60.0, 255.0])  # "mix" part: the sound's own color (bass red, mids green, highs blue)
    scr_seen = 0.0  # TV / gaming: last time the screen gave a color
    tvd_lv, seg_lv = 0.5, None  # TvD's smoothed music level, and its segments' (no flashes or punches on TvD)
    trans_at, trans_from = 0.0, {}  # the switch cross-fade: when the mode last changed, and the colors before
    normal_view_at = 0.0  # Normal: when its day curve and sliders were last refreshed
    dj_look, dj_at = "groove", 0.0  # Auto DJ: the look in use and since when
    flow_pos, flow_t = 0.0, time.time()  # flow style: position along the palette
    comfort_t = time.time()
    # sound analysis: loudness now / on average, how bright the sound is, the song section and its mood
    e_short = e_long = cent_s = 0.0
    ana_t = time.time()
    section, mood = "quiet", "upbeat"
    mood_votes, tempo_hold = dict.fromkeys(MOODS, 0.0), 0.0  # the song's mood: how long each was heard lately (~15 s)
    cur = {"screen": np.zeros(3), "beat": np.zeros(3), "wash": np.zeros(3)}  # faded colors
    print("Listening... play some music. Ctrl+C to stop.")

    mic = mic_label = None
    if DEMO:
        threading.Thread(target=ears.demo_audio_loop, args=(ear,), daemon=True).start()
        sources.set_audio("demo track (no lights touched)")
        print("DEMO mode: fake music, lights are NOT changed")
    elif AUDIO == "pulse":
        sources.set_audio("Pi: MeetUp mic")
        print("Audio: MeetUp mic on the Pi (open only while Music sync, Music in games or Music in TV mode needs it)")
    elif AUDIO_URL:
        threading.Thread(target=ears.stream_audio_loop, args=(ear, AUDIO_URL), daemon=True).start()
        sources.set_audio("MeetUp mic (network)")
        print("Audio: network stream (MeetUp mic)")
    else:
        import sounddevice as sd
        try:
            import soundcard  # noqa: F401  (on the main thread: it sets up COM there; the sound test reads laptop sound)
        except Exception:
            pass
        mics = sources.list_mics()
        if not DEMO:  # fill the dashboard's Microphone list (keeps the current pick if it still exists)
            try:
                ha_link.session.post(f"{HA_URL}/api/services/input_select/set_options", timeout=3,
                             json={"entity_id": HA_MIC, "options": list(mics)})
                helpers.poll_helpers()
            except Exception:
                pass
        mic, mic_label = sources.open_mic(sd, ear, mics, config.MIC_CHOICE, device)

    if backup:
        pi_backup.MAIN_ALIVE = pi_backup.main_alive()
        threading.Thread(target=pi_backup.watch_main_loop, daemon=True).start()
    if not DEMO:
        threading.Thread(target=helpers.ha_control_loop, daemon=True).start()
        threading.Thread(target=helpers.helper_watch_loop, daemon=True).start()
        threading.Thread(target=helpers.ha_publish_loop, daemon=True).start()
        threading.Thread(target=wifi.wifi_loop, daemon=True, name="wifi").start()
        threading.Thread(target=guard.room_guard_loop, daemon=True, name="room guard").start()
        from parts import auto_party

        def box_volume_loop():  # the TV box's volume, for the volume dimmer (Nov 6)
            while True:
                try:
                    a = ha_link.ha_state("media_player.x96q_pro_plus")
                    at = a.get("attributes", {})
                    config.BOX_VOLUME = (0.0 if at.get("is_volume_muted") else float(at["volume_level"])) \
                        if a.get("state") not in ("off", "unavailable", "standby") and at.get("volume_level") is not None else None
                except Exception:
                    config.BOX_VOLUME = None
                time.sleep(1)
        threading.Thread(target=box_volume_loop, daemon=True, name="box volume").start()
        threading.Thread(target=auto_party.auto_party_loop, daemon=True, name="auto party").start()
        threading.Thread(target=party_dj.party_dj_loop, daemon=True, name="party dj").start()
        from parts import health, telegram_ask  # Nov 7: problems first + /check /net /wifi on Telegram
        health.start(roles)
        telegram_ask.start()
        from parts import learner  # Nov 7: Light Sync learns from its own mistakes, recaps the week
        learner.start()
        from parts import prayer  # Nov 8: prayer times, Fajr's soft wake, Maghrib for the evening Normal
        prayer.start()
        from parts import approve  # Nov 12: lights on by themselves ask first, off ones tell (Undo)
        approve.start()
        if sys.platform == "win32":
            threading.Thread(target=games.laptop_game_loop, daemon=True).start()
        else:  # the Pi: a game's colours from the laptop's game helper, forgotten 2 s after the last (Nov 6)
            threading.Thread(target=games.remote_expire_loop, daemon=True, name="laptop game").start()
        if ROLE == "main":  # learning the owner's habits (habits.py: laptop only, the data stays here)
            try:
                import habits
                app_requests.HABITS = habits.start(sys.modules[__name__])
            except Exception as e:
                print(f"Habits: not learning ({e})", flush=True)
            if sys.platform == "win32":  # Home Assistant's backups copied to the USB stick (usb_backup.py)
                try:
                    import usb_backup
                    usb_backup.start(sys.modules[__name__])
                except Exception as e:
                    print(f"USB backup: not running ({e})", flush=True)
    view_url = web.start_view()
    if "--no-browser" not in sys.argv and view_url:
        import webbrowser
        webbrowser.open(view_url)
    names = {"screen": (screen, LIGHT_SCREEN), "beat": (beat, LIGHT_BEAT), "wash": (wash, LIGHT_WASH)}
    last_view = 0.0
    paused = not config.RUNNING
    off_since = None  # every mode off since (see PAUSE_GRACE_S)
    if paused:
        print("Waiting - Music sync, TV ambient and Gaming are all off in Home Assistant", flush=True)
    last_lock = 0.0
    mode = "party"
    tv_t, tv_dt = time.time(), 0.03  # TV mode's glides: seconds, not loop turns
    dark_lv = 0.0  # the Cinema look: how far the lights are up with the film's action (0 = the room dark)
    dark_tvd = 0.0  # ... TvD's own, much slower (it never punches)
    top_on = False  # ... Top switched on (it's off while the film is calm, Oct 28)

    threading.Thread(target=watch_for_stall, daemon=True, name="stall watch").start()
    try:
        while True:
            now = time.time()
            LOOP["at"] = now
            tv_dt, tv_t = min(0.1, now - tv_t), now
            if backup and pi_backup.MAIN_ALIVE and not config.STANDBY:
                config.STANDBY = True
                for w in (screen, beat, wash):
                    w.reset(w.enabled)  # drop anything still queued: the laptop drives them now
                print("Laptop Light Sync is back - handing the lights over", flush=True)
            elif backup and not pi_backup.MAIN_ALIVE and config.STANDBY:
                config.STANDBY = False
                print("Laptop Light Sync stopped - the Pi takes over", flush=True)
                helpers.poll_helpers()
                # (Nov 6: no more "the laptop's game went away, Gaming off" here - the laptop only runs the game helper
                # now, which switches Gaming off itself when the game closes; the old rule read the old laptop's stale
                # heartbeat and switched Gaming off in the middle of a game when the Pi restarted)
                if USING_LAN:
                    for w, ip in zip((screen, beat, wash), bulbs.resolve_govee(govee_ids)):
                        w.set_lan(ip)
                show_control()
                for role, w in roles:
                    w.reset(USE[role])
                paused = not config.RUNNING
                saved = {} if paused else (pi_backup.adopt_restore() or bulbs.save_states(lights))
                web.STATE["restore"] = bulbs.brief(saved) if saved else None
            if AUDIO == "pulse":  # Pi mic: open only while the beat matters
                want_mic = not config.STANDBY and config.RUNNING and ears.beat_matters()
                room_url = (config.MAIN_INFO["show_url"].rsplit("/show", 1)[0] + "/room"
                            if config.STANDBY and config.MAIN_INFO.get("room_mic") and config.MAIN_INFO.get("show_url") else None)
                want = "own" if want_mic else ("relay:" + room_url) if room_url else None
                if mic is not None and want != mic_label:  # stopped mattering, took over, or the laptop moved
                    mic.stop()
                    mic.close()
                    mic, mic_label = None, None
                    if not want:
                        print("Pi mic closed", flush=True)
                if want and mic is None:
                    mic, mic_label = (ears.PulseMic(ear) if want == "own" else ears.RoomRelay(room_url)), want
                    mic.start()
            elif mic is not None and config.MIC_CHOICE != mic_label:  # picked another mic on the dashboard
                mic.stop()
                mic.close()
                mic, mic_label = sources.open_mic(sd, ear, mics, config.MIC_CHOICE, device)
            if config.STANDBY:
                ear.pop_beat(), ear.pop_big()
                web.STATE["running"] = False
                web.STATE["mode"] = "standby"
                time.sleep(0.5)
                continue
            if not config.RUNNING:
                # every mode off: a real stop only after PAUSE_GRACE_S. Switching modes (the old one off, the new one on a
                # moment later) used to pause right in between: the lights went back to how they were before Light Sync,
                # then the new mode started on top of that (Oct 21, owner: "I press something, then something else,
                # and the system gets confused"). Meanwhile the lights just hold.
                off_since = off_since or now
                if not paused and now - off_since < PAUSE_GRACE_S:
                    ear.pop_beat(), ear.pop_big()
                    time.sleep(0.05)
                    continue
                if not paused:
                    paused = True
                    print("Paused - lights back to how they were", flush=True)
                    for w in (screen, beat, wash):  # nothing queued (all three at once: was 3 x 0.7 s)...
                        w.quiet(wait=False)
                    time.sleep(max(w.gap for w in (screen, beat, wash)) + 0.6)
                    for w in (screen, beat, wash):  # ...then strips leave streaming mode: normal commands work again
                        w.razer_stop()
                    bulbs.restore_states({e: s for e, s in saved.items() if e not in let_go()})
                    web.STATE["restore"] = None
                ear.pop_beat(), ear.pop_big()
                web.STATE["running"] = False
                web.STATE["mode"] = "paused"
                time.sleep(0.25)
                continue
            off_since = None
            if paused:
                paused = False
                if not DEMO:
                    saved = bulbs.save_states(lights)  # remember the lights as they are right now
                    web.STATE["restore"] = bulbs.brief(saved)
                    room = normal_light.room_from(saved)
                    if room:  # some lights on (all off: the last room stays): Normal brings this back later
                        normal_light.room_store(room)
                print("Started", flush=True)
                trans_at, trans_from = now, {}  # starting: the switch spin
            web.STATE["running"] = True
            for role, w in (("screen", screen), ("beat", beat), ("wash", wash)):  # dashboard: lights in Light Sync
                w.set_level(LEVEL[role])  # its own brightness level
                use = USE[role]
                if use != w.enabled:
                    if use:
                        w.take()
                        print(f"{w.entity_id}: back in Light Sync", flush=True)
                    else:
                        w.release()
                        print(f"{w.entity_id}: taken out of Light Sync (switched off)", flush=True)
            e = ear.energy
            scr = None if web.show_open() else eyes.screen_rgb  # show on the TV: palette colors, not the camera
            if config.GAMING_ON and games.GAME_ON_LAPTOP and games.laptop_rgb:
                scr = games.laptop_rgb  # game on this laptop: its own screen colors, exact and instant
            base = scr or colours.hue_rgb(hue)
            got_beat = ear.pop_beat() and e >= MIN_ENERGY
            film = eyes.screen_zones if web.STATE.get("tv_party") and not web.show_open() else None

            def pal(x, film=film):
                """Party's colour at x along the palette; in TV + Music the film's colours across the screen instead."""
                if not film:
                    return colours.pal_at(x)
                t = (x % 1.0) * len(film)
                i = int(t) % len(film)
                f = t - int(t)
                return tuple(np.array(film[i], float) * (1 - f) + np.array(film[(i + 1) % len(film)], float) * f)

            # which mode: gaming (switch wins), TV ambient (the TV leads: its colors only, even with Music sync on),
            # party (music, TV mode off)
            if ear.period():
                last_lock = now
            music_active = now - last_lock < 8
            # TV + Music (Nov 6, owner: "the same vibes of the party, just the colours taken from the TV"): Party
            # itself (the DJ, the flow, the beat, TvD's flashes) with the screen's colours as its palette
            tv_party = not config.GAMING_ON and config.AMBIENT_ON and config.TV_MUSIC
            web.STATE["tv_party"] = tv_party
            if config.GAMING_ON:
                new_mode = "gaming"
            elif tv_party:
                new_mode = "party"
            elif config.MUSIC_ON and not config.AMBIENT_ON:
                new_mode = "party"
            elif config.AMBIENT_ON or config.MUSIC_ON:
                new_mode = "ambient"
            else:
                new_mode = "normal"  # only Normal light on
            if new_mode != mode:
                web.STATE.pop("normal_note", None)
                if mode == "normal" and not DEMO:  # leaving Normal for a show: the room as it is now
                    room = normal_light.room_from(bulbs.save_states(lights))
                    if room:
                        normal_light.room_store(room)
                normal_light.ROOM["on"] = new_mode == "normal" and normal_light.room_ready(now)
                if new_mode == "normal" and app_requests.HABITS and config.NORMAL_SCENE == "Follow the day":
                    usual = app_requests.HABITS.usual_scene(now)  # opens with the scene usually picked around now
                    if usual:
                        normal_light.set_own(HA_NORMAL_SCENE, usual)
                        helpers.apply_helper(HA_NORMAL_SCENE, usual)
                        web.STATE["normal_note"] = f"Opened with {usual}: you usually pick it around now"
                mode = new_mode
                print(f"Mode: {mode}", flush=True)
                # the switch spin starts from the colors the old mode was showing
                trans_at = now
                trans_from = {role: np.array(w.shown()[0], float) for role, w in
                              (("screen", screen), ("beat", beat), ("wash", wash)) if w.want}
            # Normal: the bulbs only, TvD off; or the room as it was left (each light on or off as it was)
            rm = normal_light.ROOM["lights"] if mode == "normal" and normal_light.ROOM["on"] else None
            if rm is None and normal_light.ROOM["on"]:
                normal_light.ROOM["on"] = False  # Reset (or the other machine) emptied it: the standard Normal
            # (+ the TV blackout, Nov 6: these run every frame, so the blackout has to be part of them - Corner stayed
            # at 1 % on mute)
            # TvD is never switched off for the blackout (Nov 6, owner: "when it comes back it's like Normal first, then
            # the TV takes control"): switched back on, the strip showed its own stored white (2800 K) until streaming
            # restarted. It keeps streaming and fades to black instead (the volume dimmer's brightness goes to ~0).
            screen.set_dark(mode == "normal" and not (rm and rm.get("screen", [False])[0]))
            beat.set_dark((bool(rm) and not rm.get("beat", [False])[0]) or dark_on or quiet_dark)
            if now - normal_view_at > 30 or "normal_curve" not in web.STATE:  # Normal's day (learned), for the app
                normal_view_at = now
                curve = []
                for hr in range(24):
                    b_, k_ = normal_light.your_day(hr * 60, now)  # (Nov 7) the owner's times + what was learned
                    if app_requests.HABITS:
                        b_, k_, _ = app_requests.HABITS.normal_curve(hr * 60, b_, k_)
                    curve.append([round(b_), int(k_)])
                web.STATE["normal_curve"] = curve
                if mode == "normal":
                    nb_, nk_ = normal_light.normal_target(now)[:2]
                    lit = [v for v in (normal_light.ROOM["lights"] or {}).values() if v[0]] if normal_light.ROOM["on"] else []
                    if lit:  # the room as left: the sliders show its brightest light (and its warmth, if white)
                        top = max(lit, key=lambda v: v[1])
                        nb_, nk_ = top[1] / 2.55, top[2] or nk_
                    normal_light.sync_normal_sliders(nb_, nk_)
            # game / TV + music with a steady beat: the screen's colors, moving with the music
            blend = music_active and ((mode == "gaming" and config.GAME_MUSIC) or (mode == "ambient" and config.TV_MUSIC))

            # sound analysis (Light Sync app / dashboard): section, mood, and the mood's colors
            dta, ana_t = min(0.2, now - ana_t), now
            e_short += (e - e_short) * (1 - np.exp(-dta / 1.0))
            e_long += (e - e_long) * (1 - np.exp(-dta / 15.0))
            sp = ear.spectrum
            centroid = float(np.sum(sp * np.arange(len(sp))) / (np.sum(sp) * (len(sp) - 1) + 1e-9)) if sp.sum() > 0.05 else 0.0
            cent_s += (centroid - cent_s) * (1 - np.exp(-dta / 3.0))
            per_a, bpm_a = ear.period(), ear.bpm()
            # reaction speed: the slider, or from the tempo ("follows the rhythm"), gliding over ~4 s
            if config.REACTION_AUTO:
                config.REACTION_NOW += (songs.rhythm_reaction(bpm_a if per_a else 0) - config.REACTION_NOW) * (1 - np.exp(-dta / 4.0))
            else:
                config.REACTION_NOW = TUNE["reaction"]
            if e_long < 0.06 and e_short < 0.08:
                section = "quiet"
            elif now - web.STATE.get("big_at", 0.0) < 3:
                section = "drop"
            elif e_short - e_long > 0.12:
                section = "building up"
            elif per_a:
                section = "groove"
            else:
                section = "no steady beat"
            if per_a:
                tempo_hold = bpm_a
            tempo = tempo_hold if music_active else 0  # the tempo locked in the last 8 s (a mic's lock flickers)
            if tempo and (tempo >= 128 or (e_long > 0.55 and cent_s > 0.45)):
                m = "intense"
            elif tempo and tempo >= 105:
                m = "upbeat"
            elif tempo or e_long >= 0.25:
                m = "chill"
            else:
                m = "calm"
            song_why = songs.SONG.update(now, dta, sp, e < 0.04, bpm_a if per_a else 0)  # (near) silent: a gap, or a fade between songs
            if song_why:
                print(f"New song ({song_why})", flush=True)
                mood_votes = dict.fromkeys(MOODS, 0.0)  # the new song's mood from its own sound
            if section != "quiet":
                for k in mood_votes:
                    mood_votes[k] *= np.exp(-dta / 15.0)
                mood_votes[m] += dta
            top = max(mood_votes, key=mood_votes.get)
            # the song's look: a new song's mood is the one heard most in its first 12 s (the tempo needs ~8 s), then
            # kept for the song; well into it, only a clear lasting change (twice as much of the new mood) counts
            song_age = now - songs.SONG.at
            if songs.SONG.look_n != songs.SONG.n:
                if song_age >= 12:
                    mood, songs.SONG.look_n = top, songs.SONG.n
                    print(f"Song {songs.SONG.n}: {mood}", flush=True)
            elif top != mood and song_age >= 40 and mood_votes[top] > 2 * mood_votes[mood]:
                mood = top
            if config.MOOD_ON:
                looks = MOODS.get(mood) or [None]
                config.PALETTE = looks[songs.SONG.look_n % len(looks)] or BASE_PALETTE
            elif config.COLORS == "mine" and config.MY_HUES:
                config.PALETTE = config.MY_HUES
            else:
                config.PALETTE = COLOR_SETS.get(config.COLORS) or BASE_PALETTE

            # Auto DJ: in party mode the song's part picks the movement and the pulses (a look holds >= 6 s, a drop
            # switches at once); otherwise the owner's own Movement and Pulse choices
            if config.AUTO_DJ and mode == "party":
                if section != dj_look and (section == "drop" or now - dj_at > 6):
                    dj_look, dj_at = section, now
                config.STYLE, dj_pulse = DJ_LOOKS.get(dj_look, DJ_LOOKS["groove"])
                PULSE.update(dj_pulse)
            else:
                config.STYLE = config.USER_STYLE
                PULSE.update(USER_PULSE)

            top_dark = False  # the Cinema look switches Top off while the film is calm
            if mode == "normal" and rm:
                # Normal: the room as it was left before the film / party, each light as it was, until changed
                nk_room = normal_light.normal_target(now)[1]
                bulbs_top = max([v[1] for r_, v in rm.items() if r_ != "screen" and v and v[0]] or [0])
                for role, w in (("screen", screen), ("beat", beat), ("wash", wash)):
                    on, bri, kel, rgb = (rm.get(role) or [False, 0, None, None])[:4]
                    if not on:
                        continue
                    if role == "screen":
                        # Nov 12 (owner: "why is TvD pink?"): the room was saved with TvD's colour from Home Assistant,
                        # which still held a show's pink while TvD streamed - Normal showed it at full brightness next to
                        # the bulbs at 5 %. In Normal TvD is the same warm white as Corner and Top, never brighter
                        kel, rgb = kel or nk_room, None
                        if bulbs_top:
                            bri = min(bri, bulbs_top)
                    if config.LATE_NIGHT:  # late night: the room as left, but never above 25 % / 2400 K, like the rest of Normal
                        bri, kel = min(bri, 64), min(kel, 2400) if kel else kel
                    if kel:
                        w.set_white(kel, bri)
                    elif rgb:
                        w.set(rgb, bri, 0.5)
                top_dark = not rm.get("wash", [False])[0]
                web.STATE["normal"] = {"bri": round(max((v[1] for v in rm.values() if v[0]), default=0) / 2.55), "k": 0,
                                   "src": "as you left it", "scene": config.NORMAL_SCENE, "note": None}
            elif mode == "normal":
                # Normal light: Corner and Top true white, brightness and warmth from normal_target (the day curve,
                # learned; a scene; or a slider just moved); in from another mode it cross-fades through RGB first
                nb, nk, nsrc = normal_light.normal_target(now)
                u = min(1.0, (now - trans_at) / TRANS_S) if TRANS_S else 1.0
                f = u * u * (3 - 2 * u)
                for role, w in (("beat", beat), ("wash", wash)):
                    old = trans_from.get(role)
                    if u < 1 and old is not None and not config.COMFORT:
                        w.set(np.array(old, float) * (1 - f) + np.array(colours.kelvin_rgb(nk)) * f, nb * 2.55, 0)
                    else:
                        w.set_white(nk, nb * 2.55 * (0.35 + 0.65 * f if old is None else 1.0))
                web.STATE["normal"] = {"bri": round(nb), "k": int(nk), "src": nsrc, "scene": config.NORMAL_SCENE,
                                   "note": web.STATE.get("normal_note")}
            elif mode in ("ambient", "gaming"):
                # all lights follow the screen color, no flashes:
                # ambient = soft and slow, gaming = fast and bright
                fast = mode == "gaming"
                cine = config.CINEMA and not fast  # Cinema look: Corner and Top a low, slow glow; TvD follows the film
                if scr:
                    scr_seen = now
                    t_scr = np.array(colours.vivid(scr))
                    sides = eyes.SCREEN_SIDES if not fast else None
                    if sides:  # Nov 6: TV's own colour rules (not Music's): TvD the film, Top its mood, Corner by choice
                        whole = np.mean(np.array(sides, float), axis=0)
                        t_scr = np.array(colours.film_colour(scr))
                        rule = config.TV_CORNER
                        t_beat = np.array(colours.film_colour(colours.turn_hue(sides[0], 1 / 12)) if rule == "Colour harmony" else
                                          colours.film_colour(sides[0]) if rule == "True to the film" else
                                          colours.film_colour(colours.turn_hue(whole, 0.5)) if rule == "Contrast" else
                                          colours.film_mood(whole))
                        t_wash = np.array(colours.film_mood(whole))
                    elif cine:  # Corner: the screen's second colour (if any); Top: the room's light, a hint of the film
                        t_beat = np.array(eyes.SCREEN_SECOND if eyes.SCREEN_SECOND is not None else colours.shift_hue(scr, 0.08))
                        t_wash = np.array(colours.soft_tint(scr))
                    else:
                        t_beat = np.array(colours.shift_hue(scr, 0.05))
                        t_wash = np.array(colours.shift_hue(scr, -0.05))
                elif now - scr_seen > 10:  # no picture (yet, or for 10 s): warm white, never colors the TV doesn't show
                    t_scr = t_beat = t_wash = np.array([255.0, 180.0, 110.0])
                else:  # a short gap in the camera picture: keep the TV's last colors
                    t_scr, t_beat, t_wash = cur["screen"].copy(), cur["beat"].copy(), cur["wash"].copy()  # hold
                if cine:  # ... dark while calm, up with the action: rising over ~1.5 s, back down over ~6 s
                    tgt = TV_ACTION["level"] if now - TV_ACTION["at"] < 3 else 0.0
                    dark_lv += (tgt - dark_lv) * (1 - np.exp(-tv_dt / (1.5 if tgt > dark_lv else 6.0)))
                    dark_tvd += (tgt - dark_tvd) * (1 - np.exp(-tv_dt / (5.0 if tgt > dark_tvd else 10.0)))
                    top_on = dark_lv > (0.12 if top_on else 0.25)  # Top off while calm (two points: no on-off flicker)
                    top_dark = not top_on
                    web.STATE["tv_action"] = {"level": round(dark_lv, 2), "tvd": round(dark_tvd, 2), "raw": round(TV_ACTION["raw"], 1),
                                          "top": "on" if top_on else "off",
                                          "second": [int(v) for v in eyes.SCREEN_SECOND] if eyes.SCREEN_SECOND is not None else None}
                else:
                    dark_lv = dark_tvd = 0.0
                    top_on = False
                    web.STATE.pop("tv_action", None)
                for key, tgt in (("screen", t_scr), ("beat", t_beat), ("wash", t_wash)):
                    if fast:
                        kk = SMOOTH * 1.4
                    else:  # TV: a glide in seconds (TV_GLIDE_S), Corner / Top 1.5x slower in the Cinema look
                        tau = TV_GLIDE_S * (1.5 if cine and key != "screen" else 1.0)
                        if key == "wash" and eyes.SCREEN_SIDES:
                            tau = max(tau, 3.0)  # Top, in the owner's eyes: the mood glides over seconds, never jumps
                        if colours._lum(tgt) > colours._lum(cur[key]) + 6:
                            tau *= 2.5  # brighter / whiter comes in slower, so an explosion doesn't flash the room
                        kk = 1 - np.exp(-tv_dt / tau)
                    cur[key] = cur[key] + (tgt - cur[key]) * kk if fast else colours.glide_colour(cur[key], tgt, kk)
                step = 6  # (TV mode used 12: coarser steps, when the camera only gave a picture every ~2 s)
                q = lambda c: np.clip(np.round(c / step) * step, 0, 255)  # fewer tiny updates
                bright, tr = (GAMING_BRIGHT, 0.3) if fast else (AMBIENT_BRIGHT, 1.0)
                if blend:
                    # game / TV + music: the screen's colors, brightness rides the music, beat light punches on
                    # each beat (Comfort turns the punches into a gentle swell)
                    e_s += (e - e_s) * (0.5 if e > e_s else 0.08)
                    if got_beat:
                        beats += 1
                        flash_at = now
                    punch = max(0.0, 1 - (now - flash_at) / 0.35)
                    ride = bright * (0.7 + 0.3 * e_s)
                    if not PULSE["beat"]:  # Corner's "Pulse on the beat" off: it rides the music too
                        punch = 0.0
                    if beat.enabled:
                        screen.set(q(cur["screen"]), ride, 0)
                        beat.set(t_beat if punch > 0.7 else q(cur["beat"]), bright * 0.5 + (255 - bright * 0.5) * punch, 0)
                    else:  # beat light taken out: TvD only rides the music (it never punches: "it's blinking")
                        screen.set(q(cur["screen"]), ride, 0)
                    wash.set(q(cur["wash"]), bright * 0.6 * (0.7 + 0.3 * e_s), 0.2)
                elif cine:  # the room dark while calm: TvD a dim glow, Corner and Top ~off; up with the action
                    screen.set(q(cur["screen"]), bright * (TV_DARK + (TV_DARK_TVD_MAX - TV_DARK) * dark_tvd), tr)
                    beat.set(q(cur["beat"]), bright * 0.3 * dark_lv, tr)
                    wash.set(q(cur["wash"]), bright * 0.22 * dark_lv, tr)
                else:
                    # Nov 6 (owner, Arc Raiders on the TV: "the lights are not doing anything, just following the
                    # colours"; the test: TvD 150, Corner 111, Top 89 the whole time while the picture went 62-133).
                    # Now the brightness follows the picture (dark scene: dimmer; bright: brighter, against its range
                    # of the last minute) and the action (how fast the colours change) lifts TvD and Corner.
                    lum = eyes.SCREEN_LUM
                    if lum is not None and not fast:
                        tv_lum = lum if tv_lum is None else tv_lum + (lum - tv_lum) * (1 - np.exp(-tv_dt / 0.3))  # (Nov 6 camera tests: 0.4 calm, 0.2 jumpy)
                        tv_lo = tv_lum if tv_lo is None else min(tv_lum, tv_lo + (tv_lum - tv_lo) * tv_dt / 60)
                        tv_hi = tv_lum if tv_hi is None else max(tv_lum, tv_hi + (tv_lum - tv_hi) * tv_dt / 60)
                        rel = float(np.clip((tv_lum - tv_lo) / max(15.0, tv_hi - tv_lo), 0, 1))
                        act = float(TV_ACTION["level"]) if now - TV_ACTION["at"] < 3 else 0.0
                        # transitions (Nov 6 camera tests): 1 s calm but slow, 0.3 s jumpy (6-7 sudden changes a minute on the
                        # wall): TvD 0.5 s, Corner 0.7 s; Top (in the owner's eyes) stays gentle
                        # Nov 6 (owner: "give sound more weight on TvD"): the game's / film's sound (room mic or laptop
                        # sound) lifts TvD: quick up (~0.15 s), slower down (~0.6 s); the picture still sets the base
                        # owner after the first try: "too little" -> lower when quiet, the sound counts more, and a
                        # sudden sound (shot, explosion: louder than the last ~2 s) pushes it up hard, falling back fast
                        tv_snd += (e - tv_snd) * (1 - np.exp(-tv_dt / (0.08 if e > tv_snd else 0.35)))
                        tv_slow += (e - tv_slow) * (1 - np.exp(-tv_dt / 2.0))
                        hit = max(0.0, tv_snd - tv_slow)
                        screen.set(q(cur["screen"]), min(255, bright * (0.2 + 0.3 * rel + 0.15 * act + 1.0 * tv_snd + 1.6 * hit)), 0.15)
                        beat.set(q(cur["beat"]), min(255, bright * 0.75 * (0.5 + 0.5 * rel + 0.25 * act)), 0.7)
                        wash.set(q(cur["wash"]), bright * 0.6 * (0.6 + 0.4 * rel), tr)
                        web.STATE["tv_alive"] = {"rel": round(rel, 2), "act": round(act, 2), "sound": round(tv_snd, 2), "hit": round(hit, 2)}
                    else:
                        screen.set(q(cur["screen"]), bright, tr)
                        beat.set(q(cur["beat"]), bright * 0.75, tr)
                        wash.set(q(cur["wash"]), bright * 0.6, tr)
            elif config.STYLE == "rainbow":
                # old mode: every light its own color, hue jumps each beat
                screen.set(base, 70 + 185 * e, 0.3)
                if got_beat:
                    beats += 1
                    hue += 0.07
                    beat_rgb = colours.shift_hue(base, 0.5)
                    beat.set(beat_rgb, 255, 0)
                    flash_at = now
                elif now - flash_at > 0.12:
                    beat.set(beat_rgb, 25 + 40 * e, 0.4)
                wash.set(colours.shift_hue(base, 0.15), 50 + 150 * e, 1.0)
                hue += 0.0015 * (0.3 + e)
            else:
                # vibe mode: neon palette shared by all lights, colors fade smoothly,
                # beat light fires on the predicted beat (early by LATENCY_MS) once tempo locks
                e_s += (e - e_s) * (0.5 if e > e_s else 0.08)   # quick rise, slow fall
                per = ear.period()
                flash_now = False
                if per:
                    nb = ear.anchor + per
                    while nb < now - per / 2:
                        nb += per
                    if now >= nb - config.LATENCY_MS / 1000 and nb - next_fired > per / 2 and e >= MIN_ENERGY:
                        next_fired = nb
                        flash_now = True
                if got_beat:
                    beats += 1
                    if beats % config.BEATS_PER_COLOR == 0:
                        pal_i += 1  # next color on the phrase
                    if not per or now - flash_at > 0.2:
                        flash_now = True  # no steady tempo (or off-grid hit): flash on detection
                if flash_now:
                    flash_at = now

                # what each light listens to (dashboard): everything, or just the bass / mids / highs
                lvl = {"everything": e_s, "bass": min(1.0, ear.bands[0] * 1.6), "mids": min(1.0, ear.bands[1] * 1.6),
                       "highs": min(1.0, ear.bands[2] * 1.6), "mix": e_s}
                # "mix": the color comes from the sound itself, mixed like light: bass = red, mids = green,
                # highs = blue (bass + highs = magenta, mids + highs = cyan, bass + mids = yellow, all = white)
                b3 = np.array([lvl["bass"], lvl["mids"], lvl["highs"]])
                if b3.max() > 0.08:  # quiet: keep the last color
                    b3 = np.maximum(0.0, b3 - 0.6 * b3.min())  # what stands out, so it isn't always near-white
                    mix_c += (255 * b3 / max(1e-6, b3.max()) - mix_c) * min(1.0, 0.12 * max(1.0, min(10.0, config.REACTION_NOW)) / 5)
                drive = {r: lvl[PARTS[r]] for r in ("screen", "beat", "wash")}
                # TvD (the screen light) never flashes or punches: its level follows the music over ~0.4 s
                tvd_lv += (drive["screen"] - tvd_lv) * 0.06
                top, span = config.MAX_BRIGHT, config.MAX_BRIGHT - config.MIN_BRIGHT
                rs = max(1.0, min(10.0, config.REACTION_NOW)) / 5      # reaction speed slider

                def soft(delay):  # TvD's beat pulse: a soft swell (up 0.12 s, down 0.5 s), never a flash
                    s = now - flash_at - delay
                    return 0.0 if s < 0 else (s / 0.12 if s < 0.12 else max(0.0, 1 - (s - 0.12) / 0.5))
                bri_of = {}  # each light's brightness this frame (Pulse on the beat per light, Oct 19)

                if config.STYLE in FLOW_STYLES:
                    # the colors flow from light to light (Movement: TvD -> Corner -> Top, back, or all together),
                    # one palette step every BEATS_PER_COLOR beats ("Change colour"), and every beat ripples
                    # through the three in the same order
                    dtl, flow_t = min(0.1, now - flow_t), now
                    flow_pos += dtl * rs * ((1.0 / (config.BEATS_PER_COLOR * per)) if per else 0.12 * (0.5 + e_s)) * (0.5 if config.COMFORT else 1.0)
                    pos, delays = flow.flow_positions(flow_pos)
                    for key in cur:
                        cur[key] = pal(pos[key])

                    def ripple(delay):
                        s = now - flash_at - delay
                        return max(0.0, 1 - s / 0.4) if s >= 0 else 0.0
                    # each light: its part's level; with "Pulse on the beat" the beat's ripple too (TvD: a soft swell)
                    bri_of["screen"] = config.MIN_BRIGHT + span * (max(tvd_lv, 0.6 * soft(delays["screen"])) if PULSE["screen"] else tvd_lv)
                    for role in ("beat", "wash"):
                        bri_of[role] = (config.MIN_BRIGHT * 0.6 + (top - config.MIN_BRIGHT * 0.6) * max(0.3 * drive[role], ripple(delays[role]))
                                        if PULSE[role] else
                                        (config.MIN_BRIGHT * 0.6 + (top - config.MIN_BRIGHT * 0.6) * drive[role]) if role == "beat" else
                                        config.MIN_BRIGHT * 0.8 + span * 0.8 * drive[role])
                    screen.set(cur["screen"], bri_of["screen"], 0)
                    beat.set(cur["beat"], bri_of["beat"], 0)
                    wash.set(cur["wash"], bri_of["wash"], 0)
                else:
                    if scr:
                        target = np.array(colours.vivid(scr))
                        t_beat = np.array(colours.shift_hue(scr, 0.08))
                        t_wash = np.array(colours.shift_hue(scr, -0.08))
                    else:
                        n = len(config.PALETTE)
                        target = np.array(colours.hue_rgb(config.PALETTE[pal_i % n]))
                        t_beat = np.array(colours.hue_rgb(config.PALETTE[(pal_i + 1) % n]))
                        t_wash = np.array(colours.hue_rgb(config.PALETTE[(pal_i - 1) % n]))

                    k = min(1.0, SMOOTH * rs)
                    cur["screen"] += (target - cur["screen"]) * k
                    cur["wash"] += (t_wash - cur["wash"]) * k * 0.6
                    cur["beat"] += (t_beat - cur["beat"]) * min(1.0, k * 1.5)

                    bri_of["screen"] = config.MIN_BRIGHT + span * (max(tvd_lv, 0.6 * soft(0.0)) if PULSE["screen"] else tvd_lv)
                    screen.set(cur["screen"], bri_of["screen"], 0.2)
                    since = now - flash_at
                    for role, w, t_col in (("beat", beat, t_beat), ("wash", wash, t_wash)):
                        if not PULSE[role]:  # follows its part's level instead of punching on beats
                            bri_of[role] = (config.MIN_BRIGHT * 0.6 + (top - config.MIN_BRIGHT * 0.6) * drive[role] if role == "beat"
                                            else config.MIN_BRIGHT * 0.8 + span * drive[role] * 0.8)
                            w.set(cur[role], bri_of[role], 0.2 if role == "beat" else 0.5)
                        elif since < 0.1:
                            bri_of[role] = top
                            w.set(t_col, top, 0)  # punch: full color, full brightness
                        else:
                            fade = max(0.0, 1 - (since - 0.1) / 0.35)  # smooth decay after the hit
                            bri_of[role] = config.MIN_BRIGHT * 0.6 + (top - config.MIN_BRIGHT * 0.6) * fade * 0.7
                            w.set(cur[role], bri_of[role], 0.1)

                for role, w in (("screen", screen), ("beat", beat), ("wash", wash)):
                    if PARTS[role] == "mix":  # this light: the sound's own color, its brightness (and pulse) as set above
                        w.set(mix_c, bri_of.get(role, config.MIN_BRIGHT + span * drive[role]), 0)

            wash.set_dark(top_dark or dark_on or quiet_dark)

            # TvD as an RGBIC strip: every segment its own color - the screen left to right (ambient, gaming), or a
            # spectrum bar in party (bass in the middle, highs at the ends, pulsing on the beat)
            if screen.segmented() and screen.enabled and screen.want and screen.want[0] != "seg":
                n = screen.segments
                bri_now = screen.want[1]
                zones = None
                if config.GAMING_ON and games.GAME_ON_LAPTOP and eyes.laptop_zones:
                    zones = eyes.laptop_zones
                elif not web.show_open() and eyes.screen_zones:
                    zones = eyes.screen_zones
                if mode in ("ambient", "gaming"):
                    if zones or seg_cur is not None:
                        # no slices for a moment (a dark or grey picture): the segments glide to the one colour, the
                        # strip isn't switched to it (Oct 27, strip_log.py: one-colour frames for 0.3 s = a blip)
                        target_z = (np.array(zones, float) if zones
                                    else np.repeat(np.array(screen.want[0], float)[None, :], n, axis=0))
                        if seg_cur is None or len(seg_cur) != n:
                            seg_cur = target_z.copy()
                        kz = SMOOTH * 1.4
                        if mode == "ambient":  # TV: TvD's glide, brighter / whiter segments slower (no flashes)
                            kz = 1 - np.exp(-tv_dt / (TV_GLIDE_S * np.where(target_z @ colours.LUM > seg_cur @ colours.LUM + 6, 2.0, 1.0)))[:, None]  # brighter 2x slower (2.5x late, 1.5x jumpy: Nov 6 camera tests)
                        seg_cur += (target_z - seg_cur) * kz
                        screen.set_segments(np.clip(np.round(seg_cur / 6) * 6, 0, 255), bri_now)
                elif mode == "party" and party_dj.SCENE:  # Party Pro DJ: TvD plays its scene (parts/tvd_scenes.py)
                    per_ = ear.period()
                    cols = tvd_scenes.render(party_dj.SCENE, now, n, now - flash_at, per_, e_s, pal, flow_pos)
                    # a light smoothing (~60 ms) so the movement flows instead of stepping; strobe hits stay sharp
                    if party_dj.SCENE != "strobe" and scene_cur is not None and scene_cur.shape == cols.shape:
                        cols = scene_cur + (cols - scene_cur) * 0.6
                    scene_cur = cols
                    screen.set_segments(np.clip(cols, 0, 255), max(bri_now, int(config.MAX_BRIGHT)))
                else:
                    if PARTS["screen"] == "mix" and config.STYLE != "rainbow":  # the sound's own color along the strip
                        base_cols = np.repeat(mix_c[None, :], n, axis=0)
                    elif config.STYLE in FLOW_STYLES:  # the strip blends TvD's color into Corner's: the flow runs along it
                        pos = flow.flow_positions(flow_pos)[0]
                        base_cols = np.array([pal(pos["screen"] + (pos["beat"] - pos["screen"]) * i / max(1, n - 1))
                                              for i in range(n)])
                    elif zones:
                        base_cols = np.array(zones, float)
                    elif config.STYLE == "rainbow":
                        base_cols = np.array([colours.hue_rgb(hue + i / n * 0.6) for i in range(n)])
                    else:  # the three palette colors across the strip: wash - screen - beat
                        a_, b_, c_ = cur["wash"], cur["screen"], cur["beat"]
                        t_ = np.linspace(0, 1, n)[:, None]
                        base_cols = np.where(t_ < 0.5, a_ + (b_ - a_) * (t_ * 2), b_ + (c_ - b_) * ((t_ - 0.5) * 2))
                    half = (n - 1) / 2 or 1
                    lv = np.array([ear.spectrum[min(31, int(abs(i - (n - 1) / 2) / half * 31))] for i in range(n)])
                    # the spectrum along the strip, smoothed, never dark, no beat punch (it made TvD blink)
                    seg_lv = lv if seg_lv is None or len(seg_lv) != n else seg_lv + (lv - seg_lv) * 0.12
                    lvl = np.clip(0.5 + 0.5 * seg_lv, 0, 1)
                    screen.set_segments(base_cols * lvl[:, None], bri_now)

            # TvD LEADS (Nov 6, owner: "the TV light will be the most important one ... the top and the corner take
            # order from it: the top of the TV blue -> the top of the room blue; the corner red -> the corner red").
            # In every mode but Normal, Corner copies TvD's parts on the TV's right side (the floor lamp is right of
            # the TV, seen from the bed) and Top its parts along the TV's top, each keeping its own brightness. They
            # glide (Corner ~0.8 s, Top ~2.5 s: in the owner's eyes) and never flash.
            fdt, fol_t = min(0.2, now - fol_t), now
            lead = screen.want if screen.enabled and screen.want else None
            if mode != "normal" and lead and lead[0] == "seg" and config.STRIP_MAP and len(config.STRIP_MAP) == len(lead[1]):
                cols = np.array(lead[1], float)
                # screen.want holds the parts in the strip's own order (set_segments already reversed them), the
                # same order as STRIP_MAP (strip_map.py lights part i as frame index i). Nov 6: this was reversed once
                # more, so Corner and Top read the strip back to front.
                smap = config.STRIP_MAP
                # (the left part, part 10, had its own rule - dark except loud hits - until the owner, Nov 6: "it
                # blinks so much, just keep it following the screen like the others")
                groups = {"beat": [i for i, (u, v) in enumerate(smap) if u >= 0.9 and v > 0.1],   # right side (1-4)
                          "wash": [i for i, (u, v) in enumerate(smap) if v <= 0.1]}               # top (5-9)
                for role, w, tau in (("beat", beat, 0.8), ("wash", wash, 2.5)):
                    idx = groups[role] or list(range(len(cols)))
                    tgt = cols[idx].mean(axis=0)
                    if tgt.max() > 1:
                        tgt = tgt * (255.0 / tgt.max())  # the colour, not the strip's dimming
                    old = fol_cur.get(role)
                    fol_cur[role] = tgt if old is None else old + (tgt - old) * (1 - np.exp(-fdt / tau))
                    want = w.want
                    if w.enabled and want and want[0] not in ("white", "seg"):
                        # Nov 6, a 3-minute camera test in TV + Music: their colours were smooth, but their
                        # brightness punched on the beats (Corner 79 jumps a minute, Top 34). Followers glide their
                        # brightness too (Corner ~0.25 s, Top ~0.8 s) and never dip under half their top.
                        top_b = max(float(want[1]), fol_bri.get(role + "_top", 0.0) * 0.995)
                        fol_bri[role + "_top"] = top_b
                        b_tgt = max(float(want[1]), 0.5 * top_b)
                        ob = fol_bri.get(role)
                        fol_bri[role] = b_tgt if ob is None else ob + (b_tgt - ob) * (1 - np.exp(-fdt / (0.25 if role == "beat" else 0.8)))
                        with w.lock:
                            w.want = (tuple(int(v) for v in np.clip(fol_cur[role], 0, 255)), int(max(1, fol_bri[role])), want[2])
                        w.wake.set()
            else:
                fol_cur.clear()
                fol_bri.clear()

            # QUIET = DARK (Nov 6, owner: "no sound, no light or super low, only TvD; when the big sound comes back,
            # Top and Corner show up smoothly"): in TV and TV + Music the sound over ~1.5 s says if anything is going
            # on. Quiet (under ~0.06): Corner and Top fade out over ~3 s, TvD goes down to a low glow. Sound back
            # (over ~0.15): Corner and Top fade in over ~1.5 s. In between it holds (no flicker at the edge).
            tvish = mode == "ambient" or bool(web.STATE.get("tv_party"))
            quiet_env += (e - quiet_env) * (1 - np.exp(-fdt / 1.5))
            if tvish:
                room_tgt = 1.0 if quiet_env > 0.15 else 0.0 if quiet_env < 0.06 else room_on
                # in over ~3 s (Nov 7, owner: "more smooth" - it was 1.5 s), out over ~3 s
                room_on += (room_tgt - room_on) * (1 - np.exp(-fdt / 3.0))
                for w, floor in ((screen, 0.25), (beat, 0.0), (wash, 0.0)):
                    want = w.want
                    if not (w.enabled and want) or want[0] == "white":
                        continue
                    k = floor + (1 - floor) * room_on
                    with w.lock:  # (TvD's quiet glow at least 40: lower, its colour turns yellow / red)
                        w.want = (("seg", want[1], int(max(40, want[2] * k))) if want[0] == "seg"
                                  else (want[0], int(max(40 if w is screen else BULB_LOW, want[1] * k)), want[2]))
                web.STATE["room_on"] = round(room_on, 2)
                # Nov 7 camera test (dark room, Top alone, 9 colours from 30 % down to 1 %): the colour holds down to
                # 1 % (5 degrees at most; white a little warmer under 6 %), so the bulbs fade down to 2 % and only then
                # switch off (it was 12 % / 30 % of the way, from an older yellow-red fade-out)
                quiet_dark = room_on < 0.06
            else:
                room_on, quiet_dark = 1.0, False

            # THE VOLUME IS THE DIMMER (Nov 6): in TV and TV + Music the TV box's volume scales every light: volume 0
            # (or muted) = 35 %, 70 % and up = full; it glides over ~1 s so turning the knob fades the room smoothly
            # Nov 6 (owner): "when the screen is black and when it's muted, all the lights off". Muted or volume 0, or
            # the picture black (or the TV off) for over 0.6 s: everything fades out (~0.5 s) and goes dark; back
            # with the picture / the sound over ~1 s.
            vol = config.BOX_VOLUME
            black = tvish and (eyes.SCREEN_OFF or (eyes.SCREEN_LUM is not None and eyes.SCREEN_LUM < 15))  # (28 caught dark scenes, Nov 6 test)
            black_since = (black_since or now) if black else None
            blackout = tvish and ((vol is not None and vol <= 0.0) or (black_since is not None and now - black_since > 0.6))
            if vol is not None:
                vol_peak = max(vol, vol_peak * float(np.exp(-fdt / 10800)))
            vol_ref = config.FULL_VOLUME if config.FULL_VOLUME > 0 else max(0.15, vol_peak)  # full brightness at this volume
            web.STATE["volume_ref"] = round(vol_ref, 2)
            want_mult = (0.0 if blackout else (0.35 + 0.65 * min(1.0, vol / vol_ref)) if (vol is not None and tvish) else 1.0)
            vol_mult += (want_mult - vol_mult) * (1 - np.exp(-fdt / (0.5 if want_mult < vol_mult else 1.0)))
            # dark under 30 % (Nov 6, owner: "before it goes off it makes yellow and red"): the lowest levels lose their
            # blue and green first, so the fade skips them - down to 30 %, then dark; back from dark straight at 30 %
            now_dark = tvish and vol_mult < 0.3
            if now_dark != dark_on:  # applied by the set_dark lines each frame (TvD / Corner above, Top with top_dark)
                dark_on = now_dark
            web.STATE["blackout"] = (("muted" if vol is not None and vol <= 0.0 else "screen off" if eyes.SCREEN_OFF
                                      else f"black picture ({eyes.SCREEN_LUM:.0f})") if blackout else False)
            if tvish and dark_on and screen.want and screen.want[0] == "seg":  # TvD (never switched off): black frames
                with screen.lock:
                    screen.want = ("seg", tuple((0, 0, 0) for _ in screen.want[1]), 1)
            elif vol_mult < 0.995:
                for w in (screen, beat, wash):
                    want = w.want
                    if not (w.enabled and want) or want[0] == "white":
                        continue
                    with w.lock:  # never under 40 (TvD) / 30 (bulbs) while on: lower, the colour turns yellow / red
                        w.want = (("seg", want[1], int(max(40, want[2] * vol_mult))) if want[0] == "seg"
                                  else (want[0], int(max(40 if w is screen else BULB_LOW, want[1] * vol_mult)), want[2]))
            web.STATE["volume_dim"] = round(vol_mult, 2)

            # FLASH: white burst on all lights (big hits, or every beat in strobe mode)
            big = ear.pop_big() and (mode == "party" or blend)
            if big:
                web.STATE["big_at"] = now  # drops / big hits, for the show visuals
            # Nov 7 (owner: in TV mode "sometimes the TV light blinks, like a flash"): no white flash in plain TV mode
            # (it broke up the film); Party and TV + Music keep it. TvD still rises with the film's sound
            fire = ((config.FLASH == "hits" and big and e >= 0.4 and mode != "ambient")
                    or (config.FLASH == "strobe" and mode == "party" and flash_at == now and e >= MIN_ENERGY))
            if fire:  # Nov 6 (owner: flashing "always allowed on TvD"): the white flash is TvD's only, Comfort or not;
                screen.flash()  # Corner and Top (in the owner's eyes) never flash now
                web.STATE["white_at"] = now

            # switching modes: the colours cross-fade from the old mode's to the new one's over TRANS_S (Oct 20, owner:
            # "smoother, it lags": the Oct 19 running-light spin made every switch 1.2 s); starting from paused, a
            # gentle fade in. Comfort does its own slow cross-fade instead.
            if not config.COMFORT and now - trans_at < TRANS_S and mode != "normal":  # (Normal fades in by itself)
                u = (now - trans_at) / TRANS_S
                fade = u * u * (3 - 2 * u)
                for role, w in (("screen", screen), ("beat", beat), ("wash", wash)):
                    want = w.want
                    if not want or not w.enabled or want[0] == "white":
                        continue
                    old = trans_from.get(role)
                    mult = 1.0 if old is not None else 0.35 + 0.65 * fade
                    if want[0] == "seg":
                        cols = np.array(want[1], float)
                        if old is not None:
                            cols = old * (1 - fade) + cols * fade
                        new = ("seg", tuple(tuple(int(v) for v in c) for c in np.clip(cols, 0, 255)), int(max(1, want[2] * mult)))
                    else:
                        rgb = np.array(want[0], float)
                        if old is not None:
                            rgb = old * (1 - fade) + rgb * fade
                        new = (tuple(int(v) for v in np.clip(rgb, 0, 255)), int(max(1, want[1] * mult)), want[2])
                    with w.lock:
                        w.want = new
                    w.wake.set()

            # comfort: soften everything the modes asked for (slow crossfades, gentle brightness, warm at night)
            if config.COMFORT and not DEMO_RAW:
                dtc, comfort_t = min(0.2, now - comfort_t), now
                night = colours.is_night()
                for w in (screen, beat, wash):
                    w.comfort(dtc, night)
            else:
                comfort_t = now
                for w in (screen, beat, wash):
                    w.c_cols = None

            # (the laptop's console only: on the Pi this line was 1,403 of 3,000 log lines, Nov 9)
            if got_beat and mode == "party" and beats and beats % 8 == 0 and sys.platform == "win32":
                print(f"beats: {beats}  energy: {e:.2f}", flush=True)

            # feed the live view (~20 updates/s)
            if now - last_view > 0.05:
                last_view = now
                web.STATE["lights"] = {k: {"name": eid.replace("light.", ""),
                                       "rgb": w.shown()[0], "bri": w.shown()[1]}
                                   for k, (w, eid) in names.items()}
                web.STATE["diag"] = {"ring": eyes.DIAG.get("ring", [])[-20:], "stream_on_at": round(screen.razer_on_at, 2),
                                 "tvd": eyes.DIAG.get("tvd", [])[-30:]}
                web.STATE["analysis"] = {"bass": round(float(ear.bands[0]), 3), "mids": round(float(ear.bands[1]), 3),
                                     "highs": round(float(ear.bands[2]), 3), "brightness": round(cent_s, 3),
                                     "steadiness": round(ear.grid_r if per_a else 0.0, 3), "section": section,
                                     "mood": mood, "mood_colors": config.MOOD_ON, "loud_now": round(e_short, 3),
                                     "song": {"n": songs.SONG.n, "at": round(songs.SONG.at, 1), "why": songs.SONG.why, "shown": songs.SONG.look_n},
                                     "loud_avg": round(e_long, 3), "parts": dict(PARTS), "comfort": config.COMFORT,
                                     "reaction": round(config.REACTION_NOW, 1), "reaction_auto": config.REACTION_AUTO,
                                     "dj": ({"look": dj_look, "movement": config.STYLE, "pulse": dict(PULSE)}
                                            if config.AUTO_DJ and mode == "party" else None),
                                     # sound levels (RMS, 0-1): what's being listened to, and the room mic from the Pi
                                     "level": round(ear.rms, 4), "room_level": round(ear.room_rms, 4),
                                     # a mic: its background noise and how much of the last 2 s was above the silence level
                                     "mic_noise": round(ear.noise, 5), "mic_loud_2s": round(sum(ear.loud_2s) / ear.loud_2s.maxlen, 2)}
                sw = screen.want
                web.STATE["tvd_segments"] = ([[int(v * sw[2] / 255) for v in c] for c in (sw[1][::-1] if screen.seg_from_right else sw[1])]
                                         if sw and sw[0] == "seg" else None)
                web.STATE["energy"] = round(e, 3)
                web.STATE["bpm"] = ear.bpm()
                web.STATE["beats"] = beats
                web.STATE["beat_at"] = flash_at
                web.STATE["locked"] = bool(ear.period())
                web.STATE["flash"] = config.FLASH
                web.STATE["style"] = config.STYLE
                web.STATE["mode"] = mode
                web.STATE["music_in_game"] = blend
                web.STATE["audio"] = ("phone mic (Light Sync app)" if ear.phone_active() else
                                  ("auto: room mic (MeetUp, via the Pi)" if ear.using_room() else "auto: laptop sound")
                                  if ear.room_mode == "auto" else
                                  ("room mic (MeetUp, via the Pi)" if ear.room_active() else "room mic: waiting for the Pi")
                                  if ear.room_mode == "only" else
                                  sources.bt_label() if mic_label == sources.PHONE_BT else web.STATE.get("audio_local", ""))
                web.STATE["source"] = (f"normal light: {web.STATE['normal']['bri']} % · {web.STATE['normal']['k']} K ({web.STATE['normal']['src']})"
                                   if mode == "normal" and web.STATE.get("normal") else
                                   "laptop screen (game)" if config.GAMING_ON and games.GAME_ON_LAPTOP and games.laptop_rgb else
                                   "show on the TV (palette)" if web.show_open() else
                                   "camera (screen)" if eyes.screen_rgb else "music palette")
                web.STATE["game"] = games.GAME_ON_LAPTOP
                web.STATE["palette"] = [[int(c) for c in np.clip(cur[k], 0, 255)] for k in ("screen", "beat", "wash")]
                web.STATE["screen_rgb"] = [int(c) for c in eyes.screen_rgb] if eyes.screen_rgb else None
                web.STATE["camera"] = ("off" if not config.SNAPSHOT_URL else
                                   "idle (read in TV mode and gaming)" if eyes.CAM_IDLE else
                                   "following screen" if eyes.screen_rgb else
                                   "connected, screen off" if eyes.CAM_OK and eyes.SCREEN_OFF else
                                   "connected, screen dark/gray" if eyes.CAM_OK else "not working")
                web.STATE["history"] = (web.STATE["history"] + [[round(e, 3), int(flash_at == now)]])[-200:]

            time.sleep(1 / 40)
    except KeyboardInterrupt:
        pass
    finally:
        if mic:
            mic.stop()
        if not paused and not config.STANDBY:
            print("\nRestoring lights...")
            for w in (screen, beat, wash):
                w.razer_stop()
            time.sleep(0.3)
            bulbs.restore_states({e: s for e, s in saved.items() if e not in let_go()})
        if not DEMO and not config.STANDBY:
            try:  # tell the dashboard right away (if the window is just closed, HA notices within ~2 min)
                ha_link.session.post(f"{HA_URL}/api/states/sensor.music_sync_status", timeout=3,
                             json={"state": "offline", "attributes": {"friendly_name": "Music sync", "icon": "mdi:music-off"}})
            except Exception:
                pass
        if not DEMO and ROLE == "main":
            try:  # the Pi takes over right away instead of after MAIN_TIMEOUT
                ha_link.session.post(f"{HA_URL}/api/states/{HA_HEARTBEAT}", timeout=3,
                             json={"state": "off", "attributes": {"friendly_name": "Light Sync on the laptop", "icon": "mdi:laptop-off"}})
            except Exception:
                pass

from parts import (
    assistant, boot, bulbs, colours, config, ears, flow, eyes, games, guard, ha_link, helpers,
    normal_light, pi_backup, rocky_cue, screen_map, songs, soundtest, sources, app_requests, web, wifi,
)

class _AllParts(type(sys)):
    """Other files (habits.py, the add-on, the test tools) still say light_sync.NAME wherever NAME moved to."""

    def __getattr__(self, name):
        for part in _PARTS:
            if name in vars(part):
                return vars(part)[name]
        raise AttributeError(name)

    def __setattr__(self, name, value):
        for part in _PARTS:
            if name in vars(part):
                setattr(part, name, value)
                return
        super().__setattr__(name, value)


_PARTS = (assistant, boot, bulbs, colours, config, ears, flow, eyes, games, guard, ha_link, helpers, normal_light, pi_backup, rocky_cue, screen_map, songs, soundtest, sources, app_requests, web, wifi)
sys.modules[__name__].__class__ = _AllParts


if __name__ == "__main__":
    main()
