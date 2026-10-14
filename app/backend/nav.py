"""The ship's navigation, from its NMEA feed.

The ship broadcasts NMEA 0183 over UDP on its own network (on MSM147 from
the distribution server 192.168.103.145 to 255.255.255.255; port 30000
carries the full set — ZDA, GGA, GLL, VTG, RMC, HDT — once a second). The
Hub has a leg on that network, so a reader thread per source listens,
keeps the latest fix for the live view, and appends one row per position
fix to a CSV per UTC day under `<DATA_ROOT>/nav/`. That track is what
gives a profile its position afterwards: `position_at(time)` interpolates
it, and the new-workflow form fills latitude, longitude and date from it.

Sources are the `NAV_SOURCES` setting, comma separated: `udp:30000`
listens on a port (broadcast or unicast), `tcp:host:port` reads a TCP
NMEA server line by line. Empty means off.

Sentences are taken by type whatever the talker ($GP, $IN, $GN …):
GGA sets the position and is what a row is written for; RMC and ZDA give
the date (GGA has only the time of day); VTG and HDT give course, speed
and heading. The ship sends a second as ZDA, GGA, GLL, VTG, RMC, HDT, so
those arrive after the position: a fix is held until the next GGA or ZDA
starts a new second, and the row carries that second's own heading and
speed. Lines with a bad checksum are counted and dropped.
"""

import bisect
import csv
import os
import socket
import threading
import time
from collections import Counter, deque
from datetime import datetime, timedelta, timezone

from .models import GlobalSetting

COLUMNS = ["time_utc", "received_utc", "lat", "lon", "quality", "satellites",
           "hdop", "altitude_m", "heading", "course", "speed_kn", "source"]
# Recent rows kept in memory: lookups for the last day never touch a file.
KEEP_ROWS = 90000
# Interpolate between fixes at most this far apart; a gap wider than this
# is a hole in the track, not something to bridge.
MAX_GAP_S = 30


# ---- parsing ---------------------------------------------------------------------

def checksum_ok(line):
    """True for a sentence whose *hh checksum matches (or that has none)."""
    if not line or line[0] not in "$!":
        return False
    body, star, given = line[1:].partition("*")
    if not star:
        return True
    value = 0
    for ch in body:
        value ^= ord(ch)
    try:
        return value == int(given[:2], 16)
    except ValueError:
        return False


def _float(text):
    try:
        return float(text) if text != "" else None
    except ValueError:
        return None


