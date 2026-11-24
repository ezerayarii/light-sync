@echo off
REM Opens the Light Sync show full screen (Light Sync must already be running).
REM Put it on the TV: Win+K -> pick the X96Q box (mirror), or Win+P -> Extend and drag this window to the TV.
REM Keys in the show: F full screen, 1-4 neon/club/concert/chill, Left/Right line the visuals up with the lights, H hide hints.
start "" msedge --app=http://localhost:8089/show --start-fullscreen --new-window
