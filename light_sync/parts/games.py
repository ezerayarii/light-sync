"""Auto Gaming: noticing a game on this laptop, and its screen colours."""

import numpy as np
import os
import time

from parts.config import HA_GAMING, SCREEN_SEGMENTS
from parts import colours, config, eyes, ha_link, screen_map


GAME_DIRS = ("\\steamapps\\common\\", "\\epic games\\", "\\xboxgames\\", "\\riot games\\",
             "\\gog galaxy\\games\\", "\\ubisoft game launcher\\games\\", "\\ea games\\")

NOT_GAMES = {"explorer.exe", "msedge.exe", "chrome.exe", "firefox.exe", "opera.exe", "brave.exe", "vlc.exe",
             "mpc-hc64.exe", "wmplayer.exe", "video.ui.exe", "applicationframehost.exe", "steam.exe",
             "steamwebhelper.exe", "epicgameslauncher.exe", "discord.exe", "teams.exe", "ms-teams.exe", "zoom.exe",
             "powerpnt.exe", "claude.exe", "windowsterminal.exe", "cmd.exe", "python.exe", "pythonw.exe", "py.exe",
             # launchers / lobbies (the game itself is a different exe, e.g. "league of legends.exe")
             "leagueclient.exe", "leagueclientux.exe", "leagueclientuxrender.exe", "riotclientux.exe",
             "riotclientservices.exe", "battle.net.exe", "ubisoftconnect.exe", "upc.exe", "eadesktop.exe",
             "galaxyclient.exe", "xboxpcapp.exe", "gamebar.exe"}

# Nov 5: the game lobbies. League closes the game after every match and the client comes back for 1-3 min
# (champ select), so Gaming went off and on again every ~20 min. With a lobby in front Gaming waits (up to 15 min).
LOBBIES = {"leagueclient.exe", "leagueclientux.exe", "leagueclientuxrender.exe", "riotclientux.exe",
           "riotclientservices.exe", "battle.net.exe", "steam.exe", "steamwebhelper.exe", "epicgameslauncher.exe",
           "ubisoftconnect.exe", "upc.exe", "eadesktop.exe", "galaxyclient.exe", "xboxpcapp.exe"}
LOBBY_WAIT = 15 * 60
FRONT_EXE = ""        # exe in front right now, game or not

GAME_ON_LAPTOP = ""   # exe name of the game in the foreground right now ("" = none)

laptop_rgb = None     # main color of the laptop screen while a game runs

# Nov 6 (owner: "the Pi drives always, the laptop only sends game colours"): on the Pi these come from the laptop's
# game helper (light_sync/game_helper.py, POST /game on the show server, ~10 a second) and are forgotten 2 s after the
# last one (the game closed, the laptop asleep): the Pi goes back to the TV camera by itself
REMOTE_AT = 0.0
REMOTE_KEEP = 2.0


def take_remote(body):
    """One update from the laptop's game helper: {"game": exe, "rgb": [r, g, b] or None, "samples": the 45 points}."""
    global GAME_ON_LAPTOP, laptop_rgb, REMOTE_AT
    import json
    d = json.loads(body or b"{}")
    game = str(d.get("game") or "")[:80]
    rgb = d.get("rgb")
    rgb = tuple(max(0.0, min(255.0, float(v))) for v in rgb[:3]) if isinstance(rgb, list) and len(rgb) >= 3 else None
    samples = d.get("samples")
    samples = [tuple(max(0.0, min(255.0, float(v))) for v in c[:3]) if isinstance(c, list) and len(c) >= 3 else None
               for c in samples[:len(screen_map.SAMPLE_UV)]] if isinstance(samples, list) else []
    zones = None
    if SCREEN_SEGMENTS and len(samples) == len(screen_map.SAMPLE_UV):
        if config.STRIP_MAP:  # each part from the game's edge next to it, as for the TV
            zones = screen_map.parts_from_samples(samples, config.STRIP_MAP)
        else:
            zones = screen_map.parts_from_samples(samples, [((i + 0.5) / SCREEN_SEGMENTS, 0.5) for i in range(SCREEN_SEGMENTS)])
    if game and not GAME_ON_LAPTOP:
        print(f"Laptop game: {game} - its colours from the laptop", flush=True)
    GAME_ON_LAPTOP, laptop_rgb, eyes.laptop_zones, REMOTE_AT = game, rgb, zones, time.time()
    return {"ok": True}


