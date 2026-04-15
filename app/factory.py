from flask import Flask
from .routes import auth, processing, visualization, task_manager
from .backend.models import db, login_manager
from dotenv import load_dotenv
import os

def create_app():
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

    # Register Blueprints 
    # from .modules.processing.routes import processing_bp
    # from .modules.visualization.routes import visualization_bp
    
    app.register_blueprint(auth.auth_bp)
    app.register_blueprint(processing.processing_bp, url_prefix="/processing")
    app.register_blueprint(visualization.visualization_bp, url_prefix='/visualization')
    app.register_blueprint(task_manager.task_manager_bp, url_prefix='/task-manager')
    # app.register_blueprint(processing_bp, url_prefix='/processing')
    return app
