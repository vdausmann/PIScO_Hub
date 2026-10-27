import json
import os
from flask import Blueprint, jsonify, render_template, current_app, request
from flask_login import current_user, login_required

from ..backend.hardware_device_control import parse_config
from ..backend.models import GlobalSetting
from ..backend.hardware_poller import parse_line

from pathlib import Path


DATA_DIR = Path("hardware_control")


hardware_device_control_bp = Blueprint('hardware-device-control', __name__)

@hardware_device_control_bp.route('/')
@login_required
def hub():
    config_file_path = GlobalSetting.get("HARDWARE_CONTROL_CONFIG_FILE", "hardware_control/hardware_control_config.json")
    if not type(config_file_path) is str:
        raise ValueError("HARDWARE_CONTROL_CONFIG_FILE must be a string")

    config = parse_config(Path(config_file_path))
    poller = current_app.extensions.get("hardware_poller")
    devices = list(poller.links) if poller else []
    test_mode = request.args.get("test") == "1"
    test_commands = {}
    if test_mode:
        test_file = os.path.join(current_app.config["HUB_ROOT"],
                                 "hardware_control", "PiscoComputeCommands.test.txt")
        with open(test_file, encoding="utf-8") as f:
            _, test_commands["PIScO"] = parse_line(f.read().strip())
    return render_template(
        'hardware_device_control.html',
        config=config,
        devices=devices,
        test_mode=test_mode,
        test_commands=test_commands,
    )


@hardware_device_control_bp.route('/shutdown-all')
@login_required
def shutdown_status():
    """The latest Shutdown-All run: running, who started it, the log tail."""
    return jsonify(current_app.extensions["sea_through_shutdown"].status())


@hardware_device_control_bp.route('/shutdown-all', methods=['POST'])
@login_required
def shutdown_all():
    """Body {"confirm": "SHUTDOWN"} runs sea-through-controll's shutdown
    sequence; {"dry_run": true} only checks its configuration."""
    body = request.get_json(silent=True) or {}
    dry_run = bool(body.get("dry_run"))
    if not dry_run and body.get("confirm") != "SHUTDOWN":
        return jsonify({"error": 'confirm with {"confirm": "SHUTDOWN"}'}), 400
    try:
        return jsonify(current_app.extensions["sea_through_shutdown"].start(
            getattr(current_user, "username", "?"), dry_run))
    except RuntimeError as e:
        return jsonify({"error": str(e)}), 409
    except OSError as e:
        return jsonify({"error": str(e)}), 500
