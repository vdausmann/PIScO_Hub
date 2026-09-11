from flask import Blueprint, jsonify, render_template, request, url_for
from flask_login import login_required
import pathlib
import h5py
import h5py
from flask import Blueprint, jsonify, request, render_template

from .backend.hdf_profile_viewer import (build_tree, generate_crop, get_attributes,
                                         get_dataset_info, get_attributes, generate_frame_view,
                                         generate_raw_frame, profile_counts)

config_path = pathlib.Path(__file__).parent.resolve()
with open(config_path / "config.json") as f:
    import json
    config = json.load(f)

name = config["name"]
url = name.lower().replace("_", "-").replace(" ", "-")

hdf_profile_viewer_bp = Blueprint(
    url,
    __name__,
    template_folder="templates",
    url_prefix="/viewer/" + url,
    static_folder="static",
    static_url_path="/static"
)


@hdf_profile_viewer_bp.route("/")
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

    # Optional deep link: open straight onto one object instead of the tree
    # root. This is what an EcoTaxa object_link points at, so a classified
    # crop can be traced back to the frame it came out of.
    return render_template(
        "hdf_profile_viewer.html",
        file_path=path,
        root_level_attributes=attrs_str,
        focus_group=request.args.get("group"),
        focus_index=request.args.get("index", type=int),
    )



@hdf_profile_viewer_bp.route("/tree")
@login_required
def tree():

    path = request.args.get("path")

    if not path:
        return jsonify({"error": "missing path"}), 400

    with h5py.File(path, "r") as f:

        tree = build_tree(f)

    return jsonify(tree)


@hdf_profile_viewer_bp.route("/dataset_info")
@login_required
def dataset_info():

    file_path = request.args["file"]
    dataset_path = request.args["dataset"]

    info = get_dataset_info(file_path, dataset_path)

    if info is None:
        return jsonify({"error": "not a dataset"}), 400


    return jsonify(info)



@hdf_profile_viewer_bp.route('/dataset_data')
@login_required
def get_dataset_render():
    file_path = request.args.get('file')
    dataset_path = request.args.get('dataset')
    element_index = request.args.get('element_index', type=int)

    with h5py.File(file_path, 'r') as f:
        if dataset_path not in f:
            return jsonify({"error": "Dataset not found"}), 404
        
        dataset = f[dataset_path]

        # # --- CASE 1: Render specific single-element reconstruction image ---
        # if element_index is not None:
        #     raw_value = dataset.flat[element_index]
        #     img_b64 = generate_reconstructed_image(raw_value, dataset_path)
        #     
        #     html_content = f'<img class="img-preview" src="data:image/png;base64,{img_b64}" alt="Slice projection">'
        #     return jsonify({"type": "image", "html": html_content})
        #
        # # --- CASE 2: Whole dataset natively qualifies as a full structural matrix image ---
        # if len(dataset.shape) == 2 and dataset.shape[0] > 50:
        #     img_b64 = generate_frame_view(dataset[:])
        #     html_content = f'<img class="img-preview" src="data:image/png;base64,{img_b64}" alt="Dataset view">'
        #     return jsonify({"type": "image", "html": html_content})


        if "PixelData" in dataset_path:
            # The frame plus the geometry of every crop in it, so the page can
            # turn a click on the preview into a full-resolution crop.
            view = generate_frame_view(file_path, dataset_path)
            return jsonify({
                "type": "frame",
                "image": view["image"],
                "boxes": view["boxes"],
                "preview_size": view["preview_size"],
                "dataset": dataset_path,
            })
        else:
            matrix_data = dataset[:].tolist()
            if not isinstance(matrix_data, list):
                matrix_data = [[matrix_data]]
            elif len(matrix_data) > 0 and not isinstance(matrix_data[0], list):
                matrix_data = [matrix_data]

            flat_values = dataset[:].flatten()
            min_val = float(flat_values.min())
            max_val = float(flat_values.max())

            # Render an isolated partial layout template explicitly built for the matrix grid array
            html_content = render_template(
                'partials/matrix_grid.html',
                matrix=matrix_data,
                min_val=min_val,
                max_val=max_val,
                file_path=file_path,
                dataset_path=dataset_path
            )
            return jsonify({"type": "matrix", "html": html_content})



@hdf_profile_viewer_bp.route('/show_crop')
@login_required
def show_crop():
    file_path = request.args.get('file')
    dataset_path = request.args.get('dataset')
    element_index = request.args.get('element_index', type=int)
    isolate = request.args.get('isolate') in ("1", "true", "True", "yes")

    img_b64 = generate_crop(file_path, element_index, dataset_path, isolate)
    html_content = f'<img class="img-preview" src="data:image/png;base64,{img_b64}" alt="Dataset view">'
    return jsonify({"type": "image", "html": html_content})


@hdf_profile_viewer_bp.route('/profile')
@login_required
def profile():
    """Object counts against depth for every frame in the file."""
    file_path = request.args.get("file")
    if not file_path:
        return jsonify({"error": "No file provided"}), 400

    try:
        return jsonify(profile_counts(file_path))
    except (OSError, KeyError) as e:
        return jsonify({"error": str(e)}), 400


@hdf_profile_viewer_bp.route('/raw_frame')
@login_required
def raw_frame():
    """The unprocessed source image behind a frame group.

    The frame view is a reconstruction from the kept crops; this is what the
    camera actually recorded, at the same preview size so the two can be
    swapped under one overlay.
    """
    file_path = request.args.get('file')
    group = request.args.get('group')

    if not file_path or not group:
        return jsonify({"error": "missing file or group"}), 400

    result = generate_raw_frame(file_path, group)
    if "error" in result:
        return jsonify(result), 404
    return jsonify(result)
