import os

from flask import (Blueprint, abort, current_app, redirect, render_template,
                   send_from_directory, url_for)
from flask_login import login_required

from ..backend.modules import get_module, load_modules, modules_file

visualization_bp = Blueprint('visualization', __name__)


@visualization_bp.route('/')
@login_required
def hub():
    return render_template('visualization_hub.html', modules=load_modules(),
                           modules_file=modules_file(),
                           viewer_cards=current_app.config.get("VIEWER_CARDS", []))


@visualization_bp.route('/m/<name>')
@login_required
def module(name):
    module = get_module(name)
    if module is None:
        abort(404)
    if module["mode"] == "link":
        return redirect(module["url"])
    if module["mode"] == "static":
        # The page is framed from the Hub itself; a missing index is the
        # usual state before the tool has run once, so say so.
        if not os.path.isfile(os.path.join(module["path"], "index.html")):
            return render_template('module_embed.html', module=module,
                                   src=None, missing=module["path"])
        src = url_for('visualization.module_file', name=name, filename='index.html')
        return render_template('module_embed.html', module=module, src=src)
    return render_template('module_embed.html', module=module, src=module["url"])


@visualization_bp.route('/m/<name>/files/<path:filename>')
@login_required
def module_file(name, filename):
    """A file of a static module. send_from_directory refuses paths that
    escape the directory."""
    module = get_module(name)
    if module is None or module["mode"] != "static":
        abort(404)
    return send_from_directory(module["path"], filename)
