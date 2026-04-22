import os
from threading import Thread
import time
from datetime import datetime
from flask_sock import Sock
from .models import db, Task, Workflow, GlobalSetting, Tool



class TaskWatcher:

    def __init__(self, app):
        self.app = app
        self.clients = set()

        worker_thread = Thread(target=self.task_watcher_worker, daemon=True)
        worker_thread.start()


    def task_watcher_worker(self):
        """
        Scans for 'Running' tasks and updates their status 
        based on the state of their OS process.
        """
        with self.app.app_context():
            while True:
                running_tasks = Task.query.filter_by(status='Running').all()
                
                for task in running_tasks:
                    if task.pid:
                        self.check_task_status(task)
                
                sleep_time = GlobalSetting.get("WORKER_SLEEP_TIME", default=5)
                time.sleep(sleep_time)

    def check_task_status(self, task):
        """Checks a specific PID and updates the DB if the process ended."""
        try:
            pid, status = os.waitpid(task.pid, os.WNOHANG)
            
            if pid != 0:
                exit_code = os.waitstatus_to_exitcode(status)
                self.finalize_task(task, exit_code)
                
        except ChildProcessError:
            if not os.path.exists(f"/proc/{task.pid}"):
                self.finalize_task(task, -1) 
        except Exception as e:
            print(f"[!] Watcher error on Task {task.id}: {e}")

    def finalize_task(self, task, exit_code):
        """Updates the task and checks if the workflow is complete."""
        task.status = 'Finished' if exit_code == 0 else 'Failed'
        task.exit_code = exit_code
        task.completed_at = datetime.now()
        task.pid = None
        
        print(f"[*] Task {task.id} finished (Exit: {exit_code})")
        
        # Check if this was the last task in the workflow
        all_tasks = Task.query.filter_by(workflow_id=task.workflow_id).all()

        workflow = Workflow.query.get(task.workflow_id)
        if workflow is None:
            raise KeyError(f"Could not find workflow with id {task.workflow_id} in database.")

        if all(t.status in ['Finished', 'Failed'] for t in all_tasks):
            # check if a task failed:
            if any(t.status == 'Failed' for t in all_tasks):
                workflow.status = 'Failed'
                print(f"[!] Workflow '{workflow.name}' failed.")
            else:
                workflow.status = 'Finished'
                print(f"[!] Workflow '{workflow.name}' fully processed.")
        else:
            tool = Tool.query.filter_by(id=task.tool_id).first()
            if not isinstance(tool, Tool):
                raise KeyError(f"Could not find tool with id {task.tool_id} in database.")
            if task.status == 'Failed' and not tool.failed_ok:
                workflow.status = 'Failed'
            else:
                workflow.status = 'Pending'

        db.session.commit()


    def serialize_workflows(self):
        workflows = Workflow.query.options(
            db.joinedload(Workflow.tasks)
        ).order_by(Workflow.created_at.desc()).all()

        return [
            {
                "id": wf.id,
                "name": wf.name,
                "status": wf.status,
                "created_at": wf.created_at.isoformat() if wf.created_at else None,
                "tasks": [
                    {
                        "id": t.id,
                        "tool_id": t.tool_id,
                        "status": t.status,
                        "priority": t.priority,
                        "weight": t.weight,
                        "pid": t.pid,
                        "exit_code": t.exit_code,
                    }
                    for t in wf.tasks
                ]
            }
            for wf in workflows
        ]

