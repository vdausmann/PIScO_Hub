"""Teledyne RDI PD0 ensembles: parse, decode from a stream, summarise a file.

Layout (WorkHorse Commands and Output Data Format, PD0 chapter): an
ensemble starts with 0x7F 0x7F, then the byte count (excluding the
two-byte checksum), a spare byte, the number of data types and their
offsets from the ensemble start. Each data type begins with its two-byte
ID. The checksum is the sum of all preceding bytes modulo 65536.

Grew out of Marco Schulz's read_adcp.py (ladcp_live); the field offsets
are his, checked against the manual, plus the ones the old AutoIt tool
read for its post-download summary (transmit voltage and current, the
ensemble clock).

Velocities are kept in the instrument's coordinate system — for an LADCP
that is beam coordinates (EX00000) — in m/s with bad values as NaN.
"""

import struct
from datetime import datetime, timezone

import numpy as np

HEADER = b"\x7f\x7f"
MAX_ENSEMBLE = 16384          # far above any WorkHorse ensemble; a guard against noise

FIXED_LEADER = 0x0000
VARIABLE_LEADER = 0x0080
VELOCITY = 0x0100
CORRELATION = 0x0200
ECHO_INTENSITY = 0x0300
PERCENT_GOOD = 0x0400
STATUS = 0x0500
BOTTOM_TRACK = 0x0600

FREQUENCIES_KHZ = {0: 75, 1: 150, 2: 300, 3: 600, 4: 1200, 5: 2400}
BEAM_ANGLES = {0: 15, 1: 20, 2: 30}
BAD_VELOCITY = -32768


class PD0Error(ValueError):
    pass


def checksum(block):
    return sum(block) & 0xFFFF


def _u8(b, o):
    return b[o]


def _u16(b, o):
    return b[o] | (b[o + 1] << 8)


def _s16(b, o):
    v = _u16(b, o)
    return v - 65536 if v >= 32768 else v


def _u32(b, o):
    return struct.unpack_from("<I", b, o)[0]


def _clock(year, month, day, hour, minute, second, hundredths):
    try:
        return datetime(year, month, day, hour, minute, second, hundredths * 10000,
                        tzinfo=timezone.utc).timestamp()
    except ValueError:
        return None


def parse_ensemble(block):
    """One complete ensemble (header through checksum) → dict. Raises
    PD0Error if it is not a well-formed, checksummed ensemble."""
    if len(block) < 8 or block[:2] != HEADER:
        raise PD0Error("not an ensemble header")
    n_bytes = _u16(block, 2)
    if n_bytes + 2 != len(block):
        raise PD0Error(f"length {len(block)} != header's {n_bytes} + 2")
    if checksum(block[:n_bytes]) != _u16(block, n_bytes):
        raise PD0Error("checksum mismatch")
    n_types = block[5]
    offsets = [_u16(block, 6 + 2 * i) for i in range(n_types)]

    ens = {"types": []}
    fixed = None
    for off in offsets:
        if off + 2 > n_bytes:
            raise PD0Error(f"data type offset {off} beyond the ensemble")
        type_id = _u16(block, off)
        ens["types"].append(type_id)
        if type_id == FIXED_LEADER:
            fixed = off
            _fixed_leader(block, off, ens)
        elif type_id == VARIABLE_LEADER:
            _variable_leader(block, off, ens)
    if fixed is None:
        raise PD0Error("no fixed leader")

    shape = (ens["n_cells"], ens["n_beams"])
    count = shape[0] * shape[1]
    for off in offsets:
        type_id = _u16(block, off)
        start = off + 2
        if type_id == VELOCITY:
            raw = np.frombuffer(block, "<i2", count, start).astype(float)
            raw[raw == BAD_VELOCITY] = np.nan
            ens["vel"] = raw.reshape(shape) / 1000.0
        elif type_id in (CORRELATION, ECHO_INTENSITY, PERCENT_GOOD):
            arr = np.frombuffer(block, "u1", count, start).reshape(shape).copy()
            ens[{CORRELATION: "corr", ECHO_INTENSITY: "ei", PERCENT_GOOD: "pg"}[type_id]] = arr
    return ens