def _coord(value, hemisphere):
    """ddmm.mmmm / dddmm.mmmm plus N/S/E/W to signed decimal degrees."""
    v = _float(value)
    if v is None:
        return None
    degrees = int(v // 100)
    result = degrees + (v - degrees * 100) / 60.0
    return -result if hemisphere in ("S", "W") else result


def _clock(text):
    """hhmmss.ss to seconds of the day."""
    v = _float(text)
    if v is None or len(text) < 6:
        return None
    hh, mm = int(text[0:2]), int(text[2:4])
    return hh * 3600 + mm * 60 + float(text[4:])


def parse(line):
    """(type, fields dict) for the sentences used here, else (type, None)."""
    line = line.strip()
    if not checksum_ok(line):
        return "bad", None
    body = line[1:].split("*")[0]
    parts = body.split(",")
    kind = parts[0][-3:] if len(parts[0]) >= 5 else parts[0]
    f = parts[1:]
    try:
        if kind == "GGA" and len(f) >= 9:
            return kind, {"clock": _clock(f[0]), "lat": _coord(f[1], f[2]),
                          "lon": _coord(f[3], f[4]), "quality": int(f[5] or 0),
                          "satellites": int(f[6] or 0), "hdop": _float(f[7]),
                          "altitude_m": _float(f[8])}
        if kind == "RMC" and len(f) >= 9:
            date = None
            if len(f[8]) == 6:
                date = datetime.strptime(f[8], "%d%m%y").date()
            return kind, {"clock": _clock(f[0]), "valid": f[1] == "A",
                          "lat": _coord(f[2], f[3]), "lon": _coord(f[4], f[5]),
                          "speed_kn": _float(f[6]), "course": _float(f[7]), "date": date}
        if kind == "ZDA" and len(f) >= 4:
            return kind, {"clock": _clock(f[0]),
                          "date": datetime(int(f[3]), int(f[2]), int(f[1])).date()}
        if kind == "VTG" and len(f) >= 5:
            return kind, {"course": _float(f[0]), "speed_kn": _float(f[4])}
        if kind == "HDT" and len(f) >= 1:
            return kind, {"heading": _float(f[0])}
    except (ValueError, IndexError):
        return "bad", None
    return kind, None


class Assembler:
    """Turns a stream of sentences into fixes: dicts in COLUMNS, one per
    GGA, timestamped with the date the feed last gave. `feed` returns a
    fix once its second is complete (the next GGA or ZDA has arrived);
    `pending` is the one still collecting heading and speed."""

    def __init__(self, source):
        self.source = source
        self.date = None
        self.extra = {"heading": None, "course": None, "speed_kn": None}
        self.pending = None

    def feed(self, line, received):
        kind, f = parse(line)
        if f is None:
            return kind, None
        done = None
        if kind in ("GGA", "ZDA") and self.pending is not None:
            done, self.pending = self.pending, None
        if kind in ("ZDA", "RMC") and f.get("date"):
            self.date = f["date"]
        update = {}
        if kind == "RMC":
            update = {k: f[k] for k in ("course", "speed_kn") if f[k] is not None}
        elif kind == "VTG":
            update = {k: v for k, v in f.items() if v is not None}
        elif kind == "HDT" and f["heading"] is not None:
            update = {"heading": f["heading"]}
        elif kind == "GGA" and f["lat"] is not None and f["clock"] is not None:
            self.pending = self._fix(f, received)
        self.extra.update(update)
        if self.pending is not None:
            self.pending.update(update)
        return kind, done

    def _fix(self, gga, received):
        recv_dt = datetime.fromtimestamp(received, timezone.utc)
        date = self.date or recv_dt.date()
        stamp = datetime(date.year, date.month, date.day, tzinfo=timezone.utc) \
            + timedelta(seconds=gga["clock"])
        # Around midnight the time of day can roll over before the next
        # ZDA brings the new date: trust the receive clock to within 12 h.
        if (stamp - recv_dt).total_seconds() < -43200:
            stamp += timedelta(days=1)
        elif (stamp - recv_dt).total_seconds() > 43200:
            stamp -= timedelta(days=1)
        return {"time_utc": stamp.timestamp(), "received_utc": received,
                "lat": gga["lat"], "lon": gga["lon"], "quality": gga["quality"],
                "satellites": gga["satellites"], "hdop": gga["hdop"],
                "altitude_m": gga["altitude_m"], **self.extra, "source": self.source}


# ---- the track log -----------------------------------------------------------------

def _iso(t):
    return datetime.fromtimestamp(t, timezone.utc).isoformat(timespec="milliseconds") \
        .replace("+00:00", "Z")


def _epoch(text):
    return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()


class TrackLog:
    """nav_YYYYMMDD.csv per UTC day of the fix time."""

    def __init__(self, directory):
        self.directory = directory
        self.day = None
        self.file = None
        self.writer = None

    def path(self, day):
        return os.path.join(self.directory, f"nav_{day}.csv")

    def append(self, fix):
        day = datetime.fromtimestamp(fix["time_utc"], timezone.utc).strftime("%Y%m%d")
        if day != self.day:
            if self.file:
                self.file.close()
            os.makedirs(self.directory, exist_ok=True)
            path = self.path(day)
            new = not os.path.exists(path) or os.path.getsize(path) == 0
            self.file = open(path, "a", newline="")
            self.writer = csv.writer(self.file)
            if new:
                self.writer.writerow(COLUMNS)
            self.day = day
        row = dict(fix, time_utc=_iso(fix["time_utc"]), received_utc=_iso(fix["received_utc"]))
        self.writer.writerow(["" if row.get(c) is None else
                              (f"{row[c]:.7f}" if c in ("lat", "lon") else row[c])
                              for c in COLUMNS])
        self.file.flush()

    def read_day(self, day):
        """(times, rows) of one day's file, sorted by time."""
        path = self.path(day)
        if not os.path.isfile(path):
            return [], []
        rows = []
        with open(path, newline="") as f:
            for r in csv.DictReader(f):
                try:
                    rows.append((_epoch(r["time_utc"]), float(r["lat"]), float(r["lon"]), r))
                except (KeyError, ValueError):
                    continue
        rows.sort(key=lambda r: r[0])
        return [r[0] for r in rows], rows


# ---- the service ------------------------------------------------------------------

class NavService:
    """Reader threads for the configured sources, the latest fix, and
    lookups into the track."""

    def __init__(self, app):
        self.app = app
        with app.app_context():
            sources = GlobalSetting.get("NAV_SOURCES", "") or ""
            directory = GlobalSetting.get("NAV_LOG_DIR", "") or os.path.join(
                GlobalSetting.get("DATA_ROOT", os.path.join(app.config["HUB_ROOT"], "data")), "nav")
        self.sources = [s.strip() for s in str(sources).split(",") if s.strip()]
        self.log = TrackLog(directory)
        self.lock = threading.Lock()
        self.latest = None
        self.recent = deque(maxlen=KEEP_ROWS)
        self.status = {s: {"state": "starting", "error": None, "last_line": None,
                           "counts": Counter()} for s in self.sources}
        for source in self.sources:
            threading.Thread(target=self._run, args=(source,), daemon=True,
                             name=f"nav-{source}").start()
        app.extensions["nav"] = self

    # ---- reading ----
    def _run(self, source):
        status = self.status[source]
        while True:
            try:
                kind, _, rest = source.partition(":")
                if kind == "udp":
                    self._read_udp(source, int(rest))
                elif kind == "tcp":
                    host, _, port = rest.rpartition(":")
                    self._read_tcp(source, host, int(port))
                else:
                    status.update(state="error", error=f"unknown source type '{kind}'")
                    return
            except (OSError, ValueError) as e:
                status.update(state="error", error=str(e))
            time.sleep(5)

    def _read_udp(self, source, port):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("", port))
        sock.settimeout(10)
        assembler = Assembler(source)
        self.status[source].update(state="listening", error=None)
        with sock:
            while True:
                try:
                    data, _ = sock.recvfrom(4096)
                except socket.timeout:
                    self.status[source]["state"] = "silent"
                    continue
                for line in data.decode("ascii", "replace").splitlines():
                    self._line(source, assembler, line)

    def _read_tcp(self, source, host, port):
        assembler = Assembler(source)
        with socket.create_connection((host, port), timeout=10) as sock:
            self.status[source].update(state="connected", error=None)
            buffer = b""
            while True:
                try:
                    chunk = sock.recv(4096)
                except socket.timeout:
                    self.status[source]["state"] = "silent"
                    continue
                if not chunk:
                    raise OSError("connection closed")
                buffer += chunk
                *lines, buffer = buffer.split(b"\n")
                for line in lines:
                    self._line(source, assembler, line.decode("ascii", "replace"))

    def _line(self, source, assembler, line):
        line = line.strip()
        if not line:
            return
        received = time.time()
        kind, fix = assembler.feed(line, received)
        status = self.status[source]
        status["counts"][kind] += 1
        status["last_line"] = received
        if status["state"] == "silent":
            status["state"] = "listening"
        if assembler.pending is not None:
            with self.lock:
                self.latest = dict(assembler.pending)    # live view: no need to wait
        if fix is None:
            return
        with self.lock:
            self.recent.append(fix)
        try:
            self.log.append(fix)
        except OSError as e:
            status["error"] = f"cannot write the track log: {e}"

    # ---- reading back ----
    def snapshot(self):
        with self.lock:
            latest = dict(self.latest) if self.latest else None
        now = time.time()
        if latest:
            latest["age_s"] = round(now - latest["received_utc"], 1)
            latest["time_utc"] = _iso(latest["time_utc"])
            latest["received_utc"] = _iso(latest["received_utc"])
        return {
            "latest": latest,
            "log_dir": self.log.directory,
            "sources": {s: {"state": st["state"], "error": st["error"],
                            "last_line_age_s": None if st["last_line"] is None
                            else round(now - st["last_line"], 1),
                            "sentences": dict(st["counts"])}
                        for s, st in self.status.items()},
        }

    def position_at(self, when):
        """The ship's position at a time (epoch seconds), interpolated
        between the fixes around it; None when the track has no fix
        within MAX_GAP_S of it."""
        with self.lock:
            recent = list(self.recent)
        if recent and recent[0]["time_utc"] <= when <= recent[-1]["time_utc"] + MAX_GAP_S:
            times = [r["time_utc"] for r in recent]
            rows = [(r["time_utc"], r["lat"], r["lon"], r) for r in recent]
        else:
            day = datetime.fromtimestamp(when, timezone.utc)
            times, rows = [], []
            for d in (day - timedelta(days=1), day, day + timedelta(days=1)):
                t, r = self.log.read_day(d.strftime("%Y%m%d"))
                times += t
                rows += r
        return interpolate(times, rows, when)


