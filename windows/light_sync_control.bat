@echo off
REM Start, Restart, Stop and check Light Sync on this laptop (the desktop shortcut "Light Sync Control" opens this).
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0light_sync_control.ps1" %*
