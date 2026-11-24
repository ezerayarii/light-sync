"""Every setting and its starting value, from start_lights.bat or the add-on's options. The ones that change while
running are read as config.NAME everywhere, so all parts see the live value."""

import os
import sys


HA_URL = os.getenv("HA_URL", "http://homeassistant.local:8123").rstrip("/")

HA_TOKEN = os.getenv("HA_TOKEN", "")

LIGHT_SCREEN = os.getenv("LIGHT_SCREEN", "light.tvd")

LIGHT_BEAT = os.getenv("LIGHT_BEAT", "light.corner")

LIGHT_WASH = os.getenv("LIGHT_WASH", "light.top")

SNAPSHOT_URL = os.getenv("SNAPSHOT_URL", "")      # the TV camera: the screen's colours (TV mode), and the room when there's no ROOM_CAM_URL

ROOM_CAM_URL = os.getenv("ROOM_CAM_URL", "")      # the room camera (Oct 27: a second MeetUp, low): Room guard, Lights follow me

MIC_DEVICE = os.getenv("MIC_DEVICE")              # mic index or name; empty = default mic

AUDIO_URL = os.getenv("AUDIO_URL", "")            # e.g. rtsp://<pi>:8554/meetup_mic -> use the MeetUp mic instead

SENSITIVITY = float(os.getenv("SENSITIVITY", "1.4"))  # lower = more beats detected

STYLE = os.getenv("STYLE", "vibe")                # the movement in use (the owner's, or Auto DJ's while it's on)

USER_STYLE = STYLE                                # the owner's own movement (dashboard "Movement")

FLASH = os.getenv("FLASH", "hits")                # "off", "hits" = white flash on big hits, "strobe" = white on every beat

BIG_HIT = float(os.getenv("BIG_HIT", "1.8"))      # big hit = this many times harder than recent kicks

RUNNING = True                                    # False = paused (both Music sync and TV ambient off)

MUSIC_ON = True                                   # input_boolean.music_sync

AMBIENT_ON = False                                # input_boolean.tv_ambient

GAMING_ON = False                                 # input_boolean.gaming_mode: fast screen follow, no flashes

AMBIENT_BRIGHT = int(os.getenv("AMBIENT_BRIGHT", "150"))  # TV ambient brightness 0-255

GAMING_BRIGHT = int(os.getenv("GAMING_BRIGHT", "210"))    # gaming mode brightness 0-255

# Home Assistant dashboard controls (optional helpers - see the dashboard guide)
HA_SWITCH = "input_boolean.music_sync"

HA_FLASH = "input_select.music_flash"

WHITE = ((255, 255, 255), 255, 0)  # a flash: full white

HA_STYLE = "input_select.music_style"

HA_SENS = "input_number.music_sensitivity"

HA_AMBIENT = "input_boolean.tv_ambient"

HA_GAMING = "input_boolean.gaming_mode"

# which lights Light Sync drives (dashboard switches; off = it lets go of that light and switches it off)
HA_USE = {"screen": "input_boolean.light_sync_use_screen", "beat": "input_boolean.light_sync_use_beat",
          "wash": "input_boolean.light_sync_use_wash"}

USE = {"screen": True, "beat": True, "wash": True}

HA_GAME_MUSIC = "input_boolean.game_music"        # while gaming: lights also react to music with a steady beat

GAME_MUSIC = True

HA_TV_MUSIC = "input_boolean.tv_music"            # TV mode: the TV's colors, moving with the music's beat

HA_CINEMA = "input_boolean.light_sync_cinema"     # TV mode: Corner and Top a low, slow glow (Oct 20, "movie mode")

CINEMA = True

# TV mode's colour rules (Nov 6, owner: "other rules of colours, not the same as the sync": TvD with the TV, Top "on my
# eye" smooth, Corner "with mode"): TvD the film's real colours (not Music's full-vivid push), Top the film's mood (its
# hue, soft and slow), Corner by this choice
# Nov 6 (owner: "make the volume control the brightness, make it smooth"): the TV box's volume (0-1, read every
# second by light_sync.py) is the brightness knob in TV and TV + Music; None = not known (box off / unavailable)
BOX_VOLUME = None
# ... and at which volume the lights are full (owner: "the max volume changes, the app should adapt"): 0 = Auto (the
# loudest volume used lately, fading over ~3 h), else that volume (0-1). input_number.light_sync_full_volume (0-100 %)
HA_FULL_VOLUME = "input_number.light_sync_full_volume"
FULL_VOLUME = 0.0
HA_TV_CORNER = "input_select.light_sync_tv_corner"
TV_CORNER_RULES = ("Colour harmony", "True to the film", "Contrast", "Film mood")
TV_CORNER = "Colour harmony"

