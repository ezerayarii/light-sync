# light_sync_control.ps1 - Start, Restart, Stop and check Light Sync on this laptop, no Claude needed (Oct 30, owner).
# Open it with light_sync_control.bat (the desktop shortcut "Light Sync Control"), or give a word:
#   light_sync_control.bat start | restart | stop | status | check | errors
param([string]$Do = '')

$Root = Split-Path $PSScriptRoot -Parent
$Shortcut = Join-Path ([Environment]::GetFolderPath('Startup')) 'Light Sync.lnk'
$ErrorLog = Join-Path $Root 'logs\light_sync_errors.log'

function Get-Sync {
    # the program, and the window around it that starts it again after a crash
    Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='py.exe' OR Name='cmd.exe'" |
        Where-Object { $_.CommandLine -match 'light_sync\\light_sync\.py|light_sync\\game_helper\.py|start_lights\.bat' }
}

function Get-Mode {
    try { (Invoke-RestMethod 'http://127.0.0.1:8089/state' -TimeoutSec 3).mode } catch { $null }
}

function Show-Status {
    $mode = Get-Mode
    if ($mode) { Write-Host "Light Sync is RUNNING on this laptop. Mode: $mode" -ForegroundColor Green }
    elseif (Get-Sync) { Write-Host 'Light Sync is STARTING (or stuck, if this stays for over a minute).' -ForegroundColor Yellow }
    else { Write-Host 'Light Sync is STOPPED on this laptop. The Pi drives the lights until it is back.' -ForegroundColor Yellow }
}

function Stop-Sync {
    # the window first: otherwise it starts the program again 15 s later
    $all = @(Get-Sync)
    if (-not $all) { Write-Host 'It was already stopped.'; return }
    $all | Sort-Object { $_.Name -ne 'cmd.exe' } | ForEach-Object {
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
    }
    Write-Host 'Stopped. The Pi takes over the lights within seconds.'
}

function Start-Sync {
    if (Get-Sync) { Write-Host 'It is already running.'; Show-Status; return }
    if (Test-Path $Shortcut) { Start-Process explorer.exe -ArgumentList "`"$Shortcut`"" }
    else { Start-Process (Join-Path $Root 'start_lights.bat') -ArgumentList '--no-browser' -WorkingDirectory $Root -WindowStyle Minimized }
    Write-Host 'Starting, up to 30 seconds...'
    foreach ($i in 1..15) {
        Start-Sleep -Seconds 2
        if (Get-Mode) { break }
    }
    Show-Status
}

function Invoke-Choice([string]$choice) {
    switch ($choice) {
        { $_ -in '1', 'start' } { Start-Sync }
        { $_ -in '2', 'restart' } { Stop-Sync; Start-Sleep -Seconds 2; Start-Sync }
        { $_ -in '3', 'stop' } { Stop-Sync }
        { $_ -in '4', 'status' } { Show-Status }
        { $_ -in '5', 'check' } { Push-Location $Root; & py tools\tests\selftest.py; Pop-Location }
        { $_ -in '6', 'errors' } {
            if (Test-Path $ErrorLog) { Get-Content $ErrorLog -Tail 25 } else { Write-Host 'No errors written.' }
        }
        default { Write-Host 'Pick a number from the list.' }
    }
}

if ($Do) { Invoke-Choice $Do.ToLower(); exit }

$Host.UI.RawUI.WindowTitle = 'Light Sync Control'
while ($true) {
    Write-Host ''
    Show-Status
    Write-Host ''
    Write-Host '  1  Start'
    Write-Host '  2  Restart  (try this first when something acts strange)'
    Write-Host '  3  Stop'
    Write-Host '  4  Status'
    Write-Host '  5  Full check of the whole system (about a minute)'
    Write-Host '  6  Last errors'
    Write-Host '  Q  Close this window (Light Sync keeps running)'
    Write-Host ''
    $choice = (Read-Host 'Type a number and press Enter').Trim().ToLower()
    if ($choice -eq 'q') { break }
    Write-Host ''
    Invoke-Choice $choice
}
