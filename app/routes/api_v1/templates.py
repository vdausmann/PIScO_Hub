from flask import jsonify, request
from flask_login import current_user

from ...backend import templates, workflows
from ...backend.models import TemplateWorkflow, Workflow
from . import api_v1_bp
from .auth import api_auth


@api_v1_bp.route("/templates", methods=["GET"])
@api_auth()
def list_templates():
    rows = TemplateWorkflow.query.order_by(TemplateWorkflow.name).all()
    return jsonify({"templates": [templates.template_dict(t) for t in rows]})


@api_v1_bp.route("/templates/<string:id_or_name>", methods=["GET"])
@api_auth()
def get_template(id_or_name):
    template = templates.find(id_or_name)
    if template is None:
        return jsonify({"error": "Template not found"}), 404
    return jsonify(templates.template_dict(template, detail=True))


@api_v1_bp.route("/templates", methods=["POST"])
@api_auth("operate")
def create_template():
    """Save a template.

    Body: {"name": str, "tasks": [...], "params_schema": object?,
    "description": str?, "name_format": str?}. Instead of "tasks",
    "from_workflow": <id> copies an existing workflow's steps.
    """
    data = request.get_json(silent=True) or {}
    specs = data.get("tasks")
    if data.get("from_workflow"):
        workflow = Workflow.query.get(data["from_workflow"])
        if workflow is None:
            return jsonify({"error": "Workflow not found"}), 404
        specs = workflows.spec(workflow)["tasks"]
    try:
        template = templates.save(
            data.get("name"), specs, data.get("params_schema"),
            data.get("description"), data.get("name_format"),
            current_user.id if current_user.is_authenticated else None)
    except templates.TemplateError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify(templates.template_dict(template, detail=True)), 201


@api_v1_bp.route("/templates/<string:template_id>", methods=["DELETE"])
@api_auth("operate")
def delete_template(template_id):
    template = TemplateWorkflow.query.get(template_id)
    if template is None:
        return jsonify({"error": "Template not found"}), 404
    try:
        templates.delete(template)
    except templates.TemplateError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"status": "deleted"})
