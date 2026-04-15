import os
import signal
from flask import Blueprint, render_template, request, redirect, url_for, jsonify
from flask_login import login_required
from ...models import db, Task

processing_bp = Blueprint('processing', __name__)


@processing_bp.route('/')
@login_required
def hub():
    modules = []
    return render_template('processing_hub.html', modules=modules)



@processing_bp.route('/run/<module_id>', methods=['POST'])
@login_required
def run(module_id):
    # 1. Capture the edited text from the textarea
    edited_config = request.form.get('config_content')
    
    # 2. For now, let's just log it to see it working
    print(f"DEBUG: Starting execution for {module_id}")
    print(f"DEBUG: User Config:\n{edited_config}")
    
    # 3. Redirect back to the hub or a 'Jobs' page (which we'll build later)
    # For now, let's just go back to the hub.
    return redirect(url_for('processing.hub'))



@processing_bp.route('/step/stop/<int:step_id>', methods=['POST'])
@login_required
def stop_step(step_id):
    step = Task.query.get_or_404(step_id)
    
    if step.status == 'Running' and step.pid:
        try:
            # Send SIGTERM to the process
            os.kill(step.pid, signal.SIGTERM)
            step.status = 'Stopped'
            db.session.commit()
            return jsonify({"status": "success", "message": "Process terminated."})
        except ProcessLookupError:
            return jsonify({"status": "error", "message": "Process already dead."}), 400
            
    return jsonify({"status": "error", "message": "Step is not running."}), 400

@processing_bp.route('/step/reset/<int:step_id>', methods=['POST'])
@login_required
def reset_step(step_id):
    step = Task.query.get_or_404(step_id)
    
    # Reset to Pending so the worker picks it up again
    step.status = 'Pending'
    step.pid = None
    db.session.commit()
    
    return jsonify({"status": "success", "message": "Step reset to queue."})
