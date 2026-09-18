from flask import Blueprint, jsonify, render_template, request, send_file
from flask_login import login_required

from ..backend.hardware_control import parse_sensor_data, parse_config
from ..backend.models import GlobalSetting

from pathlib import Path


DATA_DIR = Path("hardware_control")


hardware_control_bp = Blueprint('hardware-control', __name__)

@hardware_control_bp.route('/')
@login_required
def hub():
    config_file_path = GlobalSetting.get("HARDWARE_CONTROL_CONFIG_FILE", "hardware_control/hardware_control_config.json")
    if not type(config_file_path) is str:
        raise ValueError("HARDWARE_CONTROL_CONFIG_FILE must be a string")

    config = parse_config(Path(config_file_path))
    return render_template('hardware_control.html', config=config)


@hardware_control_bp.route('/files')
@login_required
def get_files():

    files = sorted(
        file.name
        for file in DATA_DIR.glob("*.txt")
        if file.is_file()
    )

    return jsonify(files)


@hardware_control_bp.route('/sensor-data')
@login_required
def get_sensor_data():
    filename = request.args.get("file")

    if not filename:
        return jsonify({"error": "No file selected"}), 400

    # Prevent paths such as ../../some_file
    filepath = DATA_DIR / Path(filename).name

    if not filepath.is_file():
        return jsonify({"error": "File not found"}), 404

    try:
        data = parse_sensor_data(filepath)

        return jsonify(data)

    except Exception as e:
        return jsonify({"error": str(e)}), 500
