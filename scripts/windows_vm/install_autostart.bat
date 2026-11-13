@echo off
REM Creates the scheduled tasks "PISCO ship NMEA" (at startup) and
REM "PISCO serial bridges" (at logon of the current user). Run once, logged in
REM as pisco, with right-click > "Run as administrator".
REM Disable any other autostart of the bridges or of udp2com.ps1 first (the old
REM Desktop task, Startup folder), or they start twice.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0install_autostart.ps1"
if errorlevel 1 (echo Failed - did you use "Run as administrator"? & pause & exit /b 1)
echo.
echo Both start at the next reboot. To start them now instead (after stopping
echo the old copies, or reboot):
echo   schtasks /run /tn "PISCO ship NMEA"
echo   schtasks /run /tn "PISCO serial bridges"
pause
