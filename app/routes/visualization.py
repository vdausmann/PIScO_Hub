from flask import Blueprint, render_template
from flask_login import login_required

visualization_bp = Blueprint('visualization', __name__)

@visualization_bp.route('/')
@login_required
def hub():
    modules = []
    return render_template('visualization_hub.html', modules=modules)
