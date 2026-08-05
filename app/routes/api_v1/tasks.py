import os

from flask import jsonify, request

from ...backend.models import Task
from . import api_v1_bp
from .auth import api_auth
from .serializers import artifact_dict, task_dict


@api_v1_bp.route("/tasks/<string:task_id>", methods=["GET"])
@api_auth
def get_task(task_id):
    task = Task.query.get(task_id)
    if task is None:
        return jsonify({"error": "Task not found"}), 404
    data = task_dict(task)
    data["settings"] = task.settings
    return jsonify(data)


@api_v1_bp.route("/tasks/<string:task_id>/artifacts", methods=["GET"])
@api_auth
def get_task_artifacts(task_id):
    task = Task.query.get(task_id)
    if task is None:
        return jsonify({"error": "Task not found"}), 404
    return jsonify({"task_id": task.id,
                    "artifacts": [artifact_dict(a) for a in task.artifacts]})


@api_v1_bp.route("/tasks/<string:task_id>/logs", methods=["GET"])
@api_auth
def get_task_logs(task_id):
    task = Task.query.get(task_id)
    if task is None:
        return jsonify({"error": "Task not found"}), 404
    if not task.log_prefix:
        return jsonify({"error": "Task has not produced logs yet"}), 404

    stream = request.args.get("stream", "stdout")
    if stream not in ("stdout", "stderr"):
        return jsonify({"error": "stream must be 'stdout' or 'stderr'"}), 400
    path = task.log_prefix + (".log" if stream == "stdout" else ".err")

    if not os.path.isfile(path):
        return jsonify({"error": f"Log file not found: {path}"}), 404
    with open(path, "r") as f:
        lines = f.readlines()

    tail = request.args.get("tail", type=int)
    if tail is not None and tail >= 0:
        lines = lines[-tail:]

    return jsonify({"task_id": task.id, "stream": stream,
                    "content": "".join(lines)})