def interpolate(times, rows, when):
    if not times:
        return None
    i = bisect.bisect_left(times, when)
    before = rows[i - 1] if i > 0 else None
    after = rows[i] if i < len(rows) else None
    if after is not None and after[0] == when:
        before = after
    if before is not None and after is not None and after[0] - before[0] <= MAX_GAP_S:
        span = after[0] - before[0]
        w = 0.0 if span == 0 else (when - before[0]) / span
        lat = before[1] + (after[1] - before[1]) * w
        lon = before[2] + (after[2] - before[2]) * w
        gap = max(abs(when - before[0]), abs(after[0] - when))
    else:
        nearest = min((r for r in (before, after) if r is not None),
                      key=lambda r: abs(r[0] - when))
        gap = abs(nearest[0] - when)
        if gap > MAX_GAP_S:
            return None
        lat, lon = nearest[1], nearest[2]
    return {"time_utc": _iso(when), "lat": round(lat, 6), "lon": round(lon, 6),
            "nearest_fix_s": round(gap, 2),
            "date": datetime.fromtimestamp(when, timezone.utc).strftime("%Y-%m-%d")}


def parse_time(text):
    """Epoch seconds from an ISO 8601 string (UTC unless it says otherwise)
    or a number."""
    try:
        return float(text)
    except (TypeError, ValueError):
        pass
    dt = datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def service(app):
    return app.extensions.get("nav")
