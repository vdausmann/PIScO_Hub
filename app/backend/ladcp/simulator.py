"""Two simulated WorkHorse ADCPs on TCP ports, for developing the LADCP
page without the instruments. Connect with `socket://host:port`, or with
`rfc2217://host:port` when started with protocol="rfc2217" — the way the
ship's serial server (ser2net on a Raspberry Pi) offers the real ports.

What it imitates is what the Hub uses: the `>` prompt and echo, the
software break (`===`), PS0/RR/RE/RN/RY/CS/CZ/TS and the setup commands
the old LADCP tool sent, PD0 ensembles on the serial line and into a
recorder file while pinging, and YMODEM for the download. Both
instruments share one simulated cast — down at `speed` m/s to `bottom`
minus 20 m and back — so the down-looker sees the seafloor approach and
the up-looker the surface.

Over RFC 2217 the hardware break and the baud rate are imitated too: a
break wakes the instrument, CB changes its speed after the reply, and a
client at the wrong speed gets garbage. Over a raw socket there is no
break signal (use `===`) and no speed. Not imitated: the RS-422
synchronisation pulse, and anything firmware-specific in the wording of
the replies. The reply wording follows the old tool's parsers and the
WorkHorse manual; check it against a real instrument's transcript.
"""

import math
import random
import socket
import socketserver
import threading
import time
from datetime import datetime, timezone

import numpy as np

from . import pd0, ymodem

BANNER = ("\r\n[BREAK Wakeup A]\r\nWorkHorse Broadband ADCP Version 51.41\r\n"
          "Teledyne RD Instruments (c) 1996-2010\r\nAll Rights Reserved.\r\n>")
PROMPT = "\r\n>"


class Cast:
    """Depth of the package over time, shared by both instruments."""

    def __init__(self, bottom=800.0, speed=1.0, soak=60.0):
        self.bottom, self.speed, self.soak = bottom, speed, soak
        self.t0 = None

    def start(self):
        if self.t0 is None:
            self.t0 = time.time()

    def stop(self):
        self.t0 = None

    def depth(self, t):
        if self.t0 is None:
            return 0.0
        dt = max(0.0, t - self.t0)
        lowest = self.bottom - 20.0
        down = lowest / self.speed
        if dt < self.soak:
            return 5.0
        dt -= self.soak
        if dt < down:
            return 5.0 + min(lowest - 5.0, dt * self.speed)
        if dt < down + 120:
            return lowest
        return max(0.0, lowest - (dt - down - 120) * self.speed)

    def rate(self, t):
        return (self.depth(t + 0.5) - self.depth(t - 0.5))


