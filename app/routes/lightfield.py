"""The Lightfield page: browse the LIFI index, pick frames, export them.
See app/backend/lightfield.py."""
import os
from urllib.parse import quote

from flask import (Blueprint, Response, current_app, jsonify, render_template, request,
                   send_file)
from flask_login import login_required

from ..backend import dship, lightfield
from ..backend.lightfield import LightfieldError
from ..backend.models import Tool, Workflow, db
from ..backend.task_manager import create_new_workflow

lightfield_bp = Blueprint("lightfield", __name__)


def _conf():
    return lightfield.settings(current_app)


def _status(workflow_id):
    workflow = Workflow.query.get(workflow_id) if workflow_id else None
    return workflow.status if workflow else None


@lightfield_bp.route("/")
@login_required
def page():
    return render_template("lightfield.html")


@lightfield_bp.route("/api/state")
@login_required
def state():
    conf = _conf()
    recs = lightfield.recordings(conf["index"])
    running = [{"id": w.id, "name": w.name, "status": w.status}
               for w in Workflow.query.filter(Workflow.status.in_(("Pending", "Running")))
               .order_by(Workflow.created_at).all()
               if any(t.tool and t.tool.name == lightfield.INDEX_TOOL for t in w.tasks)]
    tools = {t.name for t in Tool.query.filter(
        Tool.name.in_((lightfield.INDEX_TOOL, lightfield.EXPORT_TOOL))).all() if t.available}
    return jsonify({
        "index_dir": conf["index"], "source_dir": conf["source"],
        "sources": lightfield.sources(conf["source"], conf["index"]),
        "recordings": recs, "casts": lightfield.casts(recs),
        "indexing": running, "tools": sorted(tools),
    })


@lightfield_bp.route("/api/frames")
@login_required
def frames():
    conf = _conf()
    try:
        if request.args.get("recording"):
            stem = request.args["recording"]
            rows = [dict(r, stem=stem) for r in lightfield.frames(conf["index"], stem)]
        else:
            rows = lightfield.cast_frames(conf["index"], lightfield.recordings(conf["index"]),
                                          request.args.get("cast", ""))
    except LightfieldError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"frames": rows})


@lightfield_bp.route("/img/<stem>/<kind>/<int:index>.jpg")
@login_required
def image(stem, kind, index):
    try:
        path = lightfield.image_path(_conf()["index"], stem, kind, index)
    except LightfieldError as e:
        return jsonify({"error": str(e)}), 400
    try:
        return send_file(path, mimetype="image/jpeg", max_age=86400)
    except FileNotFoundError:
        return jsonify({"error": "not indexed"}), 404


@lightfield_bp.route("/zoom/<stem>/<int:index>.jpg")
@login_required
def zoom(stem, index):
    conf = _conf()
    try:
        info = lightfield.recording(conf["index"], stem)
        if not info:
            raise LightfieldError(f"{stem} is not indexed")
        data, window = lightfield.zoom(
            info["file"], index, float(request.args.get("x", 0.5)), float(request.args.get("y", 0.5)),
            stretch=request.args.get("stretch", "1") != "0")
    except (LightfieldError, ValueError) as e:
        return jsonify({"error": str(e)}), 400
    except OSError as e:
        return jsonify({"error": f"{type(e).__name__}: {e}"}), 502
    return Response(data, mimetype="image/jpeg", headers={
        "X-Zoom-Window": "{x},{y},{width},{height},{frame_width},{frame_height}".format(**window)})


@lightfield_bp.route("/api/index", methods=["POST"])
@login_required
def start_index():
    """Queue the index over every recording: `shards` workflows, which
    the scheduler spreads over the workers that mount NAS_B."""
    conf = _conf()
    data = request.get_json(silent=True) or {}
    try:
        step, shards = int(data.get("step", 10)), int(data.get("shards", 3))
        if step < 1 or not 1 <= shards <= 10:
            raise ValueError
    except (TypeError, ValueError):
        return jsonify({"error": "step >= 1 and 1-10 shards, please"}), 400
    if not conf["source"]:
        return jsonify({"error": "LIFI_SOURCE_DIR is not set"}), 400
    service = dship.service(current_app)
    ops = service.list(service.device) if service else []
    if not ops:
        return jsonify({"error": "no CTD casts from DSHIP: the index would take frames on deck too"}), 409
    windows = lightfield.write_windows(conf["index"], ops)
    ids = []
    try:
        for k, spec in enumerate(lightfield.index_specs(conf["index"], conf["source"], step,
                                                        shards, windows)):
            ids.append(create_new_workflow(f"lifi index step {step} ({k + 1}/{shards})", [spec]))
    except (KeyError, ValueError) as e:
        db.session.rollback()
        return jsonify({"error": str(e).strip("'\"")}), 400
    return jsonify({"workflows": ids}), 201


@lightfield_bp.route("/api/exports")
@login_required
def list_exports():
    out = lightfield.exports(_conf()["index"])
    for e in out:
        e["status"] = _status(e["workflow_id"])
    return jsonify({"exports": out})


@lightfield_bp.route("/api/exports", methods=["POST"])
@login_required
def create_export():
    conf = _conf()
    data = request.get_json(silent=True) or {}
    label = str(data.get("label", ""))[:80]
    try:
        name, settings = lightfield.new_export(conf["index"], data.get("frames") or [],
                                               str(data.get("format", "png")).lower(), label)
    except LightfieldError as e:
        return jsonify({"error": str(e)}), 400
    except FileExistsError:
        return jsonify({"error": "an export of that name was just made; try again"}), 409
    try:
        workflow_id = create_new_workflow(f"lifi export {name}",
                                          [{"tool": lightfield.EXPORT_TOOL, "settings": settings}])
    except (KeyError, ValueError) as e:
        db.session.rollback()
        os.rmdir(settings["targetPath"])
        return jsonify({"error": str(e).strip("'\"")}), 400
    lightfield.record_export(conf["index"], name, workflow_id, settings, label)
    return jsonify({"name": name, "workflow_id": workflow_id}), 201


@lightfield_bp.route("/exports/<name>/<path:filename>")
@login_required
def export_file(name, filename):
    try:
        path = lightfield.export_file(_conf()["index"], name, filename)
    except LightfieldError as e:
        return jsonify({"error": str(e)}), 404
    return send_file(path, as_attachment=True, download_name=filename)


@lightfield_bp.route("/exports/<name>.zip")
@login_required
def export_zip(name):
    root = _conf()["index"]
    try:
        files = [lightfield.export_file(root, name, f["name"])
                 for e in lightfield.exports(root) if e["name"] == name for f in e["files"]]
    except LightfieldError as e:
        return jsonify({"error": str(e)}), 404
    if not files:
        return jsonify({"error": "nothing written yet"}), 404
    return Response(lightfield.zip_stream(files), mimetype="application/zip", headers={
        "Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}.zip"})
