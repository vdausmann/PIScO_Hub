# Creates the two scheduled tasks for this VM. Started by install_autostart.bat.
#
#  "PISCO ship NMEA"       udp2com.ps1 (UDP 30000 -> COM10/COM15), at startup as
#                          SYSTEM: no window needed, retries by itself, so it does
#                          not matter that the network comes up later. Restarted
#                          if it ever exits; no time limit.
#  "PISCO serial bridges"  start_bridges.bat (hub4com, COM13/COM14), 30 s after
#                          the current user logs on, in that session, with
#                          highest privileges.
$ErrorActionPreference = 'Stop'
$user = "$env:USERDOMAIN\$env:USERNAME"
$noLimit = [TimeSpan]::Zero

$a = New-ScheduledTaskAction -Execute 'powershell.exe' -WorkingDirectory 'C:\nmea' `
    -Argument '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File C:\nmea\udp2com.ps1'
$t = New-ScheduledTaskTrigger -AtStartup
$p = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
$s = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit $noLimit -MultipleInstances IgnoreNew `
    -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName 'PISCO ship NMEA' -Action $a -Trigger $t -Principal $p -Settings $s -Force | Out-Null
Write-Host 'Created "PISCO ship NMEA" (at startup, SYSTEM)'

$a = New-ScheduledTaskAction -Execute 'C:\nmea\start_bridges.bat' -WorkingDirectory 'C:\nmea'
$t = New-ScheduledTaskTrigger -AtLogOn -User $user
$t.Delay = 'PT30S'
$p = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Highest
$s = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit $noLimit -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName 'PISCO serial bridges' -Action $a -Trigger $t -Principal $p -Settings $s -Force | Out-Null
Write-Host "Created ""PISCO serial bridges"" (at logon of $user, 30 s delay)"
