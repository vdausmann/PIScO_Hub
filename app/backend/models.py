from dataclasses import dataclass
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
    workflow_id = db.Column(db.Integer, db.ForeignKey('workflow.id'), nullable=False)
    tool_id = db.Column(db.Integer, db.ForeignKey('tool.id'), nullable=False)
    priority = db.Column(db.Integer, default=1)
    weight = db.Column(db.Integer, default=1)
    status = db.Column(db.String(20), default='Pending')
    pid = db.Column(db.Integer, nullable=True)
    log_path = db.Column(db.String(255))
    settings_file_path = db.Column(db.String(255))

    started_at = db.Column(db.DateTime, nullable=True)
    completed_at = db.Column(db.DateTime, nullable=True)

    tool = db.relationship("Tool", backref="tasks")


class GlobalSetting(db.Model):
    key = db.Column(db.String(50), primary_key=True)
    value = db.Column(db.String(255))
    setting_type = db.Column(db.String(20), default="str")
    description = db.Column(db.Text)

    @classmethod
    def get(cls, key, default):
        setting = cls.query.get(key)
        if not setting:
            return default
        
        # Automatic type casting
        if setting.setting_type == 'int':
            return int(setting.value)
        if setting.setting_type == 'bool':
            return setting.value.lower() in ['true', '1', 'yes']
        return setting.value


class Tool(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), unique=True, nullable=False)
    default_priority = db.Column(db.Integer, default=1)
    default_weight = db.Column(db.Integer, default=1)
    program_path = db.Column(db.String(100), nullable=False)
    program_type = db.Column(db.String(50), nullable=False) # python, binary
    settings_template_path = db.Column(db.String(255))
    description = db.Column(db.Text) # Use Text for longer scientific docs
    



@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


