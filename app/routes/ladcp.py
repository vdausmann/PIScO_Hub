"""The LADCP page and its JSON endpoints. The work happens in the
LadcpService (app/backend/ladcp/service.py); an action returns at once
with the operation it started, and the page follows it through /status."""

from flask import Blueprint, current_app, jsonify, render_template, request
from flask_login import login_required

from ..backend.ladcp.service import Busy

ladcp_bp = Blueprint("ladcp", __name__)


def _service():
    service = current_app.extensions.get("ladcp")
    if service is None:
        raise RuntimeError("the LADCP service is not running (create_worker=False?)")
    return service


def _body():
    body = request.get_json(silent=True)
    return body if isinstance(body, dict) else {}


@ladcp_bp.errorhandler(Busy)
def _busy(e):
    return jsonify({"error": str(e)}), 409


@ladcp_bp.errorhandler(ValueError)
def _invalid(e):
    return jsonify({"error": str(e)}), 400


@ladcp_bp.errorhandler(KeyError)
def _unknown(e):
    return jsonify({"error": f"unknown instrument {e}"}), 404


@ladcp_bp.route("/")
@login_required
def page():
    return render_template("ladcp.html")


def _next_cast(refresh=False):
    """The CTD cast DSHIP says comes next, or None without DSHIP."""
    from ..backend import dship
    service = dship.service(current_app)
    if service is None or not (service.url or service.ops):
        return None
    return service.next_cast(refresh=refresh)


@ladcp_bp.route("/status")
@login_required
def status():
    out = _service().status()
    nxt = _next_cast()
    out["next_cast"] = None if nxt is None else {
        k: nxt.get(k) for k in ("cast", "operation", "station", "source", "note")}
    return jsonify(out)


@ladcp_bp.route("/live")
@login_required
def live():
    """New ensembles per instrument. `since=A:120,B:118` gives the last
    sequence number the page has for each."""
    since = {}
    for part in request.args.get("since", "").split(","):
        name, _, seq = part.partition(":")
        if name and seq.isdigit():
            since[name] = int(seq)
    return jsonify(_service().live(since))


@ladcp_bp.route("/detect", methods=["POST"])
@login_required
def detect():
    return jsonify(_service().detect().as_dict()), 202


@ladcp_bp.route("/erase", methods=["POST"])
@login_required
def erase():
    return jsonify(_service().erase(force=bool(_body().get("force"))).as_dict()), 202


@ladcp_bp.route("/start", methods=["POST"])
@login_required
def start():
    body = _body()
    voltage = body.get("deck_voltage")
    if voltage in ("", None):
        voltage = None
    else:
        voltage = float(str(voltage).replace(",", "."))
    profile = body.get("profile")
    profile = int(profile) if profile not in (None, "") else None
    cast = None
    if profile is None:
        # The LADCP profile is the CTD cast: take DSHIP's number, read
        # fresh, unless the page asked for a number of its own.
        nxt = _next_cast(refresh=True)
        if nxt is not None:
            profile = nxt["cast"]
            cast = {k: nxt.get(k) for k in ("cast", "operation", "station", "source")}
    return jsonify(_service().start(force=bool(body.get("force")), deck_voltage=voltage,
                                    profile=profile, cast=cast).as_dict()), 202


@ladcp_bp.route("/stop", methods=["POST"])
@login_required
def stop():
    return jsonify(_service().stop(download=bool(_body().get("download", True))).as_dict()), 202


@ladcp_bp.route("/config")
@login_required
def get_config():
    return jsonify(_service().config)


@ladcp_bp.route("/config", methods=["PUT"])
@login_required
def put_config():
    return jsonify(_service().update_config(_body()))


@ladcp_bp.route("/mode", methods=["POST"])
@login_required
def set_mode():
    """{"mode": "dual"} or {"mode": "single", "instrument": "ADCP-A"}."""
    body = _body()
    mode = body.get("mode")
    if mode not in ("dual", "single"):
        raise ValueError("mode: 'dual' or 'single'")
    return jsonify(_service().set_mode(mode, body.get("instrument") or None))


@ladcp_bp.route("/preview")
@login_required
def preview():
    profile = request.args.get("profile", type=int)
    return jsonify(_service().preview(profile))


@ladcp_bp.route("/profile", methods=["POST"])
@login_required
def set_profile():
    number = _body().get("profile")
    if not isinstance(number, int) or number < 0:
        raise ValueError("profile: a whole number ≥ 0 (the last profile done; the next start uses +1)")
    _service().set_profile(number)
    return jsonify(_service().state)


@ladcp_bp.route("/battery-reset", methods=["POST"])
@login_required
def battery_reset():
    _service().reset_battery()
    return jsonify(_service().state)


@ladcp_bp.route("/transcript/<name>")
@login_required
def transcript(name):
    return jsonify(_service().transcript(name, request.args.get("since", 0, type=int)))


@ladcp_bp.route("/console/<name>", methods=["POST"])
@login_required
def console(name):
    command = str(_body().get("command", "")).strip()
    if not command:
        raise ValueError("command: empty")
    service = _service()
    if name not in service.instruments:
        raise KeyError(name)
    try:
        reply = service.console(name, command)
    except OSError as e:
        return jsonify({"error": str(e)}), 502
    return jsonify({"reply": reply})
