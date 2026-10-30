from flask import Blueprint, current_app, jsonify, render_template, request
from flask_login import current_user, login_required

from ..backend.hardware_sensor_display import build_status, merge_sources, parse_config
from ..backend.hardware_poller import parse_line
from ..backend import user_alarms
from ..backend.models import GlobalSetting

from pathlib import Path


DATA_DIR = Path("hardware_control")


hardware_sensor_display_bp = Blueprint('hardware-sensor-display', __name__)


def _config():
    config_file_path = GlobalSetting.get("HARDWARE_CONTROL_CONFIG_FILE", "hardware_control/hardware_control_config.json")
    if not type(config_file_path) is str:
        raise ValueError("HARDWARE_CONTROL_CONFIG_FILE must be a string")
    return parse_config(Path(config_file_path))


def _alarm_store():
    return user_alarms.AlarmStore(user_alarms.store_path(current_app))


def _limit_store():
    return user_alarms.LimitStore(user_alarms.limits_path(current_app))


def _user():
    return getattr(current_user, "username", "?")


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
        values = {k: (v, False, None) for k, v in raw.items()}
        sources = {}
    else:
        poller = current_app.extensions.get("hardware_poller")
        if poller is None:
            return jsonify({"error": "the hardware poller is not running"}), 503
        live = poller.live()
        values, sources = merge_sources(live, live.pop("_meta", {}))

    return jsonify(build_status(config, values, sources, _alarm_store().load(),
                                _limit_store().load()))


@hardware_sensor_display_bp.route('/alarms/active')
@login_required
def active():
    """{"active": [alarm], "checked": time}: what the alarm monitor found on
    its last pass. Every Hub page polls this for its alarm banner."""
    monitor = current_app.extensions.get("alarm_monitor")
    if monitor is None:
        return jsonify({"error": "the alarm monitor is not running"}), 503
    return jsonify(monitor.snapshot())


@hardware_sensor_display_bp.route('/alarms/ack', methods=['POST'])
@login_required
def acknowledge():
    """Body {"ids": [alarm id]}: logs who acknowledged which alarm. The
    acknowledgement itself is per browser."""
    monitor = current_app.extensions.get("alarm_monitor")
    ids = (request.get_json(silent=True) or {}).get("ids")
    if monitor is None:
        return jsonify({"error": "the alarm monitor is not running"}), 503
    if not isinstance(ids, list):
        return jsonify({"error": "body must be {\"ids\": [...]}"}), 400
    monitor.acknowledge([str(i) for i in ids], _user())
    return "", 204


@hardware_sensor_display_bp.route('/alarms', methods=['POST'])
@login_required
def add_alarm():
    """Body {"field": "Pi-ExPres", "kind": "threshold", "op": ">", "value": 1500,
    "only_while": {...}?, "name": "..."?}; kinds in user_alarms."""
    try:
        alarm = _alarm_store().add(request.get_json(silent=True), _user())
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify(alarm), 201


@hardware_sensor_display_bp.route('/alarms/<alarm_id>', methods=['PATCH'])
@login_required
def update_alarm(alarm_id):
    """Body {"enabled": false} switches an alarm off without deleting it;
    a body like POST's changes its condition."""
    try:
        return jsonify(_alarm_store().update(alarm_id, request.get_json(silent=True) or {}, _user()))
    except KeyError:
        return jsonify({"error": "no such alarm"}), 404
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@hardware_sensor_display_bp.route('/alarms/<alarm_id>', methods=['DELETE'])
@login_required
def delete_alarm(alarm_id):
    try:
        _alarm_store().delete(alarm_id)
    except KeyError:
        return jsonify({"error": "no such alarm"}), 404
    return "", 204


@hardware_sensor_display_bp.route('/limits/<shorthand>', methods=['PUT'])
@login_required
def set_limit(shorthand):
    """Body {"min": 11.5, "max": 13, "condition": "Pa-PiPwr==1", "enabled": true}:
    a configured limit changed on the Hub, in place of the config file's."""
    sensor = next((s for d in _config().values() if isinstance(d, dict)
                   for s in d.get("sensors") or [] if s["shorthand"] == shorthand), None)
    if sensor is None:
        return jsonify({"error": f"{shorthand} is not a configured sensor"}), 404
    try:
        return jsonify(_limit_store().set(shorthand, request.get_json(silent=True), sensor, _user()))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@hardware_sensor_display_bp.route('/limits/<shorthand>', methods=['DELETE'])
@login_required
def reset_limit(shorthand):
    """Back to the limit in the config file."""
    try:
        _limit_store().reset(shorthand)
    except KeyError:
        return jsonify({"error": "this limit is not changed"}), 404
    return "", 204
