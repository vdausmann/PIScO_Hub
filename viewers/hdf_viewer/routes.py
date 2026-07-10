from flask import Blueprint, jsonify, render_template, request, url_for
from flask_login import login_required
import pathlib
import h5py

from .backend.hdf import build_tree, get_dataset_info, get_dataset_data, get_attributes

config_path = pathlib.Path(__file__).parent.resolve()
with open(config_path / "config.json") as f:
    import json
    config = json.load(f)

name = config["name"]
url = name.lower().replace("_", "-").replace(" ", "-")

hdf_viewer_bp = Blueprint(
    url,
    __name__,
    template_folder="templates",
    url_prefix="/viewer/" + url,
    static_folder="static",
    static_url_path="/static"
)


@hdf_viewer_bp.route("/")
@login_required
def index():
    path = request.args.get("path")

    if path is None:
        return jsonify({"error": "No path provided"}), 400

    attrs = get_attributes(path, "/")
    attrs_str = ""
    if not attrs is None:
        for key, val in attrs.items():
            attrs_str += f"{key}: {val}\n"

    return render_template(
        "hdf_viewer.html",
        file_path=path,
        root_level_attributes=attrs_str
    )



@hdf_viewer_bp.route("/tree")
@login_required
def tree():

    path = request.args.get("path")

    if not path:
        return jsonify({"error": "missing path"}), 400

    with h5py.File(path, "r") as f:

        tree = build_tree(f)

    return jsonify(tree)


@hdf_viewer_bp.route("/dataset_info")
@login_required
def dataset_info():

    file_path = request.args["file"]
    dataset_path = request.args["dataset"]

    info = get_dataset_info(file_path, dataset_path)

    if info is None:
        return jsonify({"error": "not a dataset"}), 400


    return jsonify(info)


@hdf_viewer_bp.route("/dataset_data")
@login_required
def dataset_data():

    file_path = request.args["file"]
    dataset_path = request.args["dataset"]

    info = get_dataset_data(file_path, dataset_path)

    if info is None:
        return jsonify({"error": "not a dataset"}), 400


    return jsonify(info)
