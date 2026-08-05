import os

from flask import current_app, jsonify, request

from ...backend.models import Tool, ToolProvider, db
from ...backend.tools import ManifestError, ToolRegistry, load_toolbox_manifest
from . import api_v1_bp
from .auth import api_auth
from .serializers import provider_dict


@api_v1_bp.route("/providers", methods=["GET"])
@api_auth()
def list_providers():
    providers = ToolProvider.query.order_by(ToolProvider.name).all()
    return jsonify({"providers": [provider_dict(p) for p in providers]})


@api_v1_bp.route("/providers", methods=["POST"])
@api_auth("full")
def register_provider():
    data = request.get_json(silent=True) or {}
    path = data.get("path")
    if not path:
        return jsonify({"error": "'path' is required"}), 400
    path = os.path.abspath(os.path.expanduser(path))
    if not os.path.isdir(path):
        return jsonify({"error": f"'{path}' is not a directory"}), 400

    # Validate the manifest before touching the DB.
    try:
        provider_info, descriptors = load_toolbox_manifest(path)
    except ManifestError as e:
        return jsonify({"error": str(e)}), 400

    name = data.get("name") or provider_info["name"]
    if ToolProvider.query.filter((ToolProvider.name == name) |
                                 (ToolProvider.path == path)).first():
        return jsonify({"error": f"Provider '{name}' or path already registered"}), 409

    provider = ToolProvider()
    provider.name = name
    provider.path = path
    provider.description = provider_info.get("description")
    provider.version = provider_info.get("version")
    provider.builtin = False
    db.session.add(provider)
    db.session.commit()

    report = ToolRegistry(current_app._get_current_object()).scan_all()
    return jsonify({"provider": provider_dict(provider),
                    "scan": report["providers"].get(provider.name, {})}), 201


@api_v1_bp.route("/providers/<int:provider_id>", methods=["DELETE"])
@api_auth("full")
def delete_provider(provider_id):
    provider = ToolProvider.query.get(provider_id)
    if provider is None:
        return jsonify({"error": "Provider not found"}), 404
    if provider.builtin:
        return jsonify({"error": "The builtin provider cannot be removed"}), 400

    # Keep the tool rows for task history, but hide them from new work.
    deactivated = []
    for tool in Tool.query.filter_by(provider_id=provider.id).all():
        tool.available = False
        tool.provider_id = None
        deactivated.append(tool.name)

    db.session.delete(provider)
    db.session.commit()
    return jsonify({"status": "deleted", "deactivated_tools": deactivated})