def _fixed_leader(b, o, ens):
    config_lsb, config_msb = b[o + 4], b[o + 5]
    ens.update(
        firmware=f"{b[o + 2]}.{b[o + 3]:02d}",
        frequency_khz=FREQUENCIES_KHZ.get(config_lsb & 0x07),
        convex=bool(config_lsb & 0x08),
        up=bool(config_lsb & 0x80),
        beam_angle=BEAM_ANGLES.get(config_msb & 0x03, 20),
        simulated=bool(b[o + 6]),
        n_beams=b[o + 8],
        n_cells=b[o + 9],
        pings_per_ensemble=_u16(b, o + 10),
        cell_m=_u16(b, o + 12) / 100.0,
        blank_m=_u16(b, o + 14) / 100.0,
        profiling_mode=b[o + 16],
        coord_transform=b[o + 25],
        sensor_source=b[o + 30],
        bin1_m=_u16(b, o + 32) / 100.0,
        xmit_pulse_m=_u16(b, o + 34) / 100.0,
        bandwidth=_u16(b, o + 50),
        power=b[o + 52],
    )
    # Older firmware ends the fixed leader before the serial number.
    ens["serial"] = _u32(b, o + 54) if o + 58 <= len(b) - 2 else None


def _variable_leader(b, o, ens):
    number = _u16(b, o + 2) + (b[o + 11] << 16)
    rtc = _clock(2000 + b[o + 4] if b[o + 4] < 80 else 1900 + b[o + 4], *b[o + 5:o + 11])
    y2k = None
    if o + 65 <= len(b) - 2:
        century, year = b[o + 57], b[o + 58]
        if century:
            y2k = _clock(century * 100 + year, *b[o + 59:o + 65])
    ens.update(
        number=number,
        time=y2k if y2k is not None else rtc,
        bit_result=_u16(b, o + 12),
        sound_speed=_u16(b, o + 14),
        depth_xdcr_m=_u16(b, o + 16) / 10.0,
        heading=_u16(b, o + 18) / 100.0,
        pitch=_s16(b, o + 20) / 100.0,
        roll=_s16(b, o + 22) / 100.0,
        salinity=_u16(b, o + 24),
        temperature=_s16(b, o + 26) / 100.0,
        # ADC channel 0 is transmit current, channel 1 transmit voltage:
        # raw counts. The scale depends on the instrument's frequency and
        # power board, so it is applied where it is configured.
        xmit_current_raw=b[o + 34],
        xmit_voltage_raw=b[o + 35],
        error_status=_u32(b, o + 42),
        pressure_dbar=_u32(b, o + 48) / 1000.0 if o + 52 <= len(b) - 2 else None,
    )


# ---- finding ensembles in bytes -----------------------------------------------------

class StreamDecoder:
    """Feed it bytes as they arrive from the serial line; it hands back
    the complete, valid ensembles and keeps any partial one for the next
    feed. Garbage between ensembles (a command echo, line noise, a
    banner after a break) is skipped and counted."""

    def __init__(self):
        self.buffer = bytearray()
        self.skipped = 0
        self.bad = 0

    def feed(self, data):
        self.buffer += data
        out = []
        buf = self.buffer
        pos = 0
        while True:
            start = buf.find(HEADER, pos)
            if start < 0:
                # keep a trailing 0x7F, it may be half a header
                keep = 1 if buf.endswith(b"\x7f") else 0
                self.skipped += len(buf) - pos - keep
                pos = len(buf) - keep
                break
            self.skipped += start - pos
            if len(buf) - start < 6:
                pos = start
                break
            n_bytes = _u16(buf, start + 2)
            if n_bytes < 8 or n_bytes > MAX_ENSEMBLE or buf[start + 5] == 0:
                pos = start + 1
                self.skipped += 1
                continue
            end = start + n_bytes + 2
            if end > len(buf):
                pos = start
                break
            block = bytes(buf[start:end])
            try:
                out.append((block, parse_ensemble(block)))
                pos = end
            except PD0Error:
                self.bad += 1
                self.skipped += 1
                pos = start + 1
        del buf[:pos]
        return out


def iter_file(path):
    """(raw block, ensemble) for every valid ensemble in a PD0 file."""
    decoder = StreamDecoder()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(1 << 20)
            if not chunk:
                break
            yield from decoder.feed(chunk)


