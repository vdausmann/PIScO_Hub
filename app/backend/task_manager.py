from .models import db, Workflow, Task, Tool, GlobalSetting
import time
import subprocess
import os
import uuid
import threading
from datetime import datetime


def create_new_workflow(workflow_name: str, tools: list[str], settings:
                        list[str]):
    # Load workflow config and create tasks from tools
    # 1. Create workflow in db
    # 2. Create a task in the db for each tool
    new_workflow = Workflow()
    new_workflow.name = workflow_name

    db.session.add(new_workflow)
    db.session.flush() # This gets us the task.id before committing

    for i, tool_name in enumerate(tools):
        tool = Tool.query.filter_by(name=tool_name).first()

        if tool is None:
            raise KeyError(f"Tool '{tool_name}' not registered.")


        task = Task()
        task.workflow_id = new_workflow.id
        task.tool_id = tool.id
        task.priority = tool.default_priority
        task.weight = tool.default_weight
        task.settings_file_path = settings[i]
        task.started_at = None
        task.completed_at = None

        db.session.add(task)

    db.session.commit()

    print(f"[*] Created workflow '{workflow_name}' with {len(tools)} tasks.")

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
                
                sleep_time = GlobalSetting.get("WORKER_SLEEP_TIME", default=5)
                time.sleep(sleep_time)


    def _process_next_step(self):
        # scan database for pending tasks
        current_load = get_current_load()

        max_weight = GlobalSetting.get("MAX_WEIGHT", default=10)
        remaining_capacity = max_weight - current_load

        if remaining_capacity <= 0:
            return

        # find task with the highest priority that can be dispatched. If multiple tasks 
        # have the same priority, choose the one with the oldest creation date.
        next_task = Task.query.join(Workflow).filter(
                Task.status == 'Pending',
                Task.weight <= remaining_capacity
            ).order_by(
                    Task.priority.desc(), Workflow.created_at.asc()
            ).first()


        if not next_task or not isinstance(next_task, Task):
            return

        self._dispatch_task(next_task)


    def _dispatch_task(self, task: Task):
        tool = Tool.query.filter_by(id=task.tool_id).first()
        if tool is None:
            raise KeyError(f"Could not find tool with id {task.tool_id} in database.")
        try:
            res = self._run_tool(tool, task.settings_file_path)
            if res == None:
                return

            pid, log_path = res

            task.pid = pid
            task.log_path = log_path
            task.status = "Running"
            task.workflow.status = "Running"
            task.started_at = datetime.now()
            db.session.commit()
            print(f"[*] Dispatched {tool.name} (PID: {task.pid}, Weight: {task.weight})")

        except Exception as e:
            task.status = "Failed"
            db.session.commit()
            print(f"[!] Failed to dispatch {tool.name}: {e}")


    def _run_tool(self, tool: Tool, settings_file_path: str):
        if tool.program_type == "python":
            execution_call = ["python3",  tool.program_path]
        else:
            execution_call = [tool.program_path]


        log_dir = os.path.join("logs", tool.name)
        os.makedirs(log_dir, exist_ok=True)

        # log_name = str(uuid.uuid4()) + ".log"
        log_name = "test.log"
        log_path = os.path.join(log_dir, log_name)

        try:
            with open(log_path, "w") as f:
                process = subprocess.Popen(
                    execution_call + [settings_file_path],
                    stdout=f,
                    stderr=f,
                    start_new_session=True
                )
            print(f"[*] Started {tool.name} (PID: {process.pid})")
            return (process.pid, log_path)
        except Exception as e:
            print(f"[!] Failed to start {tool.name}: {e}")
            return None
        
