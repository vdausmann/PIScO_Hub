from flask import current_app, jsonify, request

from ...backend import machines as machines_backend
from ...backend.models import Machine, db
from . import api_v1_bp
from .auth import api_auth
from .serializers import machine_dict


@api_v1_bp.route("/machines", methods=["GET"])
@api_auth()
def list_machines():
    rows = Machine.query.order_by(Machine.name).all()
    return jsonify({"machines": [machine_dict(m) for m in rows]})


@api_v1_bp.route("/machines", methods=["POST"])
@api_auth("full")
def register_machine():
    data = request.get_json(silent=True) or {}
    if not data.get("name") or not data.get("host"):
        return jsonify({"error": "'name' and 'host' are required"}), 400
    if Machine.query.filter_by(name=data["name"]).first():
        return jsonify({"error": f"Machine '{data['name']}' already registered"}), 409

    machine = Machine(is_local=False, status="unknown", max_weight=10,
                      tags="[]", enabled=True, dedicated=False)
    errors = machines_backend.apply_changes(machine, data)
    if errors:
        return jsonify({"error": " ".join(errors)}), 400
    db.session.add(machine)
    db.session.commit()

    # Answer the obvious question straight away rather than making the
    # caller wait for the next probe cycle.
    machines_backend.probe_now(machine, current_app)
    return jsonify(machine_dict(machine)), 201


@api_v1_bp.route("/machines/<int:machine_id>", methods=["PATCH"])
@api_auth("full")
def update_machine(machine_id):
    machine = Machine.query.get(machine_id)
    if machine is None:
        return jsonify({"error": "Machine not found"}), 404

    data = request.get_json(silent=True) or {}
    errors = machines_backend.apply_changes(machine, data)
    if errors:
        return jsonify({"error": " ".join(errors)}), 400
    db.session.commit()
    return jsonify(machine_dict(machine))


@api_v1_bp.route("/machines/<int:machine_id>", methods=["DELETE"])
@api_auth("full")
def delete_machine(machine_id):
    machine = Machine.query.get(machine_id)
    if machine is None:
        return jsonify({"error": "Machine not found"}), 404
    error = machines_backend.remove(machine)
    if error:
        return jsonify({"error": error}), 400 if machine.is_local else 409
    return jsonify({"status": "deleted"})
