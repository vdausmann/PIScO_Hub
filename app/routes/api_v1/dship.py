from flask import current_app, jsonify, request

from ...backend import dship, nav
from . import api_v1_bp
from .auth import api_auth


def _service():
    service = dship.service(current_app)
    if service is None or not (service.url or service.ops):
        return None
    return service


@api_v1_bp.route("/dship/status", methods=["GET"])
@api_auth()
def dship_status():
    service = _service()
    if service is None:
        return jsonify({"error": "DSHIP is not configured (DSHIP_URL)"}), 404
    return jsonify(service.status())


@api_v1_bp.route("/dship/operations", methods=["GET"])
@api_auth()
def dship_operations():
    service = _service()
    if service is None:
        return jsonify({"error": "DSHIP is not configured (DSHIP_URL)"}), 404
    return jsonify({"operations": service.list(request.args.get("device"))})


@api_v1_bp.route("/dship/at", methods=["GET"])
@api_auth()
def dship_at():
    service = _service()
    if service is None:
        return jsonify({"error": "DSHIP is not configured (DSHIP_URL)"}), 404
    try:
        when = nav.parse_time(request.args.get("time"))
    except (TypeError, ValueError):
        return jsonify({"error": "'time' must be ISO 8601 or epoch seconds"}), 400
    device = request.args.get("device") or service.device
    found = service.at(when, device)
    if found is None:
        return jsonify({"error": f"No {device} operation in DSHIP's action log at that time"}), 404
    return jsonify(found)
