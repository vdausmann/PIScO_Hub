import os
import json
import importlib
from flask import Blueprint, render_template, abort
from flask_login import login_required

visualization_bp = Blueprint('visualization', __name__)

def get_viz_modules():
    modules = []
    tools_dir = os.path.join('modules', 'visualization', 'tools')
    if not os.path.exists(tools_dir): return []
    
    for folder in os.listdir(tools_dir):
        config_path = os.path.join(tools_dir, folder, 'config.json')
        if os.path.exists(config_path):
            with open(config_path, 'r') as f:
                data = json.load(f)
                data['folder_name'] = folder # Crucial for dispatching
                modules.append(data)
    return modules


@visualization_bp.route('/')
@login_required
def hub():
    # data_dir = os.environ.get('DATA_ROOT', './data')
    # files = [f for f in os.listdir(data_dir) if os.path.isfile(os.path.join(data_dir, f))]
    modules = get_viz_modules()
    return render_template('visualization_hub.html', modules=modules)


@visualization_bp.route('/view/<module_folder>/<path:filename>')
@login_required
def view_file(module_folder, filename):
    """
    The Dispatcher: It dynamically imports the 'logic' file from the 
    specified module folder and runs it.
    """
    try:
        # Dynamically import: modules.visualization.tools.hdf5_viewer.logic
        module_path = f"modules.visualization.tools.{module_folder}.logic"
        tool_logic = importlib.import_module(module_path)
        
        # Call the standard 'handle_request' function we defined in logic.py
        data, error = tool_logic.handle_request(filename)
        
        if error:
            return f"Error: {error}", 400
            
        # We can even let the tool define its own template name
        return render_template(f'{module_folder}.html', filename=filename, structure=data)
        
    except ImportError:
        abort(404, description="Visualization module logic not found.")
    except Exception as e:
        abort(500, description=str(e))
