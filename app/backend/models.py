from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from flask_login import UserMixin


db = SQLAlchemy()
login_manager = LoginManager()
login_manager.login_view = 'auth.login'

class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(150), unique=True, nullable=False)
    password = db.Column(db.String(150), nullable=False)


class Workflow(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100))
    status = db.Column(db.String(20), default='Pending') 
    created_at = db.Column(db.DateTime, default=db.func.now())
    tasks = db.relationship('Task', backref='workflow', lazy=True, cascade="all, delete-orphan")


class Task(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    workflow_id = db.Column(db.Integer, db.ForeignKey('workflow.id'))
    tool_name = db.Column(db.String(50)) 
    priority = db.Column(db.Integer, default=1)
    weight = db.Column(db.Integer, default=1)
    status = db.Column(db.String(20), default='Pending')
    pid = db.Column(db.Integer, nullable=True)
    log_path = db.Column(db.String(255))
    settings_file_path = db.Column(db.String(255))



@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))
