"""Live sensor values from the instrument controllers, and commands back.

Each controller (the PISCO NUC first; the other cameras are stubs in the
same shape) rewrites a one-line text file of `Pi-Name_value` tokens once a
second and polls another for commands, the way Tim's Unified Controller
does. The Hub reaches those files over SSH: one thread per enabled device
reads the sensor file every tick, keeps the latest values in memory for the
hardware-control page, and appends a row to a CSV log that the profile
preparer can take as its sensor log. A command is the whole command line
re-rendered and copied over, only when something actually changed — the
controller notices by the file's mtime.

Time: the controller does not stamp its sensor line yet. Every reading
carries the Hub's receive time; when a line starts with a timestamp
(ISO 8601 or epoch seconds, then whitespace or a comma), that is taken as
the measurement time and the log records both.

Configured in `hardware_control/devices.json` (see DEVICES_FILE setting).
"""

import csv
import json
import os
import re
import subprocess
import tempfile
import threading
import time
from datetime import datetime, timezone

from . import machines
from .models import GlobalSetting

TOKEN = re.compile(r"^[A-Za-z]{1,4}-[A-Za-z0-9-]+$")      # Pi-ExPres, Pi-Shutdown-now
CONTROL_DIR = "/tmp/pisco-hub-ssh"


# ---- the line format ------------------------------------------------------------

def parse_timestamp(text):
    """Epoch seconds from an ISO stamp or a number, else None."""
    text = text.strip()
    try:
        value = float(text)
        return value / 1000.0 if value > 1e12 else (value if value > 9.7e8 else None)
    except ValueError:
        pass
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def parse_line(line):
    """(measured epoch seconds or None, {shorthand: raw value}) from one
    controller line. An optional timestamp leads, separated by whitespace
    or a comma; then `Name_value` pairs joined by `_`. Shorthands begin
    with a `Xx-` piece and may themselves contain underscores
    (`Pi-LED_DCDC_con_1`), values never do: so a key runs from its `Xx-`
    piece to just before the next one, and its last piece is the value."""
    line = line.strip()
    measured = None
    head = re.split(r"[\s,]+", line, maxsplit=1)
    if len(head) == 2 and not TOKEN.match(head[0]):
        measured = parse_timestamp(head[0])
        if measured is not None:
            line = head[1]
    parts = line.split("_")
    starts = [i for i, piece in enumerate(parts) if TOKEN.match(piece)]
    values = {}
    for n, i in enumerate(starts):
        k = starts[n + 1] if n + 1 < len(starts) else len(parts)
        if k - i < 2:
            values[parts[i]] = ""          # a key with no value
        else:
            values["_".join(parts[i:k - 1])] = parts[k - 1]
    return measured, values


def render_line(values):
    return "_".join(f"{k}_{v}" for k, v in values.items())


# ---- one device ------------------------------------------------------------------

class DeviceLink:
    """SSH to one controller host. The connection is multiplexed
    (ControlMaster), so a read every second costs a channel, not a
    handshake. Files move by scp, which works the same against Windows
    OpenSSH; only the path spelling differs."""

    def __init__(self, name, config):
        self.name = name
        self.config = config
        self.host = config["host"]
        self.user = config.get("user")
        self.windows = str(config.get("os", "")).lower().startswith("win")
        os.makedirs(CONTROL_DIR, exist_ok=True)
        self.control = os.path.join(CONTROL_DIR, "%C")

    @property
    def target(self):
        return f"{self.user}@{self.host}" if self.user else self.host

    # The connection is opened once as a detached master and every read
    # or write is a channel on it. Opening it *implicitly* (ControlMaster=
    # auto) from a call whose output is captured hangs that call: the
    # forked master inherits the pipes and keeps them open.
    def _mux(self):
        return ["-o", "ControlMaster=no", "-o", f"ControlPath={self.control}",
                "-o", "ConnectTimeout=5"]

    def ensure_master(self):
        check = subprocess.run(["ssh", "-o", f"ControlPath={self.control}", "-O", "check", self.target],
                               stdin=subprocess.DEVNULL, capture_output=True, timeout=5)
        if check.returncode == 0:
            return
        subprocess.run(["ssh", *machines.SSH_OPTIONS, "-o", "ControlMaster=yes",
                        "-o", f"ControlPath={self.control}", "-o", "ControlPersist=300",
                        "-o", "ConnectTimeout=8", "-o", "ServerAliveInterval=15", "-fN", self.target],
                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, timeout=20)

    def _run(self, argv, timeout):
        """Run once; if the master was gone, bring it up and run again."""
        for attempt in (0, 1):
            result = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True,
                                    text=True, timeout=timeout)
            if result.returncode == 0:
                return result
            if attempt == 0:
                self.ensure_master()
        raise OSError(result.stderr.strip() or f"{argv[0]} exit {result.returncode}")

    def _scp(self, extra):
        return ["scp", *machines.SSH_OPTIONS, *self._mux(), "-q", *extra]

    def _remote(self, remote_path):
        # scp takes `host:C:/Users/...` for a Windows path.
        return remote_path.replace("\\", "/") if self.windows else remote_path

    def read_file(self, remote_path, timeout=5):
        """(text, remote mtime). Copied with `scp -p`, so the file's own
        modification time comes along: the controller rewrites it every
        tick, and a moving mtime says it is alive whatever the values do."""
        fd, local = tempfile.mkstemp(prefix="hw-read-")
        os.close(fd)
        try:
            self._run(self._scp(["-p", f"{self.target}:{self._remote(remote_path)}", local]), timeout)
            with open(local, encoding="utf-8", errors="replace") as f:
                return f.read(), os.path.getmtime(local)
        finally:
            os.unlink(local)

    def write_text(self, remote_path, content, timeout=10):
        """Copy content over the remote file in place. The controller
        tolerates a torn read of a half-written file and retries."""
        fd, local = tempfile.mkstemp(prefix="hw-cmd-")
        try:
            with os.fdopen(fd, "w", newline="\n") as f:
                f.write(content)
            self._run(self._scp([local, f"{self.target}:{self._remote(remote_path)}"]), timeout)
        finally:
            os.unlink(local)


