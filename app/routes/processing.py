from flask import Blueprint, render_template
from flask_login import login_required

processing_bp = Blueprint('processing', __name__)

@processing_bp.route('/')
@login_required
def hub():
    modules = []
    return render_template('processing_hub.html', modules=modules)


