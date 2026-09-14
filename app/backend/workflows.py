"""Operations on finished workflows shared by the API and the web UI.

Both front ends must apply the same rules (what may be retried, what may
be deleted), so the rules live here and the routes only translate the
outcome into a response.
"""

import json

from .models import Artifact, db


class WorkflowStateError(Exception):
    """The workflow is in a state where the operation makes no sense."""


def retry(workflow):
    """Requeue a failed workflow from its first failed task onwards.

    Anything after that point is reset too: those results were derived
    from a run that did not complete, so they cannot be trusted. Their
    recorded artifacts go as well, so references resolve against the new
    run rather than the abandoned one.
    """
    if workflow.status in ("Running", "Pending"):
        raise WorkflowStateError(
            f"Workflow is {workflow.status}; there is nothing to retry")

    failed = [t for t in workflow.tasks if t.status == "Failed"]
    if not failed:
        raise WorkflowStateError("Workflow has no failed tasks")

    from_position = min(t.workflow_position for t in failed)
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
        task.machine_id = None

    workflow.status = "Pending"
    db.session.commit()


def delete(workflow):
    """Remove a workflow and its tasks. A running one keeps its process
    alive with nothing tracking it, so it has to finish first."""
    if workflow.status == "Running":
        raise WorkflowStateError("Cannot delete a running workflow")
    db.session.delete(workflow)
    db.session.commit()


def can_retry(workflow):
    return (workflow.status not in ("Running", "Pending")
            and any(t.status == "Failed" for t in workflow.tasks))


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
