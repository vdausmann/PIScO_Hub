"""Alarms checked on the Hub, whether or not anyone has a page open.

Once a second the monitor builds what the Status Display would show (the
configured limits and the alarms set up on the page, from the poller's
latest values) and keeps the alarms that are on. Every Hub page polls
`snapshot()` for its banner. Each start, end and acknowledgement goes to
`<DATA_ROOT>/sensor_logs/alarms/alarms_<YYYYMMDD>.csv`.

An alarm is identified by its id (`limit:Pi-HuCam`, `user:3f2a9c1e`) and
the time it started: one that ends and comes back is a new alarm, so a
browser that acknowledged the first one is told again.
"""

import csv
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from .hardware_sensor_display import active_alarms, build_status, merge_sources, parse_config
from .models import GlobalSetting
from .user_alarms import AlarmStore, store_path

INTERVAL_S = 1.0
LOG_FIELDS = ["time_utc", "event", "id", "device", "field", "name", "value", "user"]


def _iso(seconds):
    return datetime.fromtimestamp(seconds, timezone.utc).isoformat(timespec="seconds")


class AlarmMonitor:

    def __init__(self, app, start=True):
        self.app = app
        self.lock = threading.Lock()
        self.active = {}               # id -> alarm dict incl. "since" (epoch)
        self.checked = None
        self.error = None
        with app.app_context():
            data_root = GlobalSetting.get("DATA_ROOT", os.path.join(app.config["HUB_ROOT"], "data"))
        self.log_dir = os.path.join(data_root, "sensor_logs", "alarms")
        app.extensions["alarm_monitor"] = self
        if start:
            threading.Thread(target=self._run, daemon=True, name="alarm-monitor").start()

    def _run(self):
        while True:
            started = time.time()
            try:
                self.check(started)
            except Exception as e:      # keep checking; the banner shows the error
                with self.lock:
                    self.error = str(e)[:300]
            time.sleep(max(0.0, INTERVAL_S - (time.time() - started)))

    def check(self, now):
        poller = self.app.extensions.get("hardware_poller")
        if poller is None:
            return
        with self.app.app_context():
            config_file = GlobalSetting.get("HARDWARE_CONTROL_CONFIG_FILE",
                                            "hardware_control/hardware_control_config.json")
            config = parse_config(Path(config_file) if os.path.isabs(config_file)
                                  else Path(self.app.config["HUB_ROOT"]) / config_file)
        live = poller.live()
        values, sources = merge_sources(live, live.pop("_meta", {}))
        status = build_status(config, values, sources, AlarmStore(store_path(self.app)).load())
        found = active_alarms(status)

        with self.lock:
            started = {i: a for i, a in found.items() if i not in self.active}
            ended = {i: a for i, a in self.active.items() if i not in found}
            for alarm_id, alarm in found.items():
                since = self.active[alarm_id]["since"] if alarm_id in self.active else now
                self.active[alarm_id] = {**alarm, "id": alarm_id, "since": since}
            for alarm_id in ended:
                del self.active[alarm_id]
            self.checked, self.error = now, None
        for alarm_id, alarm in started.items():
            self._log(now, "start", alarm_id, alarm)
        for alarm_id, alarm in ended.items():
            self._log(now, "end", alarm_id, alarm)

    def snapshot(self):
        with self.lock:
            active = sorted(self.active.values(), key=lambda a: a["since"])
            return {
                "active": [{**a, "key": f"{a['id']}@{a['since']:.0f}", "since": _iso(a["since"])}
                           for a in active],
                "checked": _iso(self.checked) if self.checked else None,
                "error": self.error,
            }

    def acknowledge(self, ids, user):
        now = time.time()
        with self.lock:
            alarms = {i: dict(self.active[i]) for i in ids if i in self.active}
        for alarm_id, alarm in alarms.items():
            self._log(now, "acknowledged", alarm_id, alarm, user)

    def _log(self, when, event, alarm_id, alarm, user=""):
        os.makedirs(self.log_dir, exist_ok=True)
        day = datetime.fromtimestamp(when, timezone.utc).strftime("%Y%m%d")
        path = os.path.join(self.log_dir, f"alarms_{day}.csv")
        new = not os.path.exists(path)
        with open(path, "a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=LOG_FIELDS)
            if new:
                writer.writeheader()
            writer.writerow({"time_utc": _iso(when), "event": event, "id": alarm_id,
                             "device": alarm.get("device") or "", "field": alarm.get("field"),
                             "name": alarm.get("name"), "value": alarm.get("value"), "user": user})
