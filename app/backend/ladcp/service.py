"""The LADCP procedure as the Hub runs it: one background operation at a
time (detect, erase, start, stop & download), the live stream while the
instruments are deployed, and the state that has to survive a restart.

Per profile, as with ladcp_tool v1.10.2:

  detect     break into each port, read PS0 and the recorder directory;
             the one looking `primary_direction` (DOWN) is the primary
  erase      `RE ErAsE` — refused while files on an instrument have not
             been downloaded, unless forced
  start      check the clock, program the secondary first and the
             primary last (it sends the sync pulses), start both; the
             ports stay open and the ensembles they send are recorded and
             shown
  stop       break the primary first, then the secondary
  download   `RY` the profile's file from each, check it, store it under
             <data_dir>/<profile>/, copy it to the backup path, log the
             battery use

Layout under data_dir (default <DATA_ROOT>/ladcp/<cruise_id>):
  <NNN>/            the downloaded recorder files (NNNDN000.000, NNNUP000.000)
  <NNN>/serial/     what came up the cable during the cast, per instrument
  logs/             serial transcripts, one file per instrument and day
  battery.log       one line per download, like the old tool's
"""

import json
import os
import shutil
import socket
import struct
import threading
import time
import traceback
from datetime import datetime, timezone

from . import config as cfgmod
from . import pd0
from .live import LiveBuffer
from .workhorse import InstrumentError, Transcript, WorkHorse, parse_directory, parse_ps0


class Busy(RuntimeError):
    pass


class NeedsConfirmation(RuntimeError):
    """The operation would do something the operator should agree to
    first; repeat it with force."""


def _now():
    return time.time()


def sntp_offset(server, timeout=2.0):
    """Clock offset (s) of this host against an NTP server: positive when
    the server is ahead."""
    packet = b"\x1b" + 47 * b"\0"
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.settimeout(timeout)
        t0 = time.time()
        s.sendto(packet, (server, 123))
        data, _ = s.recvfrom(512)
        t3 = time.time()
    if len(data) < 48:
        raise OSError("short NTP reply")
    def ts(off):
        sec, frac = struct.unpack("!II", data[off:off + 8])
        return sec - 2208988800 + frac / 2**32
    t1, t2 = ts(32), ts(40)
    return ((t1 - t0) + (t2 - t3)) / 2


class Operation:
    """What the page shows while something runs: named steps with a state
    each, messages, and a result."""

    def __init__(self, kind, steps):
        self.kind = kind
        self.steps = [{"label": s, "state": "pending", "detail": ""} for s in steps]
        self.messages = []
        self.state = "running"
        self.result = None
        self.error = None
        self.started = _now()
        self.finished = None

    def step(self, label, state="running", detail=""):
        for s in self.steps:
            if s["label"] == label:
                s["state"] = state
                if detail:
                    s["detail"] = detail
                return
        self.steps.append({"label": label, "state": state, "detail": detail})

    def say(self, text, level="info"):
        self.messages.append({"t": _now(), "level": level, "text": text})

    def as_dict(self):
        return {"kind": self.kind, "state": self.state, "steps": self.steps,
                "messages": self.messages[-50:], "result": self.result, "error": self.error,
                "started": self.started, "finished": self.finished}


class Instrument:
    """One configured port and what is known about the ADCP behind it."""

    def __init__(self, conf, transcript_dir):
        self.conf = conf
        self.name = conf["name"]
        self.transcript = Transcript(os.path.join(
            transcript_dir, f"serial_{self.name}_{datetime.now(timezone.utc):%Y%m%d}.log"))
        self.session = WorkHorse(self.name, conf["port"], conf.get("baud", 9600),
                                 conf.get("break", "hardware"), self.transcript)
        self.info = {}            # serial, orientation, firmware, frequency
        self.directory = None     # parse_directory() of the last RR
        self.role = None          # primary | secondary | single
        self.reachable = None
        self.checked = None
        self.error = None
        self.live = None          # LiveBuffer while deployed
        self.reader = None
        self.stop_reading = threading.Event()
        self.recording = None     # path of the serial recording

    def as_dict(self, active=True):
        return {"name": self.name, "port": self.conf["port"], "baud": self.conf.get("baud"),
                "break": self.conf.get("break"), "enabled": self.conf.get("enabled", True),
                "active": active,
                "info": self.info, "role": self.role, "reachable": self.reachable,
                "checked": self.checked, "error": self.error,
                "directory": self.directory, "recording": self.recording,
                "streaming": bool(self.reader and self.reader.is_alive())}


