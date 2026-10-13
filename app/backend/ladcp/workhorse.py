"""A serial session with one RDI WorkHorse: wake it, send commands and
wait for the prompt, start it, receive its files.

The port is a pyserial URL, so the same code drives a local USB–RS485
adapter (`/dev/ttyUSB0`, `/dev/cu.usbserial-…`), a network serial server
(`rfc2217://host:port`, `socket://host:port`) and the simulator. A
hardware break needs a real port or RFC 2217; over a raw socket only the
software break (`===`) reaches the instrument.

Everything sent and received goes to a transcript file, the record to
look at when an instrument did not do what it was told.
"""

import collections
import os
import re
import threading
import time
from datetime import datetime, timezone

from . import ymodem

PROMPT = re.compile(r"(^|[\r\n])>\s*$")
ERROR = re.compile(r"\bERR\s*\d+[^\r\n]*")


class InstrumentError(IOError):
    pass


def _serial():
    try:
        import serial
    except ImportError as e:      # pragma: no cover - a deployment without pyserial
        raise InstrumentError("pyserial is not installed on the Hub (pip install pyserial)") from e
    return serial


class Transcript:
    """Tx/Rx lines with times: in memory for the page, appended to a file
    for the record."""

    def __init__(self, path=None, keep=400):
        self.path = path
        self.lines = collections.deque(maxlen=keep)
        self.lock = threading.Lock()
        self.seq = 0

    def add(self, kind, text):
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        with self.lock:
            self.seq += 1
            self.lines.append({"seq": self.seq, "t": stamp, "kind": kind, "text": text})
            if self.path:
                os.makedirs(os.path.dirname(self.path), exist_ok=True)
                with open(self.path, "a") as f:
                    f.write(f"[{stamp}] {kind}: {text}\n")

    def since(self, seq):
        with self.lock:
            return [line for line in self.lines if line["seq"] > seq]


