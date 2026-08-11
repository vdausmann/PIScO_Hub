import json

from flask import current_app, jsonify, request

from ...backend import machines as machines_backend
from ...backend.models import Machine, Task, db
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
    name = data.get("name")
    host = data.get("host")
    if not name or not host:
        return jsonify({"error": "'name' and 'host' are required"}), 400
    if Machine.query.filter_by(name=name).first():
        return jsonify({"error": f"Machine '{name}' already registered"}), 409

    machine = Machine()
    machine.name = name
    machine.host = host
    machine.ssh_user = data.get("ssh_user")
    machine.max_weight = data.get("max_weight", 10)
    machine.tags = json.dumps(data.get("tags", []))
    machine.note = data.get("note")
    machine.enabled = data.get("enabled", True)
    machine.is_local = False
    machine.status = "unknown"
    db.session.add(machine)
    db.session.commit()

    # Answer the obvious question straight away rather than making the
    # caller wait for the next probe cycle.
    from ...backend import artifacts

    ready, reason = machines_backend.probe(machine, artifacts.data_root(current_app))
    machine.status = "online" if ready else "offline"
    machine.last_probe_error = reason
    db.session.commit()

    return jsonify(machine_dict(machine)), 201


@api_v1_bp.route("/machines/<int:machine_id>", methods=["PATCH"])
@api_auth("full")
def update_machine(machine_id):
    machine = Machine.query.get(machine_id)
    if machine is None:
        return jsonify({"error": "Machine not found"}), 404

    data = request.get_json(silent=True) or {}
    for field in ("host", "ssh_user", "max_weight", "note", "enabled"):
        if field in data:
            setattr(machine, field, data[field])
    if "tags" in data:
        machine.tags = json.dumps(data["tags"])
    db.session.commit()
    return jsonify(machine_dict(machine))


@api_v1_bp.route("/machines/<int:machine_id>", methods=["DELETE"])
@api_auth("full")
def delete_machine(machine_id):
    machine = Machine.query.get(machine_id)
    if machine is None:
        return jsonify({"error": "Machine not found"}), 404
    if machine.is_local:
        return jsonify({"error": "The local machine cannot be removed"}), 400
    if Task.query.filter_by(machine_id=machine.id, status="Running").count():
        return jsonify({"error": "Machine still has running tasks"}), 409

    # Keep finished tasks; they just lose the machine reference.
    Task.query.filter_by(machine_id=machine.id).update({"machine_id": None})
    db.session.delete(machine)
    db.session.commit()
    return jsonify({"status": "deleted"})
