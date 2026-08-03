import os

from flask import Response, current_app, jsonify, request

from . import api_v1_bp
from .openapi import build_spec


@api_v1_bp.route("/openapi.json", methods=["GET"])
def openapi_spec():
    server_url = request.url_root.rstrip("/") + "/api/v1"
    return jsonify(build_spec(server_url))


@api_v1_bp.route("/docs", methods=["GET"])
def agent_docs():
    path = os.path.join(current_app.config["HUB_ROOT"], "AGENTS.md")
    if not os.path.isfile(path):
        return jsonify({"error": "AGENTS.md not found"}), 404
    with open(path, "r") as f:
        return Response(f.read(), mimetype="text/markdown")
