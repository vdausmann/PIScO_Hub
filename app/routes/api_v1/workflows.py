from flask import jsonify, request

from ...backend import workflows
from ...backend.models import Workflow, db
from ...backend.task_manager import create_new_workflow
from . import api_v1_bp
from .auth import api_auth
from .serializers import workflow_detail, workflow_summary


@api_v1_bp.route("/workflows", methods=["GET"])
@api_v1_bp.route("/workflows/", methods=["GET"])
@api_auth()
def list_workflows():
    query = Workflow.query
    status = request.args.get("status")
    if status:
        query = query.filter_by(status=status)
    workflows = query.order_by(Workflow.created_at.desc()).all()
    return jsonify({"workflows": [workflow_summary(w) for w in workflows]})


@api_v1_bp.route("/workflows", methods=["POST"])
@api_v1_bp.route("/workflows/", methods=["POST"])
@api_auth("operate")
def create_workflow():
    """Create and queue a workflow.

    Body: {"name": str, "tasks": [{"tool": str, "settings": object|str,
    "priority": int?, "weight": int?}, ...]}
    Settings given as an object are validated against the tool's schema;
    a string is stored verbatim (opaque); omitted uses the tool defaults.
    """
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "JSON body required"}), 400

    name = data.get("name")
    tasks = data.get("tasks")
    if not name or not isinstance(tasks, list) or not tasks:
        return jsonify({"error": "'name' and a non-empty 'tasks' list are required"}), 400
    for i, spec in enumerate(tasks):
        if not isinstance(spec, dict) or not spec.get("tool"):
            return jsonify({"error": f"tasks[{i}] must be an object with a 'tool' key"}), 400

    try:
        workflow_id = create_new_workflow(name, tasks)
    except KeyError as e:
        db.session.rollback()
        return jsonify({"error": str(e).strip("'\"")}), 404
    except ValueError as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 400

    workflow = Workflow.query.get(workflow_id)
    return jsonify(workflow_detail(workflow)), 201


@api_v1_bp.route("/workflows/<string:workflow_id>", methods=["GET"])
@api_auth()
def get_workflow(workflow_id):
    workflow = Workflow.query.get(workflow_id)
    if workflow is None:
        return jsonify({"error": "Workflow not found"}), 404
    return jsonify(workflow_detail(workflow))


@api_v1_bp.route("/workflows/<string:workflow_id>/retry", methods=["POST"])
@api_auth("operate")
def retry_workflow(workflow_id):
    """Requeue a failed workflow from its first failed task onwards."""
    workflow = Workflow.query.get(workflow_id)
    if workflow is None:
        return jsonify({"error": "Workflow not found"}), 404
    try:
        workflows.retry(workflow)
    except workflows.WorkflowStateError as e:
        return jsonify({"error": str(e)}), 409
    return jsonify(workflow_detail(workflow))


@api_v1_bp.route("/workflows/<string:workflow_id>", methods=["DELETE"])
@api_auth("operate")
def delete_workflow(workflow_id):
    workflow = Workflow.query.get(workflow_id)
    if workflow is None:
        return jsonify({"error": "Workflow not found"}), 404
    try:
        workflows.delete(workflow)
    except workflows.WorkflowStateError as e:
        return jsonify({"error": str(e)}), 409
    return jsonify({"status": "deleted"})
