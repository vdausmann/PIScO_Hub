from flask import Blueprint, redirect, request, jsonify, url_for
from flask_login import login_required

from app.backend.models import Workflow, db
from app.backend.task_manager import create_new_workflow, get_current_load


task_manager_bp = Blueprint('task_manager', __name__)

@task_manager_bp.route('/workflow/create', methods=['POST'])
def api_create_workflow():
    """
    Deprecated: use POST /api/v1/workflows instead.

    Expects JSON:
    {
        "name": "Gamma Ray Analysis",
        "tools": ["tool1", "tool2"],
        "settings": ["<settings content 1>", "<settings content 2>"]
    }
    Settings entries are the settings *content* (not file paths).
    """
    from .api_v1.auth import api_auth

    @api_auth("operate")
    def _create():
        data = request.get_json()

        if not data:
            return jsonify({"error": "No data provided"}), 400

        workflow_name = data.get('name')
        tools = data.get('tools', [])
        settings = data.get('settings', [])

        if not workflow_name or not tools:
            return jsonify({"error": "Workflow name and tools are required"}), 400

        if len(tools) != len(settings):
            return jsonify({"error": "Each tool must have corresponding settings"}), 400

        task_specs = [{"tool": tool, "settings": setting}
                      for tool, setting in zip(tools, settings)]

        try:
            workflow_id = create_new_workflow(workflow_name, task_specs)

            return jsonify({
                "status": "success",
                "workflow_id": workflow_id,
                "message": f"Workflow '{workflow_name}' queued with {len(tools)} tasks.",
                "deprecated": "Use POST /api/v1/workflows instead."
            }), 201

        except Exception as e:
            db.session.rollback()
            print(f"[!] Error in create_new_workflow: {e}")
            return jsonify({"error": str(e)}), 500

    return _create()



@task_manager_bp.route('/get-load', methods=['GET'])
@login_required
def get_load():
    load = get_current_load()
    return jsonify({"load": load}), 200



@task_manager_bp.route('/workflow/<string:workflow_id>/delete', methods=['POST'])
@login_required
def delete_workflow(workflow_id):
    workflow = Workflow.query.get_or_404(workflow_id)

    db.session.delete(workflow)
    db.session.commit()

    return redirect(url_for('processing.dashboard'))
