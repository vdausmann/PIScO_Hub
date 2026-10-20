"""The data upload page: to the NAS, by rsync from the user's computer or
straight from the browser. See app/backend/upload.py."""
from flask import Blueprint, jsonify, render_template, request
from flask_login import login_required

from ..backend import upload
from ..backend.models import GlobalSetting

upload_bp = Blueprint("upload", __name__)


def _error(e, status=400):
    return jsonify({"error": str(e)}), status


@upload_bp.route("/")
@login_required
def page():
    host = GlobalSetting.get("UPLOAD_SSH_HOST", "") or request.host.split(":")[0]
    return render_template("upload.html", roots=upload.roots(), ssh_host=host,
                           ssh_user=GlobalSetting.get("UPLOAD_SSH_USER", "upload"))


@upload_bp.route("/dirs")
@login_required
def dirs():
    path = request.args.get("path", "")
    try:
        entries = upload.list_dirs(path)
        info = {"path": path, "entries": entries}
        if path:
            ok, why = upload.writable(path)
            try:
                rsync_path = upload.rsync_path(path)
            except upload.UploadError:
                rsync_path = None          # outside /mnt: browser upload only
            info.update(writable=ok, why=why, rsync_path=rsync_path)
        return jsonify(info)
    except (upload.UploadError, OSError) as e:
        return _error(e)


@upload_bp.route("/mkdir", methods=["POST"])
@login_required
def mkdir():
    data = request.get_json(silent=True) or {}
    try:
        return jsonify({"path": upload.make_dir(data.get("parent", ""), data.get("name", ""))})
    except (upload.UploadError, OSError) as e:
        return _error(e)


@upload_bp.route("/check", methods=["POST"])
@login_required
def check():
    data = request.get_json(silent=True) or {}
    try:
        return jsonify({"files": upload.check(data.get("dest", ""), data.get("files", []))})
    except (upload.UploadError, OSError, KeyError, ValueError) as e:
        return _error(e)


@upload_bp.route("/chunk", methods=["POST"])
@login_required
def chunk():
    a = request.args
    try:
        have = upload.write_chunk(a.get("dest", ""), a.get("path", ""), int(a.get("offset", 0)),
                                  int(a.get("total", 0)), request.stream,
                                  overwrite=a.get("overwrite") == "1")
        return jsonify({"have": have})
    except (upload.UploadError, OSError, ValueError) as e:
        return _error(e, 409 if isinstance(e, upload.UploadError) else 400)