# Normal (Oct 20, owner: "a normal mode for every day, from 0, that learns from the past"): Light Sync drives it now
# (was Home Assistant scripts after Light Sync let go: ~10 s and a flash of the old colours). Corner and Top white
# (the bulbs' own white LEDs), TvD off; brightness and warmth follow the day (learned from the sliders), or a scene.
HA_NORMAL = "input_boolean.normal_mode"

HA_NORMAL_BRIGHT = "input_number.light_sync_normal_bright"   # 5-100 %: the brightness now (moving it = taught)

HA_NORMAL_KELVIN = "input_number.light_sync_normal_kelvin"   # 2000-6500 K: the warmth now

HA_NORMAL_SCENE = "input_select.light_sync_normal_scene"     # Follow the day / Bright / Relax / Read / Night

NORMAL_ON, NORMAL_SCENE = False, "Follow the day"

# Nov 7 (owner: "the lowest value we can, in anything that needs minimum light"): the camera test found the bulbs keep
# their colour down to 1 %, so the night is 1 % (Night scene, Normal after bedtime). LOWEST: the bulbs' lowest everywhere
LOWEST_PCT = 1
NORMAL_SCENES = {"Bright": (100, 4500), "Relax": (45, 2700), "Read": (85, 4000), "Night": (LOWEST_PCT, 2000)}

# the day without learning: (minute of the day, brightness %, kelvin) - bright neutral by day, warm in the evening,
# dim amber at night (no blue before sleep, like Comfort)
NORMAL_DAY = [(0, LOWEST_PCT, 2000), (300, LOWEST_PCT, 2000), (420, 55, 3200), (540, 85, 4500), (1020, 85, 4300), (1140, 65, 3000),
              (1260, 40, 2600), (1350, 25, 2200), (1440, LOWEST_PCT, 2000)]  # (bedtime: 1440 on the stretched day)


# Normal "where you left it" (Nov 2, owner: "back where I left it, the lifestyle one, not a fixed one"): moving a slider saves how far
# from the day curve you like it, and Normal keeps following the day that much brighter / dimmer, warmer / cooler, until you move it
# again or press Reset. In Home Assistant, so a restart or the Pi taking over keeps it. (Before: the number itself, held 2 h, then gone.)
HA_NORMAL_ADJUST = "input_text.light_sync_normal_adjust"   # "brightness points,kelvin": "0,0" = the day as it is
NORMAL_ADJUST = None      # (brightness points, kelvin) from the day curve, or None
NORMAL_RAMP = None        # (started, seconds): Fajr's soft wake, Normal rising from the lowest (Nov 8)
NORMAL_ADJUST_AT = 0.0    # when it was set (Nov 7: it lasts until the next wake-up; 0 = not known, kept)
# Normal "the room as you left it" (Nov 2, owner: "only the Corner while reading, or TvD at the desk; after a film Normal brings Corner and
# Top back, static: I want it back where it was"): which lights were on, how bright, what colour, kept in Home Assistant (both machines).
HA_NORMAL_ROOM = "input_text.light_sync_normal_room"     # {"t": when, "screen"/"beat"/"wash": [on, brightness 0-255, kelvin, rgb]}, "" = none
NORMAL_ROOM_S = 6 * 3600  # older than this: a new day, the standard Normal again

OWN_WRITES = {}           # helper -> (value, time): Light Sync's own updates of the sliders (not the owner's)

LAST_HELPER = {}          # helper -> last state seen (to tell a change from the first reading)

TV_MUSIC = True

HA_AUTO_GAMING = "input_boolean.auto_gaming"      # switch Gaming on/off by itself when a game runs on this laptop

AUTO_GAMING = True

HA_TV_BOX = "input_text.light_sync_tv_box"        # where the screen is in the camera picture (tools/tests/find_screen.py --apply)

HA_STRIP_MAP = "input_text.light_sync_strip_map"  # where each of TvD's parts sits on the screen's edge (strip_map.py)

