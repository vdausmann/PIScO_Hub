import json

from flask import current_app, jsonify, request

from ...backend.models import Tool
from ...backend.tools import ToolRegistry
from . import api_v1_bp
from .auth import api_auth
from .serializers import tool_detail, tool_summary


@api_v1_bp.route("/tools", methods=["GET"])
@api_auth
def list_tools():
    query = Tool.query
    available = request.args.get("available")
    if available is not None:
        query = query.filter(Tool.available == (available.lower() != "false"))
    tools = query.order_by(Tool.name).all()

    tag = request.args.get("tag")
    if tag:
        tools = [t for t in tools if tag in json.loads(t.tags or "[]")]

    return jsonify({"tools": [tool_summary(t) for t in tools]})


@api_v1_bp.route("/tools/<string:name>", methods=["GET"])
@api_auth
def describe_tool(name):
    tool = Tool.query.filter_by(name=name).first()
    if tool is None:
        return jsonify({"error": f"Tool '{name}' not found"}), 404
    return jsonify(tool_detail(tool))


@api_v1_bp.route("/tools/refresh", methods=["POST"])
@api_auth
def refresh_tools():
    registry = ToolRegistry(current_app._get_current_object())
    report = registry.scan_all()
    return jsonify(report)
