@echo off
REM ===== Light Sync launcher =====
REM Secrets are in secrets.yaml. install_autostart.bat makes this run at login.

set HA_URL=http://192.168.1.10:8123
REM HA token, stream password and Claude API key live in secrets.yaml (only your account can read it)

REM Cameras: motionEye streams on the Pi + their streaming login (the same for both)
REM   SNAPSHOT_URL: the TV camera, motionEye Camera3 in front of the TV (TV colors, read by the screen's four
REM   corners). ROOM_CAM_URL: the room camera, Camera1 in the room (Room guard, Lights follow me); swapped Oct 28;
REM   empty: the TV camera does both
set SNAPSHOT_URL=http://192.168.1.10:8082/
set ROOM_CAM_URL=http://192.168.1.10:8081/
set SNAP_USER=ha-stream

REM Your lights (from your dashboard)
set LIGHT_SCREEN=light.chambre_tvd
set LIGHT_BEAT=light.h6004_2
set LIGHT_WASH=light.h6004

REM Govee LAN: Light Sync talks to the bulbs directly (much faster than through HA).
REM Device ids from "py light_sync\light_sync.py --scan": found on the network at startup, so new IPs from the router don't break it.
REM A bulb that doesn't answer is controlled through Home Assistant instead.
set GOVEE_SCREEN=02:00:00:00:00:00:00:93
set GOVEE_BEAT=02:00:00:00:00:00:00:18
set GOVEE_WASH=02:00:00:00:00:00:00:87

REM TvD is an RGBIC strip (Govee H612F): colors per segment over the LAN (DreamView/Razer streaming).
REM 0 = one color. FROM_RIGHT=1: the strip's first segment is on the right of the screen (tools\tests\segment_test.py).
REM Dashboard switch "TvD segments" turns it off without editing this file.
set SCREEN_SEGMENTS=10
set SCREEN_SEGMENTS_FROM_RIGHT=1

REM AI daily summary: paste your Claude API key into secrets.yaml (anthropic_api_key)
REM Your Android box in HA (Settings -> Entities, search "media_player")
set HA_MEDIA=media_player.x96q_pro_plus

REM Tuning (optional)
set SENSITIVITY=1.4
set AMBIENT_BRIGHT=150

cd /d "%~dp0"
set PY=python
where python >nul 2>nul || set PY=py

REM put the last AI summary back on the dashboard (it disappears when HA restarts)
start "" /min %PY% light_sync\daily_summary.py --repost

REM Nov 6 (owner: "the Pi drives always, the laptop only sends game colours"): this laptop runs only the game
REM helper - Gaming on/off for a laptop game and the game's colours to the Pi. It never drives the lights.
REM For a test of the whole Light Sync on this laptop: set LIGHT_SYNC_FULL=1 before starting this file.
if not "%LIGHT_SYNC_FULL%"=="1" goto helper

REM restart by itself if it stops on an error (the reason is in logs\light_sync_errors.log);
REM closing this window or Ctrl+C stops it for good
:run
%PY% light_sync\light_sync.py %*
if %errorlevel% neq 0 (
  echo Light Sync stopped on an error - restarting in 15 s. Close this window to stop it.
  timeout /t 15 /nobreak >nul
  goto run
)
pause
goto :eof

:helper
%PY% light_sync\game_helper.py
REM 3 = another helper is already running: this window isn't needed
if %errorlevel%==3 exit /b
echo Game helper stopped - restarting in 15 s. Close this window to stop it.
timeout /t 15 /nobreak >nul
goto helper
