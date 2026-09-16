"""Operations on finished workflows shared by the API and the web UI.

Both front ends must apply the same rules (what may be retried, what may
be deleted), so the rules live here and the routes only translate the
outcome into a response.
"""

import json
from datetime import datetime

from .models import Artifact, db

TERMINAL = ("Finished", "Failed", "Cancelled")


class WorkflowStateError(Exception):
    """The workflow is in a state where the operation makes no sense."""


def retry(workflow):
    """Requeue a failed or cancelled workflow from its first failed or
    cancelled task onwards.

    Anything after that point is reset too: those results were derived
    from a run that did not complete, so they cannot be trusted. Their
    recorded artifacts go as well, so references resolve against the new
    run rather than the abandoned one.
    """
    if workflow.status in ("Running", "Pending"):
        raise WorkflowStateError(
            f"Workflow is {workflow.status}; there is nothing to retry")
    if any(t.status == "Running" for t in workflow.tasks):
        raise WorkflowStateError("A task is still winding down; retry once it has stopped")

    stopped = [t for t in workflow.tasks if t.status in ("Failed", "Cancelled")]
    if not stopped:
        raise WorkflowStateError("Workflow has no failed or cancelled tasks")

    from_position = min(t.workflow_position for t in stopped)
    for task in workflow.tasks:
        if task.workflow_position < from_position:
            continue
        Artifact.query.filter_by(task_id=task.id).delete()
        task.status = "Pending"
        task.pid = None
        task.exit_code = None
        task.started_at = None
        task.completed_at = None
        task.terminated_at = None
        task.cancelled_at = None
        task.machine_id = None

    workflow.status = "Pending"
    db.session.commit()


def cancel(workflow):
    """Stop a workflow: pending tasks never start, the running one is
    signalled by the watcher (SIGTERM, then SIGKILL after the grace
    period) and ends up Cancelled. Returns the tasks still winding down."""
    if workflow.status not in ("Pending", "Running"):
        raise WorkflowStateError(
            f"Workflow is {workflow.status}; there is nothing to cancel")
    now = datetime.now()
    winding_down = []
    for task in workflow.tasks:
        if task.status == "Pending":
            task.status = "Cancelled"
            task.cancelled_at = now
        elif task.status == "Running":
            task.cancelled_at = now
            winding_down.append(task)
    workflow.status = "Cancelled"
    db.session.commit()
    return winding_down


def delete(workflow):
    """Remove a workflow and its tasks. A running task keeps its process
    alive with nothing tracking it, so it has to stop first."""
    if workflow.status == "Running" or any(t.status == "Running" for t in workflow.tasks):
        raise WorkflowStateError("Cannot delete a workflow with a running task")
    db.session.delete(workflow)
    db.session.commit()


def can_retry(workflow):
    return (workflow.status not in ("Running", "Pending")
            and not any(t.status == "Running" for t in workflow.tasks)
            and any(t.status in ("Failed", "Cancelled") for t in workflow.tasks))


def can_cancel(workflow):
    return workflow.status in ("Pending", "Running")


def spec(workflow):
    """The workflow as a creation request: the body POST /workflows takes
    and `hub workflow create --file` reads, so a run can be repeated or
    edited and resubmitted. Structured settings come back as objects,
    with any $from/$param references intact; opaque ones stay text.
    The template name is informative: the tasks are listed in full, so
    the file recreates the run even if the template changed since."""
    tasks = []
    for task in sorted(workflow.tasks, key=lambda t: t.workflow_position):
        settings = task.settings
        if (task.tool.settings_format or "opaque") != "opaque":
            try:
                settings = json.loads(task.settings)
            except (ValueError, TypeError):
                pass
        entry = {"tool": task.tool.name, "settings": settings,
                 "priority": task.priority, "weight": task.weight}
        if task.machine is not None and not task.machine.is_local:
            entry["machine"] = task.machine.name
        tasks.append(entry)
    spec = {"name": workflow.name}
    try:
        params = json.loads(workflow.params) if workflow.params else {}
    except (ValueError, TypeError):
        params = {}
    if params:
        spec["params"] = params
    if workflow.template_name:
        spec["template"] = workflow.template_name
    spec["tasks"] = tasks
    return spec


def _params(workflow):
    try:
        return json.loads(workflow.params) if workflow.params else {}
    except (ValueError, TypeError):
        return {}


def protocol(workflows, templates=()):
    """The runs as a table: one row per workflow, one column per parameter.

    Columns follow the templates' schema order (the selected one, or all
    of them), then keys that only appear in hand-made runs, in the order
    they turn up. Returns (columns, rows); each row has the workflow,
    its params, and the time bookkeeping the protocol shows.
    """
    columns = []
    for template in templates:
        if not template.params_schema:
            continue
        try:
            keys = json.loads(template.params_schema).get("properties", {})
        except (ValueError, TypeError):
            continue
        for key in keys:
            if key not in columns:
                columns.append(key)
    rows = []
    for workflow in workflows:
        params = _params(workflow)
        for key in params:
            if key not in columns:
                columns.append(key)
        tasks = sorted(workflow.tasks, key=lambda t: t.workflow_position)
        started = [t.started_at for t in tasks if t.started_at]
        completed = [t.completed_at for t in tasks if t.completed_at]
        finished = workflow.status in TERMINAL
        rows.append({
            "workflow": workflow,
            "params": params,
            "started_at": min(started) if started else None,
            "completed_at": max(completed) if completed and finished else None,
            "task_count": len(tasks),
            "finished_count": sum(1 for t in tasks if t.status == "Finished"),
            "outputs": [a for t in tasks for a in t.artifacts],
        })
    return columns, rows


def sort_protocol(rows, key, descending=False):
    """Order rows by a parameter or a bookkeeping field. Numbers sort as
    numbers when every value parses as one; missing values go last."""
    def field(row):
        if key in ("name", "status", "created_at"):
            return getattr(row["workflow"], key)
        if key in ("started_at", "completed_at"):
            return row[key]
        return row["params"].get(key)

    values = [field(r) for r in rows]
    present = [v for v in values if v is not None and v != ""]
    numeric = bool(present) and all(_as_number(v) is not None for v in present)

    def sort_key(row):
        value = field(row)
        missing = value is None or value == ""
        if missing:
            return (1, 0)
        if numeric:
            return (0, _as_number(value))
        return (0, str(value).lower())

    return sorted(rows, key=sort_key, reverse=descending)


def _as_number(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
