from .models import db, Workflow, Task, Tool, GlobalSetting
from . import settings_engine
import json
import time
import subprocess
import os
import threading
from datetime import datetime

def create_new_workflow(workflow_name: str, task_specs: list[dict]):
    """Create a workflow from task specs.

    Each spec: {"tool": <name>, "settings": <object or text, optional>,
    "priority": <int, optional>, "weight": <int, optional>}.
    Settings are stored as text (JSON text for structured formats) and
    validated against the tool's schema when one exists.
    Raises KeyError for unknown/unavailable tools and ValueError for
    invalid settings.
    """
    new_workflow = Workflow()
    new_workflow.name = workflow_name

    db.session.add(new_workflow)
    db.session.flush() # This gets us the workflow.id before committing

    for i, spec in enumerate(task_specs):
        tool_name = spec.get("tool")
        tool = Tool.query.filter_by(name=tool_name).first()

        if tool is None:
            raise KeyError(f"Tool '{tool_name}' not registered.")
        if tool.available is False:
            raise KeyError(f"Tool '{tool_name}' is not available anymore.")

        settings = spec.get("settings")
        if settings is None:
            settings_text = settings_engine.default_settings(tool)
        elif isinstance(settings, str):
            settings_text = settings
        else:
            errors = settings_engine.validate(tool, settings)
            if errors:
                raise ValueError(
                    f"Invalid settings for tool '{tool_name}': " + "; ".join(errors))
            settings_text = json.dumps(settings, indent=2)

        task = Task()
        task.workflow_id = new_workflow.id
        task.tool_id = tool.id
        task.workflow_position = i
        task.priority = spec.get("priority", tool.default_priority)
        task.weight = spec.get("weight", tool.default_weight)
        task.settings = settings_text
        task.started_at = None
        task.completed_at = None

        db.session.add(task)

    db.session.commit()

    print(f"[*] Created workflow '{workflow_name}' with {len(task_specs)} tasks.")

    return new_workflow.id


def get_current_load():
    """Sums the weight of all steps currently marked as 'Running'."""
    running_steps = Task.query.filter_by(status='Running').all()
    return sum(step.weight for step in running_steps)


class TaskManager:
    """
    Manages the execution of tasks. Purley backend functionality, no routes connected.
    """

    def __init__(self, app) -> None:
        self.app = app


        worker_thread = threading.Thread(target=self.task_manager_worker, daemon=True)
        worker_thread.start()


    def task_manager_worker(self):
        with self.app.app_context():
            print("[*] Background Task Handler Started.")
            while True:
                try:
                    self._process_next_step()
                except Exception as e:
                    print(f"[!] Worker Error: {e}")

                sleep_time = GlobalSetting.get("POLL_INTERVAL", default=5)
                time.sleep(sleep_time)


    def _process_next_step(self):
        # scan database for pending tasks
        current_load = get_current_load()

        max_weight = GlobalSetting.get("MAX_WEIGHT", default=10)
        remaining_capacity = max_weight - current_load

        if remaining_capacity <= 0:
            return

       # Get a list of all pending workflows:
        pending_workflows = Workflow.query.filter_by(status='Pending').all()
        if len(pending_workflows) == 0:
            return

        # For each workflow, find the task with the lowest workflow_position which is not finished yet
        next_tasks = []
        for workflow in pending_workflows:
            tasks = Task.query.filter_by(workflow_id=workflow.id).order_by(Task.workflow_position.asc()).all()
            for task in tasks:
                if task.status == 'Pending':
                    next_tasks.append(task)
                    break

        if len(next_tasks) == 0:
            return

        # filter out tasks which have a higher weight than the remaining capacity
        next_tasks = [task for task in next_tasks if task.weight <= remaining_capacity]

        if len(next_tasks) == 0:
            return

        # sort tasks by priority and workflow creation date
        next_tasks.sort(key=lambda t: (t.priority, t.workflow.created_at))

        # dispatch the task with the highest priority
        next_task = next_tasks[0]
        self._dispatch_task(next_task)


    def _dispatch_task(self, task: Task):
        tool = Tool.query.filter_by(id=task.tool_id).first()
        if tool is None:
            raise KeyError(f"Could not find tool with id {task.tool_id} in database.")
        try:
            # create settings file:
            settings_dir = os.path.join(self.app.config["SETTINGS_DIR"], tool.name)
            os.makedirs(settings_dir, exist_ok=True)
            extension = settings_engine.settings_file_extension(tool)
            settings_file_path = os.path.join(settings_dir, task.id + extension)

            with open(settings_file_path, "w") as f:
                f.write(settings_engine.render_settings_file(tool, task.settings))

            res = self._run_tool(tool, task, settings_file_path)
            if res == None:
                return

            pid, log_prefix = res

            task.pid = pid
            task.log_prefix = log_prefix
            task.status = "Running"
            task.workflow.status = "Running"
            task.started_at = datetime.now()
            db.session.commit()
            print(f"[*] Dispatched {tool.name} (PID: {task.pid}, Weight: {task.weight})")

        except Exception as e:
            task.status = "Failed"
            db.session.commit()
            print(f"[!] Failed to dispatch {tool.name}: {e}")


    def _run_tool(self, tool: Tool, task: Task, settings_file_path: str):
        if tool.program_type == "python":
            execution_call = ["python3",  tool.program_path]
        else:
            execution_call = [tool.program_path]

        log_dir = os.path.join(self.app.config["LOGS_DIR"], tool.name)
        os.makedirs(log_dir, exist_ok=True)

        prefix = os.path.join(log_dir, task.id)
        log_path = prefix + ".log"
        error_path = prefix + ".err"

        try:
            with open(log_path, "w") as f, open(error_path, "w") as e:
                process = subprocess.Popen(
                    execution_call + [settings_file_path],
                    cwd=tool.working_directory,
                    stdout=f,
                    stderr=e,
                    start_new_session=True
                )
            print(f"[*] Started {tool.name} (PID: {process.pid})")
            return (process.pid, prefix)
        except Exception as e:
            print(f"[!] Failed to start {tool.name}: {e}")
            return None
        
