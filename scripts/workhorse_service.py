#!/usr/bin/env python3
"""Wake a Teledyne RDI WorkHorse ADCP with a hardware break, dump its
configuration, and optionally erase its recorder.

    python scripts/workhorse_service.py                  # read only
    python scripts/workhorse_service.py --erase          # read, then RE ErAsE
    python scripts/workhorse_service.py --set-clock      # read, then clock to UTC
    python scripts/workhorse_service.py --ignore-channel-b   # read, then SB0 + CK

The script keeps sending breaks until the instrument answers (so it can be
started before the instrument is powered), tries the usual baud rates if
none is given, and writes a transcript plus a config dump to --out. Every
step is retried after a fresh break when the link drops mid-way, which is
what BBTalk did not survive. Nothing is changed on the instrument except
the recorder erase, and only with --erase; no CR1/CK is ever sent.
"""
import argparse
import glob
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import serial

BAUDS = [9600, 115200, 19200, 38400, 57600, 4800, 2400, 1200]
PROMPT = re.compile(r"(^|[\r\n])>\s*$")
BANNER = re.compile(r"RD Instruments|Teledyne|WorkHorse|Workhorse|\[BREAK", re.I)
ERROR = re.compile(r"\bERR\s*\d+[^\r\n]*")

# What to read: system description, clock, recorder state, and the help
# listing of every command group, which shows each setting's current value.
READ_COMMANDS = ["PS0", "PS3", "OL", "TS?", "RA", "RS", "RR",
                 "C?", "E?", "W?", "B?", "T?", "S?", "P?", "R?", "A?"]


class LinkLost(Exception):
    pass


class WorkHorse:
    def __init__(self, port, log):
        self.port = port
        self.log = log
        self.ser = None
        self.baud = None

    def _note(self, kind, text):
        stamp = datetime.now(timezone.utc).strftime("%H:%M:%S.%f")[:-3]
        for line in text.splitlines() or [""]:
            self.log.write(f"{stamp} {kind:4} {line}\n")
        self.log.flush()
        if kind in ("info", "rx"):
            print(text if kind == "rx" else f"-- {text}", flush=True)

    def open(self, baud):
        if self.ser is not None and self.ser.is_open:
            if self.baud != baud:
                self.ser.baudrate = baud
        else:
            # serial_for_url also takes the Hub's serial server ports,
            # e.g. rfc2217://10.11.15.10:3002?ign_set_control
            self.ser = serial.serial_for_url(self.port, baudrate=baud, timeout=0.1)
        self.baud = baud

    def close(self):
        if self.ser is not None:
            try:
                self.ser.close()
            except Exception:
                pass
        self.ser = None

    def write(self, text):
        self.ser.write(text.encode("ascii"))
        self.ser.flush()
        self._note("tx", text.rstrip("\r"))

    def read_until(self, done, timeout, quiet=0.3):
        deadline = time.monotonic() + timeout
        buf = bytearray()
        last = time.monotonic()
        while time.monotonic() < deadline:
            chunk = self.ser.read(self.ser.in_waiting or 1)
            if chunk:
                buf += chunk
                last = time.monotonic()
                continue
            text = buf.decode("latin-1")
            if done(text) and time.monotonic() - last >= quiet:
                return text, True
        return buf.decode("latin-1"), False

    @staticmethod
    def printable(text):
        return "".join(c if c in "\r\n\t" or 32 <= ord(c) < 127 else "." for c in text)

    def try_wake(self, baud, break_s):
        """One hardware break at one baud. Returns the banner or None."""
        self.open(baud)
        self.ser.reset_input_buffer()
        self._note("info", f"hardware break ({break_s:.1f} s) at {baud} baud")
        self.ser.send_break(break_s)
        text, ok = self.read_until(lambda t: PROMPT.search(t) is not None, 4.0, quiet=0.5)
        if not ok:
            # banner may have come but the prompt got lost; ask for it again
            self.write("\r")
            more, ok = self.read_until(lambda t: PROMPT.search(t) is not None, 2.0)
            text += more
        if text.strip():
            self._note("rx", self.printable(text).strip("\r\n"))
        if ok and (BANNER.search(text) or text.strip() == ">" or PROMPT.search(text)):
            return text
        return None

    def wake(self, bauds, break_s, wait_s, interval):
        """Keep breaking until the instrument answers, cycling the bauds."""
        start = time.monotonic()
        while True:
            for baud in bauds:
                try:
                    banner = self.try_wake(baud, break_s)
                except (serial.SerialException, OSError) as e:
                    self._note("info", f"port error: {e}; reopening")
                    self.close()
                    time.sleep(1.0)
                    continue
                if banner:
                    self._note("info", f"instrument answered at {baud} baud")
                    return banner
            if wait_s and time.monotonic() - start > wait_s:
                raise SystemExit(f"no answer within {wait_s:.0f} s — check power, cable and port")
            self._note("info", f"no answer yet, breaking again in {interval:.0f} s")
            time.sleep(interval)

    def command(self, cmd, timeout=8.0):
        self.ser.reset_input_buffer()
        self.write(cmd + "\r")
        text, ok = self.read_until(lambda t: PROMPT.search(t) is not None, timeout)
        if text.strip():
            self._note("rx", self.printable(text).strip("\r\n"))
        if not ok:
            raise LinkLost(f"no prompt after {cmd!r} within {timeout:.0f} s")
        reply = PROMPT.sub("", text).strip("\r\n ")
        if reply.upper().startswith(cmd.upper()):
            reply = reply[len(cmd):].strip("\r\n ")
        return reply


