"""The Processing Hub: watching workflows and tasks in the browser.

Pages render once and then poll a partial with htmx while there is
something to watch; the partials decide themselves whether to keep
polling, so a finished workflow stops hitting the server.
"""

import csv
import io
import json
import os
from datetime import datetime

from flask import (Blueprint, Response, abort, current_app, flash, redirect,
                   render_template, request, url_for)
from flask_login import login_required

from ..backend import artifacts, machines, settings_engine, templates, workflows
from ..backend.models import Machine, Task, TemplateWorkflow, Tool, Workflow, db
from ..backend.task_manager import get_current_load

processing_bp = Blueprint('processing', __name__)

ACTIVE_STATUSES = ("Pending", "Running")
STATUS_FILTERS = ("Pending", "Running", "Finished", "Failed", "Cancelled")


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
            "Failed": "status-failed", "Cancelled": "status-cancelled"}.get(status, "status-pending")


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


# ---- protocol ---------------------------------------------------------------

def _protocol():
    """Rows and columns for the protocol from the request's filters."""
    query = Workflow.query.options(db.joinedload(Workflow.tasks))
    template_name = request.args.get("template", "")
    if template_name:
        query = query.filter_by(template_name=template_name)
        selected = templates.find(template_name)
        column_sources = [selected] if selected else []
    else:
        column_sources = TemplateWorkflow.query.order_by(TemplateWorkflow.name).all()
    status = request.args.get("status")
    if status in STATUS_FILTERS:
        query = query.filter_by(status=status)
    q = request.args.get("q", "").strip()
    if q:
        query = query.filter(Workflow.name.ilike(f"%{q}%"))
    rows_source = query.order_by(Workflow.created_at.desc()).all()
    used = {w.template_name for w in rows_source}
    column_sources = [t for t in column_sources if t.name in used]
    columns, rows = workflows.protocol(rows_source, column_sources)
    sort = request.args.get("sort") or "created_at"
    descending = request.args.get("dir", "desc" if sort == "created_at" else "asc") == "desc"
    rows = workflows.sort_protocol(rows, sort, descending)
    return columns, rows, template_name, sort, descending


@processing_bp.route('/protocol')
@login_required
def protocol():
    columns, rows, template_name, sort, descending = _protocol()
    names = [t.name for t in TemplateWorkflow.query.order_by(TemplateWorkflow.name).all()]
    # Templates that no longer exist still name past runs.
    for row in rows:
        name = row["workflow"].template_name
        if name and name not in names:
            names.append(name)
    return render_template('protocol.html', columns=columns, rows=rows,
                           template_names=names, template_name=template_name,
                           status_filters=STATUS_FILTERS,
                           active_status=request.args.get("status", ""),
                           q=request.args.get("q", ""), sort=sort, descending=descending)


@processing_bp.route('/protocol.csv')
@login_required
def protocol_csv():
    columns, rows, template_name, _, _ = _protocol()
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["name", "status", "template", "created", "started", "completed",
                     "tasks_finished", "tasks", "workflow_id"] + columns + ["outputs"])
    for row in rows:
        wf = row["workflow"]
        writer.writerow([
            wf.name, wf.status, wf.template_name or "",
            wf.created_at.isoformat(sep=" ", timespec="seconds") if wf.created_at else "",
            row["started_at"].isoformat(sep=" ", timespec="seconds") if row["started_at"] else "",
            row["completed_at"].isoformat(sep=" ", timespec="seconds") if row["completed_at"] else "",
            row["finished_count"], row["task_count"], wf.id,
        ] + [_cell(row["params"].get(c)) for c in columns]
          + ["; ".join(a.path for a in row["outputs"])])
    filename = f"protocol{'-' + template_name if template_name else ''}.csv"
    return Response(out.getvalue(), mimetype="text/csv", headers={
        "Content-Disposition": f'attachment; filename="{filename}"'})


def _cell(value):
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list)):
        return json.dumps(value)
    return value


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


@processing_bp.route('/workflow/<string:workflow_id>/cancel', methods=['POST'])
@login_required
def cancel_workflow(workflow_id):
    workflow = Workflow.query.get_or_404(workflow_id)
    try:
        winding = workflows.cancel(workflow)
        flash(f"Cancelled '{workflow.name}'."
              + (" The running task is being stopped." if winding else ""), "success")
    except workflows.WorkflowStateError as e:
        flash(str(e), "error")
    target = request.form.get("next") or url_for('processing.workflow_detail', workflow_id=workflow_id)
    return redirect(target)


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


