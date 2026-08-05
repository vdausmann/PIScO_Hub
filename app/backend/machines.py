"""Machines that tasks can run on, and their reachability.

The Hub runs on one machine but can dispatch work to others over SSH.
Remote execution assumes shared storage mounted at identical paths
everywhere: the Hub writes a settings file locally and passes that same
path to the remote tool, so nothing is copied.

Liveness is probed in its own thread, because an unreachable host can
make an SSH call hang and that must never stall scheduling.
"""

import json
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


def ssh_command(machine, remote_command):
    """The argv that runs a command on a machine over SSH."""
    target = f"{machine.ssh_user}@{machine.host}" if machine.ssh_user else machine.host
    return ["ssh", *SSH_OPTIONS, "-o", "ConnectTimeout=10", target, remote_command]


def probe(machine, timeout=5):
    """Is the machine reachable right now?"""
    if machine.is_local:
        return True
    if not machine.host:
        return False
    target = f"{machine.ssh_user}@{machine.host}" if machine.ssh_user else machine.host
    try:
        result = subprocess.run(
            ["ssh", *SSH_OPTIONS, "-o", f"ConnectTimeout={timeout}", target, "true"],
            capture_output=True, timeout=timeout + 5)
        return result.returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


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
        for machine in Machine.query.filter_by(enabled=True).all():
            reachable = probe(machine)
            status = "online" if reachable else "offline"
            if machine.status != status:
                print(f"[*] Machine '{machine.name}' is {status}.")
            machine.status = status
            if reachable:
                machine.last_seen_at = datetime.now()
        db.session.commit()
