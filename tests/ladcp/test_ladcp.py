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
