from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from flask_login import UserMixin
import uuid
from datetime import datetime


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
    created_at = db.Column(db.DateTime, default=datetime.now)
    tasks = db.relationship('Task', backref='workflow', lazy=True, cascade="all, delete-orphan")
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    # Workflow-level values (a profile's metadata, shared paths) that task
    # settings pull in through $param / $format references. JSON object.
    params = db.Column(db.Text, default="{}")
    # Which template this was instantiated from, if any; plain text so a
    # deleted template leaves the history readable.
    template_id = db.Column(db.String(36), nullable=True)
    template_name = db.Column(db.String(100), nullable=True)


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
    # When the Hub first signalled this task to stop, so it is signalled
    # once rather than on every poll while it winds down.
    terminated_at = db.Column(db.DateTime, nullable=True)
    # Set by a cancel: a pending task never starts, a running one is
    # stopped by the watcher and ends up Cancelled rather than Failed.
    cancelled_at = db.Column(db.DateTime, nullable=True)
    # Null while pending means the scheduler still has to choose one.
    machine_id = db.Column(db.Integer, db.ForeignKey('machine.id'), nullable=True)

    tool = db.relationship("Tool", backref="tasks")
    machine = db.relationship("Machine")


class Machine(db.Model):
    """A machine tasks can run on. The local machine has no host and is
    executed directly; others are reached over SSH."""
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
    host = db.Column(db.String(255), nullable=True)
    ssh_user = db.Column(db.String(100), nullable=True)
    max_weight = db.Column(db.Integer, default=10)
    tags = db.Column(db.Text, default="[]")  # JSON list, matched against Tool.requires
    enabled = db.Column(db.Boolean, default=True)
    status = db.Column(db.String(20), default='unknown')  # online, offline, unknown
    last_seen_at = db.Column(db.DateTime, nullable=True)
    # Why the last probe failed, so "offline" says what to fix
    last_probe_error = db.Column(db.Text, nullable=True)
    note = db.Column(db.Text, nullable=True)
    is_local = db.Column(db.Boolean, default=False)


class Artifact(db.Model):
    """A path a task declared as an output, and whether it appeared."""
    id = db.Column(db.Integer, primary_key=True)
    task_id = db.Column(db.String(36), db.ForeignKey('task.id'), nullable=False)
    name = db.Column(db.String(100), nullable=False)
    kind = db.Column(db.String(20), default='directory')
    path = db.Column(db.String(512), nullable=False)
    exists = db.Column(db.Boolean, nullable=True)  # None until the task finishes
    size_bytes = db.Column(db.Integer, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.now)

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
    requires = db.Column(db.Text, nullable=True)  # JSON list of machine tags
    # Seconds a task may run before it is stopped; null falls back to the
    # TASK_TIMEOUT setting, and 0 there means no limit.
    timeout_seconds = db.Column(db.Integer, nullable=True)


class ApiToken(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    token_hash = db.Column(db.String(64), unique=True, nullable=False)
    # read < operate < full; tokens predating scopes read as full
    scope = db.Column(db.String(20), default='full')
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.now)
    last_used_at = db.Column(db.DateTime, nullable=True)
    user = db.relationship('User')



class TemporaryWorkflow(db.Model):
    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name = db.Column(db.String(100))
    created_at = db.Column(db.DateTime, default=datetime.now)
    tasks = db.relationship('TemporaryTask', backref='workflow',
                            cascade="all, delete-orphan",
                            order_by="TemporaryTask.workflow_position")
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    # Same as on Workflow: the draft carries what the run will carry.
    params = db.Column(db.Text, default="{}")
    template_id = db.Column(db.String(36), nullable=True)
    template_name = db.Column(db.String(100), nullable=True)


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
    """A reusable workflow: its steps plus the parameters a run needs.

    Either saved by a user on this Hub, or shipped in a toolbox manifest
    (then provider_id is set and the row is rewritten on every scan).
    """
    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name = db.Column(db.String(100))
    description = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.now)
    tasks = db.relationship('TemplateTask', backref='workflow',
                            cascade="all, delete-orphan",
                            order_by="TemplateTask.workflow_position")
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    # JSON Schema for the params a workflow made from this template takes.
    params_schema = db.Column(db.Text, nullable=True)
    # Default workflow name, formatted with the params: "{cruise}_{station}".
    name_format = db.Column(db.String(200), nullable=True)
    provider_id = db.Column(db.Integer, db.ForeignKey('tool_provider.id'), nullable=True)
    provider = db.relationship('ToolProvider')
    manifest_hash = db.Column(db.String(64), nullable=True)


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
