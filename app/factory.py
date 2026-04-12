from flask import Flask
from .models import db, login_manager
from .modules.processing.task_manager import TaskManager
from dotenv import load_dotenv
import os

def create_app(run_worker=False):
    load_dotenv()
    app = Flask(__name__)
    
    app.config["SECRET_KEY"] = os.environ.get("FLASK_SECRET_KEY")
    if not app.config["SECRET_KEY"]:
        raise ValueError("No secret key set")
    app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL')

    db.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = 'auth.login'

    # Register Blueprints 
    from .auth.routes import auth_bp
    from .modules.processing.routes import processing_bp
    from .modules.visualization.routes import visualization_bp
    
    app.register_blueprint(auth_bp)
    app.register_blueprint(processing_bp, url_prefix='/processing')
    app.register_blueprint(visualization_bp, url_prefix='/visualization')


    @login_manager.user_loader
    def load_user(user_id):
        from .models import User
        return User.query.get(int(user_id))


    if run_worker:
        # Start the background worker thread to handle task management
        task_manager = TaskManager(app)

    return app
