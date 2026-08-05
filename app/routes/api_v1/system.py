from flask import jsonify

from ...backend.models import Machine
from ...backend.task_manager import get_current_load
from . import api_v1_bp
from .auth import api_auth
from .serializers import machine_dict

API_VERSION = "1.0"


@api_v1_bp.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "api_version": API_VERSION})


@api_v1_bp.route("/system/load", methods=["GET"])
@api_auth()
def system_load():
    machines = [machine_dict(m) for m
                in Machine.query.order_by(Machine.name).all()]
    total_capacity = sum(m["max_weight"] or 0 for m in machines if m["enabled"])
    load = get_current_load()
    return jsonify({
        "load": load,
        "max_weight": total_capacity,
        "remaining_capacity": total_capacity - load,
        "machines": machines,
    })
