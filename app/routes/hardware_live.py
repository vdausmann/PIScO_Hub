"""Live values from the instrument controllers and commands back to them.
Served by the HardwarePoller (app/backend/hardware_poller.py); kept apart
from the hardware-control page's own routes so the page can be worked on
independently."""

from flask import Blueprint, current_app, jsonify, request
from flask_login import login_required

hardware_live_bp = Blueprint("hardware-live", __name__)


def _poller():
    poller = current_app.extensions.get("hardware_poller")
    if poller is None:
        raise RuntimeError("the hardware poller is not running (create_worker=False?)")
    return poller


@hardware_live_bp.route("/")
@login_required
def live():
    """{device: {shorthand: value}} for every configured device, plus
    `_meta` per device: measured/received times, stale, error."""
    return jsonify(_poller().live())


@hardware_live_bp.route("/commands/<device>")
@login_required
def commands(device):
    """The device's current command line as a dict."""
    poller = _poller()
    if device not in poller.devices:
        return jsonify({"error": f"unknown device '{device}'"}), 404
    try:
        return jsonify(poller.command_state(device))
    except OSError as e:
        return jsonify({"error": str(e)}), 502


@hardware_live_bp.route("/commands/<device>", methods=["POST"])
@login_required
def send_commands(device):
    """Body {"Pi-SetFPS": 10, ...}: merged into the device's command line
    and copied over if anything changed. Returns the full state."""
    poller = _poller()
    if device not in poller.devices:
        return jsonify({"error": f"unknown device '{device}'"}), 404
    changes = request.get_json(silent=True)
    if not isinstance(changes, dict) or not changes:
        return jsonify({"error": "body must be a JSON object of shorthand: value"}), 400
    try:
        return jsonify(poller.send(device, changes))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except OSError as e:
        return jsonify({"error": str(e)}), 502