def remote_expire_loop():
    """The Pi: no update from the laptop for 2 s = no laptop game any more."""
    global GAME_ON_LAPTOP, laptop_rgb
    while True:
        time.sleep(0.5)
        if GAME_ON_LAPTOP and time.time() - REMOTE_AT > REMOTE_KEEP:
            print(f"Laptop game: {GAME_ON_LAPTOP} - no colours from the laptop, back to the TV camera", flush=True)
            GAME_ON_LAPTOP, laptop_rgb, eyes.laptop_zones = "", None, None

def _win32():
    """user32/kernel32/gdi32/shell32 with 64-bit-safe handle types."""
    import ctypes
    from ctypes import wintypes
    u32, k32, g32, sh = ctypes.windll.user32, ctypes.windll.kernel32, ctypes.windll.gdi32, ctypes.windll.shell32
    u32.GetForegroundWindow.restype = wintypes.HWND
    u32.GetDC.restype = wintypes.HDC
    u32.GetDC.argtypes = [wintypes.HWND]
    u32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    g32.CreateCompatibleDC.restype = wintypes.HDC
    g32.CreateCompatibleDC.argtypes = [wintypes.HDC]
    g32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
    g32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
    g32.SelectObject.restype = wintypes.HGDIOBJ
    g32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
    g32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
    g32.DeleteDC.argtypes = [wintypes.HDC]
    g32.StretchBlt.argtypes = [wintypes.HDC] + [ctypes.c_int] * 4 + [wintypes.HDC] + [ctypes.c_int] * 4 + [wintypes.DWORD]
    g32.SetStretchBltMode.argtypes = [wintypes.HDC, ctypes.c_int]
    g32.GetDIBits.argtypes = [wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT,
                              ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT]
    u32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    return ctypes, wintypes, u32, k32, g32, sh

def foreground_game():
    """(exe name, process id) of the game in the foreground on this laptop, or ("", 0) (Windows only)."""
    ctypes, wintypes, u32, k32, _, sh = _win32()
    state = ctypes.c_int(0)
    sh.SHQueryUserNotificationState(ctypes.byref(state))  # 2 = full-screen app, 3 = full-screen Direct3D
    pid = wintypes.DWORD()
    u32.GetWindowThreadProcessId(u32.GetForegroundWindow(), ctypes.byref(pid))
    h = k32.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
    global FRONT_EXE
    FRONT_EXE = ""
    if not h:
        return "", 0
    try:
        buf, size = ctypes.create_unicode_buffer(1024), wintypes.DWORD(1024)
        if not k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return "", 0
        path = buf.value.lower()
    finally:
        k32.CloseHandle(h)
    exe = os.path.basename(path)
    FRONT_EXE = exe
    if exe in NOT_GAMES:
        return "", 0
    return (exe, pid.value) if any(d in path for d in GAME_DIRS) or state.value in (2, 3) else ("", 0)

def process_alive(pid):
    """Is this process still running? (Windows)"""
    ctypes, wintypes, _, k32, _, _ = _win32()
    h = k32.OpenProcess(0x1000, False, pid)
    if not h:
        return False
    try:
        code = wintypes.DWORD()
        return bool(k32.GetExitCodeProcess(h, ctypes.byref(code))) and code.value == 259  # STILL_ACTIVE
    finally:
        k32.CloseHandle(h)

