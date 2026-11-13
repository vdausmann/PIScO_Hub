"""Cast overviews and the share mirror's file selection, without DSHIP or
a share. Run: python -m pytest tests/cast_overview"""

import csv
import io
import os
import struct
from datetime import datetime, timezone

from app.backend import cast_overview, dship, share_mirror
from app.backend.ladcp import pd0


def _ms(text):
    return int(datetime.fromisoformat(text).replace(tzinfo=timezone.utc).timestamp() * 1000)


def _ops():
    events = []
    for name, op_id, t_in, t_max, t_deck in [
            ("MSM147_4-1", 1, "2026-10-15 02:52", "2026-10-15 03:50", "2026-10-15 04:57"),
            ("MSM147_4-4", 2, "2026-10-15 11:45", "2026-10-15 12:50", "2026-10-15 13:56"),
            ("MSM147_4-7", 3, "2026-10-15 18:46", "2026-10-15 19:54", "2026-10-15 21:13")]:
        for action, t in [("in the water", t_in), ("max depth/on ground", t_max), ("on deck", t_deck)]:
            events.append({"time": _ms(t), "action": action, "operation": name, "operation_id": op_id,
                           "device": "CTD", "device_name": "CTD", "comment": "",
                           "lat": 32.66, "lon": -17.43, "water_depth_m": 2960.0, "winch": None,
                           "rope_m": None})
    return dship.operations(events, "CTD")


def _rows(text):
    return list(csv.DictReader(io.StringIO(text)))


def test_match_takes_the_cast_overlapped_most():
    ops = cast_overview.cast_ops(_ops())
    start = _ms("2026-10-15 11:46") / 1000
    assert cast_overview.match(ops, start, start + 7200)["cast"] == 2
    # starting on deck before the cast, within the lead time
    assert cast_overview.match(ops, start - 1200, start + 600)["cast"] == 2
    assert cast_overview.match(ops, _ms("2026-10-15 08:00") / 1000) is None


def _rbn(path, start, seconds):
    """A LISST-Deep raw file: 1080-byte header, 120-byte records at 1 Hz."""
    with open(path, "wb") as f:
        f.write(b"\x4c\x49" + b"\0" * 1078)
        for i in range(seconds):
            t = datetime.fromtimestamp(start + i, timezone.utc)
            words = [0xDADA] + [0] * 42 + [t.year, t.month, t.day, t.hour, t.minute, t.second] + [0] * 11
            f.write(struct.pack(">60H", *words))


def test_lisst_overview_shifts_the_clock_and_skips_deck_tests(tmp_path):
    utc = _ms("2026-10-15 11:46") / 1000
    pdt = utc - 7 * 3600
    _rbn(tmp_path / "L2880446.RBN", pdt, 600)
    _rbn(tmp_path / "L2871713.RBN", pdt - 3000, 1)
    stamp = datetime.fromtimestamp(pdt, timezone.utc)
    cols = ["0"] * 42 + [str(v) for v in (stamp.year, stamp.month, stamp.day, stamp.hour,
                                          stamp.minute, stamp.second)] + ["0"]
    (tmp_path / "export.csv").write_text(", ".join(cols) + "\n")
    (tmp_path / "export.rtx").write_text("")

    assert cast_overview.lisst_rbn(str(tmp_path / "L2880446.RBN"))[0] == 600
    rows = _rows(cast_overview.lisst_overview(str(tmp_path), _ops(), 7)[0])
    data = next(r for r in rows if r["file"] == "L2880446.RBN")
    assert data["cast"] == "2" and data["station"] == "004-04"
    assert data["start_utc"] == "2026-10-15T11:46:00Z" and data["duration_min"] == "10.0"
    assert data["processed"] == "export.csv export.rtx"
    test = next(r for r in rows if r["file"] == "L2871713.RBN")
    assert test["cast"] == "" and "test recording" in test["note"]
    # casts with no file still get a row
    assert {r["cast"] for r in rows if r["file"] == ""} == {"1", "3"}