class WorkHorse:
    def __init__(self, name, url, baud=9600, break_mode="hardware", transcript=None):
        self.name = name
        self.url = url
        self.baud = int(baud)
        self.break_mode = break_mode or "hardware"
        self.transcript = transcript or Transcript()
        self.ser = None
        self.lock = threading.RLock()

    # ---- the port ----
    def open(self):
        if self.is_open:
            return
        self.close()                   # a connection that died under us
        serial = _serial()
        try:
            self.ser = serial.serial_for_url(self.url, baudrate=self.baud, timeout=0.1)
        except (serial.SerialException, OSError, ValueError) as e:
            raise InstrumentError(f"{self.name}: cannot open {self.url}: {e}") from e
        if not self.url.startswith("rfc2217://"):
            # A stuck local adapter should not hang a write forever. The
            # RFC 2217 client has no write timeout: asking for one raises,
            # and the value sticks, failing every later baud change too.
            self.ser.write_timeout = 5
        self.transcript.add("info", f"opened {self.url} @ {self.baud}")

    def close(self):
        if self.ser is not None:
            try:
                self.ser.close()
            except Exception:
                pass
            self.transcript.add("info", "closed")
        self.ser = None

    @property
    def is_open(self):
        """Open *and* still connected. pyserial keeps `is_open` True after
        an RFC 2217 server (ser2net) drops the connection — on an idle
        timeout, or when another client takes the port — so for those
        the reader thread is what tells."""
        if self.ser is None or not self.ser.is_open:
            return False
        thread = getattr(self.ser, "_thread", None)
        if self.url.startswith("rfc2217://") and (thread is None or not thread.is_alive()):
            return False
        return True

    def reopen(self):
        self.transcript.add("info", "connection lost; reconnecting")
        self.close()
        self.open()

    def set_baud(self, baud):
        self.ser.baudrate = int(baud)
        self.transcript.add("info", f"baud {baud}")

    def _write(self, text):
        data = text.encode("ascii") if isinstance(text, str) else text
        try:
            self.ser.write(data)
            self.ser.flush()
        except Exception as e:
            raise InstrumentError(f"{self.name}: write failed: {e}") from e
        self.transcript.add("tx", data.decode("ascii", "replace").rstrip("\r"))

    def _read_until(self, done, timeout, quiet=0.15):
        """Read until done(text) holds and the line has been quiet for
        `quiet` seconds, or the timeout passes. Returns (text, matched)."""
        deadline = time.monotonic() + timeout
        buf = bytearray()
        last = time.monotonic()
        while time.monotonic() < deadline:
            try:
                chunk = self.ser.read(self.ser.in_waiting or 1)
            except Exception as e:
                raise InstrumentError(f"{self.name}: read failed: {e}") from e
            if chunk:
                buf += chunk
                last = time.monotonic()
                continue
            text = buf.decode("latin-1")
            if done(text) and time.monotonic() - last >= quiet:
                return text, True
        return buf.decode("latin-1"), False

    def _record_rx(self, text):
        printable = "".join(c if c in "\r\n\t" or 32 <= ord(c) < 127 else "·" for c in text)
        if printable.strip():
            self.transcript.add("rx", printable.strip("\r\n"))

    # ---- talking to it ----
    def wake(self, attempts=3):
        """Break into the instrument (stops it if it is pinging) and wait
        for the prompt. Returns the banner."""
        self.open()
        for attempt in range(1, attempts + 1):
            try:
                self.ser.reset_input_buffer()
            except Exception:
                pass
            try:
                self._break()
            except Exception:
                # most often a connection the server has dropped: once more on a fresh one
                try:
                    self.reopen()
                    self._break()
                except Exception as e:
                    raise InstrumentError(f"{self.name}: cannot send a break on {self.url}: {e}") from e
            text, ok = self._read_until(lambda t: PROMPT.search(t) is not None, 6.0, quiet=0.5)
            self._record_rx(text)
            if ok:
                return text[text.find("[BREAK"):] if "[BREAK" in text else text
            time.sleep(1.0)
        raise InstrumentError(f"{self.name}: no answer to the break on {self.url} "
                              f"({attempts} attempts, break mode {self.break_mode!r})")

    def _break(self):
        if self.break_mode == "hardware":
            self.transcript.add("info", "hardware break")
            self.ser.send_break(0.3)
        else:
            self._write(self.break_mode)

    def command(self, cmd, timeout=5.0, check=True):
        """Send one command and return its reply without the echo and the
        prompt. Raises InstrumentError on an `ERR nnn` reply when check."""
        with self.lock:
            try:
                self.ser.reset_input_buffer()
            except Exception:
                pass
            self._write(cmd + "\r")
            text, ok = self._read_until(lambda t: PROMPT.search(t) is not None, timeout)
            self._record_rx(text)
        if not ok:
            raise InstrumentError(f"{self.name}: no prompt after {cmd!r} within {timeout:.0f} s")
        reply = PROMPT.sub("", text)
        stripped = reply.lstrip()
        if stripped.upper().startswith(cmd.upper()):
            reply = stripped[len(cmd):]
        reply = reply.strip("\r\n ")
        if check:
            err = ERROR.search(reply)
            if err:
                raise InstrumentError(f"{self.name}: {cmd!r} rejected: {err.group(0).strip()}")
        return reply

    def send_no_prompt(self, cmd, wait=1.0):
        """For commands after which no prompt comes: CS starts pinging,
        CZ powers down."""
        with self.lock:
            self._write(cmd + "\r")
            text, _ = self._read_until(lambda t: False, wait)
            self._record_rx(text)
            return text

    def start_pinging(self, command="CS"):
        """Send the start command and do not wait: from here the line
        carries ensembles, and the reader takes it (the echo ends up as
        skipped bytes in its decoder). `$` goes without a carriage
        return, as the old tool sent it."""
        with self.lock:
            self._write(command if command == "$" else command + "\r")

    def receive_files(self, file_number, dest_dir, progress=None):
        """RY[n] and a YMODEM batch into dest_dir: [(path, size)]."""
        with self.lock:
            self.ser.reset_input_buffer()
            cmd = f"RY{file_number}" if file_number else "RY"
            self._write(cmd + "\r")
            time.sleep(0.5)
            try:
                files = ymodem.receive(self.ser, dest_dir, progress=progress)
            except ymodem.YModemError as e:
                self.transcript.add("info", f"YMODEM failed: {e}")
                raise InstrumentError(f"{self.name}: download failed: {e}") from e
            for path, size in files:
                self.transcript.add("info", f"received {os.path.basename(path)} ({size} bytes)")
            text, _ = self._read_until(lambda t: PROMPT.search(t) is not None, 10.0)
            self._record_rx(text)
            return files

    def read_stream(self, n=4096):
        return self.ser.read(n)


# ---- reading the replies ---------------------------------------------------------

def parse_ps0(text):
    def field(label):
        m = re.search(label + r"\s*:\s*([^\r\n]+)", text, re.I)
        return m.group(1).strip() if m else None
    orientation = (field("Orientation") or "").upper()
    return {
        "serial": field(r"Instrument S/N"),
        "orientation": "UP" if orientation.startswith("UP") else
                       "DOWN" if orientation.startswith("DOWN") else None,
        "frequency": field("Frequency"),
        "firmware": field("CPU Firmware"),
    }


FILE_LINE = re.compile(
    r"(?P<stem>[A-Za-z0-9_\-]{1,8})[ .](?P<ext>\d{3})\s+(?P<size>\d+)(?:\s*bytes)?"
    r"(?:\s+(?P<date>\d{2,4}/\d\d/\d\d[ ,]+\d\d:\d\d:\d\d))?")
FREE = re.compile(r"free\s*=\s*(\d+)", re.I)


def parse_directory(text):
    """RR listing → {"files": [{name, size, date}], "free_bytes"}. Same
    reading as the old tool: a line with `NAME 000 size date` is a file
    (zero-length ones are not), `free = n` the space left."""
    files = []
    for line in text.splitlines():
        m = FILE_LINE.search(line)
        if m and int(m.group("size")) > 0:
            files.append({"name": f"{m.group('stem')}.{m.group('ext')}",
                          "size": int(m.group("size")), "date": m.group("date")})
    free = FREE.search(text)
    return {"files": files, "free_bytes": int(free.group(1)) if free else None,
            "empty": "No files found" in text}
