"""The Hub-side alarm monitor: alarms found without a page open, their
start/end/acknowledgement log, and the endpoint every page polls.
Run: python -m pytest tests/status_display"""

import csv
import glob
import os

import pytest

from app.backend.alarm_monitor import AlarmMonitor
from app.backend.user_alarms import AlarmStore, store_path


class FakePoller:
    def __init__(self):
        self.values = {"Pi-ExPres": "1848.59", "Pi-HuCam": "5.0"}

    def live(self):
        return {"PIScO": dict(self.values),
                "_meta": {"PIScO": {"enabled": True, "host": "h", "stale": False,
                                    "changed": None, "error": None, "config_device": "PIScO",
                                    "since": {}}}}


@pytest.fixture
def setup(tmp_path, monkeypatch):
    from app.factory import create_app
    app = create_app(create_worker=False)
    app.config["HUB_ROOT"] = str(tmp_path)
    os.makedirs(tmp_path / "hardware_control")
    import shutil
    shutil.copy("hardware_control/hardware_control_config.json", tmp_path / "hardware_control")
    poller = FakePoller()
    app.extensions["hardware_poller"] = poller
    monitor = AlarmMonitor(app, start=False)
    monitor.log_dir = str(tmp_path / "alarms")
    return app, poller, monitor


def log_rows(monitor):
    rows = []
    for path in sorted(glob.glob(os.path.join(monitor.log_dir, "*.csv"))):
        with open(path, newline="") as f:
            rows += list(csv.DictReader(f))
    return [(r["event"], r["id"], r["user"]) for r in rows]


def test_limit_and_user_alarm_start_and_end(setup):
    app, poller, monitor = setup
    AlarmStore(store_path(app)).add(
        {"field": "Pi-ExPres", "kind": "threshold", "op": ">", "value": "1500"}, "veit")
    monitor.check(1000.0)
    ids = [a["id"] for a in monitor.snapshot()["active"]]
    assert len(ids) == 1 and ids[0].startswith("user:")

    poller.values["Pi-HuCam"] = "80"          # above the configured max of 75
    monitor.check(1001.0)
    active = {a["id"]: a for a in monitor.snapshot()["active"]}
    assert "limit:Pi-HuCam" in active
    assert active["limit:Pi-HuCam"]["device"] == "PIScO"
    assert active[ids[0]]["key"].endswith("@1000")   # start time kept across checks

    poller.values.update({"Pi-HuCam": "5.0", "Pi-ExPres": "10"})
    monitor.check(1002.0)
    assert monitor.snapshot()["active"] == []
    assert [e for e, _, _ in log_rows(monitor)] == ["start", "start", "end", "end"]


def test_acknowledgement_is_logged_with_the_user(setup):
    app, poller, monitor = setup
    poller.values["Pi-HuCam"] = "80"
    monitor.check(1000.0)
    monitor.acknowledge(["limit:Pi-HuCam", "limit:gone"], "veit")
    assert log_rows(monitor)[-1] == ("acknowledged", "limit:Pi-HuCam", "veit")


def test_active_endpoint(setup):
    app, poller, monitor = setup
    app.add_url_rule("/", "index", lambda: "")
    app.config["LOGIN_DISABLED"] = True
    poller.values["Pi-HuCam"] = "80"
    monitor.check(1000.0)
    client = app.test_client()
    state = client.get("/hardware-control/sensor-display/alarms/active").get_json()
    assert [a["id"] for a in state["active"]] == ["limit:Pi-HuCam"]
    assert client.post("/hardware-control/sensor-display/alarms/ack",
                       json={"ids": ["limit:Pi-HuCam"]}).status_code == 204
    assert client.post("/hardware-control/sensor-display/alarms/ack", json={}).status_code == 400


def test_changed_limit_reaches_the_monitor_and_the_page(setup):
    app, poller, monitor = setup
    app.add_url_rule("/", "index", lambda: "")
    app.config["LOGIN_DISABLED"] = True
    client = app.test_client()
    poller.values["Pi-HuCam"] = "80"
    url = "/hardware-control/sensor-display/limits/Pi-HuCam"
    assert client.put(url, json={"max": "90"}).status_code == 200
    assert client.put(url, json={"max": "hot"}).status_code == 400
    assert client.put("/hardware-control/sensor-display/limits/Pi-Nope",
                      json={"max": "1"}).status_code == 404
    monitor.check(1000.0)
    assert monitor.snapshot()["active"] == []
    hu = next(l for l in client.get("/hardware-control/sensor-display/status").get_json()["limits"]
              if l["field"] == "Pi-HuCam")
    assert hu["max"] == "90" and hu["changed"]
    assert client.delete(url).status_code == 204
    monitor.check(1001.0)
    assert [a["id"] for a in monitor.snapshot()["active"]] == ["limit:Pi-HuCam"]
    page = client.get("/hardware-control/sensor-display/").get_data(as_text=True)
    assert 'data-limits-url=\'/hardware-control/sensor-display/limits/\'' in page