def test_clock_offset_schedule():
    offsets = cast_overview.clock_offsets("7, 0@2026-10-21T16:00")
    assert cast_overview._offset_at(offsets, _ms("2026-10-21 12:45") / 1000) == 7 * 3600
    assert cast_overview._offset_at(offsets, _ms("2026-10-21 19:45") / 1000) == 0
    assert cast_overview._offset_at(cast_overview.clock_offsets(7), 0) == 7 * 3600


def test_ladcp_overview_matches_files_and_flags_duplicates(tmp_path):
    start = _ms("2026-10-15 18:53") / 1000
    data = b"".join(pd0.encode_ensemble(number=i, time=start + 2 * i, n_cells=10) for i in range(50))
    for rel in ("013/serial/013UP_ADCP-B_20261015185310.bin", "013_1/013UP000.000",
                "013_2/013UP000.000", "_incoming/013/ADCP-B/013UP000.000.part"):
        os.makedirs(tmp_path / os.path.dirname(rel), exist_ok=True)
        (tmp_path / rel).write_bytes(data)
    (tmp_path / "013" / "dship.json").write_text('{"cast": 12}')

    rows = _rows(cast_overview.ladcp_overview(str(tmp_path), _ops())[0])
    files = [r for r in rows if r["file"]]
    assert [r["file"] for r in files] == ["013/serial/013UP_ADCP-B_20261015185310.bin",
                                          "013_1/013UP000.000", "013_2/013UP000.000"]
    assert all(r["cast"] == "3" and r["looking"] == "up" and r["ensembles"] == "50" for r in files)
    assert "dship.json says cast 12" in files[1]["note"]
    assert "same data as 013_1/013UP000.000" in files[2]["note"]


def test_mirror_leaves_out_unfinished_downloads(tmp_path):
    for rel in ("013/013UP000.000", "_incoming/013/x.000.part", "logs/a.log", ".DS_Store"):
        os.makedirs(tmp_path / os.path.dirname(rel) if os.path.dirname(rel) else tmp_path, exist_ok=True)
        (tmp_path / rel).write_text("x")
    assert sorted(share_mirror.local_files(str(tmp_path))) == ["013/013UP000.000", "logs/a.log"]
    assert share_mirror.parse_url("//192.168.100.34/public_wiss/MSM147") == \
        ("192.168.100.34", r"\\192.168.100.34\public_wiss\MSM147")


def test_share_browse_refuses_paths_out_of_the_share():
    import pytest
    from app.backend import share_browse
    assert share_browse.clean("/MSM147//02_CTD/") == "MSM147/02_CTD"
    assert share_browse.clean("MSM147\\02_CTD") == "MSM147/02_CTD"
    for bad in ("../etc", "MSM147/../../x", "./a"):
        with pytest.raises(share_browse.BrowseError):
            share_browse.clean(bad)


class _FakeSmb:
    """smbclient's stat/open_file over a dict of {share path: (bytes, mtime)}."""

    def __init__(self, files):
        self.files = files
        self.opened = 0

    def stat(self, path, **kw):
        data, mtime = self.files[path]
        return os.stat_result((0o100644, 0, 0, 1, 0, 0, len(data), mtime, mtime, mtime))

    def open_file(self, path, mode, **kw):
        self.opened += 1
        return io.BytesIO(self.files[path][0])


def test_pull_copies_only_changed(tmp_path):
    share = share_mirror.Share.__new__(share_mirror.Share)
    share.root, share.kw = r"\\srv\public_wiss\MSM147", {}
    path = r"\\srv\public_wiss\MSM147\06_underway\TSG\TSG.dat"
    share.smb = _FakeSmb({path: (b"date time\tlat\n", 1_800_000_000)})
    local = str(tmp_path / "share" / "06_underway" / "TSG" / "TSG.dat")

    assert share.get("06_underway/TSG/TSG.dat", local)
    assert open(local, "rb").read() == b"date time\tlat\n"
    assert os.path.getmtime(local) == 1_800_000_000
    assert not share.get("06_underway/TSG/TSG.dat", local)       # unchanged: no copy
    assert share.smb.opened == 1

    share.smb.files[path] = (b"date time\tlat\nrow\n", 1_800_000_300)
    assert share.get("06_underway/TSG/TSG.dat", local)
    assert open(local, "rb").read().endswith(b"row\n")
    assert not os.path.exists(local + ".hubpart")
