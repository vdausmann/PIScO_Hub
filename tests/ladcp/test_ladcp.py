"""The LADCP backend against the simulator: PD0, YMODEM, the serial
session and the whole profile cycle. Run: python -m pytest tests/ladcp"""

import os
import socket
import threading
import time

import numpy as np
import pytest

from app.backend.ladcp import config as cfgmod
from app.backend.ladcp import pd0, ymodem
from app.backend.ladcp.service import LadcpService
from app.backend.ladcp.simulator import Simulator
from app.backend.ladcp.workhorse import parse_directory, parse_ps0


# ---- PD0 -------------------------------------------------------------------------

def _ensemble(**kw):
    args = dict(number=7, time=1_790_000_000.25, n_cells=20)
    args.update(kw)
    return pd0.encode_ensemble(**args)


def test_pd0_round_trip():
    ei = np.full((20, 4), 70)
    ei[12] = 170
    vel = np.full((20, 4), 0.5)
    vel[19, 2] = np.nan
    ens = pd0.parse_ensemble(_ensemble(heading=271.5, pitch=-3.2, roll=1.1, temperature=-1.25,
                                       ei=ei, vel=vel, serial=4711, up=True))
    assert ens["number"] == 7 and ens["serial"] == 4711 and ens["up"] is True
    assert ens["heading"] == 271.5 and ens["pitch"] == -3.2 and ens["temperature"] == -1.25
    assert abs(ens["time"] - 1_790_000_000.25) < 0.011
    assert np.isnan(ens["vel"][19, 2]) and ens["vel"][0, 0] == 0.5
    assert pd0.bottom_distance(ens) == ens["bin1_m"] + 12 * ens["cell_m"]
    # up-looker: positive along-beam velocity means rising
    assert pd0.vertical_velocity(ens) < 0


def test_bottom_detector_ignores_range_decay():
    """Echo intensity falls off with range; the near cells being the
    strongest is not a bottom. A real hit is found wherever it is."""
    rs = np.random.default_rng(1)
    rng = 10 + 10 * np.arange(20)
    decay = np.maximum(150 - 45 * np.log10(1 + rng / 8), 42)[:, None]
    for _ in range(300):
        ei = decay + rs.normal(0, 2.5, (20, 4))
        assert pd0.bottom_distance({"ei": ei, "bin1_m": 10, "cell_m": 10}) is None
    for k in (2, 9, 19):
        ei = decay + rs.normal(0, 2.5, (20, 4))
        ei[k] += 70
        ei[k + 1:] = 40
        assert pd0.bottom_distance({"ei": ei, "bin1_m": 10, "cell_m": 10}) == 10 + 10 * k


def test_pd0_rejects_bad_checksum():
    block = bytearray(_ensemble())
    block[40] ^= 0xFF
    with pytest.raises(pd0.PD0Error):
        pd0.parse_ensemble(bytes(block))


def test_stream_decoder_resyncs_across_garbage_and_splits():
    a, b = _ensemble(number=1), _ensemble(number=2)
    corrupt = bytearray(_ensemble(number=3))
    corrupt[-1] ^= 1
    stream = b"CS\r\n" + a + b"\x7f\x7f\xff" + bytes(corrupt) + b"noise" + b
    dec = pd0.StreamDecoder()
    got = []
    for i in range(0, len(stream), 37):            # arrives in odd-sized pieces
        got += [e["number"] for _, e in dec.feed(stream[i:i + 37])]
    assert got == [1, 2]
    assert dec.bad >= 1


# ---- YMODEM ----------------------------------------------------------------------

class _SockPort:
    def __init__(self, sock):
        sock.settimeout(0.1)
        self.sock = sock

    def read(self, n):
        try:
            return self.sock.recv(n)
        except socket.timeout:
            return b""

    def write(self, data):
        self.sock.sendall(data)

    def flush(self):
        pass


@pytest.mark.parametrize("size", [0, 1, 128, 1000, 1024, 5000])
def test_ymodem_batch(tmp_path, size):
    left, right = socket.socketpair()
    content = os.urandom(size)
    files = [("001DN000.000", content), ("001DN001.000", b"tail")]
    sender = threading.Thread(target=ymodem.send, args=(_SockPort(left), files))
    sender.start()
    got = ymodem.receive(_SockPort(right), str(tmp_path))
    sender.join(5)
    assert [os.path.basename(p) for p, _ in got] == ["001DN000.000", "001DN001.000"]
    assert (tmp_path / "001DN000.000").read_bytes() == content
    assert (tmp_path / "001DN001.000").read_bytes() == b"tail"


