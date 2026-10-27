"""Shutdown-All runs sea-through-controll's script once at a time and keeps
its output. Run: python -m pytest tests/status_display"""

import time

import pytest
from flask import Flask

from app.backend import sea_through


@pytest.fixture
def runner(tmp_path, monkeypatch):
    checkout = tmp_path / "sea-through-controll"
    checkout.mkdir()
    (checkout / sea_through.SCRIPT).write_text(
        "import sys, time\n"
        "print('dry' if '--dry-run' in sys.argv else 'real')\n"
        "time.sleep(0.3)\n"
        "sys.exit(3 if '--dry-run' not in sys.argv else 0)\n")
    app = Flask(__name__)
    app.config["HUB_ROOT"] = str(tmp_path)
    r = sea_through.ShutdownRunner(app)
    monkeypatch.setattr(r, "settings", lambda: (str(checkout), str(tmp_path / "logs")))
    return r


def wait(runner):
    for _ in range(50):
        if not runner.status()["running"]:
            return runner.status()["run"]
        time.sleep(0.1)
    raise AssertionError("run did not finish")


def test_dry_run_passes_the_flag_and_logs(runner):
    runner.start("veit", dry_run=True)
    run = wait(runner)
    assert run["exit_code"] == 0 and run["dry_run"] and run["user"] == "veit"
    assert "dry" in run["log"] and run["log"][0].startswith("# dry run by veit")
    assert run["log_file"].endswith("_dry-run.log")


def test_one_run_at_a_time_and_failure_is_reported(runner):
    runner.start("veit", dry_run=False)
    with pytest.raises(RuntimeError):
        runner.start("veit", dry_run=True)
    run = wait(runner)
    assert run["exit_code"] == 3 and "real" in run["log"] and "# exit 3" in run["log"]


def test_missing_checkout(runner, monkeypatch, tmp_path):
    monkeypatch.setattr(runner, "settings", lambda: (str(tmp_path / "nope"), str(tmp_path)))
    with pytest.raises(FileNotFoundError):
        runner.start("veit", dry_run=True)