HA_ROOM_TV_BOX = "input_text.light_sync_room_tv_box"  # where the TV is in the room camera's picture, left out (find_screen.py --room --apply)

SCREEN_SEGMENTS = int(os.getenv("SCREEN_SEGMENTS", "0"))  # TvD as an RGBIC strip: colors per segment (0 = one color)

SCREEN_SEGMENTS_FROM_RIGHT = os.getenv("SCREEN_SEGMENTS_FROM_RIGHT", "0") == "1"  # strip starts on the screen's right

HA_SEGMENTS = "input_boolean.light_sync_tvd_segments"  # dashboard switch "TvD segments"

SEGMENTS_ON = True

# Comfort (eye-friendly, default on): no flashes or strobe (photosensitivity guidance: WCAG 2.3.1), no beat pulsing
# (low-frequency light modulation is what IEEE 1789 links to headaches): brightness follows the music slowly in a
# narrow band, colors less saturated and crossfading slowly; warm and dimmer at night (no blue in the evening)
HA_COMFORT = "input_boolean.light_sync_comfort"

COMFORT = True

HA_LATE_NIGHT = "input_boolean.late_night_mode"

LATE_NIGHT = False

COMFORT_FADE = 2.0       # s: color crossfade time constant

COMFORT_SWELL = 2.5      # s: brightness follows the loudness this slowly

COMFORT_CAP = (210, 130)  # max brightness by day / at night (0-255)

# sound options (dashboard / app): what each light listens to, mood colors, and the tuning sliders
HA_PARTS = {"screen": "input_select.light_sync_part_screen", "beat": "input_select.light_sync_part_beat",
            "wash": "input_select.light_sync_part_wash"}

PARTS = {"screen": "everything", "beat": "bass", "wash": "highs"}

PART_OPTIONS = ["everything", "bass", "mids", "highs", "mix"]  # mix = color from the sound: bass red, mids green, highs blue

# each light (Oct 19): pulses on the beat or not, and its own brightness level (every mode)
HA_PULSE = {"screen": "input_boolean.light_sync_pulse_screen", "beat": "input_boolean.light_sync_pulse_beat",
            "wash": "input_boolean.light_sync_pulse_wash"}

PULSE = {"screen": False, "beat": True, "wash": False}  # TvD's "pulse" is a soft swell: it never flashes

HA_LEVEL = {"screen": "input_number.light_sync_level_screen", "beat": "input_number.light_sync_level_beat",
            "wash": "input_number.light_sync_level_wash"}

LEVEL = {"screen": 1.0, "beat": 1.0, "wash": 1.0}

USER_PULSE = dict(PULSE)  # the owner's own pulse choices (Auto DJ overrides PULSE while it's on)

# Auto DJ (Oct 19, local): the song's part picks the movement and the beat pulses; the owner's own choices are kept
HA_AUTO_DJ = "input_boolean.light_sync_auto_dj"

AUTO_DJ = False

DJ_LOOKS = {  # section -> (movement, pulses)
    "quiet": ("together", {"screen": False, "beat": False, "wash": False}),
    "building up": ("flow", {"screen": False, "beat": True, "wash": False}),
    "drop": ("together", {"screen": True, "beat": True, "wash": True}),
    "groove": ("bounce", {"screen": False, "beat": True, "wash": False}),
    "no steady beat": ("each own", {"screen": False, "beat": False, "wash": False}),
}

HA_MOOD = "input_boolean.light_sync_mood_colors"   # older switch; "Colours" = mood replaces it once that exists

MOOD_ON = True

# the colors and how they move (Music settings): a color set, how many beats between changes, and the movement
HA_COLORS = "input_select.light_sync_colors"       # mood (the music picks) / neon / sunset / ocean / forest / fire / mine

COLORS = "mood"

COLOR_SETS = {  # hues 0-1 (neon = BASE_PALETTE)
    "sunset": [0.98, 0.03, 0.07, 0.11, 0.93, 0.88],  # red, orange, amber, gold, pink, magenta
    "ocean": [0.50, 0.53, 0.58, 0.62, 0.66, 0.45],   # cyan, teal, sky, blue, deep blue, aqua
    "forest": [0.25, 0.30, 0.36, 0.42, 0.18, 0.47],  # lime, green, emerald, sea green, yellow-green, teal
    "fire": [0.0, 0.03, 0.06, 0.09, 0.12, 0.97],     # red, red-orange, orange, amber, yellow, crimson
}

