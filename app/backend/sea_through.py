"""Shutdown-All: sea-through-controll's shutdown sequence, started from the Hub.

The sequence is Anton's `set_shutdown_and_upload.py`, run from its own
checkout (SEA_THROUGH_DIR) so its configuration stays where he keeps it:
`upload_order.txt` says what happens in which order - the Keller log is
fetched from the Propel-Akku first, then each controller's command file
gets its `*-Shutdown`/`*-ShutdownCom` fields set to 1, with a pause
after each upload. The Hub only starts it, keeps one run at a time, and
logs what it printed.

The run is a child of the Hub: a Hub restart while it runs stops it
part-way, leaving some controllers shut down and others not.
"""

import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone

from .models import GlobalSetting

SCRIPT = "set_shutdown_and_upload.py"
TIMEOUT_S = 900                  # Keller log copy + uploads + the pauses between them


def _iso(seconds):
    if seconds is None:
        return None
    return datetime.fromtimestamp(seconds, timezone.utc).isoformat(timespec="seconds")


class ShutdownRunner:
    """One run at a time; state kept for the page until the next run."""

    def __init__(self, app):
        self.app = app
        self.lock = threading.Lock()
        self.run = None
        app.extensions["sea_through_shutdown"] = self

    def settings(self):
        with self.app.app_context():
            directory = GlobalSetting.get("SEA_THROUGH_DIR", "/opt/pisco/tools/sea-through-controll")
            data_root = GlobalSetting.get("DATA_ROOT", os.path.join(self.app.config["HUB_ROOT"], "data"))
        return directory, os.path.join(data_root, "sensor_logs", "shutdown")

    def start(self, user, dry_run):
        directory, log_dir = self.settings()
        script = os.path.join(directory, SCRIPT)
        if not os.path.isfile(script):
            raise FileNotFoundError(f"{script} not found (SEA_THROUGH_DIR)")
        with self.lock:
            if self.run and self.run["exit_code"] is None:
                raise RuntimeError("a shutdown run is already in progress")
            os.makedirs(log_dir, exist_ok=True)
            started = time.time()
            stamp = datetime.fromtimestamp(started, timezone.utc).strftime("%Y%m%d_%H%M%S")
            log_path = os.path.join(log_dir, f"shutdown_{stamp}{'_dry-run' if dry_run else ''}.log")
            log = open(log_path, "w", buffering=1)
            log.write(f"# {'dry run' if dry_run else 'Shutdown-All'} by {user} at {_iso(started)}\n")
            proc = subprocess.Popen([sys.executable, "-u", SCRIPT, *(["--dry-run"] if dry_run else [])],
                                    cwd=directory, stdin=subprocess.DEVNULL, stdout=log,
                                    stderr=subprocess.STDOUT)
            self.run = {"user": user, "dry_run": dry_run, "started": started, "finished": None,
                        "exit_code": None, "log_file": log_path}
            threading.Thread(target=self._wait, args=(proc, log), daemon=True,
                             name="sea-through-shutdown").start()
        return self.status()

    def _wait(self, proc, log):
        try:
            code = proc.wait(timeout=TIMEOUT_S)
        except subprocess.TimeoutExpired:
            proc.kill()
            code = proc.wait()
            log.write(f"# stopped by the Hub after {TIMEOUT_S} s\n")
        log.write(f"# exit {code}\n")
        log.close()
        with self.lock:
            self.run["exit_code"] = code
            self.run["finished"] = time.time()

    def status(self, tail=60):
        with self.lock:
            run = dict(self.run) if self.run else None
        if run is None:
            return {"running": False, "run": None}
        try:
            with open(run["log_file"], encoding="utf-8", errors="replace") as f:
                lines = f.read().splitlines()[-tail:]
        except OSError:
            lines = []
        return {"running": run["exit_code"] is None,
                "run": {**run, "started": _iso(run["started"]), "finished": _iso(run["finished"]),
                        "log": lines}}