def clean_file(path, dest):
    """Write only the valid ensembles of `path` to `dest` (what Marco's
    clean_pd0 did for serial recordings). Returns (kept, skipped bytes)."""
    kept = 0
    decoder = StreamDecoder()
    with open(path, "rb") as f, open(dest, "wb") as out:
        while True:
            chunk = f.read(1 << 20)
            if not chunk:
                break
            for block, _ in decoder.feed(chunk):
                out.write(block)
                kept += 1
    return kept, decoder.skipped


# ---- derived quantities ---------------------------------------------------------------

def vertical_velocity(ens, cells=slice(0, 4)):
    """Rough vertical speed of the instrument through the water (m/s,
    positive downward) from the first few cells' beam velocities: the
    mean along-beam velocity over all beams, divided by cos(beam angle).
    Sign as the old LADCP tool's depth integral had it — positive beam
    velocity on a down-looker means it is descending. Assumes still water
    and a level instrument; good enough to show how deep the package is,
    not for processing. None when the data are not in beam coordinates."""
    vel = ens.get("vel")
    if vel is None or (ens.get("coord_transform", 0) & 0x18) != 0:
        return None
    sample = vel[cells]
    if not np.isfinite(sample).any():
        return None
    w = float(np.nanmean(sample)) / np.cos(np.radians(ens.get("beam_angle", 20)))
    return -w if ens.get("up") else w