class DeviceState:
    """What the page and the log see for one device."""

    def __init__(self, name, config):
        self.name = name
        self.config = config
        self.lock = threading.Lock()
        self.values = {}
        self.measured = None           # controller's stamp, epoch seconds
        self.received = None           # Hub's, epoch seconds
        self.changed = None            # Hub time the file's mtime last moved
        self.mtime = None              # the file's mtime on the controller
        self.raw = None
        self.error = None
        self.commands = None           # full command state, or None until read

    def snapshot(self):
        with self.lock:
            stale_after = float(self.config.get("stale_after_s", 5))
            now = time.time()
            # Liveness by the file's mtime moving, not by clocks: the
            # controller rewrites it every tick, and its clock may be off.
            stale = self.changed is None or now - self.changed > stale_after
            return {
                "values": dict(self.values),
                "meta": {
                    "enabled": bool(self.config.get("enabled")),
                    "host": self.config.get("host"),
                    "measured": _iso(self.measured), "received": _iso(self.received),
                    "changed": _iso(self.changed), "stale": stale, "error": self.error,
                    "clock_offset_s": (round(self.received - self.measured, 3)
                                       if self.measured and self.received else None),
                },
            }


def _iso(seconds):
    if seconds is None:
        return None
    return datetime.fromtimestamp(seconds, timezone.utc).isoformat(timespec="milliseconds")


# ---- the poller --------------------------------------------------------------------