def find_port():
    ports = sorted(glob.glob("/dev/cu.usbserial*") + glob.glob("/dev/cu.PL2303*")
                   + glob.glob("/dev/cu.usbmodem*") + glob.glob("/dev/ttyUSB*"))
    if not ports:
        raise SystemExit("no USB serial adapter found; give --port")
    if len(ports) > 1:
        print(f"several adapters, using {ports[0]}: {ports}")
    return ports[0]


def run_step(wh, args, cmd, timeout=8.0, retries=4):
    """A command that survives a dropped link: re-break and send it again."""
    for attempt in range(retries + 1):
        try:
            return wh.command(cmd, timeout)
        except (LinkLost, serial.SerialException, OSError) as e:
            wh._note("info", f"{e} (attempt {attempt + 1}); waking again")
            wh.wake([wh.baud] + [b for b in args.bauds if b != wh.baud],
                    args.break_s, args.wait, args.interval)
    raise SystemExit(f"gave up on {cmd!r} after {retries + 1} attempts")


def set_clock(wh, args, out):
    """TS takes effect when the carriage return arrives, so send it on the
    second it names."""
    before = run_step(wh, args, "TS?")
    now = time.time()
    target = int(now) + 2                       # leave time for the write
    time.sleep(target - now - 0.01)
    stamp = datetime.fromtimestamp(target, timezone.utc).strftime("%y/%m/%d,%H:%M:%S")
    run_step(wh, args, f"TS{stamp}", retries=0)
    after = run_step(wh, args, "TS?")
    wh._note("info", f"clock set to {stamp} UTC; host UTC now "
                     f"{datetime.now(timezone.utc):%H:%M:%S}")
    with open(out / "config.txt", "a") as f:
        f.write(f"\n## clock\nbefore: {before}\nset:    TS{stamp} (UTC)\nafter:  {after}\n")


