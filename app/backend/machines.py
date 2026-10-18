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


def can_run(machine, required):
    """Whether a machine may take a tool with these requirements.

    Its tags must cover them all. A dedicated machine additionally takes
    only tools that ask for one of its tags: a tool that requires nothing
    runs anywhere else, but not there.
    """
    tags = machine_tags(machine)
    if not required <= tags:
        return False
    return not machine.dedicated or bool(required & tags)


def machine_load(machine_id):
    """Weight of everything currently running on one machine."""
    running = Task.query.filter_by(status="Running", machine_id=machine_id).all()
    return sum(task.weight for task in running)


def apply_changes(machine, data):
    """Validate edits to a machine and apply them; return the errors.

    Shared by the API and the machines page so both accept the same
    things. Nothing is applied when anything is wrong. Keys absent from
    `data` are left alone.
    """
    errors = []
    changes = {}

    if "name" in data:
        name = (data["name"] or "").strip()
        clash = Machine.query.filter(Machine.name == name, Machine.id != machine.id).first()
        if not name:
            errors.append("A name is required.")
        elif clash:
            errors.append(f"Machine '{name}' already exists.")
        changes["name"] = name

    for key in ("host", "ssh_user", "note"):
        if key in data:
            changes[key] = (data[key] or "").strip() or None
    if not machine.is_local and "host" in changes and not changes["host"]:
        errors.append("A host is required for a machine reached over SSH.")

    if "max_weight" in data:
        try:
            changes["max_weight"] = int(data["max_weight"])
            if changes["max_weight"] < 0:
                raise ValueError
        except (TypeError, ValueError):
            errors.append("Capacity must be a whole number, 0 or more.")

    if "tags" in data:
        tags = data["tags"]
        if isinstance(tags, str):
            tags = tags.replace(",", " ").split()
        if not isinstance(tags, list) or not all(isinstance(t, str) for t in tags):
            errors.append("Tags must be a list of strings.")
        else:
            # Order kept, duplicates and blanks dropped.
            changes["tags"] = json.dumps(list(dict.fromkeys(t.strip() for t in tags if t.strip())))

    for key in ("enabled", "dedicated"):
        if key in data:
            changes[key] = bool(data[key])
    if machine.is_local and changes.get("enabled") is False:
        errors.append("The local machine cannot be disabled.")

    if not errors:
        for key, value in changes.items():
            setattr(machine, key, value)
    return errors


def remove(machine):
    """Delete a machine; return why not, or None when it is gone.

    Finished tasks are kept and just lose the machine reference.
    """
    if machine.is_local:
        return "The local machine cannot be removed."
    if Task.query.filter_by(machine_id=machine.id, status="Running").count():
        return f"'{machine.name}' still has running tasks."
    Task.query.filter_by(machine_id=machine.id).update({"machine_id": None})
    db.session.delete(machine)
    db.session.commit()
    return None


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


def probe_now(machine, app):
    """Probe one machine at once and record the result."""
    from . import artifacts

    ready, reason = probe(machine, artifacts.data_root(app))
    machine.status = "online" if ready else "offline"
    machine.last_probe_error = reason
    if ready:
        machine.last_seen_at = datetime.now()
    db.session.commit()
    return ready, reason


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
