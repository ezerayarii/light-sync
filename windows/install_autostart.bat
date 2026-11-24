@echo off
REM Run once. Makes Light Sync start (minimized) when you log in,
REM and writes the AI daily summary every morning at 5:00.
cd /d "%~dp0"
REM the project folder (one up from windows\)
for %%I in ("%~dp0..") do set DIR=%%~fI\

powershell -NoProfile -Command ^
  "$s=(New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Startup')+'\Light Sync.lnk');" ^
  "$s.TargetPath='%DIR%start_lights.bat'; $s.Arguments='--no-browser'; $s.WorkingDirectory='%DIR%'; $s.WindowStyle=7; $s.Save()"

powershell -NoProfile -Command ^
  "$a=New-ScheduledTaskAction -Execute '%DIR%run_summary.bat' -WorkingDirectory '%DIR%';" ^
  "$t=New-ScheduledTaskTrigger -Daily -At 05:00;" ^
  "$o=New-ScheduledTaskSettingsSet -StartWhenAvailable;" ^
  "Register-ScheduledTask -TaskName 'Light Sync daily summary' -Action $a -Trigger $t -Settings $o -Force | Out-Null"

echo.
echo Done:
echo  - Light Sync will start minimized every time you log in
echo  - AI daily summary runs every day at 5:00 (or as soon as the laptop wakes up)
echo To undo: run uninstall_autostart.bat
pause
