#!/usr/bin/env python3
"""Watch the two LADCP ports while you plug instruments in and out.

    python scripts/workhorse_monitor.py                       # both serial-server ports
    python scripts/workhorse_monitor.py --port /dev/cu.PL2303G-USBtoUART130

Every --interval seconds each port is probed: a carriage return first (an
awake instrument answers with its prompt), and a hardware break when that
gets nothing. In between, the line is listened to, so a wake-up banner the
instrument prints on its own is caught and classified:

  [POWERON Wakeup]   it lost power and came back — supply sag or short
  [BREAK Wakeup B]   a break arrived on channel B, the shared RS-485 sync
                     pair — a held-low or noisy sync line
  [BREAK Wakeup A]   a break on its own serial line (normally ours)

Type a note and press Enter at any time ("plugged in 20507") and it goes
into the log at that moment. Ctrl-C ends the run and powers both
instruments down (CZ). The Hub's LADCP page must not hold the ports.
"""
import argparse
import csv
import re
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import serial

DEFAULT_PORTS = ["rfc2217://10.11.15.10:3001?ign_set_control",
                 "rfc2217://10.11.15.10:3002?ign_set_control"]
PROMPT = re.compile(r"(^|[\r\n])>\s*$")
WAKEUP = re.compile(r"\[(POWERON|BREAK)\s+Wakeup\s*([AB])?\]", re.I)
SERIAL = re.compile(r"Instrument S/N:\s*(\d+)")

print_lock = threading.Lock()


def now():
    return datetime.now(timezone.utc)


def label(port):
    m = re.search(r":(\d+)(\?|$)", port)
    return m.group(1) if m and port.startswith(("rfc2217", "socket")) else Path(port).name


def printable(text):
    return "".join(c if c in "\r\n\t" or 32 <= ord(c) < 127 else "." for c in text)


class Log:
    def __init__(self, out):
        out.mkdir(parents=True, exist_ok=True)
        self.text = open(out / "monitor.log", "a")
        self.csv_file = open(out / "probes.csv", "a", newline="")
        self.csv = csv.writer(self.csv_file)
        if self.csv_file.tell() == 0:
            self.csv.writerow(["time_utc", "port", "state", "how", "reply_s", "wakeup", "serial"])
        self.lock = threading.Lock()

    def event(self, port, text, show=True):
        stamp = now().strftime("%H:%M:%S.%f")[:-3]
        line = f"{stamp}  {port:>6}  {text}"
        with self.lock:
            self.text.write(line + "\n")
            self.text.flush()
        if show:
            with print_lock:
                print(line, flush=True)

    def probe(self, port, state, how, reply_s, wakeup, serial_no):
        with self.lock:
            self.csv.writerow([now().isoformat(timespec="milliseconds"), port, state, how,
                               "" if reply_s is None else f"{reply_s:.2f}", wakeup or "", serial_no or ""])
            self.csv_file.flush()


