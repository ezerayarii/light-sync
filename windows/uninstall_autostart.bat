@echo off
del "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\Light Sync.lnk" 2>nul
powershell -NoProfile -Command "Unregister-ScheduledTask -TaskName 'Light Sync daily summary' -Confirm:$false" 2>nul
echo Autostart and the daily summary task are removed.
pause
