"""The ship's share, browsed from the Hub. See app/backend/share_browse.py."""
from urllib.parse import quote

from flask import Blueprint, Response, jsonify, render_template, request
from flask_login import login_required

from ..backend import share_browse

share_browse_bp = Blueprint("share-browse", __name__)


@share_browse_bp.route("/")
@login_required
def page():
    try:
        conf = share_browse.settings()
    except share_browse.BrowseError as e:
        return render_template("share_browse.html", error=str(e), conf=None)
    return render_template("share_browse.html", error=None,
                           conf={k: conf[k] for k in ("server", "share", "start")})


@share_browse_bp.route("/list")
@login_required
def listing():
    try:
        return jsonify(share_browse.list_dir(request.args.get("path", "")))
    except share_browse.BrowseError as e:
        return jsonify({"error": str(e)}), 400
    except OSError as e:
        return jsonify({"error": f"{type(e).__name__}: {e}"}), 502


@share_browse_bp.route("/file")
@login_required
def file():
    try:
        name, size, mime, inline, chunks = share_browse.open_file(request.args.get("path", ""))
    except share_browse.BrowseError as e:
        return jsonify({"error": str(e)}), 400
    except OSError as e:
        return jsonify({"error": f"{type(e).__name__}: {e}"}), 502
    disposition = "inline" if inline and request.args.get("download") != "1" else "attachment"
    if mime == "text/plain":
        mime = "text/plain; charset=utf-8"
    return Response(chunks, mimetype=mime, headers={
        "Content-Length": str(size),
        "Content-Disposition": f"{disposition}; filename*=UTF-8''{quote(name)}",
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "sandbox",
    })
