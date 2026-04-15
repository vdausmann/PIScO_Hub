from flask import Blueprint, render_template, request, jsonify
from flask_login import login_required

from app.backend.task_manager import create_new_workflow

task_manager_bp = Blueprint('task_manager', __name__)

@task_manager_bp.route('/workflow/create', methods=['POST'])
@login_required
def api_create_workflow():
    """
    Expects JSON:
    {
        "name": "Gamma Ray Analysis",
        "tools": ["segmenter", "c_analyzer"],
        "settings": ["/path/to/seg_config.json", "/path/to/ana_config.json"]
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



    return jsonify({
        "status": "success",
    }), 201