def bottom_distance(ens, threshold=25, min_beams=3):
    """Distance (m, along the instrument axis) to a hard echo in the
    profile — the seafloor for a down-looker, the surface for an
    up-looker — or None. Echo intensity falls off with range, so the
    near cells are always the strongest; a hard target is a rise: per
    beam, the cell (beyond the first two) that rises most above the
    cells up to three before it, if that rise is at least half the
    `threshold` and the cell stands `threshold` counts above the beam's
    median. At least `min_beams` beams must agree within two cells."""
    ei = ens.get("ei")
    if ei is None or ei.shape[0] < 5:
        return None
    hits = []
    for beam in range(ei.shape[1]):
        profile = ei[:, beam].astype(float)
        median = np.median(profile[2:])
        best, best_rise = None, threshold / 2.0
        for k in range(2, len(profile)):
            rise = profile[k] - profile[max(0, k - 3):k - 1].min()
            if rise >= best_rise and profile[k] - median >= threshold:
                best, best_rise = k, rise
        if best is not None:
            hits.append(best)
    if len(hits) < min_beams:
        return None
    hits.sort()
    middle = hits[len(hits) // 2]
    agree = [h for h in hits if abs(h - middle) <= 2]
    if len(agree) < min_beams:
        return None
    cell = float(np.median(agree))
    return ens["bin1_m"] + cell * ens["cell_m"]


def summarize(paths, voltage_scale=None, current_scale=None):
    """What the old tool reported after a download, for one instrument's
    files of a profile: ensembles, start/end time, ping hours, transmit
    voltage (start/mean/end) and current, and the depth reached from the
    integrated vertical velocity."""
    n = 0
    first = last = None
    volts, amps = [], []
    depth = max_depth = 0.0
    prev_t = None
    info = {}
    for path in paths:
        for _, ens in iter_file(path):
            n += 1
            t = ens.get("time")
            if first is None:
                first = t
                info = {k: ens.get(k) for k in ("serial", "up", "frequency_khz", "n_cells",
                                                "cell_m", "bin1_m", "firmware", "coord_transform")}
            last = t
            volts.append(ens.get("xmit_voltage_raw", 0))
            amps.append(ens.get("xmit_current_raw", 0))
            w = vertical_velocity(ens)
            if w is not None and t is not None and prev_t is not None and 0 < t - prev_t < 60:
                depth += w * (t - prev_t)
                max_depth = max(max_depth, depth)
            prev_t = t
    if n == 0:
        return {"ensembles": 0, **info}
    v = np.array(volts, float)
    a = np.array(amps, float)
    raw = {"voltage_mean_raw": round(float(v.mean()), 1), "current_mean_raw": round(float(a.mean()), 1)}
    if voltage_scale:
        v = v * voltage_scale
    if current_scale:
        a = a * current_scale
    return {
        **info,
        "ensembles": n,
        "start": first,
        "end": last,
        "ping_hours": (last - first) / 3600.0 if first is not None and last is not None else None,
        "voltage_start": round(float(v[min(1, n - 1)]), 2),
        "voltage_mean": round(float(v.mean()), 2),
        "voltage_end": round(float(v[-1]), 2),
        "current_mean": round(float(a.mean()), 2),
        "voltage_scaled": bool(voltage_scale),
        **raw,
        "max_depth_m": round(float(max_depth), 1),
        "end_depth_m": round(float(depth), 1),
    }


# ---- writing ensembles (the simulator and the tests) ----------------------------------

def encode_ensemble(*, number, time, n_cells, cell_m=10.0, bin1_m=None, blank_m=0.0,
                    up=False, frequency_khz=300, beam_angle=20, heading=0.0, pitch=0.0,
                    roll=0.0, temperature=10.0, xmit_voltage_raw=150, xmit_current_raw=60,
                    serial=0, pings=1, profiling_mode=15, coord_transform=0,
                    sensor_source=0b0011101, power=255, vel=None, corr=None, ei=None, pg=None,
                    depth_xdcr_m=0.0, pressure_dbar=0.0):
    """A WorkHorse-shaped PD0 ensemble. Arrays are (n_cells, 4); vel in
    m/s with NaN for bad."""
    n_beams = 4
    count = n_cells * n_beams
    freq_bits = {v: k for k, v in FREQUENCIES_KHZ.items()}[frequency_khz]
    angle_bits = {v: k for k, v in BEAM_ANGLES.items()}[beam_angle]
    if bin1_m is None:
        bin1_m = blank_m + cell_m

    fixed = bytearray(59)
    struct.pack_into("<HBB", fixed, 0, FIXED_LEADER, 51, 41)
    fixed[4] = freq_bits | 0x08 | 0x40 | (0x80 if up else 0)
    fixed[5] = angle_bits | 0x40
    fixed[8], fixed[9] = n_beams, n_cells
    struct.pack_into("<HHH", fixed, 10, pings, round(cell_m * 100), round(blank_m * 100))
    fixed[16] = profiling_mode
    fixed[25] = coord_transform
    fixed[30] = sensor_source
    fixed[31] = 0b0011101
    struct.pack_into("<HH", fixed, 32, round(bin1_m * 100), round(cell_m * 100))
    fixed[52] = power
    struct.pack_into("<I", fixed, 54, serial)
    fixed[58] = beam_angle

    dt = datetime.fromtimestamp(time, tz=timezone.utc)
    hundredths = dt.microsecond // 10000
    var = bytearray(65)
    struct.pack_into("<HH", var, 0, VARIABLE_LEADER, number & 0xFFFF)
    var[4:11] = bytes([dt.year % 100, dt.month, dt.day, dt.hour, dt.minute, dt.second, hundredths])
    var[11] = (number >> 16) & 0xFF
    struct.pack_into("<HHHhhHh", var, 14, 1500, round(depth_xdcr_m * 10), round(heading * 100) % 36000,
                     round(pitch * 100), round(roll * 100), 35, round(temperature * 100))
    var[34], var[35] = xmit_current_raw, xmit_voltage_raw
    struct.pack_into("<I", var, 48, round(pressure_dbar * 1000))
    var[57:65] = bytes([dt.year // 100, dt.year % 100, dt.month, dt.day, dt.hour, dt.minute,
                        dt.second, hundredths])

    def block(type_id, payload):
        return struct.pack("<H", type_id) + payload

    if vel is None:
        vel = np.zeros((n_cells, n_beams))
    v = np.where(np.isfinite(vel), np.round(np.nan_to_num(vel) * 1000), BAD_VELOCITY)
    parts = [bytes(fixed), bytes(var),
             block(VELOCITY, np.clip(v, -32768, 32767).astype("<i2").tobytes())]
    for type_id, arr, default in ((CORRELATION, corr, 120), (ECHO_INTENSITY, ei, 60),
                                  (PERCENT_GOOD, pg, 100)):
        if arr is None:
            arr = np.full((n_cells, n_beams), default)
        parts.append(block(type_id, np.clip(arr, 0, 255).astype("u1").tobytes()))

    header_len = 6 + 2 * len(parts)
    offsets, pos = [], header_len
    for p in parts:
        offsets.append(pos)
        pos += len(p)
    n_bytes = pos
    out = bytearray(HEADER) + struct.pack("<HBB", n_bytes, 0, len(parts))
    for off in offsets:
        out += struct.pack("<H", off)
    for p in parts:
        out += p
    assert len(out) == n_bytes and count == n_cells * n_beams
    out += struct.pack("<H", checksum(out))
    return bytes(out)
