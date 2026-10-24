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

A folder of the PISCO3 recorder's `.h5` files is read like the
preparer's `PiscoHdf5Source`: the files whose names overlap the cast's
time window, `n_frames` of each, `timestamp_ms` as the time, frames in
time order, then the window applied — the skips count within it, as in
`prepare()`. Those names carry no pressure; it comes from the sensor log
field when that holds the Keller logs (`kellerlog_*.csv`, P1_bar), the
way the quick-look video gets it. The window applies to an image folder
too, should one be given.

The field of view is detected here as well (see fov.py), on frames spread
over the kept part.
"""

import csv
import glob
import math
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from . import fov
from .models import GlobalSetting

IMAGE_EXTENSIONS = (".png", ".jp2", ".j2k", ".tif", ".tiff", ".bmp", ".jpg", ".jpeg")
HDF5_EXTENSIONS = (".h5", ".hdf5")

# The convention, as pisco_profile_dataset.naming parses it.
_FRAME_RE = re.compile(
    r"^(?P<cruise>[^_]+)_(?P<station>[^_]+)_(?P<instrument>[^_]+)_"
    r"(?P<meta>[^_]+)_(?P<datetime>\d{8}-\d+)$")
_META_RE = re.compile(
    r"^(?P<pressure>[\d.]+)(?P<punit>d?bar)"
    r"(?:-(?P<lat>[\d.]+)(?P<latdir>[NS])-(?P<lon>[\d.]+)(?P<londir>[EW]))?"
    r"(?:-(?P<temp>[\d.]+)C)?$")
# The PISCO3 camera's own names (MSM147): 20261012-04121098_000.953bar_22.18C,
# time to the centisecond and pressure in bar. Mirrors
# pisco_profile_dataset.naming.parse_camera_name.
_CAMERA_RE = re.compile(
    r"^(?P<date>\d{8})-(?P<clock>\d{6})(?P<centi>\d{2})"
    r"_(?P<pressure>-?[\d.]+)(?P<punit>d?bar)(?:_(?P<temp>-?[\d.]+)C)?$")
# Names outside both: any "<number>dbar" and a date-time stamp.
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

    c = _CAMERA_RE.match(stem)
    if c:
        try:
            time = datetime.strptime(c.group("date") + c.group("clock"), "%Y%m%d%H%M%S") \
                .replace(tzinfo=timezone.utc).timestamp() + int(c.group("centi")) * 0.01
            pressure = float(c.group("pressure")) * (10.0 if c.group("punit") == "bar" else 1.0)
            return time, -1, pressure, None
        except ValueError:
            pass

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


def _resolve(path, kind="directory"):
    """The path, kept below FILE_EXPLORER_ROOT like every browsable path."""
    root = Path(GlobalSetting.get("FILE_EXPLORER_ROOT", default="/") or "/").resolve()
    target = (root / path).resolve()
    if target != root and root not in target.parents:
        raise PreviewError("path outside FILE_EXPLORER_ROOT")
    if kind == "directory" and not target.is_dir():
        raise PreviewError(f"not a directory: {target}")
    if kind == "any" and not target.exists():
        raise PreviewError(f"no such file or directory: {target}")
    return target


def parse_time(value):
    """Epoch seconds from the form's start/end time (ISO 8601 or epoch,
    UTC when no zone is given), None when empty."""
    if value in (None, ""):
        return None
    text = str(value).strip()
    try:
        return float(text)
    except ValueError:
        pass
    try:
        when = datetime.fromisoformat(text.replace("Z", "+00:00").replace(" ", "T"))
    except ValueError:
        raise PreviewError(f"not a time: {text!r} (ISO 8601 or epoch seconds, UTC)")
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when.timestamp()


class Listing:
    """The frames of a profile in the preparer's order: rows of (time,
    order, key, pressure, metadata), and how to load one."""

    def __init__(self, kind, directory, rows, warnings=()):
        self.kind, self.directory, self.rows = kind, directory, rows
        self.warnings = list(warnings)

    def name(self, index):
        key = self.rows[index][2]
        return key if self.kind == "images" else f"{os.path.basename(key[0])}[{key[1]}]"

    def load(self, index):
        """The frame as an array, unchanged."""
        key = self.rows[index][2]
        if self.kind == "images":
            import cv2
            image = cv2.imread(str(self.directory / key), cv2.IMREAD_UNCHANGED)
            if image is None:
                raise PreviewError(f"cannot decode {key}")
            if image.ndim == 3:
                image = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY if image.shape[2] == 3
                                     else cv2.COLOR_BGRA2GRAY)
            return image
        import h5py
        import hdf5plugin  # noqa: F401  (the recorder compresses with blosc)
        path, i = key
        with h5py.File(path, "r") as f:
            frame = f["frames"][i]
            if str(_attr(f, "layout", "uint16")) == "packed" and \
                    str(_attr(f, "pixel_format", "Mono8")) in ("Mono12Packed", "Mono12"):
                raise PreviewError("Mono12Packed recordings are not shown in the preview")
            return frame


def _attr(f, key, default):
    value = f.attrs.get(key, default)
    return value.decode() if isinstance(value, bytes) else value


def _image_rows(directory):
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
    return rows


# Leeway around the start minute in a recording's name, as in the preparer.
NAME_SLACK = 120


def _name_minute(path):
    """Epoch seconds of the UTC start minute in `<prefix>_YYYYMMDD_HHMM.h5`."""
    parts = os.path.splitext(os.path.basename(path))[0].rsplit("_", 2)
    if len(parts) != 3:
        return None
    try:
        return datetime.strptime(parts[1] + parts[2], "%Y%m%d%H%M").replace(
            tzinfo=timezone.utc).timestamp()
    except ValueError:
        return None


def _in_window(files, start, end):
    if start is None and end is None:
        return files
    starts = [_name_minute(f) for f in files]
    if None in starts:
        return files
    keep = []
    for i, f in enumerate(files):
        t1 = starts[i + 1] if i + 1 < len(files) else math.inf
        if end is not None and starts[i] - NAME_SLACK > end:
            continue
        if start is not None and t1 + NAME_SLACK < start:
            continue
        keep.append(f)
    return keep


def _hdf5_rows(files):
    import h5py
    import numpy as np
    rows, warnings = [], []
    for path in files:
        try:
            with h5py.File(path, "r") as f:
                if "frames" not in f:
                    warnings.append(f"{os.path.basename(path)} is not a recording (no /frames)")
                    continue
                times = f["timestamp_ms"][()] if "timestamp_ms" in f else None
                if "n_frames" in f.attrs:
                    n = int(f.attrs["n_frames"])
                elif times is not None:
                    n = int(np.count_nonzero(times))
                else:
                    n = f["frames"].shape[0]
                n = min(n, f["frames"].shape[0])
        except OSError as e:
            # The preparer stops on such a file; better to know now.
            warnings.append(f"{os.path.basename(path)} cannot be opened ({e}); "
                            "the preparer will fail on it")
            continue
        for i in range(n):
            time = float(times[i]) / 1000.0 if times is not None else math.nan
            rows.append((time, len(rows), (path, i), None, None))
    return rows, warnings


def _keller_files(path, start, end):
    """The Keller logs for a sensor-log field: the file, or those of a
    folder whose names (kellerlog_YYYYMMDD_HHMMSS.csv) overlap the window."""
    if os.path.isfile(path):
        return [path]
    files = sorted(glob.glob(os.path.join(path, "kellerlog_*.csv")))
    stamped = []
    for f in files:
        m = re.search(r"(\d{8})_(\d{6})", os.path.basename(f))
        t = datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S").replace(
            tzinfo=timezone.utc).timestamp() if m else None
        stamped.append((t, f))
    if start is None and end is None:
        return files
    keep = []
    for i, (t, f) in enumerate(stamped):
        nxt = stamped[i + 1][0] if i + 1 < len(stamped) else math.inf
        if t is None or ((end is None or t <= end) and (start is None or (nxt or math.inf) >= start)):
            keep.append(f)
    return keep


# A frame further than this from any log row gets no pressure.
MAX_SENSOR_GAP = 5.0


def _keller_pressure(path, times, start, end):
    """Pressure in dbar at each time, interpolated in the Keller logs
    (P1_bar, times from UNIXTS or TS_ISO); None where none is close."""
    import numpy as np
    stamps, bars = [], []
    for name in _keller_files(path, start, end):
        with open(name, newline="", encoding="utf-8", errors="replace") as f:
            for row in csv.DictReader(f):
                try:
                    p = float(row["P1_bar"])
                    t = float(row["UNIXTS"]) if row.get("UNIXTS") else parse_time(row["TS_ISO"])
                except (KeyError, TypeError, ValueError, PreviewError):
                    continue
                stamps.append(t), bars.append(p)
    if not stamps:
        return None
    order = np.argsort(stamps)
    stamps, bars = np.asarray(stamps)[order], np.asarray(bars)[order] * 10.0
    times = np.asarray(times, dtype=float)
    out = np.interp(times, stamps, bars)
    at = np.clip(np.searchsorted(stamps, times), 1, len(stamps) - 1)
    gap = np.minimum(np.abs(times - stamps[at - 1]), np.abs(stamps[at] - times))
    out[~np.isfinite(times) | (gap > MAX_SENSOR_GAP)] = np.nan
    return [None if not math.isfinite(v) else round(float(v), 2) for v in out]


# The ordered listing of the last few profiles: the plot, the image
# preview, the field of view and every change of the kept range ask for
# the same one, and a cast can be 70 000 files on NFS. A changed folder
# has a new mtime.
_CACHE = {}
_CACHE_SIZE = 4


def listing(path, start=None, end=None, sensor_log=None):
    """The profile's frames in the preparer's order, inside the window."""
    directory = _resolve(path)
    start, end = parse_time(start), parse_time(end)
    log = _resolve(sensor_log, "any") if sensor_log else None
    key = (str(directory), directory.stat().st_mtime_ns, start, end,
           str(log) if log else None, log.stat().st_mtime_ns if log else None)
    if key in _CACHE:
        return _CACHE[key]

    rows = _image_rows(directory)
    kind, warnings = "images", []
    if not rows:
        files = sorted(str(directory / n) for n in os.listdir(directory)
                       if n.lower().endswith(HDF5_EXTENSIONS))
        if not files:
            raise PreviewError(f"no image files or .h5 recordings in {directory}")
        kind = "hdf5"
        files = _in_window(files, start, end)
        if not files:
            raise PreviewError("no recording in the folder overlaps the cast's time window")
        rows, warnings = _hdf5_rows(files)
        if not rows:
            raise PreviewError("; ".join(warnings) or f"no frames recorded in {directory}")
    rows.sort(key=lambda r: (r[0] if not math.isnan(r[0]) else math.inf, r[1], str(r[2])))

    if start is not None or end is not None:
        total = len(rows)
        lo = start if start is not None else -math.inf
        hi = end if end is not None else math.inf
        rows = [r for r in rows if lo <= r[0] <= hi]
        if not rows:
            raise PreviewError(f"none of the {total:,} frames lies in the cast's time window")

    if log is not None and any(r[3] is None for r in rows):
        try:
            pressures = _keller_pressure(str(log), [r[0] for r in rows], start, end)
        except OSError as e:
            pressures, warnings = None, warnings + [f"sensor log: {e}"]
        if pressures is None:
            warnings.append("the sensor log holds no Keller pressure (P1_bar) to show")
        else:
            rows = [r if r[3] is not None else (r[0], r[1], r[2], p, r[4])
                    for r, p in zip(rows, pressures)]

    result = Listing(kind, directory, rows, warnings)
    while len(_CACHE) >= _CACHE_SIZE:
        _CACHE.pop(next(iter(_CACHE)))
    _CACHE[key] = result
    return result


def scan(path, start=None, end=None, sensor_log=None):
    """The frames of a profile in the preparer's order, with what their
    names (or the sensor log) say."""
    found = listing(path, start, end, sensor_log)
    rows = found.rows

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
        "path": str(found.directory),
        "kind": found.kind,
        "frames": n,
        "with_pressure": len(pressures),
        "first": found.name(0),
        "last": found.name(n - 1),
        "max_pressure": max(pressures) if pressures else None,
        "deepest_frame": deepest,
        "metadata": first_meta,
        "warnings": found.warnings,
        "points": points,
    }


# Longest side of the preview image; the page shows it in a side column.
PREVIEW_SIZE = 900


def to_jpeg(image, size=PREVIEW_SIZE):
    """An 8-bit, scaled-down JPEG of a frame for the browser — the camera
    writes TIFF, often 12 or 16 bit, which browsers do not show."""
    import cv2
    import numpy as np
    height, width = image.shape[:2]
    if image.dtype != np.uint8:
        # Scale by what the frame actually uses, so a 12-bit image in a
        # 16-bit container is not nearly black; the 99.9th percentile
        # keeps a few hot pixels from setting the scale.
        top = float(np.percentile(image, 99.9)) or 1.0
        image = np.clip(image.astype(np.float32) * (255.0 / top), 0, 255).astype(np.uint8)
    scale = size / max(height, width)
    if scale < 1:
        image = cv2.resize(image, (round(width * scale), round(height * scale)),
                           interpolation=cv2.INTER_AREA)
    ok, jpeg = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 85])
    if not ok:
        raise PreviewError("cannot encode the frame")
    return jpeg.tobytes()


def frame_image(path, index, start=None, end=None, sensor_log=None):
    """(JPEG bytes, info dict) of frame `index` in the preparer's order."""
    found = listing(path, start, end, sensor_log)
    rows = found.rows
    index = min(max(int(index), 0), len(rows) - 1)
    time, _, _, pressure, _ = rows[index]
    image = found.load(index)
    height, width = image.shape[:2]
    return to_jpeg(image), {
        "index": index, "name": found.name(index), "width": width, "height": height,
        "bit_depth": image.dtype.itemsize * 8,
        "pressure": pressure, "time": None if math.isnan(time) else round(time, 2),
    }


# Frames the field of view is fitted on: enough for the median to drop
# what one frame holds, few enough to read quickly (PISCO3: 20 MB each).
FOV_FRAMES = 7


def detect_fov(path, first=0, last=None, start=None, end=None, sensor_log=None):
    """The field of view, fitted on frames spread over the kept part."""
    found = listing(path, start, end, sensor_log)
    n = len(found.rows)
    first = min(max(int(first or 0), 0), n - 1)
    last = n - 1 if last in (None, "") else min(max(int(last), first), n - 1)
    count = min(FOV_FRAMES, last - first + 1)
    picks = sorted({round(first + (last - first) * (k + 0.5) / count) for k in range(count)})
    try:
        result = fov.detect([found.load(i) for i in picks])
    except fov.FovError as e:
        raise PreviewError(f"field of view not found: {e}")
    result["frames"] = picks
    return result
