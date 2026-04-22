from flask import Blueprint, redirect, render_template, request, jsonify, url_for
from flask_login import login_required

from app.backend.models import Workflow, db
from app.backend.task_manager import create_new_workflow, get_current_load


task_manager_bp = Blueprint('task_manager', __name__)

@task_manager_bp.route('/workflow/create', methods=['POST'])
# @login_required
def api_create_workflow():
    """
    Expects JSON:
    {
        "name": "Gamma Ray Analysis",
        "tools": ["tool1", "tool2"],
        "settings": ["/path/to/config1.json", "/path/to/config2.json"]
    }
    """
    data = request.get_json()

    if not data:
        return jsonify({"error": "No data provided"}), 400


    workflow_name = data.get('name')
    tools = data.get('tools', [])
    settings = data.get('settings', [])

    if not workflow_name or not tools:
        return jsonify({"error": "Workflow name and tools are required"}), 400

    if len(tools) != len(settings):
        return jsonify({"error": "Each tool must have a corresponding settings file path"}), 400

    # for path in settings:
    #     if not os.path.exists(path):
    #         return jsonify({"error": f"Settings file not found at: {path}"}), 400

    try:
        workflow_id = create_new_workflow(workflow_name, tools, settings)

        return jsonify({
            "status": "success",
            "workflow_id": workflow_id,
            "message": f"Workflow '{workflow_name}' queued with {len(tools)} tasks."
        }), 201

    except Exception as e:
        print(f"[!] Error in create_new_workflow: {e}")
        return jsonify({"error": str(e)}), 500



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
