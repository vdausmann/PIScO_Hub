"""Reading the LIFI index, building exports from it, the zip download and
the zoom. Run: python -m pytest tests/lightfield"""

import csv
import io
import json
import os
import zipfile
from datetime import datetime, timezone

import h5py
import numpy as np
import pytest

from app.backend import lightfield
from app.backend.lightfield import LightfieldError

FIELDS = ["index", "time_utc", "window", "mean", "noise", "peak", "activity"]


def write_index(root, stem, file, rows, n_frames=100):
    d = os.path.join(root, stem)
    os.makedirs(os.path.join(d, "thumbs"))
    with open(os.path.join(d, "info.json"), "w") as f:
        json.dump({"file": file, "stem": stem, "n_frames": n_frames, "steps": [10]}, f)
    with open(os.path.join(d, "frames.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, FIELDS)
        w.writeheader()
        for index, t, window, activity in rows:
            w.writerow({"index": index, "time_utc": t, "window": window, "mean": 5,
                        "noise": 0.5, "peak": 9, "activity": activity})


@pytest.fixture
def index(tmp_path):
    root = str(tmp_path / "lifi_index")
    write_index(root, "lifi_20261012_2356", "/nas/lifi_20261012_2356.h5", [
        (0, "2026-10-12T23:56:01.000Z", "", 0.0),
        (10, "2026-10-12T23:58:01.000Z", "MSM147_2-15 (cast 8)", 0.002),
    ])
    write_index(root, "lifi_20261013_0011", "/nas/lifi_20261013_0011.h5", [
        (0, "2026-10-13T00:11:01.000Z", "MSM147_2-15 (cast 8)", 0.01),
        (10, "2026-10-13T00:12:01.000Z", "MSM147_2-15 (cast 8)", 0.0),
        (20, "2026-10-13T00:13:01.000Z", "garbage", "not a number"),   # half-written row
    ])
    os.makedirs(os.path.join(root, "exports"))
    return root


def test_casts_span_recordings_in_time_order(index):
    recs = lightfield.recordings(index)
    assert [r["stem"] for r in recs] == ["lifi_20261012_2356", "lifi_20261013_0011"]
    assert recs[1]["indexed"] == 2
    casts = lightfield.casts(recs)
    assert [c["label"] for c in casts] == ["MSM147_2-15 (cast 8)", lightfield.NO_CAST]
    assert casts[0]["frames"] == 3
    assert casts[0]["recordings"] == ["lifi_20261012_2356", "lifi_20261013_0011"]
    frames = lightfield.cast_frames(index, recs, "MSM147_2-15 (cast 8)")
    assert [(f["stem"][-4:], f["index"]) for f in frames] == [("2356", 10), ("0011", 0), ("0011", 10)]


def test_a_stem_cannot_leave_the_index(index):
    for stem in ("../etc", "", "a/b", None):
        with pytest.raises(LightfieldError):
            lightfield.frames(index, stem)
    with pytest.raises(LightfieldError):
        lightfield.image_path(index, "lifi_20261013_0011", "../../x", 1)


def test_export_takes_paths_from_the_index_not_the_page(index):
    now = datetime(2026, 11, 2, 10, 0, tzinfo=timezone.utc)
    name, settings = lightfield.new_export(
        index, [{"stem": "lifi_20261013_0011", "index": 10}], "png", "Copepod / big!", now)
    assert name == "20261102-100000_Copepod_big"
    assert settings["frames"] == [{"file": "/nas/lifi_20261013_0011.h5", "index": 10}]
    assert settings["targetPath"] == os.path.join(index, "exports", name)
    with pytest.raises(LightfieldError):
        lightfield.new_export(index, [{"stem": "lifi_20261013_0011", "index": 100}], "png", "", now)
    with pytest.raises(LightfieldError):
        lightfield.new_export(index, [{"stem": "unknown", "index": 1}], "png", "", now)
    with pytest.raises(LightfieldError):
        lightfield.new_export(index, [{"stem": "lifi_20261013_0011", "index": 1}], "exe", "", now)


def test_exports_list_what_was_written_and_zip_it(index):
    name, settings = lightfield.new_export(
        index, [{"stem": "lifi_20261013_0011", "index": 0}, {"stem": "lifi_20261013_0011", "index": 10}],
        "png", "two")
    lightfield.record_export(index, name, "wf-1", settings, "two")
    target = settings["targetPath"]
    payload = {}
    for i, size in ((0, 3000), (10, 70000)):
        fname = f"lifi_20261013_0011_{i:05d}.png"
        payload[fname] = os.urandom(size)
        with open(os.path.join(target, fname), "wb") as f:
            f.write(payload[fname])
    with open(os.path.join(target, "manifest.json"), "w") as f:
        json.dump({"files": [{"name": n, "bytes": len(b), "time_utc": ""} for n, b in payload.items()],
                   "errors": []}, f)

    (listed,) = lightfield.exports(index)
    assert listed["workflow_id"] == "wf-1" and listed["requested"] == 2
    assert listed["bytes"] == 73000

    paths = [lightfield.export_file(index, name, n) for n in payload]
    data = b"".join(lightfield.zip_stream(paths, chunk=4096))
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        assert zf.testzip() is None
        assert {n: zf.read(n) for n in zf.namelist()} == payload

    for bad in ("manifest.json", "../request.json", ".hidden"):
        with pytest.raises(LightfieldError):
            lightfield.export_file(index, name, bad)


def test_zoom_reads_a_full_resolution_window(tmp_path):
    path = str(tmp_path / "lifi.h5")
    frame = np.zeros((300, 500), np.uint8)
    frame[140:160, 440:460] = 200
    with h5py.File(path, "w") as f:
        ds = f.create_dataset("frames", (2, 300, 500), np.uint8, chunks=(1, 300, 500))
        ds[1] = frame
    data, window = lightfield.zoom(path, 1, 0.95, 0.5, width=100, height=80, stretch=False)
    assert window == {"x": 400, "y": 110, "width": 100, "height": 80, "frame_width": 500, "frame_height": 300}
    import cv2
    img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_GRAYSCALE)
    assert img.shape == (80, 100) and img[40, 50] > 150 and img[5, 5] < 30
    with pytest.raises(LightfieldError):
        lightfield.zoom(path, 5, 0.5, 0.5)


def test_index_specs_split_the_recordings():
    specs = lightfield.index_specs("/idx", "/nas/hdf5", 10, 3, "/idx/windows.json")
    assert [s["settings"]["shard"] for s in specs] == ["0/3", "1/3", "2/3"]
    assert all(s["tool"] == "pisco_index_lifi" and s["settings"]["step"] == 10 for s in specs)


def test_sources_say_why_a_recording_is_left_out(index, tmp_path):
    src = tmp_path / "hdf5"
    src.mkdir()
    for stem in ("lifi_20261012_2356", "lifi_20261013_0011", "lifi_20261013_0100",
                 "lifi_20261013_0200", "lifi_20261013_0300", "lifi_20261013_0400"):
        (src / f"{stem}.h5").write_bytes(b"")
    os.makedirs(os.path.join(index, "skipped"))
    for stem, reason in (("lifi_20261013_0100", "outside_casts"), ("lifi_20261013_0200", "broken"),
                         ("lifi_20261013_0300", "unreadable")):           # readable again since
        with open(os.path.join(index, "skipped", stem + ".json"), "w") as f:
            json.dump({"reason": reason, "detail": "why"}, f)
    if os.geteuid() != 0:
        os.chmod(src / "lifi_20261013_0400.h5", 0)

    got = lightfield.sources(str(src), index)
    stems = {r: [x["stem"] for x in v] for r, v in got["left_out"].items()}
    assert (got["total"], got["indexed"]) == (6, 2)
    assert stems["outside_casts"] == ["lifi_20261013_0100"]
    assert stems["broken"] == ["lifi_20261013_0200"]
    if os.geteuid() != 0:
        assert stems["unreadable"] == ["lifi_20261013_0400"]
        assert got["left_out"]["unreadable"][0]["detail"] == "mode 0"
        assert stems["waiting"] == ["lifi_20261013_0300"]
    assert [r["stem"] for r in lightfield.recordings(index)] == ["lifi_20261012_2356", "lifi_20261013_0011"]