HA_MY_COLORS = "input_text.light_sync_my_colors"   # "mine": up to 6 colors, "#rrggbb,#rrggbb,..."

MY_HUES = []

HA_CHANGE = "input_select.light_sync_color_change"  # a new color every 1 / 2 / 4 / 8 beats

CHANGE_BEATS = {"every beat": 1, "2 beats": 2, "4 beats": 4, "8 beats": 8}

FLOW_STYLES = ("flow", "flow back", "bounce", "together")  # input_select.music_style ("Movement"); "each own" = vibe

HA_NUMBERS = {"input_number.light_sync_reaction": "reaction", "input_number.light_sync_bright_min": "bright_min",
              "input_number.light_sync_bright_max": "bright_max", "input_number.light_sync_beat_lock": "beat_lock",
              "input_number.light_sync_latency": "latency"}

TUNE = {"reaction": 5.0, "bright_min": 62.0, "bright_max": 82.0, "beat_lock": 0.45, "latency": None}

HA_REACTION_AUTO = "input_boolean.light_sync_reaction_auto"  # reaction speed follows the rhythm (the song's tempo)

REACTION_AUTO = True

REACTION_NOW = 5.0  # the reaction speed in use: the slider, or from the tempo when it follows the rhythm

BASE_PALETTE = None  # the neon palette (set below, once PALETTE exists)

MOODS = {  # palettes (hues 0-1) picked by the music's mood; each new song of a mood takes the next one (SongWatch), so
    # two songs in a row look different (None = the neon palette)
    "calm": [[0.08, 0.05, 0.02, 0.97, 0.11, 0.94],          # warm amber, rose
             [0.10, 0.07, 0.13, 0.04, 0.95],                # peach, gold
             [0.95, 0.90, 0.02, 0.85, 0.08]],               # rose, lavender
    "chill": [[0.55, 0.60, 0.66, 0.72, 0.78, 0.50],         # teal, blue, violet
              [0.50, 0.53, 0.58, 0.62, 0.66, 0.45],         # ocean
              [0.62, 0.70, 0.78, 0.86, 0.58]],              # blue, violet, pink
    "upbeat": [None,                                        # neon
               [0.98, 0.03, 0.07, 0.11, 0.93, 0.88],        # sunset
               [0.25, 0.48, 0.85, 0.92, 0.53]],             # lime, cyan, magenta
    "intense": [[0.98, 0.92, 0.86, 0.02, 0.55, 0.95],       # red, magenta, a cyan accent
                [0.0, 0.03, 0.06, 0.09, 0.12, 0.97],        # fire
                [0.78, 0.85, 0.98, 0.02, 0.72]],            # deep violet, red
}

HA_MIC = "input_select.music_mic"                 # which mic to listen with (options filled in by this script)

MIC_CHOICE = "Auto (laptop sound or room mic)"

# laptop + Pi: the laptop ("main") drives the lights while it runs; the Pi add-on ("backup") stands by and
# takes over when the laptop sleeps, is closed or Light Sync stops there, then hands back when it returns
ROLE = os.getenv("ROLE", "main")

HOST = os.getenv("LIGHT_SYNC_HOST", "laptop")     # shown on the dashboard: which machine drives the lights

HA_HEARTBEAT = "sensor.light_sync_laptop"         # the laptop says "on" here every 2 s while it runs

MAIN_TIMEOUT = float(os.getenv("MAIN_TIMEOUT", "8"))  # backup: laptop silent this long = gone

AUDIO = os.getenv("AUDIO", "")                    # "pulse" = Pi add-on: the MeetUp mic through HA's audio

STANDBY = False                                   # backup: the laptop is running, hands off the lights

PHONE_HOLD = 1.5                                  # s: while a phone sends sound (app "Listen"), the local mic is ignored

ROOM_HOLD = 1.5                                   # s: the room sound from the Pi (camera mic) counts as live this long

LOCAL_HOLD = 4.0                                  # s: Auto: the laptop counts as playing this long after its last sound

LOCAL_PLAYING = 0.001                             # Auto: laptop sound above this level = something is playing