class LadcpService:
    def __init__(self, config_path, state_path, data_root, autostart=True):
        self.config_path = config_path
        self.state_path = state_path
        self.data_root = data_root
        self.lock = threading.RLock()
        self.operation = None
        self.history = []
        self.config = cfgmod.load(config_path)
        self.state = self._load_state()
        self.history = self.state.setdefault("history", [])
        self.instruments = {}
        self._build_instruments()
        if autostart and self.state.get("deployed") and self.config.get("reattach_on_restart"):
            threading.Thread(target=self._reattach, daemon=True, name="ladcp-reattach").start()

    # ---- configuration and state ----
    def data_dir(self):
        d = self.config.get("data_dir") or os.path.join(self.data_root, "ladcp", self.config["cruise_id"])
        return d

    def _build_instruments(self):
        for inst in self.instruments.values():
            self._stop_reader(inst)
            inst.session.close()
        logs = os.path.join(self.data_dir(), "logs")
        self.instruments = {c["name"]: Instrument(c, logs) for c in self.config["instruments"]}

    def _load_state(self):
        state = {"profile": 0, "last_action": None, "deployed": None, "total_pings": 0,
                 "total_ping_hours": 0.0, "downloaded": {}, "last_download": None}
        if os.path.exists(self.state_path):
            with open(self.state_path) as f:
                state.update(json.load(f))
        return state

    def _save_state(self):
        os.makedirs(os.path.dirname(self.state_path), exist_ok=True)
        tmp = self.state_path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self.state, f, indent=2)
        os.replace(tmp, self.state_path)

    def update_config(self, new):
        with self.lock:
            if self._busy() or self.state.get("deployed"):
                raise Busy("the configuration cannot change while an operation runs or the ADCPs are deployed")
            self.config = cfgmod.save(self.config_path, new)
            self._build_instruments()
            return self.config

    def set_mode(self, mode, instrument=None):
        """Dual or single ADCP; in single mode, which instrument."""
        new = dict(self.config, mode=mode)
        if mode == "single":
            new["single_instrument"] = instrument or self.config.get("single_instrument")
        return self.update_config(new)

    def set_profile(self, number):
        with self.lock:
            if self._busy():
                raise Busy("an operation is running")
            self.state["profile"] = int(number)
            self._save_state()

    def preview(self, profile=None):
        """The commands each instrument would get for the next start."""
        profile = self.state["profile"] + 1 if profile is None else profile
        roles = self._roles()
        out = {}
        for inst in self._enabled():
            role = roles.get(inst.name) or "primary"
            orientation = inst.info.get("orientation") or self.config["primary_direction"]
            out[inst.name] = {"role": role, "commands": [
                {"cmd": c, "what": w} for c, w in
                cfgmod.setup_commands(self.config, role, profile, orientation)]}
        return out

    # ---- status for the page ----
    def status(self):
        with self.lock:
            active = cfgmod.active_names(self.config)
            return {
                "config": self.config,
                "state": self.state,
                "data_dir": self.data_dir(),
                "operation": self.operation.as_dict() if self.operation else None,
                "instruments": [i.as_dict(i.name in active) for i in self.instruments.values()],
                "history": self.history[-20:],
            }

    def live(self, since=None):
        out = {}
        for inst in self.instruments.values():
            if inst.live is not None:
                out[inst.name] = inst.live.snapshot((since or {}).get(inst.name, 0))
                out[inst.name]["streaming"] = bool(inst.reader and inst.reader.is_alive())
                out[inst.name]["role"] = inst.role
                out[inst.name]["orientation"] = inst.info.get("orientation")
        return out

    def transcript(self, name, since=0):
        inst = self.instruments.get(name)
        if inst is None:
            raise KeyError(name)
        return inst.transcript.since(since)

    # ---- running operations ----
    def _busy(self):
        return self.operation is not None and self.operation.state == "running"

    def _enabled(self):
        names = cfgmod.active_names(self.config)
        return [i for i in self.instruments.values() if i.name in names]

    def _launch(self, kind, steps, fn, *args):
        with self.lock:
            if self._busy():
                raise Busy(f"'{self.operation.kind}' is still running")
            op = Operation(kind, steps)
            self.operation = op

        def run():
            # The final state is published last, after the ports are
            # released: "done" on the page means the ports are free again.
            final, error = "done", None
            try:
                op.result = fn(op, *args)
            except NeedsConfirmation as e:
                final, error = "needs_confirmation", str(e)
            except Exception as e:      # the page shows it; the log keeps the trace
                final, error = "failed", str(e)
                op.say(traceback.format_exc(limit=3), "debug")
                for s in op.steps:
                    if s["state"] == "running":
                        s["state"] = "failed"
            finally:
                self._release_ports()
                op.finished = _now()
                op.error = error
                op.state = final
                with self.lock:
                    self.history.append({"kind": kind, "state": op.state, "error": op.error,
                                         "started": op.started, "finished": op.finished,
                                         "profile": self.state.get("profile")})
                    del self.history[:-50]
                    self.state["history"] = self.history
                    self._save_state()
        threading.Thread(target=run, daemon=True, name=f"ladcp-{kind}").start()
        return op

    def detect(self):
        steps = [f"Contact {i.name}" for i in self._enabled()] + ["Assign roles"]
        return self._launch("detect", steps, self._detect)

    def erase(self, force=False):
        steps = [f"Contact {i.name}" for i in self._enabled()] + ["Check downloads"] + \
                [f"Erase {i.name}" for i in self._enabled()]
        return self._launch("erase", steps, self._erase, force)

    def start(self, force=False, deck_voltage=None, profile=None, cast=None):
        """`cast`: the DSHIP CTD operation the profile number came from
        (dship.next_cast), recorded with the deployment."""
        steps = ["Check clock"] + [f"Contact {i.name}" for i in self._enabled()] + \
                ["Assign roles", "Program secondary", "Program primary", "Start secondary",
                 "Start primary", "Live data"]
        return self._launch("start", steps, self._start, force, deck_voltage, profile, cast)

    def stop(self, download=True):
        steps = ["Stop primary", "Stop secondary", "Assign roles"]
        if download:
            steps += ["Download", "Check files", "Store and back up", "Battery log"]
        return self._launch("stop" if not download else "stop_download", steps, self._stop, download)

    def console(self, name, command):
        """One command typed by the operator, outside a deployment."""
        with self.lock:
            if self._busy():
                raise Busy(f"'{self.operation.kind}' is still running")
            if self.state.get("deployed"):
                raise Busy("the ADCPs are deployed; stop them first")
            inst = self.instruments[name]
        with inst.session.lock:
            try:
                if command.strip() in ("===", "BREAK"):
                    return inst.session.wake()
                inst.session.open()
                return inst.session.command(command, timeout=15.0, check=False)
            finally:
                inst.session.close()       # the instrument stays awake; the port is free again

    def _release_ports(self):
        """Close the connections an operation used, unless the ADCPs are
        deployed and their stream is being read. Closing does not touch
        the instruments, and a serial server (ser2net) serves one client
        per port: an idle connection would only block others and be
        dropped by the server sooner or later."""
        if self.state.get("deployed"):
            return
        for inst in self.instruments.values():
            if inst.reader is None or not inst.reader.is_alive():
                inst.session.close()

    # ---- the steps ----
    def _contact(self, op, inst, label=None):
        label = label or f"Contact {inst.name}"
        op.step(label)
        self._stop_reader(inst)
        try:
            inst.session.wake()
            inst.info = parse_ps0(inst.session.command("PS0"))
            inst.directory = parse_directory(inst.session.command("RR", timeout=15.0))
            inst.reachable, inst.error, inst.checked = True, None, _now()
        except InstrumentError as e:
            inst.reachable, inst.error, inst.checked = False, str(e), _now()
            inst.info = {}
            op.step(label, "failed", str(e))
            op.say(str(e), "error")
            return False
        free = inst.directory.get("free_bytes")
        files = inst.directory.get("files", [])
        detail = ((f"{inst.name}: " if not label.endswith(inst.name) else "") +
                  f"S/N {inst.info.get('serial')}, {inst.info.get('orientation')}, "
                  f"{len(files)} file(s)" + (f", {free / 1e6:.1f} MB free" if free is not None else ""))
        op.step(label, "done", detail)
        if free is not None and free < self.config["memory_warning_bytes"]:
            op.say(f"{inst.name}: only {free / 1e6:.1f} MB free on the recorder — erase after the "
                   "next download", "warning")
        return True

    def _roles(self):
        """{name: role} from orientations: the one looking primary_direction
        is the primary; a lone instrument is `single`."""
        found = [i for i in self._enabled() if i.reachable and i.info.get("orientation")]
        roles = {}
        if len(found) == 1:
            roles[found[0].name] = "single"
        elif len(found) == 2:
            a, b = found
            want = self.config["primary_direction"]
            if b.info["orientation"] == want and a.info["orientation"] != want:
                a, b = b, a
            roles[a.name], roles[b.name] = "primary", "secondary"
        return roles

    def _assign(self, op):
        op.step("Assign roles")
        roles = self._roles()
        for inst in self.instruments.values():
            inst.role = roles.get(inst.name)
        if not roles:
            op.step("Assign roles", "failed", "no instrument answered")
            raise InstrumentError("no ADCP answered — check the deck box, cables and ports")
        found = [i for i in self._enabled() if i.reachable]
        if len(found) == 2 and len({i.info.get("orientation") for i in found}) == 1:
            op.say("both instruments report the same orientation "
                   f"({found[0].info.get('orientation')}); check the mounting", "warning")
        op.step("Assign roles", "done",
                ", ".join(f"{n}: {r}" for n, r in roles.items()))
        return roles

    def _detect(self, op):
        for inst in self._enabled():
            self._contact(op, inst)
        self._assign(op)
        for inst in self._enabled():
            if inst.reachable:
                inst.session.send_no_prompt("CZ", wait=0.5)
        return {i.name: {"info": i.info, "role": i.role, "files": (i.directory or {}).get("files")}
                for i in self._enabled()}

    def _not_downloaded(self, inst):
        """Files on the recorder that are not in data_dir with that size."""
        have = {}
        for root, _, names in os.walk(self.data_dir()):
            for n in names:
                if n.upper().endswith(".000"):
                    have.setdefault(n.upper(), set()).add(os.path.getsize(os.path.join(root, n)))
        return [f for f in (inst.directory or {}).get("files", [])
                if f["size"] not in have.get(f["name"].upper(), set())]

    def _erase(self, op, force):
        for inst in self._enabled():
            self._contact(op, inst)
        reachable = [i for i in self._enabled() if i.reachable]
        if not reachable:
            raise InstrumentError("no ADCP answered")
        op.step("Check downloads")
        missing = {i.name: [f["name"] for f in self._not_downloaded(i)] for i in reachable}
        missing = {k: v for k, v in missing.items() if v}
        if missing and not force:
            op.step("Check downloads", "failed", "files not downloaded")
            raise NeedsConfirmation("files on the recorder have not been downloaded: " +
                                    "; ".join(f"{k}: {', '.join(v)}" for k, v in missing.items()) +
                                    ". Erase anyway?")
        op.step("Check downloads", "done", "forced" if missing else "all downloaded")
        for inst in self._enabled():
            label = f"Erase {inst.name}"
            if not inst.reachable:
                op.step(label, "skipped")
                continue
            if not (inst.directory or {}).get("files"):
                op.step(label, "done", "already empty")
                inst.session.send_no_prompt("CZ", wait=0.5)
                continue
            op.step(label)
            inst.session.command("RE ErAsE", timeout=60.0)
            inst.directory = parse_directory(inst.session.command("RR", timeout=15.0))
            inst.session.send_no_prompt("CZ", wait=0.5)
            left = inst.directory.get("files", [])
            if left:
                op.step(label, "failed", f"{len(left)} file(s) still there")
                raise InstrumentError(f"{inst.name}: erase left {len(left)} file(s)")
            op.step(label, "done")
        self.state["last_action"] = "erase"
        self._save_state()

    def _check_clock(self, op):
        op.step("Check clock")
        server = self.config.get("ntp_server")
        if not server:
            op.step("Check clock", "skipped", "no NTP server configured — the Hub's clock is used as is")
            return
        try:
            offset = sntp_offset(server)
        except OSError as e:
            op.step("Check clock", "failed", f"{server}: {e}")
            op.say(f"could not check the clock against {server}: {e}", "warning")
            return
        limit = self.config["max_clock_offset_s"]
        if abs(offset) > limit:
            op.step("Check clock", "failed", f"Hub clock off by {offset:+.2f} s")
            raise InstrumentError(f"the Hub's clock is {offset:+.2f} s off {server} (limit {limit} s); "
                                  "fix the Hub's time sync before starting")
        op.step("Check clock", "done", f"offset {offset:+.3f} s")

    def _program(self, op, inst, profile, label):
        op.step(label)
        orientation = inst.info.get("orientation")
        for cmd, what in cfgmod.setup_commands(self.config, inst.role, profile, orientation,
                                               now=datetime.now(timezone.utc)):
            inst.session.command(cmd, timeout=10.0)
        readback = {q: inst.session.command(q, timeout=10.0, check=False) for q in cfgmod.READBACK}
        op.step(label, "done", f"{inst.name} as {inst.role}, {cfgmod.deployment_name(profile, orientation)}")
        return readback

    def _start(self, op, force, deck_voltage, profile, cast=None):
        if self.state.get("last_action") == "start" and not force:
            raise NeedsConfirmation("the last action was a START — the ADCPs may be running "
                                    "already. Start them again?")
        self._check_clock(op)
        for inst in self._enabled():
            self._contact(op, inst)
        roles = self._assign(op)
        if len([i for i in self._enabled()]) == 2 and len(roles) < 2 and not force:
            raise NeedsConfirmation("only one ADCP answered but two are configured. "
                                    "Start a single instrument?")
        number = int(profile) if profile else self.state["profile"] + 1
        by_role = {inst.role: inst for inst in self.instruments.values() if inst.role}
        secondary = by_role.get("secondary")
        primary = by_role.get("primary") or by_role.get("single")
        readback = {}
        if secondary:
            readback[secondary.name] = self._program(op, secondary, number, "Program secondary")
        else:
            op.step("Program secondary", "skipped")
        readback[primary.name] = self._program(op, primary, number, "Program primary")

        # From here the instruments are being started: record it, so a
        # failure halfway is not mistaken for "nothing happened".
        self.state.update(profile=number, last_action="start", deck_voltage=deck_voltage,
                          deployed={"profile": number, "started": _now(), "cast": cast,
                                    "instruments": {i.name: {"role": i.role,
                                                             "orientation": i.info.get("orientation"),
                                                             "serial": i.info.get("serial")}
                                                    for i in (secondary, primary) if i}})
        self._save_state()
        if cast:
            # Which CTD cast this profile is, beside its data, so the cast
            # and the LADCP files can be found from each other.
            base = os.path.join(self.data_dir(), f"{number:03d}")
            os.makedirs(base, exist_ok=True)
            with open(os.path.join(base, "dship.json"), "w") as f:
                json.dump(cast, f, indent=2)
            op.say(f"Profile {number:03d} is CTD cast {cast.get('cast')}"
                   + (f" ({cast['operation']}, station {cast['station']})" if cast.get("operation")
                      else " (predicted: DSHIP has no open CTD operation yet)"), "info")
        start = self.config["start_command"]
        if secondary:
            op.step("Start secondary")
            self._start_one(secondary, start, number)
            op.step("Start secondary", "done")
        else:
            op.step("Start secondary", "skipped")
        op.step("Start primary")
        self._start_one(primary, start, number)
        op.step("Start primary", "done")

        op.step("Live data")
        if not self.config["live_output"]:
            op.step("Live data", "skipped", "serial output is off (live_output)")
        else:
            deadline = time.time() + 15
            started = [i for i in (secondary, primary) if i]
            while time.time() < deadline and not all(i.live.count for i in started):
                time.sleep(0.5)
            got = {i.name: i.live.count for i in started}
            silent = [n for n, c in got.items() if not c]
            if silent:
                op.step("Live data", "failed", "no ensembles from " + ", ".join(silent))
                op.say("no data from " + ", ".join(silent) + " within 15 s. A secondary waits for "
                       "the primary's pulse; check that it pings. If one stays silent, "
                       "START again.", "warning")
            else:
                op.step("Live data", "done", ", ".join(f"{n}: {c} ensembles" for n, c in got.items()))
        if secondary:
            op.say("ADCPs started. Check within 3 minutes that the UP ADCP pings.", "info")
        else:
            op.say(f"{primary.name} started on its own (no sync).", "info")
        return {"profile": number, "readback": readback}

    def _start_one(self, inst, start_command, profile):
        inst.live = LiveBuffer(threshold=self.config["bottom_threshold"])
        inst.session.start_pinging(start_command)
        self._start_reader(inst, profile)

    def _start_reader(self, inst, profile):
        serial_dir = os.path.join(self.data_dir(), f"{int(profile):03d}", "serial")
        os.makedirs(serial_dir, exist_ok=True)
        name = cfgmod.deployment_name(profile, inst.info.get("orientation"))
        inst.recording = os.path.join(
            serial_dir, f"{name}_{inst.name}_{datetime.now(timezone.utc):%Y%m%d%H%M%S}.bin")
        if inst.live is None:
            inst.live = LiveBuffer(threshold=self.config["bottom_threshold"])
        inst.stop_reading.clear()
        inst.reader = threading.Thread(target=self._read_loop, args=(inst,), daemon=True,
                                       name=f"ladcp-read-{inst.name}")
        inst.reader.start()

    def _read_loop(self, inst):
        decoder = pd0.StreamDecoder()
        with open(inst.recording, "ab") as rec:
            while not inst.stop_reading.is_set():
                try:
                    data = inst.session.read_stream(4096)
                except Exception as e:
                    inst.error = f"stream: {e}"
                    inst.live.note_error(str(e))
                    time.sleep(2)
                    try:
                        inst.session.close()
                        inst.session.open()
                    except InstrumentError:
                        pass
                    continue
                if not data:
                    continue
                for block, ens in decoder.feed(data):
                    rec.write(block)
                    inst.live.add(ens)
                rec.flush()
                inst.live.skipped = decoder.skipped

    def _stop_reader(self, inst):
        if inst.reader is not None:
            inst.stop_reading.set()
            inst.reader.join(timeout=3)
            inst.reader = None

    def _reattach(self):
        """After a Hub restart during a cast: reopen the ports and keep
        reading, without sending anything to the instruments."""
        deployed = self.state["deployed"]
        for name, info in deployed.get("instruments", {}).items():
            inst = self.instruments.get(name)
            if inst is None:
                continue
            inst.role = info.get("role")
            inst.info = {"orientation": info.get("orientation"), "serial": info.get("serial")}
            try:
                inst.session.open()
            except InstrumentError as e:
                inst.error = str(e)
                continue
            inst.transcript.add("info", "reattached after a Hub restart; nothing sent")
            self._start_reader(inst, deployed["profile"])

    def _stop(self, op, download):
        deployed = self.state.get("deployed") or {}
        profile = deployed.get("profile") or self.state["profile"]
        order = sorted(self._enabled(), key=lambda i: {"primary": 0, "single": 0}.get(
            i.role or (deployed.get("instruments", {}).get(i.name) or {}).get("role"), 1))
        stopped = []
        for n, inst in enumerate(order):
            label = "Stop primary" if n == 0 else "Stop secondary"
            self._stop_reader(inst)
            if self._contact(op, inst, label):
                stopped.append(inst)
        for label in ("Stop primary", "Stop secondary"):
            if any(s["label"] == label and s["state"] == "pending" for s in op.steps):
                op.step(label, "skipped")
        if not stopped:
            raise InstrumentError("no ADCP answered the break")
        self._assign(op)
        self.state.update(last_action="stop", deployed=None)
        self._save_state()
        if not download:
            for inst in stopped:
                inst.session.send_no_prompt("CZ", wait=0.5)
            return {"profile": profile}
        return self._download(op, stopped, profile)

    def _download(self, op, insts, profile):
        dl = self.config["download"]
        incoming = os.path.join(self.data_dir(), "_incoming", f"{int(profile):03d}")
        op.step("Download")
        results = {}
        errors = {}

        def one(inst):
            files = (inst.directory or {}).get("files", [])
            if not files:
                results[inst.name] = []
                op.say(f"{inst.name}: no files on the recorder", "warning")
                return
            numbers = [len(files)] if dl["files"] == "last" else [0]
            dest = os.path.join(incoming, inst.name)
            baud = dl.get("baud")
            switch = baud and baud != inst.session.baud and inst.conf.get("break") == "hardware"
            try:
                if switch:
                    inst.session.command(f"CB{cfgmod.BAUD_CODES[baud]}11", check=False)
                    inst.session.set_baud(baud)
                got = []
                for number in numbers:
                    got += inst.session.receive_files(number, dest, progress=lambda name, r, t: op.step(
                        "Download", "running", f"{inst.name}: {name} {r}/{t or '?'} bytes"))
                results[inst.name] = got
            except InstrumentError as e:
                errors[inst.name] = str(e)
            finally:
                if switch:
                    try:
                        inst.session.command(f"CB{cfgmod.BAUD_CODES[inst.session.baud]}11", check=False)
                    except InstrumentError:
                        pass
                    inst.session.set_baud(inst.session.baud)
                try:
                    inst.directory = parse_directory(inst.session.command("RR", timeout=15.0))
                    inst.session.send_no_prompt("CZ", wait=0.5)
                except InstrumentError:
                    pass

        if dl.get("simultaneous") and len(insts) > 1:
            threads = [threading.Thread(target=one, args=(i,)) for i in insts]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
        else:
            for inst in insts:
                one(inst)
        if errors:
            op.step("Download", "failed", "; ".join(f"{k}: {v}" for k, v in errors.items()))
            raise InstrumentError("download failed: " + "; ".join(errors.values()))
        op.step("Download", "done", ", ".join(
            f"{k}: {len(v)} file(s)" for k, v in results.items()))

        op.step("Check files")
        summaries = {}
        for inst in insts:
            paths = [p for p, _ in results.get(inst.name, [])]
            stem = cfgmod.deployment_name(profile, inst.info.get("orientation"))
            ours = [p for p in paths if os.path.basename(p).upper().startswith(stem)]
            if len(ours) > 1:
                op.say(f"{inst.name}: {len(ours)} files for profile {profile:03d} "
                       f"({', '.join(os.path.basename(p) for p in ours)}): restarted with the same "
                       "number, power loss, or lost sync. Keep them; processing appends them.", "warning")
            if paths and not ours:
                op.say(f"{inst.name}: no file named {stem}*.000 among "
                       f"{', '.join(os.path.basename(p) for p in paths)}", "warning")
            summaries[inst.name] = pd0.summarize(ours or paths, self.config.get("voltage_scale"),
                                                 self.config.get("current_scale"))
            summaries[inst.name]["files"] = [os.path.basename(p) for p in paths]
        op.step("Check files", "done", "; ".join(
            f"{k}: {v.get('ensembles', 0)} ensembles, max {v.get('max_depth_m', '?')} m"
            for k, v in summaries.items()))

        op.step("Store and back up")
        folder = self._profile_folder(profile)
        os.makedirs(folder, exist_ok=True)
        for inst in insts:
            for path, size in results.get(inst.name, []):
                target = os.path.join(folder, os.path.basename(path))
                shutil.move(path, target)
                self.state["downloaded"][os.path.basename(path).upper()] = size
        shutil.rmtree(incoming, ignore_errors=True)
        try:
            os.rmdir(os.path.dirname(incoming))
        except OSError:
            pass                       # another profile's download is still there
        backup = self.config.get("backup_dir")
        backed_up = None
        if backup:
            try:
                dest = os.path.join(backup, os.path.basename(folder))
                if os.path.exists(dest):
                    op.say(f"backup: {dest} already exists, not overwritten", "warning")
                else:
                    shutil.copytree(folder, dest)
                    backed_up = dest
            except OSError as e:
                op.say(f"backup to {backup} failed: {e}", "warning")
        else:
            op.say("no backup path set — consider one on another disk", "info")
        op.step("Store and back up", "done", folder + (f" → {backed_up}" if backed_up else ""))

        op.step("Battery log")
        pings = sum(s.get("ensembles", 0) for s in summaries.values())
        hours = [s.get("ping_hours") or 0 for s in summaries.values()]
        self.state["total_pings"] += pings
        self.state["total_ping_hours"] = round(
            self.state["total_ping_hours"] + (sum(hours) / len(hours) if hours else 0), 2)
        line = {"time": datetime.now(timezone.utc).isoformat(timespec="seconds"), "profile": profile,
                "total_pings": self.state["total_pings"],
                "total_ping_hours": self.state["total_ping_hours"],
                "deck_voltage": self.state.get("deck_voltage"),
                **{f"{k}_voltage_mean": v.get("voltage_mean") for k, v in summaries.items()},
                **{f"{k}_current_mean": v.get("current_mean") for k, v in summaries.items()}}
        with open(os.path.join(self.data_dir(), "battery.log"), "a") as f:
            f.write(json.dumps(line) + "\n")
        self.state["last_download"] = {"profile": profile, "folder": folder, "summaries": summaries,
                                       "time": _now()}
        self._save_state()
        op.step("Battery log", "done", f"{self.state['total_ping_hours']} h since the battery swap")
        return {"profile": profile, "folder": folder, "summaries": summaries}

    def _profile_folder(self, profile):
        """<data_dir>/<NNN>, or <NNN>_1, _2 … if that already holds files
        (the old tool's rule: never mix two downloads)."""
        base = os.path.join(self.data_dir(), f"{int(profile):03d}")
        candidate, n = base, 0
        while True:
            names = [f for f in os.listdir(candidate) if f != "serial"] if os.path.isdir(candidate) else []
            if not names:
                return candidate
            n += 1
            candidate = f"{base}_{n}"

    def reset_battery(self):
        with self.lock:
            self.state["total_pings"] = 0
            self.state["total_ping_hours"] = 0.0
            self._save_state()
            os.makedirs(self.data_dir(), exist_ok=True)
            with open(os.path.join(self.data_dir(), "battery.log"), "a") as f:
                f.write(json.dumps({"time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                    "event": "battery swapped"}) + "\n")