def ignore_channel_b(wh, args, out):
    """SB0, saved with CK so it survives a power cycle. CK saves every
    current setting as the user default, which is what the Hub's setup
    does anyway."""
    before = run_step(wh, args, "SB?")
    lines = [f"before: {before}"]
    for cmd in ("SB0", "CK"):
        reply = run_step(wh, args, cmd, timeout=15.0)
        err = ERROR.search(reply)
        lines.append(f"{cmd}: {reply or 'ok'}")
        if err:
            with open(out / "config.txt", "a") as f:
                f.write("\n## channel B\n" + "\n".join(lines) + "\n")
            raise SystemExit(f"{cmd} rejected: {err.group(0)}")
    after = run_step(wh, args, "SB?")
    lines.append(f"after:  {after}")
    wh._note("info", "channel B breaks now ignored (SB0, kept with CK)")
    with open(out / "config.txt", "a") as f:
        f.write("\n## channel B\n" + "\n".join(lines) + "\n")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", help="serial device or rfc2217:// URL (default: first USB serial adapter)")
    ap.add_argument("--baud", type=int, help="only try this baud rate (default: scan the usual ones)")
    ap.add_argument("--break", dest="break_s", type=float, default=0.5,
                    help="hardware break length in seconds (RDI wants > 0.3)")
    ap.add_argument("--wait", type=float, default=0,
                    help="give up after this many seconds without an answer (default: never)")
    ap.add_argument("--interval", type=float, default=3.0, help="pause between break rounds")
    ap.add_argument("--ignore-channel-b", action="store_true",
                    help="SB0 + CK: ignore breaks on channel B (the RS-485 sync pair), kept over power cycles")
    ap.add_argument("--set-clock", action="store_true", help="set the instrument clock to this computer's UTC time")
    ap.add_argument("--erase", action="store_true", help="erase the recorder (RE ErAsE) after reading")
    ap.add_argument("--no-sleep", action="store_true",
                    help="leave it awake at the end instead of sending CZ (power down)")
    ap.add_argument("--out", default="workhorse_" + datetime.now().strftime("%Y%m%d_%H%M%S"),
                    help="directory for transcript and config dump")
    args = ap.parse_args()
    args.bauds = [args.baud] if args.baud else BAUDS

    port = args.port or find_port()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "transcript.log", "w") as log:
        wh = WorkHorse(port, log)
        wh._note("info", f"port {port}; waiting for the instrument (Ctrl-C to stop)")
        try:
            banner = wh.wake(args.bauds, args.break_s, args.wait, args.interval)

            # Erase-first would be faster on a flaky link, but the directory
            # listing goes into the dump before anything is destroyed.
            dump = [f"# WorkHorse config read {datetime.now(timezone.utc).isoformat()} "
                    f"on {port} at {wh.baud} baud\n", "## wake-up banner\n",
                    wh.printable(banner).strip() + "\n"]
            for cmd in READ_COMMANDS:
                timeout = 30.0 if cmd == "RR" else 8.0
                reply = run_step(wh, args, cmd, timeout)
                dump += [f"\n## {cmd}\n", reply + "\n"]
                (out / "config.txt").write_text("".join(dump))   # keep what we have if it dies
            print(f"\nconfig written to {out / 'config.txt'}")

            if args.set_clock:
                set_clock(wh, args, out)

            if args.ignore_channel_b:
                ignore_channel_b(wh, args, out)

            if args.erase:
                wh._note("info", "erasing recorder (RE ErAsE) — this can take minutes")
                try:
                    reply = wh.command("RE ErAsE", timeout=900)
                    if ERROR.search(reply):
                        raise SystemExit(f"erase rejected: {ERROR.search(reply).group(0)}")
                except (LinkLost, serial.SerialException, OSError) as e:
                    wh._note("info", f"link lost during erase ({e}); waking to check")
                    wh.wake([wh.baud], args.break_s, args.wait, args.interval)
                after = {cmd: run_step(wh, args, cmd, 30.0) for cmd in ("RA", "RS", "RR")}
                with open(out / "config.txt", "a") as f:
                    for cmd, reply in after.items():
                        f.write(f"\n## {cmd} after erase\n{reply}\n")
                print("\nafter erase:\n" + "\n".join(f"{k}: {v}" for k, v in after.items()))

            if not args.no_sleep:
                wh.write("CZ\r")
                text, _ = wh.read_until(lambda t: False, 1.5)
                if text.strip():
                    wh._note("rx", wh.printable(text).strip())
                wh._note("info", "sent CZ: instrument powered down until the next break")
        except KeyboardInterrupt:
            wh._note("info", "stopped by user")
            return 130
        finally:
            wh.close()
    print(f"transcript: {out / 'transcript.log'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
