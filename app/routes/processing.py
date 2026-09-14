"""The Processing Hub: watching workflows and tasks in the browser.

Pages render once and then poll a partial with htmx while there is
something to watch; the partials decide themselves whether to keep
polling, so a finished workflow stops hitting the server.
"""

import json
import os
from datetime import datetime

from flask import (Blueprint, Response, abort, current_app, flash, redirect,
                   render_template, request, url_for)
from flask_login import login_required

from ..backend import artifacts, settings_engine, workflows
from ..backend.models import Machine, Task, Workflow, db
from ..backend.task_manager import get_current_load

processing_bp = Blueprint('processing', __name__)

ACTIVE_STATUSES = ("Pending", "Running")
STATUS_FILTERS = ("Pending", "Running", "Finished", "Failed")


# ---- template helpers -------------------------------------------------------

@processing_bp.app_template_filter("duration")
def duration_filter(task):
    """How long a task ran, or has been running so far."""
    if not task.started_at:
        return ""
    end = task.completed_at or datetime.now()
    seconds = int((end - task.started_at).total_seconds())
    if seconds < 0:
        return ""
    hours, rest = divmod(seconds, 3600)
    minutes, seconds = divmod(rest, 60)
    if hours:
        return f"{hours}h {minutes:02d}m"
    if minutes:
        return f"{minutes}m {seconds:02d}s"
    return f"{seconds}s"


@processing_bp.app_template_filter("clock")
def clock_filter(value):
    if not value:
        return ""
    if value.date() == datetime.now().date():
        return value.strftime("%H:%M:%S")
    return value.strftime("%Y-%m-%d %H:%M")


@processing_bp.app_template_filter("explorer_dir")
def explorer_dir_filter(artifact):
    """The directory the File Explorer should open for an artifact."""
    path = artifact.path.rstrip(os.sep)
    if artifact.kind == "directory":
        return path
    return os.path.dirname(path)


@processing_bp.app_template_filter("status_class")
def status_class_filter(status):
    return {"Running": "status-running", "Finished": "status-done",
            "Failed": "status-failed"}.get(status, "status-pending")


@processing_bp.app_template_filter("from_json")
def from_json_filter(text):
    try:
        return json.loads(text) if text else {}
    except (ValueError, TypeError):
        return {}


@processing_bp.app_template_filter("pretty_json")
def pretty_json_filter(text):
    try:
        return json.dumps(json.loads(text), indent=2)
    except (ValueError, TypeError):
        return text or ""


def _capacity():
    machines = Machine.query.filter_by(enabled=True).all()
    return {"load": get_current_load(),
            "max_weight": sum(m.max_weight or 0 for m in machines)}


@processing_bp.app_context_processor
def inject_nav():
    # Every page carries the sub-navigation's load pill; pass the function
    # so only templates that render the pill pay for the queries.
    return {"capacity": _capacity}


# ---- workflows --------------------------------------------------------------

def _filtered_workflows():
    query = Workflow.query.options(db.joinedload(Workflow.tasks))
    status = request.args.get("status")
    if status in STATUS_FILTERS:
        query = query.filter_by(status=status)
    q = request.args.get("q", "").strip()
    if q:
        query = query.filter(Workflow.name.ilike(f"%{q}%"))
    return query.order_by(Workflow.created_at.desc()).all()


@processing_bp.route('/')
@login_required
def dashboard():
    return render_template('processing_dashboard.html',
                           workflows=_filtered_workflows(),
                           status_filters=STATUS_FILTERS,
                           active_status=request.args.get("status", ""),
                           q=request.args.get("q", ""))


@processing_bp.route('/partials/workflows')
@login_required
def workflows_partial():
    return render_template("partials/workflow_list.html",
                           workflows=_filtered_workflows())


@processing_bp.route('/partials/load')
@login_required
def load_partial():
    return render_template("partials/load_pill.html")