class PortWatch(threading.Thread):
    def __init__(self, port, args, log, stop):
        super().__init__(daemon=True)
        self.port, self.args, self.log, self.stop = port, args, log, stop
        self.name_ = label(port)
        self.ser = None
        self.state = None
        self.serial_no = None
        self.buf = ""

    # ---- line handling ----
    def open(self):
        if self.ser is None:
            self.ser = serial.serial_for_url(self.port, baudrate=self.args.baud, timeout=0.1)
            self.log.event(self.name_, f"opened {self.port} @ {self.args.baud}", show=False)

    def close(self):
        if self.ser is not None:
            try:
                self.ser.close()
            except Exception:
                pass
        self.ser = None

    def write(self, data):
        self.ser.write(data)
        self.ser.flush()

    def listen(self, seconds, until=None):
        """Read for `seconds` (or until `until(text)` holds after a quiet
        moment). Returns what came; every chunk is checked for banners."""
        deadline = time.monotonic() + seconds
        got = ""
        last = time.monotonic()
        while time.monotonic() < deadline and not self.stop.is_set():
            chunk = self.ser.read(self.ser.in_waiting or 1)
            if chunk:
                got += chunk.decode("latin-1")
                last = time.monotonic()
                continue
            if until and until(got) and time.monotonic() - last >= 0.2:
                break
        if got:
            self.log.event(self.name_, "rx " + printable(got).strip().replace("\r\n", " | ")
                           .replace("\n", " | ").replace("\r", " | "), show=False)
        return got

    # ---- probing ----
    def probe(self):
        """(state, how, reply_s, banner_text)"""
        t0 = time.monotonic()
        self.ser.reset_input_buffer()
        self.write(b"\r")
        got = self.listen(1.5, until=lambda t: PROMPT.search(t) is not None)
        if PROMPT.search(got):
            return "ALIVE", "prompt", time.monotonic() - t0, got
        t0 = time.monotonic()
        self.ser.send_break(self.args.break_s)
        got += self.listen(5.0, until=lambda t: PROMPT.search(t) is not None)
        if PROMPT.search(got) or WAKEUP.search(got):
            return "ALIVE", "break", time.monotonic() - t0, got
        return ("NOISE" if got.strip() else "SILENT"), "break", None, got

    def classify(self, text, prompted):
        """Name a wake-up banner; unprompted ones are the interesting ones."""
        for m in WAKEUP.finditer(text):
            kind = m.group(1).upper()
            chan = (m.group(2) or "").upper()
            what = f"[{kind} Wakeup{(' ' + chan) if chan else ''}]"
            if kind == "POWERON":
                why = "instrument lost power and restarted"
            elif chan == "B":
                why = "break on channel B (shared RS-485 sync pair)"
            else:
                why = "break on its own line" + ("" if prompted else " — not sent by us")
            loud = not (prompted and kind == "BREAK" and chan != "B")
            self.log.event(self.name_, f"{'!!' if loud else '  '} {what}: {why}"
                                       f"{'' if prompted else ' (UNPROMPTED)'}", show=True)
            yield what

    def run(self):
        next_probe = 0.0
        while not self.stop.is_set():
            try:
                self.open()
                if time.monotonic() >= next_probe:
                    state, how, dt, got = self.probe()
                    m = SERIAL.search(got)
                    if m:
                        self.serial_no = m.group(1)
                    wakeups = list(self.classify(got, prompted=True))
                    if state != self.state:
                        extra = f" in {dt:.1f} s via {how}" if dt is not None else ""
                        if state == "NOISE":
                            extra = ": bytes but no prompt — wrong baud or a disturbed line: " \
                                    + printable(got).strip()[:80] + f" (hex {got.encode('latin-1')[:32].hex(' ')})"
                        self.log.event(self.name_, f"==> {state}{extra}")
                    self.state = state
                    self.log.probe(self.name_, state, how, dt, ";".join(wakeups), self.serial_no)
                    next_probe = time.monotonic() + self.args.interval
                else:
                    got = self.listen(min(1.0, max(0.1, next_probe - time.monotonic())))
                    if got.strip():
                        list(self.classify(got, prompted=False))
                        if not WAKEUP.search(got):
                            self.log.event(self.name_, "unprompted bytes: " + printable(got).strip()[:80]
                                           + f" (hex {got.encode('latin-1')[:32].hex(' ')})")
            except (serial.SerialException, OSError, EOFError) as e:
                if self.state != "PORT-ERROR":
                    self.log.event(self.name_, f"==> PORT-ERROR: {e}; reopening")
                self.state = "PORT-ERROR"
                self.log.probe(self.name_, "PORT-ERROR", "", None, "", self.serial_no)
                self.close()
                self.stop.wait(3.0)
                next_probe = 0.0

    def power_down(self):
        try:
            self.open()
            self.write(b"CZ\r")
            self.listen(1.0)
            self.log.event(self.name_, "sent CZ (power down)")
        except Exception as e:
            self.log.event(self.name_, f"CZ failed: {e}")
        self.close()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", action="append", help="port or rfc2217:// URL; repeat for several "
                                                    "(default: serial-server ports 3001 and 3002)")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--break", dest="break_s", type=float, default=0.5)
    ap.add_argument("--interval", type=float, default=5.0, help="seconds between probes of a port")
    ap.add_argument("--no-sleep", action="store_true", help="do not send CZ at the end")
    ap.add_argument("--out", default="workhorse_monitor_" + datetime.now().strftime("%Y%m%d_%H%M%S"))
    args = ap.parse_args()

    out = Path(args.out)
    log = Log(out)
    stop = threading.Event()
    watches = [PortWatch(p, args, log, stop) for p in (args.port or DEFAULT_PORTS)]
    log.event("", f"monitoring {', '.join(w.port for w in watches)}; log in {out}/")
    print("Type a note + Enter to mark the log (e.g. 'plugged in 20507'); Ctrl-C to stop.\n", flush=True)
    for w in watches:
        w.start()
    try:
        for line in sys.stdin:
            if line.strip():
                log.event("NOTE", line.strip())
                with print_lock:
                    print("   states: " + "  ".join(f"{w.name_}={w.state}" for w in watches), flush=True)
        stop.wait()           # stdin closed: keep running until Ctrl-C
    except KeyboardInterrupt:
        pass
    stop.set()
    for w in watches:
        w.join(timeout=8)
    if not args.no_sleep:
        for w in watches:
            w.power_down()
    log.event("", "stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
