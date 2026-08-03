from flask import jsonify

from ...backend.models import GlobalSetting
from ...backend.task_manager import get_current_load
from . import api_v1_bp
from .auth import api_auth

API_VERSION = "1.0"


@api_v1_bp.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "api_version": API_VERSION})


@api_v1_bp.route("/system/load", methods=["GET"])
@api_auth
def system_load():
    load = get_current_load()
    max_weight = GlobalSetting.get("MAX_WEIGHT", default=10)
    return jsonify({"load": load, "max_weight": max_weight,
                    "remaining_capacity": max_weight - load})
