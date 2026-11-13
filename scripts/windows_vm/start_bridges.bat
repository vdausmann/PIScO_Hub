@echo off
REM ==========================================================================
REM  Virtual COM ports on this Windows VM for the serial ports of the
REM  Raspberry Pi serial server, using hub4com + com0com.
REM
REM  The Pi (ser2net, RFC 2217 = serial port control over telnet) offers each
REM  physical port on a TCP port 3001..3004. com0com creates virtual port
REM  pairs CNCAx <-> CNCBx: programs open the A side (named COM11..COM14),
REM  hub4com holds the B side and forwards it over the network to the Pi.
REM
REM  IMPORTANT: a Pi port serves ONE client at a time. Ports read directly by
REM  another machine must not be bridged here.
REM
REM    Pi port  Baud    Windows  Instrument                     Used by
REM    3001     115200  COM11    LADCP (cable A)                PIScO Hub
REM    3002     115200  COM12    LADCP (cable B)                PIScO Hub
REM    3003     19200   COM13    ? (fill in)                    this VM
REM    3004     4800    COM14    ? (fill in, NMEA?)             this VM
REM    UDP 30000 (ship) -   COM15    Ship NMEA (via udp2com.ps1)    this VM
REM
REM  Started at logon by the scheduled task "PISCO serial bridges"
REM  (install_autostart.bat). Progress is written to C:\nmea\startup.log.
REM ==========================================================================
setlocal
set PI_IP=10.11.15.10
set HUB4COM=C:\hub4com-2.1.0.0-386\hub4com.exe
set LOG=C:\nmea\startup.log

echo %date% %time% starting >> "%LOG%"

REM --- wait until the Pi answers ---------------------------------------------
REM  At logon the network may not be up yet; hub4com would fail once and exit.
REM  find "TTL=" so that "destination unreachable" replies do not count.
set TRIES=0
:wait_pi
ping -n 1 -w 1000 %PI_IP% | find "TTL=" >nul
if not errorlevel 1 goto pi_ok
set /a TRIES+=1
if %TRIES% GEQ 60 (
  echo %date% %time% Pi %PI_IP% not reachable after 5 min, starting anyway >> "%LOG%"
  goto pi_ok
)
timeout /t 5 /nobreak >nul
goto wait_pi
:pi_ok
echo %date% %time% Pi reachable, starting bridges >> "%LOG%"

REM --- COM11 / COM12: LADCP, read by the PIScO Hub directly ------------------
REM  The Hub connects to rfc2217://10.11.15.10:3001 and :3002 itself. These two
REM  bridges are skipped so they do not take the ports away from it. To use the
REM  old Windows LADCP tool again, delete the "goto" line below - and stop
REM  using the ports in the Hub.
goto skip_ladcp

start "COM11-Bridge" %HUB4COM% --baud=115200 --octs=off ^
  --create-filter=escparse,com,parse ^
  --create-filter=purge,com,purge ^
  --create-filter=pinmap,com,pinmap:"--rts=cts --dtr=dsr" ^
  --create-filter=linectl,com,lc:"--br=local --lc=local" ^
  --add-filters=0:com ^
  --create-filter=telnet,tcp,telnet:"--comport=client" ^
  --create-filter=pinmap,tcp,pinmap:"--rts=cts --dtr=dsr --break=break" ^
  --create-filter=linectl,tcp,lc:"--br=remote --lc=remote" ^
  --add-filters=1:tcp ^
  \\.\CNCB0 --use-driver=tcp *%PI_IP%:3001

start "COM12-Bridge" %HUB4COM% --baud=115200 --octs=off ^
  --create-filter=escparse,com,parse ^
  --create-filter=purge,com,purge ^
  --create-filter=pinmap,com,pinmap:"--rts=cts --dtr=dsr" ^
  --create-filter=linectl,com,lc:"--br=local --lc=local" ^
  --add-filters=0:com ^
  --create-filter=telnet,tcp,telnet:"--comport=client" ^
  --create-filter=pinmap,tcp,pinmap:"--rts=cts --dtr=dsr --break=break" ^
  --create-filter=linectl,tcp,lc:"--br=remote --lc=remote" ^
  --add-filters=1:tcp ^
  \\.\CNCB1 --use-driver=tcp *%PI_IP%:3002

:skip_ladcp

REM --- COM13 <-> Pi port 3003, 19200 baud ------------------------------------
start "COM13-Bridge" %HUB4COM% --baud=19200 --octs=off ^
  --create-filter=escparse,com,parse ^
  --create-filter=purge,com,purge ^
  --create-filter=pinmap,com,pinmap:"--rts=cts --dtr=dsr" ^
  --create-filter=linectl,com,lc:"--br=local --lc=local" ^
  --add-filters=0:com ^
  --create-filter=telnet,tcp,telnet:"--comport=client" ^
  --create-filter=pinmap,tcp,pinmap:"--rts=cts --dtr=dsr --break=break" ^
  --create-filter=linectl,tcp,lc:"--br=remote --lc=remote" ^
  --add-filters=1:tcp ^
  \\.\CNCB2 --use-driver=tcp *%PI_IP%:3003

REM --- COM14 <-> Pi port 3004, 4800 baud -------------------------------------
start "COM14-Bridge" %HUB4COM% --baud=4800 --octs=off ^
  --create-filter=escparse,com,parse ^
  --create-filter=purge,com,purge ^
  --create-filter=pinmap,com,pinmap:"--rts=cts --dtr=dsr" ^
  --create-filter=linectl,com,lc:"--br=local --lc=local" ^
  --add-filters=0:com ^
  --create-filter=telnet,tcp,telnet:"--comport=client" ^
  --create-filter=pinmap,tcp,pinmap:"--rts=cts --dtr=dsr --break=break" ^
  --create-filter=linectl,tcp,lc:"--br=remote --lc=remote" ^
  --add-filters=1:tcp ^
  \\.\CNCB3 --use-driver=tcp *%PI_IP%:3004

REM --- COM15: ship NMEA from the UDP broadcast --------------------------------
REM  Not started here: C:\nmea\udp2com.ps1 runs as its own scheduled task
REM  "PISCO ship NMEA" (at startup, SYSTEM). It writes the datagrams into COM10;
REM  programs read the other end of the com0com pair, COM15. The ship feed has
REM  no depth sentences: untick "NMEA depth data added" in the Seasave .xmlcon.

echo %date% %time% bridges started >> "%LOG%"
endlocal
