from flask import Blueprint, current_app, jsonify, render_template, request
from flask_login import login_required

from ..backend.hardware_sensor_display import (display_values, load_sensors, merge_sources,
                                               parse_config)
from ..backend.hardware_poller import parse_line
from ..backend.models import GlobalSetting

from pathlib import Path


DATA_DIR = Path("hardware_control")


hardware_sensor_display_bp = Blueprint('hardware-sensor-display', __name__)


def _config():
    config_file_path = GlobalSetting.get("HARDWARE_CONTROL_CONFIG_FILE", "hardware_control/hardware_control_config.json")
    if not type(config_file_path) is str:
        raise ValueError("HARDWARE_CONTROL_CONFIG_FILE must be a string")
    return parse_config(Path(config_file_path))


@hardware_sensor_display_bp.route('/')
@login_required
def hub():
    # ?file=<name> shows a local hardware_control/*.txt instead of the
    # controllers, for working on the page without an instrument.
    return render_template('hardware_sensor_display.html', config=_config(),
                           test_file=request.args.get("file", ""))


@hardware_sensor_display_bp.route('/status')
@login_required
def status():
    """{"sensors": {shorthand: display}, "devices": {device: [source]}}:
    the latest values from the hardware poller, formatted and checked
    against the configured limits and alarm conditions."""
    config = _config()
    test_file = request.args.get("file")
    if test_file:
        # Prevent paths such as ../../some_file
        filepath = DATA_DIR / Path(test_file).name
        if not filepath.is_file():
            return jsonify({"error": "File not found"}), 404
        lines = filepath.read_text(encoding="utf-8", errors="replace").strip().splitlines()
        _, raw = parse_line(lines[-1] if lines else "")
        values = {k: (v, False) for k, v in raw.items()}
        sources = {}
    else:
        poller = current_app.extensions.get("hardware_poller")
        if poller is None:
            return jsonify({"error": "the hardware poller is not running"}), 503
        live = poller.live()
        values, sources = merge_sources(live, live.pop("_meta", {}))

    return jsonify({
        "sensors": display_values(load_sensors(config), values),
        "devices": sources,
    })
