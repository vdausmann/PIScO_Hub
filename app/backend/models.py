from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from flask_login import UserMixin
import uuid


db = SQLAlchemy()
login_manager = LoginManager()
login_manager.login_view = 'auth.login'


class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(150), unique=True, nullable=False)
    password = db.Column(db.String(150), nullable=False)

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


class Workflow(db.Model):
    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name = db.Column(db.String(100))
    status = db.Column(db.String(20), default='Pending') 
    created_at = db.Column(db.DateTime, default=db.func.now())
    tasks = db.relationship('Task', backref='workflow', lazy=True, cascade="all, delete-orphan")
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)


class Task(db.Model):
    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    workflow_id = db.Column(db.Integer, db.ForeignKey('workflow.id'), nullable=False)
    workflow_position = db.Column(db.Integer, nullable=False)
    tool_id = db.Column(db.Integer, db.ForeignKey('tool.id'), nullable=False)
    priority = db.Column(db.Integer, default=1)
    weight = db.Column(db.Integer, default=1)
    status = db.Column(db.String(20), default='Pending')
    pid = db.Column(db.Integer, nullable=True)
    log_prefix = db.Column(db.String(255))
    settings = db.Column(db.Text, default="")
    exit_code = db.Column(db.Integer, nullable=True)

    started_at = db.Column(db.DateTime, nullable=True)
    completed_at = db.Column(db.DateTime, nullable=True)

    tool = db.relationship("Tool", backref="tasks")


class Artifact(db.Model):
    """A path a task declared as an output, and whether it appeared."""
    id = db.Column(db.Integer, primary_key=True)
    task_id = db.Column(db.String(36), db.ForeignKey('task.id'), nullable=False)
    name = db.Column(db.String(100), nullable=False)
    kind = db.Column(db.String(20), default='directory')
    path = db.Column(db.String(512), nullable=False)
    exists = db.Column(db.Boolean, nullable=True)  # None until the task finishes
    size_bytes = db.Column(db.Integer, nullable=True)
    created_at = db.Column(db.DateTime, default=db.func.now())

    task = db.relationship('Task', backref=db.backref(
        'artifacts', cascade="all, delete-orphan"))


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


class ToolProvider(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
    path = db.Column(db.String(255), unique=True, nullable=False)
    description = db.Column(db.Text)
    version = db.Column(db.String(50))
    # builtin providers use the legacy tools/<name>/config.json layout
    builtin = db.Column(db.Boolean, default=False)
    last_scanned_at = db.Column(db.DateTime, nullable=True)
    tools = db.relationship('Tool', backref='provider', lazy=True)


class Tool(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), unique=True, nullable=False)
    default_priority = db.Column(db.Integer, default=1)
    default_weight = db.Column(db.Integer, default=1)
    program_path = db.Column(db.String(256), nullable=False)
    working_directory = db.Column(db.String(256), nullable=False)
    program_type = db.Column(db.String(50), nullable=False) # python, binary
    settings_template = db.Column(db.Text, default="")
    failed_ok = db.Column(db.Boolean, default=False)
    description = db.Column(db.Text) # Use Text for longer scientific docs

    provider_id = db.Column(db.Integer, db.ForeignKey('tool_provider.id'), nullable=True)
    settings_format = db.Column(db.String(20), default="opaque")  # opaque, json, ini
    settings_schema = db.Column(db.Text, nullable=True)  # JSON Schema (JSON text)
    inputs = db.Column(db.Text, nullable=True)   # JSON list
    outputs = db.Column(db.Text, nullable=True)  # JSON list
    tags = db.Column(db.Text, nullable=True)     # JSON list of strings
    version = db.Column(db.String(50), nullable=True)
    available = db.Column(db.Boolean, default=True)
    config_hash = db.Column(db.String(64), nullable=True)


class ApiToken(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    token_hash = db.Column(db.String(64), unique=True, nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=db.func.now())
    last_used_at = db.Column(db.DateTime, nullable=True)
    user = db.relationship('User')



class TemporaryWorkflow(db.Model):
    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name = db.Column(db.String(100))
    created_at = db.Column(db.DateTime, default=db.func.now())
    tasks = db.relationship('TemporaryTask', backref='workflow',
                            cascade="all, delete-orphan",
                            order_by="TemporaryTask.workflow_position")
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)


class TemporaryTask(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    workflow_id = db.Column(db.String(36), db.ForeignKey('temporary_workflow.id'), nullable=False)
    tool_id = db.Column(db.Integer, db.ForeignKey('tool.id'), nullable=False)
    workflow_position = db.Column(db.Integer, nullable=False)

    settings = db.Column(db.Text, default="")
    priority = db.Column(db.Integer, default=1)
    weight = db.Column(db.Integer, default=1)
    tool = db.relationship('Tool')

class TemplateWorkflow(db.Model):
    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name = db.Column(db.String(100))
    created_at = db.Column(db.DateTime, default=db.func.now())
    tasks = db.relationship('TemplateTask', backref='workflow',
                            cascade="all, delete-orphan",
                            order_by="TemplateTask.workflow_position")
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)


class TemplateTask(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    workflow_id = db.Column(db.String(36), db.ForeignKey('template_workflow.id'), nullable=False)
    tool_id = db.Column(db.Integer, db.ForeignKey('tool.id'), nullable=False)
    workflow_position = db.Column(db.Integer, nullable=False)

    settings = db.Column(db.Text, default="")
    priority = db.Column(db.Integer, default=1)
    weight = db.Column(db.Integer, default=1)
    tool = db.relationship('Tool')




class Viewer(db.Model):

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name = db.Column(db.String(100), nullable=False, unique=True)
    description = db.Column(db.Text)
    extensions = db.Column(db.Text)
    path = db.Column(db.String(255), nullable=False, unique=True)
    type = db.Column(db.String(50), nullable=False)
    url = db.Column(db.String(255)) 
