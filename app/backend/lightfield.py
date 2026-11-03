"""The LIFI (light-field camera) recordings, browsed by eye.

The recordings are too big to look through directly: 13392x9528 frames,
about 70 MB each stored, 2000 to a 15-minute HDF5 file on NAS_B. The
pisco_index_lifi tool (pisco_frame_finder) reads them once on the workers
and leaves a background-subtracted thumbnail and view per frame under
LIFI_INDEX_DIR, one folder per recording (see its lifi_index.py). This
module reads that index for the Lightfield page, queues the index and
export tasks, and serves what an export wrote, so a frame picked on the
page ends up whole on the computer the page is open on.

The Hub itself only reads one full frame when someone asks for a zoom
into it, one at a time.
"""
import csv
import json
import os
import re
import threading
from datetime import datetime, timezone

import numpy as np

from .models import GlobalSetting
from .zip_stream import zip_stream  # noqa: F401  (the export download)

INDEX_TOOL = "pisco_index_lifi"
EXPORT_TOOL = "pisco_export_lifi"
FORMATS = ("png", "jpg", "tif")
NO_CAST = "(outside casts)"

_STEM = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_zoom_lock = threading.Semaphore(1)
_cache = {}
_cache_lock = threading.Lock()


class LightfieldError(Exception):
    pass


def settings(app):
    data_root = GlobalSetting.get("DATA_ROOT", os.path.join(app.config["HUB_ROOT"], "data"))
    return {
        "index": GlobalSetting.get("LIFI_INDEX_DIR", "") or os.path.join(data_root, "lifi_index"),
        "source": GlobalSetting.get("LIFI_SOURCE_DIR", "") or "",
    }


# ---- reading the index --------------------------------------------------------

def _check_stem(stem):
    if not isinstance(stem, str) or not _STEM.match(stem):
        raise LightfieldError(f"not a recording: {stem!r}")
    return stem


def _cached(path, loader):
    """loader(path), reread when the file changes."""
    try:
        mtime = os.stat(path).st_mtime_ns
    except FileNotFoundError:
        return None
    with _cache_lock:
        hit = _cache.get(path)
        if hit and hit[0] == mtime:
            return hit[1]
    value = loader(path)
    with _cache_lock:
        _cache[path] = (mtime, value)
    return value


def _read_json(path):
    with open(path) as f:
        return json.load(f)


def _read_frames(path):
    rows = []
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            try:
                rows.append({"index": int(r["index"]), "time": r["time_utc"],
                             "window": r["window"] or NO_CAST,
                             "mean": float(r["mean"]), "peak": float(r["peak"]),
                             "activity": float(r["activity"])})
            except (KeyError, ValueError):
                continue                       # a half-written row
    return rows


def recording(root, stem):
    """info.json of one indexed recording, or None."""
    return _cached(os.path.join(root, _check_stem(stem), "info.json"), _read_json)


def frames(root, stem):
    return _cached(os.path.join(root, _check_stem(stem), "frames.csv"), _read_frames) or []


def recordings(root):
    """Indexed recordings, oldest first, with their frames counted per cast."""
    if not os.path.isdir(root):
        return []
    out = []
    for stem in sorted(os.listdir(root)):
        if not _STEM.match(stem) or stem in ("exports", "skipped"):
            continue
        if not os.path.exists(os.path.join(root, stem, "frames.csv")):
            continue
        info = recording(root, stem) or {}
        rows = frames(root, stem)
        casts = {}
        for r in rows:
            casts[r["window"]] = casts.get(r["window"], 0) + 1
        out.append({
            "stem": stem, "file": info.get("file"), "n_frames": info.get("n_frames"),
            "indexed": len(rows), "steps": info.get("steps", []), "updated": info.get("updated"),
            "first": rows[0]["time"] if rows else None, "last": rows[-1]["time"] if rows else None,
            "casts": casts,
        })
    return out