class Instrument:
    def __init__(self, name, serial, up, cast):
        self.name, self.serial, self.up, self.cast = name, serial, up, cast
        self.lock = threading.RLock()
        self.params = {}
        self.factory()
        self.files = {}               # name -> bytearray
        self.rn = "RDI_"
        self.pinging = False
        self.asleep = False
        self.clock_offset = 0.0
        self.number = 0
        self.current_file = None
        self.output = None            # the connection's send function, while one is open
        self.voltage = 160.0
        self.baud = 115200            # the instrument's line speed (RFC 2217 mode only)
        self.pending_baud = None      # CB takes effect after its reply

    def factory(self):
        self.params = {"WN": "20", "WS": "1000", "WF": "0", "WP": "1", "WM": "1", "WV": "175",
                       "WB": "0", "EZ": "1111101", "EX": "11111", "CF": "11111", "TP": "00:00.10",
                       "TE": "00:00:01.60", "SM": "0", "SA": "001", "SI": "0", "SW": "0",
                       "ST": "0", "CQ": "255", "SB": "1", "CB": "411"}

    def now(self):
        return time.time() + self.clock_offset

    # ---- commands ----
    def handle(self, line):
        """Reply text for one command line (echo excluded, prompt included)."""
        cmd = line.strip()
        up = cmd.upper()
        if not cmd:
            return PROMPT
        if up == "CR1":
            self.factory()
            return "\r\n[Parameters set to FACTORY defaults]" + PROMPT
        if up == "CK":
            return "\r\n[Parameters saved as USER defaults]" + PROMPT
        if up.startswith("TS"):
            arg = cmd[2:].strip()
            if arg == "?":
                return "\r\nTS = " + datetime.fromtimestamp(self.now(), timezone.utc).strftime(
                    "%y/%m/%d,%H:%M:%S") + " --- Time Set (yr/mon/day,hour:min:sec)" + PROMPT
            try:
                set_to = datetime.strptime(arg, "%y/%m/%d,%H:%M:%S").replace(tzinfo=timezone.utc)
            except ValueError:
                return "\r\nERR 021:  INVALID DATE/TIME" + PROMPT
            self.clock_offset = set_to.timestamp() - time.time()
            return PROMPT
        if up == "PS0":
            return ("\r\n    Instrument S/N:  {sn}\r\n       Frequency:  307200 HZ\r\n"
                    "   Configuration:  4 BEAM, JANUS\r\n     Match Layer:  10\r\n"
                    "      Beam Angle:  20 DEGREES\r\n    Beam Pattern:  CONVEX\r\n"
                    "     Orientation:  {o}\r\n       Sensor(s):  HEADING  TILT 1  TILT 2  TEMPERATURE\r\n"
                    "    CPU Firmware:  51.41 [0]\r\n   Boot Code Ver:  Required:  1.16   Actual:  1.16"
                    ).format(sn=self.serial, o="UP" if self.up else "DOWN") + PROMPT
        if up == "RR":
            return self.directory() + PROMPT
        if up == "RA?":
            return f"\r\nRA = {len(self.files)} -- Number of deployments" + PROMPT
        if up == "RE ERASE" and cmd[3:] == "ErAsE":
            self.files.clear()
            return "\r\nRecorder erased." + PROMPT
        if up.startswith("RN"):
            arg = cmd[2:].strip()
            if arg == "?":
                return f"\r\nRN = {self.rn} ----------- Deployment Name" + PROMPT
            self.rn = arg[:5]
            return PROMPT
        if up in ("S?", "T?", "W?", "E?", "C?"):
            keys = sorted(k for k in self.params if k.startswith(up[0]))
            return "".join(f"\r\n{k} {self.params[k]}" for k in keys) + PROMPT
        if up == "CZ":
            self.asleep = True
            return "\r\nPowering Down"
        if up == "CS":
            return None            # handled by the connection: starts pinging
        if up.startswith("RY"):
            return None            # handled by the connection: YMODEM
        if up.startswith("CB") and len(up) == 5 and up[2:].isdigit():
            # Like the real one: the reply still goes out at the old speed.
            # CR1 leaves the baud rate alone (communication settings are not
            # part of the factory reset in this simulator).
            code = int(up[2])
            baud = {1: 1200, 2: 2400, 3: 4800, 4: 9600, 5: 19200, 6: 38400, 7: 57600, 8: 115200}.get(code)
            if baud is None:
                return "\r\nERR 012:  PARAMETER OUT OF BOUNDS" + PROMPT
            self.params["CB"] = up[2:]
            self.pending_baud = baud
            return PROMPT
        if len(up) >= 3 and up[:2] in self.params:
            self.params[up[:2]] = cmd[2:].strip()
            return PROMPT
        if up.startswith("SB") or up.startswith("CB") or up.startswith("SM"):
            self.params[up[:2]] = cmd[2:].strip()
            return PROMPT
        return "\r\nERR 010:  UNRECOGNIZED COMMAND" + PROMPT

    def directory(self):
        if not self.files:
            return "\r\nRecorder Directory:\r\nNo files found.\r\nTotal bytes free = 15998976 bytes"
        lines = ["", "Recorder Directory:"]
        used = 0
        for name, data in self.files.items():
            stem, ext = name.split(".")
            lines.append(f"{stem} {ext} {len(data):14d} {datetime.now(timezone.utc):%Y/%m/%d %H:%M:%S}")
            used += len(data)
        lines.append(f"Total bytes free = {16000000 - used} bytes")
        return "\r\n".join(lines)

    # ---- pinging ----
    def start_pinging(self):
        with self.lock:
            k = 0
            while f"{self.rn}{k:03d}.000" in self.files:
                k += 1
            self.current_file = f"{self.rn}{k:03d}.000"
            if self.params.get("CF", "11111")[4] == "1":
                self.files[self.current_file] = bytearray()
            self.pinging = True
            self.number = 0
        if not self.up:
            self.cast.start()
        threading.Thread(target=self._ping_loop, daemon=True, name=f"sim-{self.name}").start()

    def stop_pinging(self):
        with self.lock:
            was = self.pinging
            self.pinging = False
            self.asleep = False
        if was and not self.up:
            self.cast.stop()         # the cast ends when the down-looker stops
        return was

    def _interval(self):
        try:
            h, m, s = self.params.get("TE", "00:00:01.60").split(":")
            return max(0.2, int(h) * 3600 + int(m) * 60 + float(s))
        except ValueError:
            return 1.6

    def _ping_loop(self):
        interval = self._interval()
        next_t = time.time()
        while True:
            with self.lock:
                if not self.pinging:
                    return
                block = self.ensemble(time.time())
                if self.current_file in self.files:
                    self.files[self.current_file] += block
                send = self.output if self.params.get("CF", "11111")[3] == "1" else None
            if send:
                try:
                    send(block)
                except OSError:
                    pass
            next_t += interval
            time.sleep(max(0.0, next_t - time.time()))

    def ensemble(self, t):
        self.number += 1
        n = int(self.params.get("WN", 20))
        cell = int(self.params.get("WS", 1000)) / 100.0
        blank = int(self.params.get("WF", 0)) / 100.0
        bin1 = blank + cell
        rng = bin1 + cell * np.arange(n)
        depth = self.cast.depth(t)
        w = self.cast.rate(t)                  # +down
        # echo intensity: falls off with range, noise floor ~40 counts
        ei = 150 - 45 * np.log10(1 + rng / 8.0)
        ei = np.maximum(ei, 42)[:, None] + np.random.normal(0, 2.5, (n, 4))
        target = (self.cast.bottom - depth) if not self.up else depth
        if 0 < target < rng[-1] + cell:
            k = int(round((target - bin1) / cell))
            if 0 <= k < n:
                ei[k, :] += 70 + np.random.normal(0, 5, 4)
                ei[k + 1:, :] = 40 + np.random.normal(0, 2, (n - k - 1, 4))
        s20, c20 = math.sin(math.radians(20)), math.cos(math.radians(20))
        u = 0.25 * math.sin(depth / 150.0)
        v = 0.15 * math.cos(depth / 200.0)
        along = w * c20 if not self.up else -w * c20
        vel = np.empty((n, 4))
        vel[:, 0] = along + u * s20
        vel[:, 1] = along - u * s20
        vel[:, 2] = along + v * s20
        vel[:, 3] = along - v * s20
        vel += np.random.normal(0, 0.03, (n, 4))
        vel[rng > 140, :] = np.nan
        if 0 < target < rng[-1]:
            vel[rng > target, :] = np.nan
        corr = np.clip(128 - rng[:, None] * 0.5 + np.random.normal(0, 4, (n, 4)), 20, 128)
        self.voltage = max(110.0, self.voltage - 0.002)
        return pd0.encode_ensemble(
            number=self.number, time=self.now(), n_cells=n, cell_m=cell, blank_m=blank,
            bin1_m=bin1, up=self.up, heading=(t * 3) % 360, pitch=2.0 * math.sin(t / 7),
            roll=1.5 * math.cos(t / 5), temperature=max(2.0, 20 - depth * 0.015),
            xmit_voltage_raw=int(self.voltage), xmit_current_raw=60 + random.randint(-3, 3),
            serial=self.serial, profiling_mode=int(self.params.get("WM", 15) or 15),
            vel=vel, ei=ei, corr=corr, power=int(self.params.get("CQ", 255)))


