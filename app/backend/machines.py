"""Machines that tasks can run on, and their reachability.

The Hub runs on one machine but can dispatch work to others over SSH.
Remote execution assumes shared storage mounted at identical paths
everywhere: the Hub writes a settings file locally and passes that same
path to the remote tool, so nothing is copied.

Liveness is probed in its own thread, because an unreachable host can
make an SSH call hang and that must never stall scheduling.
"""

import json
import os
import shlex
import subprocess
import threading
import time
from datetime import datetime

from .models import GlobalSetting, Machine, Task, db

SSH_OPTIONS = [
    "-o", "BatchMode=yes",
    "-o", "StrictHostKeyChecking=accept-new",
]


def ensure_local_machine(app):
    """Seed the machine representing the Hub's own host.

    Single-machine setups keep working untouched: everything lands here,
    with the capacity that used to be the global MAX_WEIGHT.
    """
    machine = Machine.query.filter_by(is_local=True).first()
    if machine is None:
        machine = Machine()
        machine.name = "local"
        machine.is_local = True
        machine.host = None
        machine.max_weight = GlobalSetting.get("MAX_WEIGHT", default=10)
        machine.tags = "[]"
        machine.note = "The machine the Hub itself runs on."
        db.session.add(machine)
    machine.enabled = True if machine.enabled is None else machine.enabled
    machine.status = "online"
    machine.last_seen_at = datetime.now()
    db.session.commit()
    return machine


def machine_tags(machine):
    try:
        return set(json.loads(machine.tags or "[]"))
    except (ValueError, TypeError):
        return set()


def tool_requirements(tool):
    try:
        return set(json.loads(tool.requires or "[]"))
    except (ValueError, TypeError):
        return set()


def machine_load(machine_id):
    """Weight of everything currently running on one machine."""
    running = Task.query.filter_by(status="Running", machine_id=machine_id).all()
    return sum(task.weight for task in running)


def ssh_command(machine, remote_command, connect_timeout=10):
    """The argv that runs a command on a machine over SSH."""
    target = f"{machine.ssh_user}@{machine.host}" if machine.ssh_user else machine.host
    return ["ssh", *SSH_OPTIONS, "-o", f"ConnectTimeout={connect_timeout}",
            target, remote_command]


def probe(machine, data_root=None, timeout=5):
    """Can this machine actually do work? Returns (ok, reason).

    Reachability alone is not readiness. A machine that answers ssh but
    cannot write the shared storage fails every task sent to it, and the
    failure names a path the Hub chose, which reads as a Hub bug rather
    than a missing mount. So the probe checks the storage too, and says
    which of the two went wrong.
    """
    if machine.is_local:
        return _check_local_storage(data_root)

    if not machine.host:
        return False, "no host configured"

    # Per-machine name: several machines may probe the same shared
    # directory at once.
    check = "true"
    if data_root:
        probe_file = os.path.join(data_root, f".hub-probe-{machine.name}")
        check = "test -d {0} && touch {1} && rm -f {1}".format(
            shlex.quote(data_root), shlex.quote(probe_file))

    try:
        result = subprocess.run(ssh_command(machine, check, timeout),
                                capture_output=True, timeout=timeout + 5)
    except subprocess.TimeoutExpired:
        return False, f"no response within {timeout + 5}s"
    except OSError as e:
        return False, f"could not run ssh: {e}"

    if result.returncode == 0:
        return True, None

    stderr = result.stderr.decode(errors="replace").strip().splitlines()
    detail = stderr[-1] if stderr else f"exit code {result.returncode}"
    if result.returncode == 255:
        return False, f"ssh failed: {detail}"
    return False, f"cannot use {data_root}: {detail}"


def _check_local_storage(data_root):
    if not data_root:
        return True, None
    try:
        os.makedirs(data_root, exist_ok=True)
    except OSError as e:
        return False, f"cannot create {data_root}: {e}"
    if not os.access(data_root, os.W_OK):
        return False, f"{data_root} is not writable"
    return True, None


class MachineMonitor:
    """Keeps each machine's reachability up to date."""

    def __init__(self, app) -> None:
        self.app = app
        thread = threading.Thread(target=self._worker, daemon=True)
        thread.start()

    def _worker(self):
        with self.app.app_context():
            print("[*] Machine Monitor Started.")
            while True:
                try:
                    self._probe_all()
                except Exception as e:
                    print(f"[!] Machine Monitor Error: {e}")

                time.sleep(GlobalSetting.get("MACHINE_PROBE_INTERVAL", default=30))

    def _probe_all(self):
        from . import artifacts

        root = artifacts.data_root(self.app)
        for machine in Machine.query.filter_by(enabled=True).all():
            ready, reason = probe(machine, root)
            status = "online" if ready else "offline"
            if machine.status != status:
                print(f"[*] Machine '{machine.name}' is {status}"
                      f"{': ' + reason if reason else ''}.")
            machine.status = status
            machine.last_probe_error = reason
            if ready:
                machine.last_seen_at = datetime.now()
        db.session.commit()
