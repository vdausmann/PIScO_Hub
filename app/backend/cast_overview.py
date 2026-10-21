"""Which CTD cast each instrument recording belongs to, as a CSV.

Many instruments ride on the CTD rosette, and each keeps its own files
under its own names and numbers; the DSHIP CTD cast is what ties them
together (see dship.py). For every recording this module finds its
start and end in the data themselves, picks the CTD operation it
overlaps most — from LEAD_S before the CTD goes in until it is back on
deck — and writes one row per recording with that cast's station,
times and position. Casts without a recording get a row of their own,
so a gap shows as a gap.

Instruments:

  LADCP  the PD0 files under the LADCP data folder: the recorder
         downloads (<NNN>/, <NNN>_1/ …) and the live serial captures
         (<NNN>/serial/), times from the ensembles. The profile folder's
         dship.json is checked against the match.
  LISST  LISST-Deep raw files (L<day of year><HHMM>.RBN): a 1080-byte
         header, then 120-byte records marked 0xDADA, 1 Hz, with year,
         month, day, hour, minute and second in words 43-48. The
         instrument keeps its own clock (MSM147: PDT, 7 h behind UTC),
         corrected by LISST_CLOCK_OFFSET_H. Processed exports (.csv,
         _rs.csv, .rtx) are tied to the raw file by their first time.
"""

import csv
import io
import json
import os
import struct
from datetime import datetime, timezone

from . import dship

# Shorter than this is a deck test, not a cast.
MIN_RECORDING_S = 60

CAST_COLUMNS = ["cast", "station", "operation", "ctd_in_water_utc", "ctd_max_depth_utc",
                "ctd_on_deck_utc", "lat", "lon", "water_depth_m"]


def _iso(epoch_s):
    if epoch_s is None:
        return ""
    return datetime.fromtimestamp(epoch_s, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def cast_ops(ops, device="CTD"):
    """The cast device's numbered operations, in cast order."""
    return sorted((o for o in ops if o["device"] == device and o["cast"] is not None),
                  key=lambda o: o["cast"])


def match(ops, start_s, end_s=None):
    """The cast a recording from start_s to end_s belongs to: the one it
    overlaps most, counting from LEAD_S before in-water to on deck."""
    end_s = start_s if end_s is None else end_s
    best, best_overlap = None, None
    for op in ops:
        if op["start"] is None:
            continue
        lo = op["start"] / 1000 - dship.LEAD_S
        hi = op["end"] / 1000 if op["end"] is not None else datetime.now(timezone.utc).timestamp()
        overlap = min(end_s, hi) - max(start_s, lo)
        if overlap < 0:
            continue
        if best is None or overlap > best_overlap:
            best, best_overlap = op, overlap
    return best


def cast_fields(op):
    if op is None:
        return {k: "" for k in CAST_COLUMNS}

    def t(event):
        return _iso(event["time"] / 1000) if event and event.get("time") is not None else ""
    position = op["in_water"] or (op["events"][0] if op["events"] else None) or {}
    return {
        "cast": op["cast"], "station": op["station"] or "", "operation": op["name"],
        "ctd_in_water_utc": t(op["in_water"]), "ctd_max_depth_utc": t(op["max_depth"]),
        "ctd_on_deck_utc": t(op["on_deck"]),
        "lat": "" if position.get("lat") is None else position["lat"],
        "lon": "" if position.get("lon") is None else position["lon"],
        "water_depth_m": "" if position.get("water_depth_m") is None else position["water_depth_m"],
    }


def _table(rows, ops, columns, missing_note):
    """Rows sorted by cast and time, plus one row per cast with none."""
    seen = {r["cast"] for r in rows if r["cast"] != ""}
    for op in ops:
        if op["cast"] not in seen:
            note = missing_note + (" (cast aborted in DSHIP)" if op["aborted"] else "")
            rows.append({**cast_fields(op), "note": note})
    rows.sort(key=lambda r: (r["cast"] == "", r["cast"] if r["cast"] != "" else 0, r.get("start_utc", "")))
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=CAST_COLUMNS + columns, extrasaction="ignore",
                            lineterminator="\n")
    writer.writeheader()
    for r in rows:
        writer.writerow({k: r.get(k, "") for k in CAST_COLUMNS + columns})
    return out.getvalue(), len(rows)


# ---- LADCP ----------------------------------------------------------------------------

LADCP_COLUMNS = ["profile", "file", "source", "looking", "start_utc", "end_utc", "ensembles",
                 "size_bytes", "note"]

_pd0_cache = {}


def _pd0_times(path):
    """Ensembles, first and last time of a PD0 file, cached by size and mtime."""
    from .ladcp import pd0
    st = os.stat(path)
    key = (path, st.st_size, st.st_mtime)
    if key not in _pd0_cache:
        n, first, last = 0, None, None
        try:
            for _, ens in pd0.iter_file(path):
                n += 1
                t = ens.get("time")
                if t is not None:
                    first = t if first is None else first
                    last = t
        except OSError:
            pass
        _pd0_cache[key] = (n, first, last)
    return _pd0_cache[key]


def ladcp_files(root):
    """(relative path, profile, source) for each PD0 file under root."""
    found = []
    for entry in sorted(os.listdir(root)) if os.path.isdir(root) else []:
        folder = os.path.join(root, entry)
        head = entry.split("_")[0]
        if not os.path.isdir(folder) or not head.isdigit():
            continue                      # logs/, _incoming/ (unfinished downloads)
        profile = int(head)
        for name in sorted(os.listdir(folder)):
            if name.upper().endswith(".000"):
                found.append((f"{entry}/{name}", profile, "recorder"))
        serial = os.path.join(folder, "serial")
        if os.path.isdir(serial):
            for name in sorted(os.listdir(serial)):
                if name.lower().endswith(".bin"):
                    found.append((f"{entry}/serial/{name}", profile, "serial"))
    return found


