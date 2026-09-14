from flask import Blueprint, jsonify, render_template, request, send_file
from flask_login import login_required
from pathlib import Path
import json
import os

from ..backend.file_explorer import get_files
from ..backend.models import Viewer


file_explorer_bp = Blueprint('file-explorer', __name__)


@file_explorer_bp.route("/files")
@login_required
def list_files():

    path = request.args.get("path", "")

    try:
        result = get_files(path)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    return jsonify(result)

@file_explorer_bp.route('/')
@login_required
def hub():
    return render_template('file_explorer.html')


@file_explorer_bp.route("/download")
@login_required
def download():

    path = request.args.get("path")
    if not path:
        return jsonify({"error": "No path provided"}), 400

    if not os.path.exists(path):
        return jsonify({"error": "File not found"}), 404

    if not os.path.isfile(path):
        return jsonify({"error": "Path is not a file"}), 400


    return send_file(
        path,
        as_attachment=True
    )


@file_explorer_bp.route("/find_viewers")
@login_required
def find_viewers():

    path = request.args.get("path")

    if not path:
        return jsonify({
            "viewer": None
        })


    extension = (
        Path(path)
        .suffix
        .lower()
        .replace(".", "")
    )


    viewers = Viewer.query.all()


    possible_viewers = []

    for viewer in viewers:

        print(viewer.name)

        extensions = json.loads(
            viewer.extensions
        )

        if "*" in extensions:
            possible_viewers.append(viewer)
            # return jsonify({
            #     "viewer": viewer.url
            # })


        if extension in extensions:
            possible_viewers.append(viewer)
            # return jsonify({
            #     "viewer": viewer.url
            # })


    return jsonify([
        {
            "id": v.id,
            "name": v.name,
            "url": v.url
        }
        for v in possible_viewers
    ])
