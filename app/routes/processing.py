from flask import Blueprint, render_template
from flask_login import login_required
from ..backend.models import Workflow, db

processing_bp = Blueprint('processing', __name__)

@processing_bp.route('/')
@login_required
def dashboard():
    # .joinedload ensures that wf.tasks are fetched in one single SQL query
    workflows = Workflow.query.options(db.joinedload(Workflow.tasks)).order_by(Workflow.created_at.desc()).all()
    return render_template('processing_dashboard.html', workflows=workflows)

@processing_bp.route('/workflows/updates')
def workflow_updates():
    # Eager load tasks to keep it fast
    workflows = Workflow.query.options(db.joinedload(Workflow.tasks)).all()
    return render_template('_workflow_table.html', workflows=workflows)
