from flask import Blueprint, redirect, render_template, url_for
from flask_login import login_required
from ..backend.models import TemporaryWorkflow, Workflow, db, Task, Tool

processing_bp = Blueprint('processing', __name__)

@processing_bp.route('/')
@login_required
def dashboard():
    # .joinedload ensures that wf.tasks are fetched in one single SQL query
    workflows = Workflow.query.options(db.joinedload(Workflow.tasks)).order_by(Workflow.created_at.desc()).all()
    return render_template('processing_dashboard.html', workflows=workflows)

@processing_bp.route('/partials/workflows')
@login_required
def workflows_partial():
    workflows = Workflow.query.options(
        db.joinedload(Workflow.tasks)
    ).order_by(Workflow.created_at.desc()).all()

    return render_template("workflows.html", workflows=workflows)

@processing_bp.route('/task/<string:task_id>/log')
@login_required
def view_log(task_id):
    task = Task.query.get_or_404(task_id)

    if not isinstance(task, Task):
        return "Task not found", 404

    log_content = ""
    error_content = ""

    if task.log_prefix:
        log_path = f"{task.log_prefix}.log"
        try:
            with open(log_path, "r") as f:
                log_content = f.read()
        except Exception as e:
            log_content = f"Error reading log file: {e}"

    if task.log_prefix:
        err_path = f"{task.log_prefix}.err"
        try:
            with open(err_path, "r") as f:
                error_content = f.read()
        except Exception as e:
            error_content = f"Error reading error file: {e}"

    return render_template("task_log.html", task=task,
                           log_content=log_content, error_content=error_content)

