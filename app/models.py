from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from flask_login import UserMixin


db = SQLAlchemy()
login_manager = LoginManager()

class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(150), unique=True, nullable=False)
    password = db.Column(db.String(150), nullable=False)


class Workflow(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100))
    status = db.Column(db.String(20), default='Pending') 
    created_at = db.Column(db.DateTime, default=db.func.now())
    steps = db.relationship('Task', backref='workflow', lazy=True, cascade="all, delete-orphan")


class Task(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    workflow_id = db.Column(db.Integer, db.ForeignKey('workflow.id'))
    module_name = db.Column(db.String(50)) # e.g., 'c_analyzer'
    priority = db.Column(db.Integer, default=1)
    weight = db.Column(db.Integer, default=1)
    status = db.Column(db.String(20), default='Pending')
    pid = db.Column(db.Integer, nullable=True) # To stop the process
    config_snapshot = db.Column(db.Text) # The text config used at start
    log_path = db.Column(db.String(255))
