"""Versioned JSON API for the Hub.

All endpoints live under /api/v1 and accept either a browser session
(flask-login) or an API token via ``Authorization: Bearer <token>``.
Errors are always returned as JSON: {"error": "..."}.
"""

from flask import Blueprint, jsonify

api_v1_bp = Blueprint('api_v1', __name__)


@api_v1_bp.errorhandler(404)
def _not_found(e):
    return jsonify({"error": "Not found"}), 404


@api_v1_bp.errorhandler(405)
def _method_not_allowed(e):
    return jsonify({"error": "Method not allowed"}), 405


@api_v1_bp.errorhandler(500)
def _server_error(e):
    return jsonify({"error": "Internal server error"}), 500


from . import (auth, tools, providers, workflows, tasks, machines, system,  # noqa: E402,F401
               templates,
               docs)
