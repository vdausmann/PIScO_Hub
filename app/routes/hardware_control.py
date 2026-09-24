from flask import Blueprint, render_template
from flask_login import login_required

from pathlib import Path



hardware_control_bp = Blueprint('hardware-control', __name__)

@hardware_control_bp.route('/')
@login_required
def hub():
    return render_template('hardware_control.html')
