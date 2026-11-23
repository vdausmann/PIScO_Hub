# LADCP control and live view

The Hub runs the two lowered ADCPs (RDI WorkHorse, one looking down, one up)
on the CTD rosette: it programs them, starts them, shows what they measure
during the cast, and downloads and files their data afterwards. It replaces
the AutoIt/TeraTerm `ladcp_tool` (v1.10.2) and builds on Marco Schulz's
`ladcp_live`.

Page: **Hardware → LADCP** (`/hardware-control/ladcp/`).

## Per profile

1. **Erase** (when the recorders are getting full). The Hub refuses if a file
   on an instrument has not been downloaded, unless you confirm.
2. **Start**. Type the deck-box voltage if you read one; it goes into the
   battery log. The Hub:
   1. checks its clock against the NTP server, if one is set;
   2. breaks into both ports and reads each instrument's configuration and
      recorder;
   3. makes the one looking `primary_direction` (DOWN) the primary;
   4. programs the secondary first and the primary last, with the sequence
      shown under *Setup → Show commands*;
   5. starts both.

   If only one ADCP answers, it asks whether to start that one alone.
3. **Watch** the Live tab. Both instruments are drawn against distance from
   the package, the up-looker above the line and the down-looker below:
   - the echo-intensity waterfall;
   - the latest profile for each beam;
   - heading, pitch, roll, temperature and transmit voltage;
   - the **distance to the bottom** (down-looker) or to the surface
     (up-looker);
   - a depth estimate integrated from the vertical speed. It guides the
     winch; it is not a pressure.
4. **Stop & download** after the cast. The Hub:
   1. breaks the primary, then the secondary;
   2. YMODEMs each one's last file;
   3. checks it (ensembles, time span, voltage, depth reached, several files
      for one profile);
   4. stores it and copies it to the backup path;
   5. adds a line to the battery log.

Operations run one at a time and show their steps as they go. If something
would repeat or destroy work (starting twice, erasing undownloaded files), the
Hub stops and asks; *Yes, go ahead* repeats the action with confirmation.

## Files

Under `data_dir` (default `<DATA_ROOT>/ladcp/<cruise_id>`):

| Path | Contents |
|---|---|
| `NNN/NNNDN000.000`, `NNN/NNNUP000.000` | the recorder files, as downloaded |
| `NNN/serial/*.bin` | the ensembles that came up the cable during the cast: a second copy, valid ensembles only |
| `logs/serial_<instrument>_<day>.log` | every byte exchanged with each instrument (the troubleshooting record) |
| `battery.log` | one JSON line per download, plus battery swaps |

If `NNN/` already holds files, a new download goes to `NNN_1/` and so on,
as the old tool did. The profile counter, the totals since the last battery
swap and the deployment in progress live in `hardware_control/state/ladcp.json`.

## Configuration

Everything is set on the **Setup** tab and saved to `hardware_control/ladcp.json`
(the global setting `LADCP_CONFIG_FILE`; not under version control, since it
describes the ship). The ADCP setup keeps the parameter names of
`ladcp_tool.ini`, and its defaults are MSM142's values.

**Ports** default to the ship's serial server: a Raspberry Pi
(`10.11.15.10`) running ser2net, which offers the deck box's two lines with
RFC 2217 on TCP 3001 and 3002 (`rfc2217://10.11.15.10:3001?ign_set_control`,
break `hardware`). Keep `?ign_set_control`: ser2net answers a break request
differently from what pyserial expects, and without the option every break
fails with "remote rejected value for option 'control'". A port serves one client at a time, so the Windows VM's
hub4com/com0com bridges for those two ports (COM11/COM12) must not be in use
while the Hub has them. The CTD and the other instruments stay on Windows.
Other set-ups work too, because ports are pyserial URLs:

| Where the deck box's USB–RS485 plugs in | Port | Break |
|---|---|---|
| The Hub's own host | `/dev/ttyUSB0` (better: `/dev/serial/by-id/…`) | `hardware` |
| Another computer running an RFC 2217 server (`ser2net`, pyserial's `rfc2217_server.py`) | `rfc2217://host:port` | `hardware` |
| A network serial server (Moxa NPort etc.) in TCP-server mode | `socket://host:port` | `===` (a raw socket cannot carry a break) |

`live_output` sets the serial-output digit of `CF` (the old tool used
`CF11101`, output off). The live view needs it on, and the line has to stay
connected during the cast.

After a Hub restart during a cast, the Hub reopens the ports and keeps
recording without sending anything (`reattach_on_restart`).

## Developing without instruments

```
python -m scripts.ladcp_simulator --bottom 800 --speed 1
```

This runs two simulated WorkHorses with RFC 2217, like the Pi, on
`rfc2217://127.0.0.1:7001` (down) and `:7002` (up), with break `hardware`.
Enter those ports on the Setup tab. Over RFC 2217 the simulator also
imitates the hardware break and the baud rate: a client at the wrong speed
gets garbage. `--protocol socket` gives a raw TCP line instead, with the
software break `===`. They share one cast: they go down
at `speed` to 20 m above `bottom` and come back up. Tests:
`python -m pytest tests/ladcp`.

## Not yet verified against real instruments

The simulator's replies follow the WorkHorse manual and the old tool's
parsers, not a real instrument's transcript. Check these at the first real
deployment:

- the wording of `PS0` and `RR` (serial number, orientation, file list, free
  bytes);
- YMODEM against the real firmware, and against ser2net's handling of
  binary data and breaks;
- the baud switch for downloads over the cable;
- the bottom-echo threshold on real echo intensities;
- the transmit-voltage scale (0.592157 V/count was commented out in the old
  tool, which logged raw counts; the summaries keep both values).


## Profile numbers are CTD casts

With DSHIP connected (`DSHIP_URL`), the profile number is the CTD cast
number: the page's *Next profile* shows the CTD operation DSHIP has open
(`= CTD cast · MSM147_5-1`), or the number the next one will get when
DSHIP has none yet (*predicted*). **Start** reads DSHIP once more and
programs that number, unless a number is given explicitly; the DSHIP
operation is kept with the deployment and as `<profile>/dship.json`.
Every CTD operation counts, aborted ones and ones that never went in too,
so numbers can be skipped. Without DSHIP the profile counter applies as
before.

## Yoyo casts

Tick **Yoyo cast** before Start (or while deployed) and the Hub watches
the depth estimate for turns: a heave of at least 150 m that takes back
most of the depth gained since the last turn, followed by lowering again
by 30 m. It then suggests a split, with the turn marked on the depth plot.
**Split here** (or **Split: new profile** at any time) starts the next
profile; **Not a new cast** looks for the next turn instead.

A split changes nothing on the instruments. The serial capture continues
in `<new profile>/serial/`; the recorder keeps writing the one file named
after the profile the cast started with, and Stop & download stores it
in that folder. The new profile's `dship.json` says where it was split
from (`split_from`, `turn_utc`) and where the recorder file is
(`recorder_file_in`).

The new number is DSHIP's next CTD operation if one exists by then. If
DSHIP still shows the same operation, the page asks first: the split then
becomes the current cast + 1, and `<operation>+1` is added to
`DSHIP_CAST_CORRECTIONS` so later casts are numbered one higher as well.
Confirm only if the CTD really started a new cast (a new hex file); this
is what went wrong on MSM147 at 4-13 (hex 018 + 019 in one operation),
which shifted every later LADCP profile by one.

