from flask import Blueprint, render_template, current_app
from flask_login import login_required

from ..backend.hardware_device_control import parse_config
from ..backend.models import GlobalSetting

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
    return render_template(
        'hardware_device_control.html',
        config=config,
        devices=devices,
    )
