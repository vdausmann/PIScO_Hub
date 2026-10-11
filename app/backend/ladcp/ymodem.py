"""YMODEM batch receive — how a WorkHorse hands over its recorder files
after `RY` (TeraTerm's `ymodemrecv` in the old tool), and the matching
send for the simulator.

Receive: send 'C' (CRC mode); block 0 carries "name\\0size ..." and an
empty name ends the batch; data blocks are SOH (128 bytes) or STX (1024),
with sequence number, its complement and a CRC-16/XMODEM. EOT ends a
file: the first is NAKed and the repeat ACKed, as the protocol asks.
"""

import os
import time

SOH, STX, EOT, ACK, NAK, CAN, CRC = 0x01, 0x02, 0x04, 0x06, 0x15, 0x18, ord("C")


class YModemError(IOError):
    pass


def crc16(data, crc=0):
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) if crc & 0x8000 else (crc << 1)
            crc &= 0xFFFF
    return crc


class _Port:
    """Exact-length reads with a deadline on top of a pyserial port."""

    def __init__(self, ser):
        self.ser = ser

    def read(self, n, timeout):
        deadline = time.monotonic() + timeout
        out = bytearray()
        while len(out) < n:
            chunk = self.ser.read(n - len(out))
            if chunk:
                out += chunk
            elif time.monotonic() > deadline:
                break
        return bytes(out)

    def write(self, b):
        self.ser.write(bytes(b) if isinstance(b, (bytes, bytearray)) else bytes([b]))
        self.ser.flush()


def _read_block(port, first, timeout):
    size = 128 if first == SOH else 1024
    rest = port.read(2 + size + 2, timeout)
    if len(rest) < 2 + size + 2:
        return None, None
    seq, inv = rest[0], rest[1]
    data = rest[2:2 + size]
    if seq ^ inv != 0xFF or crc16(data) != (rest[-2] << 8 | rest[-1]):
        return seq, None
    return seq, data


def receive(ser, dest_dir, progress=None, timeout=10.0, retries=10, first_timeout=30.0):
    """Receive a YMODEM batch into dest_dir. Returns [(path, size)].
    `progress(name, received, total)` is called as blocks arrive."""
    port = _Port(ser)
    files = []
    os.makedirs(dest_dir, exist_ok=True)
    wait = first_timeout
    while True:
        # Ask for the header block with 'C' until the sender answers.
        # Text before the transfer (the command echo, "Sending...") is
        # skipped byte by byte within each attempt's window.
        header = None
        window = max(wait / retries, 1.0)
        for _ in range(retries):
            port.write(CRC)
            deadline = time.monotonic() + window
            while header is None and time.monotonic() < deadline:
                first = port.read(1, 0.2)
                if not first:
                    continue
                if first[0] == CAN:
                    raise YModemError("sender cancelled")
                if first[0] == EOT:
                    port.write(ACK)       # trailing EOT of the previous file
                    continue
                if first[0] not in (SOH, STX):
                    continue
                seq, data = _read_block(port, first[0], timeout)
                if seq == 0 and data is not None:
                    header = data
                else:
                    port.write(NAK)
            if header is not None:
                break
        if header is None:
            raise YModemError("no YMODEM header from the instrument")
        wait = timeout
        name = header.split(b"\0", 1)[0].decode("ascii", "replace")
        if not name:
            port.write(ACK)
            return files
        meta = header[len(name) + 1:].split(b"\0", 1)[0].split()
        total = int(meta[0]) if meta and meta[0].isdigit() else None
        port.write(ACK)
        path = os.path.join(dest_dir, os.path.basename(name))
        received = _receive_file(port, path, total, timeout, retries,
                                 (lambda r: progress(name, r, total)) if progress else None)
        files.append((path, received))


def _receive_file(port, path, total, timeout, retries, progress):
    expected = 1
    received = 0
    errors = 0
    tmp = path + ".part"
    with open(tmp, "wb") as out:
        port.write(CRC)
        eot_seen = False
        while True:
            first = port.read(1, timeout)
            if not first:
                errors += 1
                if errors > retries:
                    raise YModemError(f"{os.path.basename(path)}: timed out after {received} bytes")
                port.write(NAK)
                continue
            b = first[0]
            if b == EOT:
                if not eot_seen:
                    eot_seen = True
                    port.write(NAK)
                    continue
                port.write(ACK)
                break
            if b == CAN:
                raise YModemError("sender cancelled")
            if b not in (SOH, STX):
                continue
            seq, data = _read_block(port, b, timeout)
            if data is None:
                errors += 1
                if errors > retries:
                    raise YModemError(f"{os.path.basename(path)}: too many bad blocks")
                port.write(NAK)
                continue
            if seq == (expected - 1) & 0xFF:
                port.write(ACK)            # a repeat of a block we have
                continue
            if seq != expected & 0xFF:
                raise YModemError(f"{os.path.basename(path)}: block {seq}, expected {expected & 0xFF}")
            if total is not None:
                data = data[:max(0, total - received)]
            out.write(data)
            received += len(data)
            expected += 1
            errors = 0
            port.write(ACK)
            if progress:
                progress(received)
    if total is None:
        _strip_padding(tmp)
        received = os.path.getsize(tmp)
    os.replace(tmp, path)
    return received


def _strip_padding(path):
    with open(path, "rb+") as f:
        data = f.read()
        stripped = data.rstrip(b"\x1a")
        f.seek(0)
        f.truncate()
        f.write(stripped)


def send(ser, files, timeout=10.0, retries=10):
    """Send [(name, bytes)] as one YMODEM batch (the simulator's side)."""
    port = _Port(ser)

    def wait_for(expected, how_long):
        deadline = time.monotonic() + how_long
        while time.monotonic() < deadline:
            b = port.read(1, 0.5)
            if b and b[0] in expected:
                return b[0]
            if b and b[0] == CAN:
                raise YModemError("receiver cancelled")
        raise YModemError(f"no {expected} from the receiver")

    def block(seq, data, size):
        data = data.ljust(size, b"\x1a" if seq else b"\0")
        crc = crc16(data)
        return bytes([SOH if size == 128 else STX, seq & 0xFF, 0xFF - (seq & 0xFF)]) + data + \
            bytes([crc >> 8, crc & 0xFF])

    def send_block(packet):
        for _ in range(retries):
            port.write(packet)
            if wait_for((ACK, NAK), timeout) == ACK:
                return
        raise YModemError("block not acknowledged")

    for name, content in list(files) + [(None, None)]:
        wait_for((CRC,), timeout * 3)
        if name is None:
            send_block(block(0, b"", 128))
            return
        send_block(block(0, name.encode() + b"\0" + str(len(content)).encode(), 128))
        wait_for((CRC,), timeout)
        seq = 1
        for i in range(0, len(content), 1024):
            chunk = content[i:i + 1024]
            send_block(block(seq, chunk, 1024 if len(chunk) > 128 else 128))
            seq += 1
        for _ in range(retries):
            port.write(EOT)
            if wait_for((ACK, NAK), timeout) == ACK:
                break
