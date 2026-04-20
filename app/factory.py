from flask import Flask
from .routes import auth, processing, visualization, task_manager, global_settings
from .backend.models import db, login_manager
from .backend.global_settings import seed_settings
from .backend.tools import register_tools
from dotenv import load_dotenv
import os

def create_app(create_worker=True):
    """
    Creates the app
    """

    load_dotenv()
    app = Flask(__name__)
    
    app.config["SECRET_KEY"] = os.environ.get("FLASK_SECRET_KEY")
    if not app.config["SECRET_KEY"]:
        raise ValueError("No secret key set")
    app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL')

    db.init_app(app)
    login_manager.init_app(app)

    with app.app_context():
        db.create_all()

    seed_settings(app)

    # Register Blueprints 
    # from .modules.processing.routes import processing_bp
    # from .modules.visualization.routes import visualization_bp
    
    app.register_blueprint(auth.auth_bp)
    app.register_blueprint(processing.processing_bp, url_prefix="/processing")
    app.register_blueprint(visualization.visualization_bp, url_prefix='/visualization')
    app.register_blueprint(task_manager.task_manager_bp, url_prefix='/task-manager')
    app.register_blueprint(global_settings.settings_bp)
    # app.register_blueprint(processing_bp, url_prefix='/processing')


    # Register tools:
    register_tools(app)

    if create_worker:
        from .backend.task_manager import TaskManager
        from .backend.task_watcher import TaskWatcher
        TaskManager(app)
        TaskWatcher(app)


    return app
