"""Lifecycle hooks: run a command or POST a webhook when a task or a
workflow ends.

Declared in the file named by the HOOKS_FILE setting (`hooks.json` in the
Hub root by default; absent means no hooks), a JSON list:

    [{"on": "task.failed",
      "run": "notify-send PIScO '{tool} failed in {workflow_name}'"},
     {"on": "workflow.finished", "post": "https://example.org/hooks/pisco"},
     {"on": "workflow.*", "run": "/opt/pisco/bin/on-workflow.sh"}]

Events: task.finished, task.failed, task.cancelled, workflow.finished,
workflow.failed, workflow.cancelled; `*` after the dot matches any of a
kind. A `run` command is formatted with the event's fields (below) and
also gets them as HUB_* environment variables; a `post` URL receives them
as a JSON body. Hooks run in their own thread and never block the
watcher; failures are printed, not raised. The file is re-read on every
event, so edits take effect at once.

Fields: event, workflow_id, workflow_name, workflow_status, task_id,
tool, position, task_status, exit_code, machine.
"""

import json
import os
import shlex
import subprocess
import threading
import urllib.request

from .models import GlobalSetting

EVENTS = {"task.finished", "task.failed", "task.cancelled",
          "workflow.finished", "workflow.failed", "workflow.cancelled"}


def _load(app):
    path = GlobalSetting.get("HOOKS_FILE", "hooks.json")
    if not os.path.isabs(path):
        path = os.path.join(app.config["HUB_ROOT"], path)
    if not os.path.isfile(path):
        return []
    try:
        with open(path) as f:
            hooks = json.load(f)
    except (OSError, ValueError) as e:
        print(f"[!] hooks: cannot read {path}: {e}")
        return []
    return [h for h in hooks if isinstance(h, dict) and h.get("on")]


def _matches(pattern, event):
    if pattern == event or pattern == "*":
        return True
    kind, _, name = pattern.partition(".")
    return name == "*" and event.startswith(kind + ".")


def fields_for(task, event):
    """The event's fields, from a task and its workflow."""
    workflow = task.workflow
    return {
        "event": event,
        "workflow_id": workflow.id, "workflow_name": workflow.name or "",
        "workflow_status": workflow.status,
        "task_id": task.id, "tool": task.tool.name if task.tool else "",
        "position": task.workflow_position, "task_status": task.status,
        "exit_code": task.exit_code if task.exit_code is not None else "",
        "machine": task.machine.name if getattr(task, "machine", None) else "",
    }


def fire(app, event, fields):
    """Run every hook declared for the event, off the calling thread."""
    if event not in EVENTS:
        raise ValueError(f"unknown hook event '{event}'")
    hooks = [h for h in _load(app) if _matches(str(h["on"]), event)]
    if not hooks:
        return
    threading.Thread(target=_run_all, args=(hooks, fields), daemon=True,
                     name=f"hook-{event}").start()


def _run_all(hooks, fields):
    for hook in hooks:
        try:
            if hook.get("run"):
                _run(str(hook["run"]), fields)
            if hook.get("post"):
                _post(str(hook["post"]), fields)
        except Exception as e:  # a hook's failure is its own problem
            print(f"[!] hook {hook.get('on')} failed: {e}")


def _run(command, fields):
    env = dict(os.environ)
    env.update({f"HUB_{k.upper()}": str(v) for k, v in fields.items()})
    try:
        rendered = command.format(**{k: shlex.quote(str(v)) for k, v in fields.items()})
    except (KeyError, IndexError, ValueError) as e:
        raise ValueError(f"bad placeholder in '{command}': {e}")
    result = subprocess.run(rendered, shell=True, env=env, capture_output=True,
                            text=True, timeout=300)
    if result.returncode != 0:
        raise RuntimeError(f"'{rendered}' exited {result.returncode}: "
                           f"{result.stderr.strip()[:300]}")


def _post(url, fields):
    body = json.dumps(fields).encode()
    request = urllib.request.Request(url, data=body, method="POST",
                                     headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=30) as response:
        if response.status >= 300:
            raise RuntimeError(f"POST {url} returned {response.status}")
