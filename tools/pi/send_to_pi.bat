@echo off
REM Lets the Pi download the Light Sync add-on and the Neon Club theme from this laptop (see docs\pi-addon.md).
REM Serves ONLY those files (never secrets.yaml) until you close this window.
cd /d "%~dp0..\.."
py tools\pi\send_to_pi.py
pause