GAME_FROM = os.getenv("GAME_FROM", "")            # the Pi: only this address (the laptop) may send a game's colours (POST /game)
LAPTOP_MAC = os.getenv("LAPTOP_MAC", "02:00:00:00:00:04")  # Nov 8: the router moved the laptop (.204 -> .203); its Wi-Fi MAC doesn't change


def laptop_ip():
    """The laptop's address now: where the Pi's Wi-Fi scan sees its MAC, else GAME_FROM."""
    try:
        from parts import wifi
        d = wifi.WIFI.get(LAPTOP_MAC)
        if d and d.get("ip"):
            return d["ip"]
    except Exception:
        pass
    return GAME_FROM


def from_laptop(client):
    """Only the laptop may send game colours, its presence, updates, the Wi-Fi lists (POST to the show server)."""
    return bool(client) and client in (GAME_FROM, laptop_ip())

PHONE_FROM = os.getenv("PHONE_FROM", "")          # laptop: only this address (the Pi) may pass phone sound on

MAIN_INFO = {}                                    # backup: the laptop's last heartbeat (show_url, restore, game)

BEATS_PER_COLOR = int(os.getenv("BEATS_PER_COLOR", "8"))  # a new color every N beats ("Change colour" on the dashboard)

BULB_LAN_RATE = float(os.getenv("BULB_LAN_RATE", "4"))     # at most this many commands a second to a bulb over LAN

MIN_ENERGY = float(os.getenv("MIN_ENERGY", "0.15"))       # ignore beats quieter than this (talking, quiet parts)

BROAD_K = float(os.getenv("BROAD_K", "1.5"))             # mic beats: a rise this many std devs above usual

AC_WINDOW = 8.0                                          # mic beats: s of sound the tempo is worked out from

AC_CONF = float(os.getenv("AC_CONF", "0.2"))             # mic beats: how clear the rhythm must be to lock (0-1)

AC_PRIOR = float(os.getenv("AC_PRIOR", "1.0"))           # mic beats: how strongly ~120 BPM is preferred (octaves)

MIC_GATE_X = float(os.getenv("MIC_GATE_X", "2.5"))       # a mic: silence = this many times the room's noise
# a mic: sound counts as music when this share of the last 2 s was above that; once a tempo is locked, the lower one keeps it going
# (Oct 31, the real room, empty: 0.12 usual, up to 0.33; a beat from the laptop's speakers: 0.63 usual, never under 0.48)
MIC_MUSIC_SHARE = float(os.getenv("MIC_MUSIC_SHARE", "0.4"))
MIC_KEEP_SHARE = float(os.getenv("MIC_KEEP_SHARE", "0.25"))
MIC_MIN_GATE = float(os.getenv("MIC_MIN_GATE", "0.001"))  # the silence level never goes under this: the noise floor follows the quietest moment down into the room's own sound (0.0005-0.0009; a quiet beat: 0.0013)

SNAP_USER = os.getenv("SNAP_USER")

SNAP_PASS = os.getenv("SNAP_PASS")

VIEW_PORT = int(os.getenv("VIEW_PORT", "8089"))

SHOW_PORT = int(os.getenv("SHOW_PORT", "8090"))   # show visuals for the home network (HA dashboard, TV box); 0 = off

# Govee LAN (optional, much faster than going through HA): IPs from --scan
GOVEE_SCREEN = os.getenv("GOVEE_SCREEN", "")

GOVEE_BEAT = os.getenv("GOVEE_BEAT", "")

GOVEE_WASH = os.getenv("GOVEE_WASH", "")

USING_LAN = bool(GOVEE_SCREEN or GOVEE_BEAT or GOVEE_WASH)

# ms the lights lag behind the music; flashes fire this early on a steady beat
LATENCY_MS = float(os.getenv("LATENCY_MS", "70" if USING_LAN else "180"))

SMOOTH = float(os.getenv("SMOOTH", "0.18"))      # color fade speed per frame (lower = smoother)