class HardwarePoller:
    """One reader thread per enabled device; commands go out from the
    request thread that asked for them."""

    def __init__(self, app):
        self.app = app
        self.devices = {}
        self.links = {}
        with app.app_context():
            self.devices_file = self._resolve(
                GlobalSetting.get("HARDWARE_DEVICES_FILE", "hardware_control/devices.json"))
            self.log_root = os.path.join(
                GlobalSetting.get("DATA_ROOT", os.path.join(app.config["HUB_ROOT"], "data")),
                "sensor_logs")
        self.state_dir = os.path.join(app.config["HUB_ROOT"], "hardware_control", "state")
        # The hardware-control page reads the last line of a data/*.dat
        # file in the controller's own format; the latest line lands there.
        self.dat_dir = os.path.join(app.config["HUB_ROOT"], "data")
        for name, config in self._load_devices().items():
            self.devices[name] = DeviceState(name, config)
            if config.get("enabled") and config.get("host"):
                self.links[name] = DeviceLink(name, config)
                threading.Thread(target=self._run, args=(name,), daemon=True,
                                 name=f"hw-poll-{name}").start()
        app.extensions["hardware_poller"] = self

    def _resolve(self, path):
        return path if os.path.isabs(path) else os.path.join(self.app.config["HUB_ROOT"], path)

    def _load_devices(self):
        if not os.path.isfile(self.devices_file):
            return {}
        with open(self.devices_file) as f:
            devices = json.load(f)
        return {name: cfg for name, cfg in devices.items() if isinstance(cfg, dict)}

    # ---- reading ----
    def _run(self, name):
        state, link = self.devices[name], self.links[name]
        interval = float(state.config.get("interval_s", 1.0))
        log = SensorLogWriter(os.path.join(self.log_root, name), name)
        try:
            link.ensure_master()
        except (OSError, subprocess.TimeoutExpired):
            pass                       # the first read reports it
        while True:
            started = time.time()
            try:
                raw, mtime = link.read_file(state.config["sensor_file"])
                self._ingest(state, raw, mtime, started, log)
            except (OSError, subprocess.TimeoutExpired) as e:
                with state.lock:
                    state.error = str(e)[:300]
                    state.received = started
            self._write_state(state)
            time.sleep(max(0.0, interval - (time.time() - started)))

    def _ingest(self, state, raw, mtime, received, log):
        line = raw.strip().splitlines()[-1] if raw.strip() else ""
        measured, values = parse_line(line)
        with state.lock:
            if mtime != state.mtime:
                state.changed = received
            state.mtime = mtime
            state.raw = line
            state.values = values
            state.measured = measured
            state.received = received
            state.error = None if values else "sensor file is empty or has no Pi- tokens"
        if values:
            log.append(received, measured, values)

    def _write_state(self, state):
        os.makedirs(self.state_dir, exist_ok=True)
        path = os.path.join(self.state_dir, f"{state.name}.json")
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(state.snapshot(), f, indent=1)
        os.replace(tmp, path)
        with state.lock:
            raw = state.raw
        if raw:
            os.makedirs(self.dat_dir, exist_ok=True)
            dat = os.path.join(self.dat_dir, f"{state.name}.dat")
            with open(dat + ".tmp", "w") as f:
                f.write(raw + "\n")
            os.replace(dat + ".tmp", dat)

    # ---- for the routes ----
    def live(self):
        """{device: {shorthand: value}} plus `_meta` per device — what the
        page's displaySensorData() consumes."""
        out, meta = {}, {}
        for name, state in self.devices.items():
            snap = state.snapshot()
            out[name] = snap["values"]
            meta[name] = snap["meta"]
        out["_meta"] = meta
        return out

    def command_state(self, name):
        state = self.devices[name]
        # Treat the controller's command file as the source of truth. The
        # in-memory copy is only a cache for snapshots and must not hide
        # changes made directly on the controller.
        self._read_commands(name)
        with state.lock:
            return dict(state.commands or {})

    def _read_commands(self, name):
        """The controller wants the whole field set in one line, so the
        current line on the device is the starting point — never a stub."""
        state, link = self.devices[name], self.links[name]
        raw, _ = link.read_file(state.config["command_file"])
        _, values = parse_line(raw.strip().splitlines()[-1] if raw.strip() else "")
        if not values:
            raise OSError(f"{name}: the command file on {link.host} has no Pi- tokens")
        with state.lock:
            state.commands = values

    def send(self, name, changes):
        """Merge changes into the device's command line and copy it over,
        if anything changed. Returns the command state afterwards."""
        if name not in self.links:
            raise OSError(f"{name} is not an enabled device")
        state, link = self.devices[name], self.links[name]
        # Merge against the latest file contents so external edits to the
        # controller's command file are preserved.
        self._read_commands(name)
        with state.lock:
            merged = dict(state.commands)
            for key, value in changes.items():
                if not TOKEN.match(key.split("_")[0]) and not TOKEN.match(key):
                    raise ValueError(f"'{key}' is not a command shorthand")
                merged[key] = _fmt(value)
            if merged == state.commands:
                return dict(merged)
            line = render_line(merged)
        link.write_text(state.config["command_file"], line + "\n")
        with state.lock:
            state.commands = merged
        self._log_command(name, changes, line)
        return dict(merged)

    def _log_command(self, name, changes, line):
        directory = os.path.join(self.log_root, name)
        os.makedirs(directory, exist_ok=True)
        with open(os.path.join(directory, "commands.log"), "a") as f:
            f.write(f"{_iso(time.time())}\t{json.dumps(changes)}\t{line}\n")


def _fmt(value):
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


# ---- the log ---------------------------------------------------------------------

class SensorLogWriter:
    """One CSV per device and UTC day, in the controller's own layout
    (`TS_ISO` then the `Pi-` columns) plus `TS_RECEIVED`, so the profile
    preparer reads it like the controller's sensorlog. A row per tick,
    changed or not — the preparer wants a sample per second, and a
    stalled controller shows as repeated rows rather than silence."""

    def __init__(self, directory, name):
        self.directory = directory
        self.name = name
        self.day = None
        self.file = None
        self.writer = None
        self.columns = None

    def append(self, received, measured, values):
        day = datetime.fromtimestamp(received, timezone.utc).strftime("%Y%m%d")
        columns = list(values)
        if self.file is None or day != self.day or columns != self.columns:
            self._open(day, columns)
        self.writer.writerow({
            "TS_ISO": _iso(measured if measured is not None else received),
            "TS_RECEIVED": _iso(received), **values})
        self.file.flush()

    def _open(self, day, columns):
        if self.file:
            self.file.close()
        os.makedirs(self.directory, exist_ok=True)
        path = os.path.join(self.directory, f"sensorlog_{self.name}_{day}.csv")
        fieldnames = ["TS_ISO", "TS_RECEIVED", *columns]
        new = not os.path.exists(path) or os.path.getsize(path) == 0
        if not new:
            with open(path, newline="") as f:
                existing = next(csv.reader(f), None)
            if existing != fieldnames:
                # The controller's column set changed mid-day: keep the old
                # file whole and continue in a numbered one.
                n = 1
                while os.path.exists(path):
                    path = os.path.join(self.directory, f"sensorlog_{self.name}_{day}_{n}.csv")
                    n += 1
                new = True
        self.file = open(path, "a", newline="")
        self.writer = csv.DictWriter(self.file, fieldnames=fieldnames, extrasaction="ignore",
                                     restval="")
        if new:
            self.writer.writeheader()
        self.day, self.columns = day, columns