# ---- replies ---------------------------------------------------------------------

def test_parse_replies():
    ps0 = "    Instrument S/N:  12345\r\n     Orientation:  DOWN\r\n    CPU Firmware:  51.41 [0]"
    assert parse_ps0(ps0) == {"serial": "12345", "orientation": "DOWN", "frequency": None,
                              "firmware": "51.41 [0]"}
    rr = ("Recorder Directory:\r\n001DN000 000         123456 2026/10/10 12:00:00\r\n"
          "001DN001 000              0 2026/10/10 12:00:00\r\nTotal bytes free = 15000 bytes")
    d = parse_directory(rr)
    assert d["files"] == [{"name": "001DN000.000", "size": 123456, "date": "2026/10/10 12:00:00"}]
    assert d["free_bytes"] == 15000
    assert parse_directory("No files found.\r\nfree = 7")["empty"] is True


def test_setup_sequence_matches_old_tool():
    cfg = cfgmod.merged({})
    primary = [c for c, _ in cfgmod.setup_commands(cfg, "primary", 3, "DOWN")]
    secondary = [c for c, _ in cfgmod.setup_commands(cfg, "secondary", 3, "UP")]
    single = [c for c, _ in cfgmod.setup_commands(cfg, "single", 3, "DOWN")]
    assert primary[:2] == ["CR1", "SB0"] and primary[2].startswith("TS ")
    assert "TP 00:00.10" in primary and "TE 00:00:01.60" in primary
    assert "CF11111" in primary                    # live output switches the serial digit on
    assert ["SM1", "SI0", "SA001", "SW6500"] == [c for c in primary if c[:2] in ("SM", "SI", "SA", "SW")]
    assert ["SM2", "SA001", "ST200"] == [c for c in secondary if c[:2] in ("SM", "SA", "ST")]
    assert not any(c.startswith("SM") for c in single)
    assert primary[-2:] == ["RN 003DN", "CK"] and secondary[-2] == "RN 003UP"
    cfg["live_output"] = False
    assert "CF11101" in [c for c, _ in cfgmod.setup_commands(cfg, "primary", 3, "DOWN")]


def test_config_validation():
    cfg = cfgmod.merged({})
    cfgmod.validate(cfg)
    cfg["primary"]["bin_number"] = 0
    with pytest.raises(ValueError, match="primary/bin_number"):
        cfgmod.validate(cfg)


# ---- the whole cycle against the simulator ---------------------------------------

