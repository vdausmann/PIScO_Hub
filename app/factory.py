from flask import Flask
from .routes import (auth, processing, visualization, task_manager, global_settings,
    workflow_builder, file_explorer, deep_link, hardware_control)
from .routes.api_v1 import api_v1_bp
from .backend.models import db, login_manager
from .backend.viewers import ViewerManager
from .backend.global_settings import seed_settings
from .backend.migrations import migrate
from .backend.machines import ensure_local_machine
from .backend.tools import ToolRegistry
from dotenv import load_dotenv
from pathlib import Path
import os

def create_app(create_worker=True):
    """
    Creates the app
    """

    # Anchor all runtime directories to the repo root so the server (and
    # scripts) work from any CWD.
    hub_root = str(Path(__file__).resolve().parent.parent)
    load_dotenv(os.path.join(hub_root, ".env"))
    app = Flask(__name__)
    # Key order carries meaning in places (a template's parameters are
    # shown in the order they were defined), so responses keep it.
    app.json.sort_keys = False

    app.config["SECRET_KEY"] = os.environ.get("FLASK_SECRET_KEY")
    if not app.config["SECRET_KEY"]:
        raise ValueError("No secret key set, run the 'setup_env.py' script")
    app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL')
    app.config["HUB_ROOT"] = hub_root
    # Logs stay local to the Hub, which serves them through the API; task
    # settings live on shared storage with the task's outputs, since a
    # remote worker has to read them.
    app.config["LOGS_DIR"] = os.path.join(hub_root, "logs")

    db.init_app(app)
    login_manager.init_app(app)

    with app.app_context():
        db.create_all()
        migrate(db)

    seed_settings(app)

    # Register Blueprints
    app.register_blueprint(auth.auth_bp)
    app.register_blueprint(processing.processing_bp, url_prefix="/processing")
    app.register_blueprint(visualization.visualization_bp, url_prefix='/visualization')
    app.register_blueprint(file_explorer.file_explorer_bp,
                           url_prefix='/file-explorer')
    app.register_blueprint(task_manager.task_manager_bp, url_prefix='/task-manager')
    app.register_blueprint(global_settings.settings_bp)
    app.register_blueprint(workflow_builder.workflow_bp,
                           url_prefix='/workflow-builder')
    app.register_blueprint(api_v1_bp, url_prefix='/api/v1')
    app.register_blueprint(hardware_control.hardware_control_bp,
                           url_prefix='/hardware-control')
    # Deliberately short: these URLs are stored per object in EcoTaxa's
    # object_link, which is only 255 characters wide.
    app.register_blueprint(deep_link.deep_link_bp, url_prefix='/v')

    # Register tools and viewers:
    with app.app_context():
        ensure_local_machine(app)

        registry = ToolRegistry(app)
        registry.ensure_builtin_provider()
        registry.scan_all()

        ViewerManager(
            app,
            os.path.join(hub_root, "viewers")
        ).load_viewers()

    # Create objects:
    if create_worker:
        # TaskManager:
        from .backend.machines import MachineMonitor
        from .backend.task_manager import TaskManager
        from .backend.task_watcher import TaskWatcher
        TaskManager(app)
        TaskWatcher(app)
        MachineMonitor(app)


    return app
