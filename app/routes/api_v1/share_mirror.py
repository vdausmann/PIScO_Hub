from flask import current_app, jsonify

from ...backend import share_mirror
from . import api_v1_bp
from .auth import api_auth


@api_v1_bp.route("/mirror/status", methods=["GET"])
@api_auth()
def mirror_status():
    """The share mirror: last run, error, and per instrument the overview
    rows and files sent."""
    service = share_mirror.service(current_app)
    if service is None:
        return jsonify({"error": "The share mirror is not running"}), 404
    return jsonify(service.status())


@api_v1_bp.route("/mirror/run", methods=["POST"])
@api_auth("operate")
def mirror_run():
    """Write the overviews and copy to the share now, not at the next interval."""
    service = share_mirror.service(current_app)
    if service is None:
        return jsonify({"error": "The share mirror is not running"}), 404
    service.trigger()
    return jsonify({"triggered": True}), 202
