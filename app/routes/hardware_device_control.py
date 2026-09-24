import json
import os
from flask import Blueprint, render_template, current_app, request
from flask_login import login_required

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