def _wait(service, timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        op = service.operation
        if op is not None and op.state != "running":
            return op
        time.sleep(0.1)
    raise AssertionError(f"operation still running: {service.operation.as_dict()}")


def _make_rig(tmp_path, protocol, baud=115200, download_baud=115200):
    """A simulator and a service talking to it. rfc2217 is how the ship's
    serial server offers the ports (hardware break, real baud rates); a
    raw socket only carries the software break."""
    sim = Simulator(ports=(0, 0), bottom=200, speed=60, soak=0, protocol=protocol, baud=baud).start()
    brk = "hardware" if protocol == "rfc2217" else "==="
    conf = cfgmod.merged({
        "cruise_id": "TEST",
        "download": {"baud": download_baud},
        "instruments": [
            # the up-looker on the first port, so roles come from orientation
            {"name": "A", "port": sim.urls[1], "baud": baud, "break": brk, "enabled": True},
            {"name": "B", "port": sim.urls[0], "baud": baud, "break": brk, "enabled": True},
        ],
    })
    for role in ("primary", "secondary"):
        conf[role]["time_per_ensemble"] = 0.3
    cfgmod.save(str(tmp_path / "ladcp.json"), conf)
    service = LadcpService(str(tmp_path / "ladcp.json"), str(tmp_path / "state.json"),
                           str(tmp_path / "data"))
    return service, sim


def _teardown(service, sim):
    for inst in service.instruments.values():
        service._stop_reader(inst)
        inst.session.close()
    sim.stop()


@pytest.fixture(params=["socket", "rfc2217"])
def rig(tmp_path, request):
    service, sim = _make_rig(tmp_path, request.param)
    yield service, sim, tmp_path
    _teardown(service, sim)


def test_profile_cycle(rig):
    service, sim, tmp_path = rig

    service.detect()
    op = _wait(service)
    assert op.state == "done", op.as_dict()
    roles = {i.name: i.role for i in service.instruments.values()}
    assert roles == {"A": "secondary", "B": "primary"}

    service.start(deck_voltage=48.2)
    op = _wait(service)
    assert op.state == "done", op.as_dict()
    assert service.state["profile"] == 1 and service.state["deployed"]
    dn, up = sim.instruments
    assert dn.params["SM"] == "1" and up.params["SM"] == "2" and dn.rn == "001DN"

    time.sleep(2.5)                                     # past the bottom at 60 m/s
    live = service.live()
    assert live["B"]["count"] > 3 and live["A"]["count"] > 3
    assert any(r["target"] for r in live["B"]["records"]), "down-looker never saw the bottom"
    since = {"B": live["B"]["seq"]}
    assert len(service.live(since)["B"]["records"]) <= 2

    # a second start without stopping asks first
    service.start()
    op = _wait(service)
    assert op.state == "needs_confirmation"
    assert service.state["deployed"], "a refused start must not end the deployment"

    service.stop(download=True)
    op = _wait(service)
    assert op.state == "done", op.as_dict()
    folder = op.result["folder"]
    assert sorted(os.listdir(folder)) == ["001DN000.000", "001UP000.000", "serial"]
    summary = op.result["summaries"]["B"]
    assert summary["ensembles"] > 3 and summary["max_depth_m"] > 20
    serial_files = os.listdir(os.path.join(folder, "serial"))
    assert len(serial_files) == 2
    recorded = sum(1 for _ in pd0.iter_file(os.path.join(folder, "serial",
                                                          [f for f in serial_files if "DN" in f][0])))
    assert recorded >= summary["ensembles"] - 2       # the cable got (almost) everything
    assert (tmp_path / "data" / "ladcp" / "TEST" / "battery.log").read_text().count("\n") == 1
    assert not service.state["deployed"]

    # downloaded, so erase goes through without asking
    service.erase()
    op = _wait(service)
    assert op.state == "done", op.as_dict()
    assert not dn.files and not up.files


def test_single_mode(rig):
    """With one ADCP on the rosette the other port is never touched: no
    break, no waiting for it, no sync, and only its file comes down."""
    service, sim, _ = rig
    dn, up = sim.instruments
    service.instruments["A"].conf["port"] = "socket://127.0.0.1:9"   # nobody there
    service.set_mode("single", "B")
    assert [i.name for i in service._enabled()] == ["B"]
    assert [i["active"] for i in service.status()["instruments"]] == [False, True]

    t0 = time.time()
    service.start()
    op = _wait(service)
    assert op.state == "done", op.as_dict()
    assert time.time() - t0 < 15
    assert "Contact A" not in [s["label"] for s in op.steps]
    assert service.instruments["B"].role == "single"
    assert dn.rn == "001DN" and dn.params["SM"] == "0" and up.rn == "RDI_"
    assert service.preview()["B"]["role"] == "single"
    with pytest.raises(Exception, match="deployed"):
        service.set_mode("dual")

    time.sleep(1.0)
    service.stop(download=True)
    op = _wait(service)
    assert op.state == "done", op.as_dict()
    assert sorted(os.listdir(op.result["folder"])) == ["001DN000.000", "serial"]

    service.set_mode("dual")
    assert [i.name for i in service._enabled()] == ["A", "B"]


def test_single_instrument_must_exist():
    cfg = cfgmod.merged({"mode": "single", "single_instrument": "nope"})
    with pytest.raises(ValueError, match="single_instrument"):
        cfgmod.validate(cfg)
    cfg["single_instrument"] = None                     # the first enabled one
    cfgmod.validate(cfg)
    assert cfgmod.active_names(cfg) == ["ADCP-A"]


def test_erase_refuses_undownloaded(rig):
    service, sim, _ = rig
    sim.instruments[0].files["009DN000.000"] = bytearray(b"x" * 100)
    service.erase()
    op = _wait(service)
    assert op.state == "needs_confirmation" and "009DN000.000" in op.error
    assert sim.instruments[0].files
    service.erase(force=True)
    assert _wait(service).state == "done"
    assert not sim.instruments[0].files


def test_reattach_after_restart(rig):
    service, sim, tmp_path = rig
    service.start()
    assert _wait(service).state == "done"
    # the Hub restarts: a new service on the same state, the old one gone
    for inst in service.instruments.values():
        service._stop_reader(inst)
        inst.session.close()
    again = LadcpService(service.config_path, service.state_path, service.data_root)
    deadline = time.time() + 5
    while time.time() < deadline and not all(
            i.live and i.live.count for i in again.instruments.values()):
        time.sleep(0.2)
    assert all(i.live and i.live.count for i in again.instruments.values())
    assert sim.instruments[0].pinging, "reattaching must not stop the instruments"
    again.stop(download=False)
    assert _wait(again).state == "done"
    for inst in again.instruments.values():
        inst.session.close()


def test_download_switches_baud_over_rfc2217(tmp_path):
    """Instruments on 9600 baud, downloads at 115200: CB811, change the
    port's speed, receive, and back to 9600 for the next commands — over
    the ship's kind of link, where a wrong speed means garbage."""
    service, sim = _make_rig(tmp_path, "rfc2217", baud=9600, download_baud=115200)
    try:
        service.start()
        op = _wait(service)
        assert op.state == "done", op.as_dict()
        time.sleep(1.5)
        service.stop(download=True)
        op = _wait(service)
        assert op.state == "done", op.as_dict()
        assert sorted(f for f in os.listdir(op.result["folder"]) if f.endswith(".000")) == \
            ["001DN000.000", "001UP000.000"]
        assert all(i.baud == 9600 for i in sim.instruments), "instruments not back on 9600"
        sent = [line["text"] for line in service.instruments["B"].transcript.since(0)
                if line["kind"] == "tx"]
        assert "CB811" in sent and "CB411" in sent
        # still talking at 9600 afterwards
        service.detect()
        assert _wait(service).state == "done"
    finally:
        _teardown(service, sim)


def test_hardware_break_needed_over_rfc2217(tmp_path):
    """Over RFC 2217 a hardware break wakes the instrument; a wrong baud
    rate on the Hub's side is reported as no answer, not as success."""
    service, sim = _make_rig(tmp_path, "rfc2217")
    try:
        from app.backend.ladcp.workhorse import InstrumentError, WorkHorse
        wh = WorkHorse("X", sim.urls[0], baud=115200, break_mode="hardware")
        assert "BREAK Wakeup" in wh.wake()
        wh.close()
        wrong = WorkHorse("Y", sim.urls[0], baud=9600, break_mode="hardware")
        with pytest.raises(InstrumentError, match="no answer"):
            wrong.wake(attempts=1)
        wrong.close()
    finally:
        _teardown(service, sim)


def test_dropped_connection_is_reopened(tmp_path):
    """ser2net drops idle connections; pyserial still calls the port open.
    The next break must reconnect instead of failing with a broken pipe."""
    service, sim = _make_rig(tmp_path, "rfc2217")
    try:
        from app.backend.ladcp.workhorse import WorkHorse
        wh = WorkHorse("X", sim.urls[0], baud=115200, break_mode="hardware")
        assert "BREAK Wakeup" in wh.wake()
        sim.drop_clients()
        time.sleep(0.5)
        assert "BREAK Wakeup" in wh.wake()
        assert "Instrument S/N" in wh.command("PS0")
        wh.close()
    finally:
        _teardown(service, sim)


def test_ports_released_between_operations(rig):
    """Outside a deployment the Hub holds no connection, so other clients
    can use the ports and an idle timeout cannot leave a dead one behind;
    while deployed it keeps reading, and survives the server dropping it."""
    service, sim, _ = rig
    service.detect()
    assert _wait(service).state == "done"
    assert not any(i.session.is_open for i in service.instruments.values())
    assert not sim.clients
    service.console("B", "===")
    assert service.console("B", "RN?").startswith("RN =")
    assert not service.instruments["B"].session.is_open

    service.start()
    assert _wait(service).state == "done"
    assert all(i.session.is_open for i in service.instruments.values())
    before = service.live()["B"]["count"]
    sim.drop_clients()                          # mid-cast: the server drops the stream
    deadline = time.time() + 15
    while time.time() < deadline and service.live()["B"]["count"] <= before + 2:
        time.sleep(0.3)
    assert service.live()["B"]["count"] > before + 2, "the stream did not resume"
    assert sim.instruments[0].pinging, "reconnecting must not stop the instruments"
    service.stop(download=True)
    assert _wait(service).state == "done"


def test_live_history_is_thinned_and_sent_once(monkeypatch):
    """A page starting from nothing gets the thinned history for the long
    windows; a page that keeps polling does not get it again."""
    from app.backend.ladcp import live

    clock = [1000.0]
    monkeypatch.setattr(live.time, "time", lambda: clock[0])
    buf = live.LiveBuffer()
    for i in range(60):                      # one ensemble a second for a minute
        buf.add({"number": i, "time": 1000 + i, "ei": np.full((3, 4), 80),
                 "corr": np.ones((3, 4)), "vel": np.ones((3, 4))})
        clock[0] += 1
    first = buf.snapshot(0)
    assert len(first["records"]) == 60
    assert [h["seq"] for h in first["history"]] == [1, 11, 21, 31, 41, 51]
    assert first["history"][0]["eim"] == [80, 80, 80]
    assert "ei" not in first["history"][0]
    assert buf.snapshot(first["seq"])["history"] == []


# ---- yoyo casts ------------------------------------------------------------------

def _feed(det, depths, dt=10):
    out = None
    for i, d in enumerate(depths):
        out = det.feed(i * dt, d) or out
    return out


def test_yoyo_turn_is_suggested_after_a_long_heave_and_lowering():
    from app.backend.ladcp.live import YoyoDetector
    down = list(range(0, 1001, 10))
    up = list(range(1000, 19, -10))
    hold = [20] * 30                                   # waiting at SL=20m
    again = list(range(20, 101, 10))
    sg = _feed(YoyoDetector(), down + up + hold + again)
    assert sg is not None
    assert sg["peak_depth"] == 1000 and sg["turn_depth"] == 20
    # the turn is where it was shallowest, not when the lowering was noticed
    assert sg["turn_time"] == (len(down) + len(up) - 1) * 10


def test_yoyo_ignores_an_ordinary_cast_and_bottom_wiggles():
    from app.backend.ladcp.live import YoyoDetector
    # a normal cast: down, up, on deck
    assert _feed(YoyoDetector(), list(range(0, 1001, 10)) + list(range(1000, -1, -10))) is None
    # heaving 100 m near the bottom and lowering again is not a turn
    wiggle = list(range(0, 1001, 10)) + list(range(1000, 899, -10)) + list(range(900, 1001, 10))
    assert _feed(YoyoDetector(), wiggle) is None
    # a long heave that stays deep (1000 -> 700 of 1000 m) is not back near the surface
    mid = list(range(0, 1001, 10)) + list(range(1000, 699, -10)) + list(range(700, 801, 10))
    assert _feed(YoyoDetector(), mid) is None


def test_yoyo_rebase_after_a_dismissal():
    from app.backend.ladcp.live import YoyoDetector
    det = YoyoDetector()
    cycle = list(range(0, 1001, 10)) + list(range(1000, 19, -10)) + list(range(20, 101, 10))
    assert _feed(det, cycle) is not None
    det.rebase(100, 0)
    assert det.suggestion is None
    assert det.feed(1, 150) is None


def test_split_moves_the_serial_capture_and_keeps_the_recorder_profile(rig):
    import json
    service, sim, tmp_path = rig
    service.start(profile=20, cast={"cast": 20, "operation": "X_4-13", "station": "004-13",
                                    "source": "dship"}, yoyo=True)
    op = _wait(service)
    assert op.state == "done", op.as_dict()
    time.sleep(1.5)
    out = service.split(21, {"cast": 21, "operation": "X_4-13", "source": "split"}, turn_time=None)
    assert out["profile"] == 21 and out["recorder_profile"] == 20
    with pytest.raises(ValueError):
        service.split(20)                              # already part of this cast
    yo = service.yoyo_status()
    assert yo["enabled"] and [s["profile"] for s in yo["segments"]] == [20, 21]
    time.sleep(1.5)
    data = tmp_path / "data" / "ladcp" / "TEST"
    info = json.loads((data / "021" / "dship.json").read_text())
    assert info["split_from"] == "020" and info["recorder_file_in"] == "020"
    assert len(os.listdir(data / "021" / "serial")) == 2
    assert all(os.path.getsize(data / "021" / "serial" / f) > 0 for f in os.listdir(data / "021" / "serial"))

    service.stop(download=True)
    op = _wait(service)
    assert op.state == "done", op.as_dict()
    # the recorder file keeps its name and goes with the profile the cast started as,
    # and the dship.json there does not push it into 020_1
    assert op.result["folder"].endswith("020")
    assert sorted(f for f in os.listdir(op.result["folder"]) if f.endswith(".000")) == \
        ["020DN000.000", "020UP000.000"]