# TV mode (Oct 27, owner: "make it more smooth"): the screen's colour is averaged over TV_STEADY_S before it's made vivid
# (a passing tint in a dull scene used to become a new full colour), then TvD glides to it with TV_GLIDE_S as its time
# constant, Corner / Top 3x slower (the Cinema look's soft glow), brighter colours 2.5x slower again (no flash).
# The Cinema look (Oct 27, owner during a film: "too much light, I want the room dark when there's no action, and go
# auto"): dark while the film is calm or the TV shows nothing, the lights come up with the action. Action = how fast
# the screen's colours change over 1 s (not its brightness: the camera sees the TV's backlight shimmer, ~30/255 from
# one picture to the next even on a paused film). A Pokémon film measured 16-54 in 5 s stretches with the far camera;
# with the camera in front of the TV (Oct 28: straightened, 60 ms, far less noise) ~2-3 calm, 10-26 with action.
TV_ACTION_LO = float(os.getenv("TV_ACTION_LO", "6"))   # colour change over 1 s (x1000) up to here: calm, the room dark

TV_ACTION_HI = float(os.getenv("TV_ACTION_HI", "24"))  # from here: full action, the lights at TV mode's brightness

TV_DARK = float(os.getenv("TV_DARK", "0.25"))         # TvD while calm (share of its TV brightness); Corner / Top ~off

TV_DARK_TVD_MAX = float(os.getenv("TV_DARK_TVD_MAX", "0.5"))  # TvD with full action: it only breathes slowly with the

# film (Oct 27, owner: "fix the flash hit on TvD, each moment it flashes": up 30 -> 115 in 1.5 s with every action)
TV_ACTION = {"raw": 0.0, "level": 0.0, "at": 0.0}       # the camera's action measure; level 0-1

# TV mode's speed (Oct 28, owner: "make it more live"): the camera's colours averaged over TV_STEADY_S (was 0.6),
# TvD gliding over TV_GLIDE_S (was 0.7), Corner / Top 1.5x that in the Cinema look (was 3x). A change on the TV
# reaches TvD in ~0.7 s and the bulbs in ~1 s (was ~1.5-2 s and ~3 s). Brighter still comes in 2.5x slower: no flash.
TV_STEADY_S = float(os.getenv("TV_STEADY_S", "0.3"))

# Nov 6 (owner: "the TV mode ... I want it smooth"): 0.6 s, between the old 0.7 (slow to follow) and 0.4
TV_GLIDE_S = float(os.getenv("TV_GLIDE_S", "0.6"))

TRANS_S = float(os.getenv("TRANS_S", "0.8"))     # switching modes: the colours cross-fade this long (s)

PAUSE_GRACE_S = float(os.getenv("PAUSE_GRACE_S", "1.5"))  # every mode off this long before Light Sync stops (a switch: less)

MIN_BRIGHT = int(os.getenv("MIN_BRIGHT", "70"))   # brightness floor 0-255 (lower = bigger swing with the music)

MAX_BRIGHT = 255                                   # brightness ceiling (both from the dashboard's brightness sliders)

# neon palette (hues 0-1), ordered so neighbours blend nicely; no muddy yellow-greens
PALETTE = [0.92, 0.85, 0.78, 0.70, 0.62, 0.53, 0.48, 0.0, 0.05, 0.97]

BASE_PALETTE = list(PALETTE)

DEMO = "--demo" in sys.argv

DEMO_RAW = "--raw" in sys.argv  # demo without the comfort filter (to compare)

# Where the monitor sits in your camera image (relative x1, y1, x2, y2)
TV_BOX = tuple(float(v) for v in os.getenv("TV_BOX", "0.23,0.45,0.35,0.59").split(","))

ROOM_TV_BOX = None  # the TV in the room camera's picture (HA_ROOM_TV_BOX), None: not in view / not measured

# The screen by its four corners (Oct 28): the TV camera now sits in front of the TV, below it, and sees the screen as
# a trapezoid; a box around it takes in the wall the lights colour. HA_TV_BOX then holds 8 numbers (top-left,
# top-right, bottom-right, bottom-left, x,y each); TV mode straightens that shape into a flat picture of the screen
# (warp_quad). TV_BOX stays the box around it (the wall ring, the re-check, the preview). None: a plain box.
TV_QUAD = None

# Where each of TvD's parts (in the strip's own order) sits round the screen, as u (across) : v (down) on its edge
# (Oct 28, owner: the strip starts on top of the TV, runs right, down the right side, ends on the desk; nothing on
# the left - it was read as a line under the screen). tools/tests/strip_map.py measures it with the camera. Each part
# then takes its colour from the screen next to it. None: slices left to right as before.
STRIP_MAP = None

RATE = 22050

BLOCK = 512  # ~23 ms per audio block (faster beat detection)