def casts(recs):
    """The casts the indexed frames belong to, in time order."""
    by = {}
    for rec in recs:
        for label, n in rec["casts"].items():
            c = by.setdefault(label, {"label": label, "frames": 0, "recordings": [],
                                      "first": rec["first"]})
            c["frames"] += n
            c["recordings"].append(rec["stem"])
    return sorted(by.values(), key=lambda c: (c["label"] == NO_CAST, c["first"] or ""))


def cast_frames(root, recs, label):
    """Every indexed frame of one cast across its recordings."""
    out = []
    for rec in recs:
        if label not in rec["casts"]:
            continue
        for r in frames(root, rec["stem"]):
            if r["window"] == label:
                out.append(dict(r, stem=rec["stem"]))
    return out


# Why a recording in the source folder is not in the index. The index
# tool notes its reason in skipped/<stem>.json; readability is checked
# here, since a file's mode can change after the index ran.
LEFT_OUT = ("unreadable", "broken", "outside_casts", "no_cast_frames", "waiting")


def sources(source, root):
    """Every recording in the source folder, sorted by what became of it:
    {"total", "indexed", "left_out": {reason: [{"stem", "detail"}]}}."""
    out = {"total": 0, "indexed": 0, "left_out": {r: [] for r in LEFT_OUT}}
    if not source or not os.path.isdir(source):
        return out
    for name in sorted(os.listdir(source)):
        if not (name.startswith("lifi_") and name.endswith(".h5")):
            continue
        stem = name[:-3]
        out["total"] += 1
        if _STEM.match(stem) and os.path.exists(os.path.join(root, stem, "frames.csv")):
            out["indexed"] += 1
            continue
        path = os.path.join(source, name)
        if not os.access(path, os.R_OK):
            reason, detail = "unreadable", f"mode {os.stat(path).st_mode & 0o777:o}"
        else:
            try:
                note = _cached(os.path.join(root, "skipped", stem + ".json"), _read_json) or {}
            except ValueError:
                note = {}
            reason, detail = note.get("reason"), note.get("detail", "")
            if reason not in LEFT_OUT or reason == "unreadable":   # readable by now
                reason, detail = "waiting", ""
        out["left_out"][reason].append({"stem": stem, "detail": detail})
    return out


def image_path(root, stem, kind, index):
    if kind not in ("thumbs", "views"):
        raise LightfieldError(f"unknown image kind {kind!r}")
    return os.path.join(root, _check_stem(stem), kind, f"{int(index):05d}.jpg")


# ---- the closer look ----------------------------------------------------------

def zoom(path, index, fx, fy, width=1600, height=1000, stretch=True):
    """JPEG of a full-resolution window of a frame, centred on the
    fractions (fx, fy) of its width and height. The frame is one chunk,
    so the whole of it is read; one zoom at a time keeps the Hub light."""
    import cv2 as cv
    import h5py
    try:
        import hdf5plugin  # noqa: F401
    except ImportError:
        pass
    with _zoom_lock:
        with h5py.File(path, "r") as f:
            ds = f["frames"]
            h, w = int(ds.shape[1]), int(ds.shape[2])
            if not 0 <= index < ds.shape[0]:
                raise LightfieldError(f"frame {index} is not in {os.path.basename(path)}")
            frame = ds[index]
    width, height = min(width, w), min(height, h)
    x0 = int(np.clip(fx * w - width / 2, 0, w - width))
    y0 = int(np.clip(fy * h - height / 2, 0, h - height))
    crop = frame[y0:y0 + height, x0:x0 + width]
    if stretch:
        lo, hi = np.percentile(crop, [1, 99.97])
        crop = (np.clip((crop.astype(np.float32) - lo) / max(hi - lo, 1), 0, 1) ** 0.5 * 255).astype(np.uint8)
    ok, data = cv.imencode(".jpg", crop, [cv.IMWRITE_JPEG_QUALITY, 90])
    return data.tobytes(), {"x": x0, "y": y0, "width": width, "height": height,
                            "frame_width": w, "frame_height": h}


# ---- queueing -----------------------------------------------------------------