def grab_laptop_screen(w=64, h=36):
    """Tiny copy of the laptop screen (Windows GDI, no extra packages) as an RGB array."""
    ctypes, wintypes, u32, _, g32, _ = _win32()

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                    ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                    ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                    ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]

    sw, sh_ = u32.GetSystemMetrics(0), u32.GetSystemMetrics(1)
    src = u32.GetDC(None)
    dc = g32.CreateCompatibleDC(src)
    bmp = g32.CreateCompatibleBitmap(src, w, h)
    old = g32.SelectObject(dc, bmp)
    try:
        g32.SetStretchBltMode(dc, 4)  # HALFTONE: average the pixels instead of skipping them
        g32.StretchBlt(dc, 0, 0, w, h, src, 0, 0, sw, sh_, 0x00CC0020)  # SRCCOPY
        bi = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
        buf = (ctypes.c_ubyte * (w * h * 4))()
        g32.GetDIBits(dc, bmp, 0, h, buf, ctypes.byref(bi), 0)
    finally:
        g32.SelectObject(dc, old)
        g32.DeleteObject(bmp)
        g32.DeleteDC(dc)
        u32.ReleaseDC(None, src)
    return np.frombuffer(buf, np.uint8).reshape(h, w, 4)[:, :, 2::-1]  # BGRA -> RGB

def laptop_game_loop():
    """Turns Gaming on 3 s after a game comes to the front and off 10 s after the game itself closed (only if it
    turned it on itself; switched off by hand = stays off for that game). While gaming here, reads the screen color.
    Oct 27: it went by the window in front only, so every switch to another window during a match (chat, browser)
    turned Gaming off after 10 s and on again after 3 s back: 6-14 s gaps every 1-2 min during League. Now a game
    tabbed away from still counts while its process runs, and the lights keep its last colours meanwhile.
    Nov 5: with a game lobby in front (League's client between matches) it stays on up to 15 min."""
    global GAME_ON_LAPTOP, laptop_rgb
    import ctypes
    import cv2
    ctypes.windll.user32.SetProcessDPIAware()  # real pixels, so the whole screen is captured
    seen = gone = 0.0
    auto_on, skip = False, ""
    playing, playing_pid = "", 0  # the game last seen in front, while its process runs
    while True:
        now = time.time()
        try:
            front, pid = foreground_game()
        except Exception:
            front, pid = "", 0
        if front:
            playing, playing_pid = front, pid
        elif playing:
            try:
                if not process_alive(playing_pid):  # the game itself closed (League: after every match)
                    playing, playing_pid = "", 0
            except Exception:
                playing, playing_pid = "", 0
        game = playing
        GAME_ON_LAPTOP = game
        seen, gone = (seen or now, 0.0) if game else (0.0, gone or now)
        if not game:
            skip = ""
        if config.AUTO_GAMING:
            if auto_on and not config.GAMING_ON and game:  # switched off by hand while playing
                auto_on, skip = False, game
            if game and game != skip and not config.GAMING_ON and now - seen > 3:
                print(f"Game: {game} - Gaming on", flush=True)
                ha_link.set_switch(HA_GAMING, True)
                auto_on = True
            elif auto_on and not game and now - gone > (LOBBY_WAIT if FRONT_EXE in LOBBIES else 10):
                print("Game closed - Gaming off", flush=True)
                ha_link.set_switch(HA_GAMING, False)
                auto_on = False
        if game and config.GAMING_ON and not front:
            time.sleep(0.25)  # tabbed away from the game: keep its last colours, not the window in front
        elif game and config.GAMING_ON:
            try:
                img = grab_laptop_screen()
                hsv = cv2.cvtColor(np.ascontiguousarray(img[:, :, ::-1]), cv2.COLOR_BGR2HSV).reshape(-1, 3)
                px = img.reshape(-1, 3)[(hsv[:, 1] > 70) & (hsv[:, 2] > 60)]  # colorful pixels only
                laptop_rgb = colours.vivid(px.mean(axis=0)) if len(px) > 20 else None
                lap = np.ascontiguousarray(img[:, :, ::-1])  # each part from the game's edge next to it, as for the TV
                eyes.laptop_zones = ((screen_map.strip_colors(lap, config.STRIP_MAP) if config.STRIP_MAP else screen_map.zone_colors(lap, SCREEN_SEGMENTS))
                                if SCREEN_SEGMENTS else None)
            except Exception:
                laptop_rgb = eyes.laptop_zones = None
            time.sleep(0.08)  # ~12 color reads/s while playing
        else:
            laptop_rgb = eyes.laptop_zones = None
            time.sleep(1.0)