@processing_bp.route('/workflow/<string:workflow_id>')
@login_required
def workflow_detail(workflow_id):
    workflow = Workflow.query.get_or_404(workflow_id)
    return render_template('workflow_detail.html', workflow=workflow)


@processing_bp.route('/partials/workflow/<string:workflow_id>')
@login_required
def workflow_partial(workflow_id):
    workflow = Workflow.query.get_or_404(workflow_id)
    return render_template('partials/workflow_tasks.html', workflow=workflow)


@processing_bp.route('/workflow/<string:workflow_id>/retry', methods=['POST'])
@login_required
def retry_workflow(workflow_id):
    workflow = Workflow.query.get_or_404(workflow_id)
    try:
        workflows.retry(workflow)
        flash(f"Requeued '{workflow.name}'.", "success")
    except workflows.WorkflowStateError as e:
        flash(str(e), "error")
    return redirect(url_for('processing.workflow_detail', workflow_id=workflow_id))


@processing_bp.route('/workflow/<string:workflow_id>/delete', methods=['POST'])
@login_required
def delete_workflow(workflow_id):
    workflow = Workflow.query.get_or_404(workflow_id)
    name = workflow.name
    try:
        workflows.delete(workflow)
        flash(f"Deleted '{name}'.", "success")
    except workflows.WorkflowStateError as e:
        flash(str(e), "error")
        return redirect(url_for('processing.workflow_detail', workflow_id=workflow_id))
    return redirect(url_for('processing.dashboard'))


@processing_bp.route('/workflow/<string:workflow_id>/export.json')
@login_required
def export_workflow(workflow_id):
    """The workflow as `hub workflow create --file` input."""
    workflow = Workflow.query.get_or_404(workflow_id)
    body = json.dumps(workflows.spec(workflow), indent=2)
    filename = f"{workflow.name or workflow.id}.json".replace(os.sep, "_")
    return Response(body, mimetype="application/json", headers={
        "Content-Disposition": f'attachment; filename="{filename}"'})


# ---- tasks ------------------------------------------------------------------

def _dispatched_settings(task):
    """The settings file the tool was actually given, with $from
    references resolved, if it has been dispatched yet."""
    workspace = artifacts.task_workspace(current_app, task)
    path = os.path.join(workspace, "settings"
                        + settings_engine.settings_file_extension(task.tool))
    if not os.path.isfile(path):
        return path, None
    with open(path) as f:
        return path, f.read()


@processing_bp.route('/task/<string:task_id>')
@login_required
def task_detail(task_id):
    task = Task.query.get_or_404(task_id)
    settings_path, dispatched = _dispatched_settings(task)
    return render_template('task_detail.html', task=task,
                           workspace=artifacts.task_workspace(current_app, task),
                           settings_path=settings_path,
                           dispatched_settings=dispatched,
                           stream=request.args.get("stream", "stdout"),
                           tail=request.args.get("tail", 200, type=int))


@processing_bp.route('/task/<string:task_id>/log')
@login_required
def view_log(task_id):
    # The old log page; its URL is linked from elsewhere.
    return redirect(url_for('processing.task_detail', task_id=task_id))


@processing_bp.route('/partials/task/<string:task_id>/logs')
@login_required
def task_logs_partial(task_id):
    task = Task.query.get_or_404(task_id)
    stream = request.args.get("stream", "stdout")
    if stream not in ("stdout", "stderr"):
        abort(400)
    tail = request.args.get("tail", 200, type=int)

    content, missing = "", None
    if not task.log_prefix:
        missing = "The task has not been started yet."
    else:
        path = task.log_prefix + (".log" if stream == "stdout" else ".err")
        try:
            with open(path) as f:
                lines = f.readlines()
            if tail and tail > 0:
                lines = lines[-tail:]
            content = "".join(lines)
        except OSError as e:
            missing = f"Log file not readable: {e}"
    return render_template('partials/task_logs.html', task=task,
                           stream=stream, tail=tail, content=content,
                           missing=missing)