def _dship_json(root, profile):
    try:
        with open(os.path.join(root, f"{profile:03d}", "dship.json")) as f:
            return json.load(f).get("cast")
    except (OSError, ValueError):
        return None


def ladcp_overview(root, ops):
    """CSV text and row count for the LADCP data under root."""
    ops = cast_ops(ops)
    rows, seen = [], {}
    for rel, profile, source in ladcp_files(root):
        path = os.path.join(root, rel)
        n, first, last = _pd0_times(path)
        size = os.path.getsize(path)
        name = os.path.basename(rel).upper()
        looking = "up" if "UP" in name[3:6] else "down" if "DN" in name[3:6] else ""
        notes = []
        op = match(ops, first, last) if first is not None else None
        if n == 0:
            notes.append("no valid ensembles")
        elif op is None:
            notes.append("no CTD cast at this time")
        labelled = _dship_json(root, profile)
        if labelled is not None and op is not None and labelled != op["cast"]:
            notes.append(f"dship.json says cast {labelled}")
        twin = seen.get((source, size, n, first, last))
        if twin:
            notes.append(f"same data as {twin}")
        seen.setdefault((source, size, n, first, last), rel)
        if source == "serial":
            notes.append("live capture over the cable")
        rows.append({**cast_fields(op), "profile": f"{profile:03d}", "file": rel, "source": source,
                     "looking": looking, "start_utc": _iso(first), "end_utc": _iso(last),
                     "ensembles": n, "size_bytes": size, "note": "; ".join(notes)})
    return _table(rows, ops, LADCP_COLUMNS, "no LADCP file found")


# ---- LISST ----------------------------------------------------------------------------

LISST_COLUMNS = ["file", "start_lisst_clock", "start_utc", "end_utc", "duration_min", "records",
                 "size_bytes", "processed", "note"]

LISST_RECORD = 120
LISST_MARK = b"\xda\xda"


def _lisst_time(record):
    y, mo, d, h, mi, s = struct.unpack_from(">6H", record, 86)
    try:
        return datetime(y, mo, d, h, mi, s, tzinfo=timezone.utc).timestamp()
    except ValueError:
        return None


def lisst_rbn(path):
    """(records, first time, last time) of a LISST-Deep .RBN file, in the
    instrument's clock."""
    with open(path, "rb") as f:
        data = f.read()
    start = 0
    while True:
        start = data.find(LISST_MARK, start)
        if start < 0 or start + LISST_RECORD > len(data):
            return 0, None, None
        if _lisst_time(data[start:start + LISST_RECORD]) is not None:
            break
        start += 1
    n = (len(data) - start) // LISST_RECORD
    first = _lisst_time(data[start:start + LISST_RECORD])
    for i in range(n - 1, -1, -1):
        rec = data[start + i * LISST_RECORD:start + (i + 1) * LISST_RECORD]
        if rec[:2] == LISST_MARK and _lisst_time(rec) is not None:
            return n, first, _lisst_time(rec)
    return n, first, first


def lisst_processed(root):
    """{first time: [file names]} for the processed exports: a .csv's
    first row holds year, month, day, hour, minute, second in columns
    43-48; its _rs.csv and .rtx go with it."""
    by_stem = {}
    for name in sorted(os.listdir(root)):
        stem, ext = os.path.splitext(name)
        if ext.lower() in (".csv", ".rtx"):
            by_stem.setdefault(stem[:-3] if stem.endswith("_rs") else stem, []).append(name)
    found = {}
    for stem, names in by_stem.items():
        try:
            with open(os.path.join(root, stem + ".csv")) as f:
                cols = f.readline().split(",")
            t = datetime(*(int(float(c)) for c in cols[42:48]), tzinfo=timezone.utc).timestamp()
        except (OSError, ValueError, TypeError, IndexError):
            continue
        found.setdefault(t, []).extend(names)
    return found


def lisst_overview(root, ops, offset_h=0.0):
    """CSV text and row count for the LISST files under root."""
    ops = cast_ops(ops)
    shift = offset_h * 3600
    processed = lisst_processed(root) if os.path.isdir(root) else {}
    rows = []
    for name in sorted(os.listdir(root)) if os.path.isdir(root) else []:
        if not name.upper().endswith(".RBN"):
            continue
        path = os.path.join(root, name)
        n, first, last = lisst_rbn(path)
        notes = []
        op = None
        if first is None:
            notes.append("no records")
        elif (last - first) < MIN_RECORDING_S:
            notes.append(f"test recording ({int(last - first) + 1} s), not matched to a cast")
        else:
            op = match(ops, first + shift, last + shift)
            if op is None:
                notes.append("no CTD cast at this time")
        rows.append({**cast_fields(op), "file": name,
                     "start_lisst_clock": "" if first is None else
                     datetime.fromtimestamp(first, timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
                     "start_utc": _iso(None if first is None else first + shift),
                     "end_utc": _iso(None if last is None else last + shift),
                     "duration_min": "" if first is None else round((last - first) / 60, 1),
                     "records": n, "size_bytes": os.path.getsize(path),
                     "processed": " ".join(processed.get(first, [])), "note": "; ".join(notes)})
    return _table(rows, ops, LISST_COLUMNS, "no LISST file found")