class Line:
    """One client connection to one instrument, as the instrument's serial
    line sees it. The transport (raw TCP or RFC 2217) feeds what the client
    sent and delivers what the instrument writes; a session thread plays
    the instrument. It has the attributes pyserial's RFC 2217 PortManager
    drives, so a client's break, baud rate and line settings arrive here.

    With RFC 2217 the line has a speed: if the client's baud rate differs
    from the instrument's, what crosses is garbage, as on a real wire.
    """

    def __init__(self, instrument, send, rfc2217=False):
        self.instrument = instrument
        self._send = send
        self.rfc2217 = rfc2217
        self.input = bytearray()
        self.cond = threading.Condition()
        self.closed = False
        self.break_pending = False
        self.write_lock = threading.Lock()
        # what PortManager reads and sets
        self.name = instrument.name
        self.baudrate = instrument.baud
        self.bytesize, self.parity, self.stopbits = 8, "N", 1
        self.xonxoff = self.rtscts = False
        self.rts = self.dtr = True
        self.cts = self.dsr = self.cd = True
        self.ri = False
        self._break = False

    # ---- PortManager's side ----
    @property
    def break_condition(self):
        return self._break

    @break_condition.setter
    def break_condition(self, on):
        if on and not self._break:
            self.signal_break()
        self._break = bool(on)

    def reset_input_buffer(self):
        pass

    def reset_output_buffer(self):
        pass

    # ---- transport side ----
    def _in_sync(self):
        return not self.rfc2217 or self.baudrate == self.instrument.baud

    def feed(self, data):
        if not self._in_sync():
            return                       # framing errors: the instrument hears nothing
        with self.cond:
            self.input += data
            self.cond.notify_all()

    def signal_break(self):
        with self.cond:
            self.break_pending = True
            self.cond.notify_all()

    def close(self):
        with self.cond:
            self.closed = True
            self.cond.notify_all()

    # ---- the instrument's side (also what ymodem.send uses) ----
    def read(self, n=1, timeout=0.2):
        with self.cond:
            if not self.input and not self.closed and not self.break_pending:
                self.cond.wait(timeout)
            if self.closed:
                raise OSError("client gone")
            out = bytes(self.input[:n])
            del self.input[:n]
            return out

    def write(self, data):
        if isinstance(data, str):
            data = data.encode("latin-1")
        if not self._in_sync():
            data = bytes(random.randrange(256) for _ in data)
        with self.write_lock:
            self._send(data)

    def flush(self):
        pass

    def run(self):
        """Play the instrument until the client goes away."""
        inst = self.instrument
        with inst.lock:
            inst.output = self.write
        line = ""
        tail = ""
        try:
            while True:
                if self.break_pending:
                    with self.cond:
                        self.break_pending = False
                        self.input.clear()
                    line = tail = ""
                    inst.stop_pinging()
                    inst.asleep = False
                    self.write(BANNER)
                    continue
                data = self.read(4096)
                if not data:
                    continue
                text = data.decode("latin-1")
                tail += text
                if "===" in tail:
                    self.signal_break()
                    continue
                tail = tail[-2:]
                if inst.pinging or inst.asleep:
                    continue                 # only a break gets through
                for ch in text:
                    if ch == "\r":
                        cmd, line = line, ""
                        self.write(cmd)      # echo
                        reply = inst.handle(cmd)
                        if reply is not None:
                            self.write(reply)
                            if inst.pending_baud:
                                time.sleep(0.05)
                                inst.baud, inst.pending_baud = inst.pending_baud, None
                        elif cmd.strip().upper() == "CS":
                            inst.start_pinging()
                        elif cmd.strip().upper().startswith("RY"):
                            self._ymodem(cmd.strip()[2:].strip())
                    elif ch in "\n=":
                        continue
                    else:
                        line += ch
        except OSError:
            pass
        finally:
            with inst.lock:
                if inst.output is self.write:
                    inst.output = None

    def _ymodem(self, arg):
        inst = self.instrument
        names = list(inst.files)
        if arg:
            try:
                names = [names[int(arg) - 1]]
            except (ValueError, IndexError):
                self.write("\r\nERR 040:  FILE NOT FOUND" + PROMPT)
                return
        self.write("\r\nSending via YModem...\r\n")
        try:
            ymodem.send(self, [(n, bytes(inst.files[n])) for n in names])
        except ymodem.YModemError as e:
            self.write(f"\r\nYMODEM failed: {e}")
        time.sleep(0.2)
        self.write(PROMPT)


