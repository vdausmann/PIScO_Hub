from flask import current_app, jsonify, request

from ...backend import nav
from . import api_v1_bp
from .auth import api_auth


@api_v1_bp.route("/nav/latest", methods=["GET"])
@api_auth()
def nav_latest():
    service = nav.service(current_app)
    if service is None or not service.sources:
        return jsonify({"error": "No navigation source configured (NAV_SOURCES)"}), 404
    return jsonify(service.snapshot())


@api_v1_bp.route("/nav/at", methods=["GET"])
@api_auth()
def nav_at():
    service = nav.service(current_app)
    if service is None:
        return jsonify({"error": "No navigation source configured (NAV_SOURCES)"}), 404
    try:
        when = nav.parse_time(request.args.get("time"))
    except (TypeError, ValueError):
        return jsonify({"error": "'time' must be ISO 8601 or epoch seconds"}), 400
    position = service.position_at(when)
    if position is None:
        return jsonify({"error": f"No ship position within {nav.MAX_GAP_S} s of that time "
                                 f"in the track log"}), 404
    return jsonify(position)