# ---- machines --------------------------------------------------------------

def _placement():
    """For every available tool, the enabled machines allowed to run it.

    The scheduler's own rule (machines.can_run), so the page shows where a
    task can actually land, not a reimplementation that could drift.
    """
    # Capacity 0 takes nothing, which is how the Hub's own host is kept free.
    enabled = [m for m in Machine.query.filter_by(enabled=True).order_by(Machine.name).all()
               if (m.max_weight or 0) > 0]
    rows = []
    for tool in Tool.query.filter_by(available=True).order_by(Tool.name).all():
        required = machines.tool_requirements(tool)
        rows.append({"tool": tool, "requires": sorted(required),
                     "machines": [m for m in enabled if machines.can_run(m, required)]})
    return rows


def _known_tags():
    """Tags worth offering: those tools ask for, and those machines carry."""
    tags = set()
    for tool in Tool.query.filter_by(available=True).all():
        tags |= machines.tool_requirements(tool)
    for machine in Machine.query.all():
        tags |= machines.machine_tags(machine)
    return sorted(tags)


@processing_bp.route('/machines')
@login_required
def machine_list():
    rows = Machine.query.order_by(Machine.is_local.desc(), Machine.name).all()
    return render_template('machines.html', machines=rows,
                           loads={m.id: machines.machine_load(m.id) for m in rows},
                           placement=_placement())


@processing_bp.route('/machines/new', methods=['GET', 'POST'])
@processing_bp.route('/machines/<int:machine_id>', methods=['GET', 'POST'])
@login_required
def machine_edit(machine_id=None):
    machine = Machine.query.get_or_404(machine_id) if machine_id else None
    errors = []
    if request.method == 'POST':
        form = request.form
        data = {"name": form.get("name"), "note": form.get("note"),
                "max_weight": form.get("max_weight"), "tags": form.get("tags", ""),
                "dedicated": "dedicated" in form}
        target = machine or Machine(is_local=False, status="unknown", enabled=True)
        if not target.is_local:
            data.update(host=form.get("host"), ssh_user=form.get("ssh_user"),
                        enabled="enabled" in form)
        errors = machines.apply_changes(target, data)
        if not errors:
            if machine is None:
                db.session.add(target)
            db.session.commit()
            if target.is_local:
                flash(f"Saved '{target.name}'.", "success")
            else:
                # Host or user may have changed: say straight away whether it works.
                ready, reason = machines.probe_now(target, current_app)
                flash(f"Saved '{target.name}' - " + ("online." if ready else f"offline: {reason}"),
                      "success" if ready else "error")
            return redirect(url_for('processing.machine_list'))
        db.session.rollback()

    return render_template('machine_edit.html', machine=machine, errors=errors,
                           form=request.form if errors else None,
                           known_tags=_known_tags())


@processing_bp.route('/machines/<int:machine_id>/check', methods=['POST'])
@login_required
def machine_check(machine_id):
    machine = Machine.query.get_or_404(machine_id)
    ready, reason = machines.probe_now(machine, current_app)
    flash(f"'{machine.name}' is {'online' if ready else 'offline'}"
          + (f": {reason}" if reason else "."), "success" if ready else "error")
    return redirect(url_for('processing.machine_list'))


@processing_bp.route('/machines/<int:machine_id>/toggle', methods=['POST'])
@login_required
def machine_toggle(machine_id):
    machine = Machine.query.get_or_404(machine_id)
    errors = machines.apply_changes(machine, {"enabled": not machine.enabled})
    if errors:
        flash(" ".join(errors), "error")
    else:
        db.session.commit()
        flash(f"'{machine.name}' is now {'enabled' if machine.enabled else 'disabled'}"
              + ("" if machine.enabled else "; tasks already running there finish.")
              , "success")
    return redirect(url_for('processing.machine_list'))


@processing_bp.route('/machines/<int:machine_id>/delete', methods=['POST'])
@login_required
def machine_delete(machine_id):
    machine = Machine.query.get_or_404(machine_id)
    name = machine.name
    error = machines.remove(machine)
    flash(error or f"Removed '{name}'.", "error" if error else "success")
    return redirect(url_for('processing.machine_list'))