def index_specs(root, source, step, shards, windows_path):
    """Task specs for `shards` index workflows over the whole source."""
    shards = max(1, int(shards))
    return [{"tool": INDEX_TOOL, "settings": {
        "sourcePath": source, "targetPath": root, "windows": windows_path,
        "step": int(step), "shard": f"{k}/{shards}"}} for k in range(shards)]


def write_windows(root, operations):
    """The CTD casts as the index tool reads them (in-water to on-deck)."""
    os.makedirs(root, exist_ok=True)
    path = os.path.join(root, "windows.json")
    with open(path + ".tmp", "w") as f:
        json.dump({"operations": operations}, f)
    os.replace(path + ".tmp", path)
    return path


def _slug(text):
    return re.sub(r"[^A-Za-z0-9_-]+", "_", text or "").strip("_")[:40]


def new_export(root, picks, fmt, label="", now=None):
    """(export folder name, task settings) for frames picked as
    [{stem, index}]. Paths come from the index, never from the page."""
    if fmt not in FORMATS:
        raise LightfieldError(f"format must be one of {', '.join(FORMATS)}")
    if not picks:
        raise LightfieldError("no frames picked")
    chosen = []
    for p in picks:
        info = recording(root, p.get("stem"))
        if not info or not info.get("file"):
            raise LightfieldError(f"{p.get('stem')!r} is not an indexed recording")
        try:
            index = int(p["index"])
        except (KeyError, TypeError, ValueError):
            raise LightfieldError(f"bad frame index in {p!r}") from None
        if not 0 <= index < int(info.get("n_frames") or 0):
            raise LightfieldError(f"{p['stem']} has no frame {index}")
        chosen.append({"file": info["file"], "index": index})
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%d-%H%M%S")
    name = stamp + ("_" + _slug(label) if _slug(label) else "")
    target = os.path.join(root, "exports", name)
    os.makedirs(target, exist_ok=False)
    return name, {"frames": chosen, "format": fmt, "targetPath": target}


def record_export(root, name, workflow_id, settings, label):
    with open(os.path.join(root, "exports", name, "request.json"), "w") as f:
        json.dump({"workflow_id": workflow_id, "label": label, "format": settings["format"],
                   "frames": settings["frames"],
                   "created": datetime.now(timezone.utc).isoformat(timespec="seconds")}, f, indent=2)


def export_dir(root, name):
    if not _STEM.match(name or ""):
        raise LightfieldError(f"not an export: {name!r}")
    path = os.path.join(root, "exports", name)
    if not os.path.isdir(path):
        raise LightfieldError(f"no export {name!r}")
    return path


def exports(root):
    """Exports, newest first: what was asked for and what is written."""
    base = os.path.join(root, "exports")
    if not os.path.isdir(base):
        return []
    out = []
    for name in sorted(os.listdir(base), reverse=True):
        path = os.path.join(base, name)
        req_path = os.path.join(path, "request.json")
        if not os.path.exists(req_path):
            continue
        try:
            req = _read_json(req_path)
            manifest = _read_json(os.path.join(path, "manifest.json")) \
                if os.path.exists(os.path.join(path, "manifest.json")) else {}
        except ValueError:
            continue                           # being written
        files = manifest.get("files", [])
        out.append({"name": name, "label": req.get("label", ""), "format": req.get("format"),
                    "created": req.get("created"), "workflow_id": req.get("workflow_id"),
                    "requested": len(req.get("frames", [])),
                    "files": [{"name": f["name"], "bytes": f["bytes"], "time": f.get("time_utc")}
                              for f in files],
                    "errors": manifest.get("errors", []),
                    "bytes": sum(f["bytes"] for f in files)})
    return out


def export_file(root, name, filename):
    path = os.path.join(export_dir(root, name), os.path.basename(filename))
    if filename.startswith(".") or not os.path.isfile(path) or filename.endswith(".json"):
        raise LightfieldError(f"no file {filename!r} in {name}")
    return path
