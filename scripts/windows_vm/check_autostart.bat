@echo off
REM Lists everything that starts automatically and mentions the bridges.
echo === Scheduled tasks ===
powershell -NoProfile -Command "Get-ScheduledTask | Where-Object { ($_.Actions | Out-String) -match 'nmea|hub4com|bridge|udp2com|Desktop|\.bat' } | ForEach-Object { '{0}{1}  [{2}]  runs as {3} ({4})  -> {5}' -f $_.TaskPath, $_.TaskName, $_.State, $_.Principal.UserId, $_.Principal.LogonType, (($_.Actions | ForEach-Object { $_.Execute + ' ' + $_.Arguments }) -join '; ') }"
echo.
echo === Startup folders and Run keys ===
powershell -NoProfile -Command "Get-CimInstance Win32_StartupCommand | Format-Table -AutoSize Name, Command, Location"
echo.
echo === Running now ===
tasklist | findstr /i "hub4com powershell"
echo.
echo === Last startup log ===
if exist C:\nmea\startup.log (powershell -NoProfile -Command "Get-Content C:\nmea\startup.log -Tail 10") else (echo C:\nmea\startup.log does not exist - start_bridges.bat has not run)
pause
