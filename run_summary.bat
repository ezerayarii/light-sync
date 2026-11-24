@echo off
REM Used by the 5:00 scheduled task. Settings are read from start_lights.bat.
cd /d "%~dp0"
set PY=python
where python >nul 2>nul || set PY=py
if not exist logs mkdir logs
%PY% light_sync\daily_summary.py >> logs\summary_log.txt 2>&1