def _handler(instrument, rfc2217, clients):
    class Handler(socketserver.BaseRequestHandler):
        def handle(self):
            sock = self.request
            clients.add(sock)
            sock.settimeout(0.2)
            send_lock = threading.Lock()

            def raw_send(data):
                with send_lock:
                    sock.sendall(data)

            manager = None
            if rfc2217:
                from serial.rfc2217 import PortManager

                class _Conn:
                    write = staticmethod(raw_send)

                line = Line(instrument, lambda d: raw_send(b"".join(manager.escape(d))), True)
                manager = PortManager(line, _Conn())
            else:
                line = Line(instrument, raw_send)
            session = threading.Thread(target=line.run, daemon=True, name=f"sim-line-{instrument.name}")
            session.start()
            try:
                while True:
                    try:
                        data = sock.recv(4096)
                    except socket.timeout:
                        continue
                    except OSError:
                        return
                    if not data:
                        return
                    if manager is not None:
                        data = b"".join(manager.filter(data))
                    if data:
                        line.feed(data)
            finally:
                clients.discard(sock)
                line.close()

    return Handler


class _Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


class Simulator:
    """Start with `Simulator(ports=(7001, 7002)).start()`; the first port
    is the down-looker. protocol "socket" is a raw TCP line (software break
    only, no baud rate); "rfc2217" speaks RFC 2217 like ser2net on the
    ship's serial server, carrying hardware breaks and baud-rate changes,
    with the instruments starting at `baud`."""

    def __init__(self, ports=(7001, 7002), host="127.0.0.1", bottom=800.0, speed=1.0, soak=60.0,
                 protocol="socket", baud=115200):
        if protocol not in ("socket", "rfc2217"):
            raise ValueError("protocol: socket or rfc2217")
        self.cast = Cast(bottom=bottom, speed=speed, soak=soak)
        self.instruments = [Instrument("DN", 24001, False, self.cast),
                            Instrument("UP", 24002, True, self.cast)]
        for instrument in self.instruments:
            instrument.baud = baud
        self.protocol = protocol
        self.clients = set()
        self.servers = []
        self.host, self.ports = host, ports

    def start(self):
        for instrument, port in zip(self.instruments, self.ports):
            server = _Server((self.host, port), _handler(instrument, self.protocol == "rfc2217", self.clients))
            threading.Thread(target=server.serve_forever, daemon=True,
                             name=f"sim-server-{port}").start()
            self.servers.append(server)
        return self

    @property
    def urls(self):
        return [f"{self.protocol}://{self.host}:{s.server_address[1]}" for s in self.servers]

    def drop_clients(self):
        """Cut every client connection, as ser2net does on an idle timeout
        or when another client takes the port. The instruments carry on."""
        for sock in list(self.clients):
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    def stop(self):
        for instrument in self.instruments:
            instrument.stop_pinging()
        for server in self.servers:
            server.shutdown()
            server.server_close()
