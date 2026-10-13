"""A quick look at a profile's frames before anything is processed.

The camera names each frame after where it was taken,

    M203_012-01_PISCO2_0009.94dbar-38.58N-027.32W-22.18C_20260921-05400026

so listing the frame directory is enough to draw the profile's depth
over time — no image is opened. The new-workflow page plots it so the
part worth processing can be chosen by eye; the choice goes to the
preparer as `skipFirstNFrames` / `skipLastNFrames`.

Those are counts, so the order here must be the preparer's exactly:
`ImageDirSource` in pisco_profile_dataset (same extensions, empty files
dropped, not recursive, sorted by frame time, then by the name's counter
or else the position in the name-sorted listing, then by name). Change one, change the other.
"""

import math
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from .models import GlobalSetting

IMAGE_EXTENSIONS = (".png", ".jp2", ".j2k", ".tif", ".tiff", ".bmp", ".jpg", ".jpeg")

# The convention, as pisco_profile_dataset.naming parses it.
_FRAME_RE = re.compile(
    r"^(?P<cruise>[^_]+)_(?P<station>[^_]+)_(?P<instrument>[^_]+)_"
    r"(?P<meta>[^_]+)_(?P<datetime>\d{8}-\d+)$")
_META_RE = re.compile(
    r"^(?P<pressure>[\d.]+)(?P<punit>d?bar)"
    r"(?:-(?P<lat>[\d.]+)(?P<latdir>[NS])-(?P<lon>[\d.]+)(?P<londir>[EW]))?"
    r"(?:-(?P<temp>[\d.]+)C)?$")
# Names outside the convention: any "<number>dbar" and a date-time stamp.
_LOOSE_PRESSURE = re.compile(r"(\d+(?:\.\d+)?)(d?bar)")
_LOOSE_TIME = re.compile(r"(\d{8})[-_T](\d{6})(?:\.(\d{1,6}))?(?:[-_](\d{1,10}))?")

# Points sent to the browser; the plot cannot show more anyway.
MAX_POINTS = 4000


class PreviewError(Exception):
    pass


def parse_name(stem):
    """(time in epoch seconds or NaN, counter or -1, pressure in dbar or
    None, metadata dict or None) from a file name without extension.
    Metadata only for names that follow the convention."""
    m = _FRAME_RE.match(stem)
    if m:
        date_part, _, tail = m.group("datetime").partition("-")
        try:
            time = datetime.strptime(date_part + tail[:6], "%Y%m%d%H%M%S") \
                .replace(tzinfo=timezone.utc).timestamp()
            time += (int(tail[6:]) if tail[6:] else 0) * 0.01
        except ValueError:
            m = None
    if m:
        meta = {"cruise": m.group("cruise"), "station": m.group("station"),
                "instrument": m.group("instrument")}
        pressure = None
        mm = _META_RE.match(m.group("meta"))
        if mm:
            pressure = float(mm.group("pressure")) * (10.0 if mm.group("punit") == "bar" else 1.0)
            if mm.group("lat"):
                lat, lon = float(mm.group("lat")), float(mm.group("lon"))
                meta["lat"] = -lat if mm.group("latdir") == "S" else lat
                meta["lon"] = -lon if mm.group("londir") == "W" else lon
        meta["date"] = datetime.fromtimestamp(time, tz=timezone.utc).strftime("%Y-%m-%d")
        return time, -1, pressure, meta

    pressure = None
    p = _LOOSE_PRESSURE.search(stem)
    if p:
        pressure = float(p.group(1)) * (10.0 if p.group(2) == "bar" else 1.0)
    time, counter = math.nan, -1
    t = _LOOSE_TIME.search(stem)
    if t:
        counter = int(t.group(4)) if t.group(4) else -1
        try:
            time = datetime.strptime(t.group(1) + t.group(2), "%Y%m%d%H%M%S") \
                .replace(tzinfo=timezone.utc).timestamp()
            if t.group(3):
                time += float("0." + t.group(3))
        except ValueError:
            pass
    return time, counter, pressure, None


def _resolve(path):
    """The directory, kept below FILE_EXPLORER_ROOT like every browsable path."""
    root = Path(GlobalSetting.get("FILE_EXPLORER_ROOT", default="/") or "/").resolve()
    target = (root / path).resolve()
    if target != root and root not in target.parents:
        raise PreviewError("path outside FILE_EXPLORER_ROOT")
    if not target.is_dir():
        raise PreviewError(f"not a directory: {target}")
    return target


def scan(path):
    """The frames of a directory in the preparer's order, with what their
    names say."""
    directory = _resolve(path)
    rows = []
    with os.scandir(directory) as entries:
        names = sorted(e.name for e in entries)
    for name in names:
        stem, ext = os.path.splitext(name)
        if ext.lower() not in IMAGE_EXTENSIONS:
            continue
        try:
            if os.path.getsize(directory / name) == 0:
                continue
        except OSError:
            continue
        time, counter, pressure, meta = parse_name(stem)
        rows.append((time, counter if counter >= 0 else len(rows), name, pressure, meta))
    if not rows:
        raise PreviewError(f"no image files in {directory}")

    rows.sort(key=lambda r: (r[0] if not math.isnan(r[0]) else math.inf, r[1], r[2]))

    n = len(rows)
    stride = max(1, math.ceil(n / MAX_POINTS))
    points = []
    for i in range(0, n, stride):
        time, _, _, pressure, _ = rows[i]
        points.append([i, None if math.isnan(time) else round(time, 2), pressure])
    if points[-1][0] != n - 1:
        time, _, _, pressure, _ = rows[-1]
        points.append([n - 1, None if math.isnan(time) else round(time, 2), pressure])

    pressures = [r[3] for r in rows if r[3] is not None]
    deepest = max(range(n), key=lambda i: rows[i][3] if rows[i][3] is not None else -1) \
        if pressures else None
    first_meta = next((r[4] for r in rows if r[4]), None)
    return {
        "path": str(directory),
        "frames": n,
        "with_pressure": len(pressures),
        "first": rows[0][2],
        "last": rows[-1][2],
        "max_pressure": max(pressures) if pressures else None,
        "deepest_frame": deepest,
        "metadata": first_meta,
        "points": points,
    }
