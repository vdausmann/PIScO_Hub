import time
import subprocess
import os
import signal
import threading
from datetime import datetime
from ...models import db, Task, Workflow



class TaskManager:
    MAX_WEIGHT = 10

    def __init__(self, app) -> None:
        self.app = app


        worker_thread = threading.Thread(target=self.start_worker, daemon=True)
        worker_thread.start()


    def get_current_load(self):
        """Sums the weight of all steps currently marked as 'Running'."""
        running_steps = Task.query.filter_by(status='Running').all()
        return sum(step.weight for step in running_steps)


    def start_worker(self):
        """Initializes the background loop within the app context."""
        with self.app.app_context():
            print("[*] Background Task Handler Started.")
            while True:
                try:
                    self.process_next_step()
                except Exception as e:
                    print(f"[!] Worker Error: {e}")
                
                time.sleep(2)

    def process_next_step(self):
        current_load = self.get_current_load()
        remaining_capacity = self.MAX_WEIGHT - current_load

        if remaining_capacity <= 0:
            return

        next_task = Task.query.join(Workflow).filter(
                Task.status == 'Pending',
                Task.weight <= remaining_capacity
            ).order_by(
                    Task.priority.desc(), Workflow.created_at.asc()
            ).first()


        if not next_task or not isinstance(next_task, Task):
            return

        self.dispatch_task(next_task)


    def dispatch_task(self, task: Task):
        executable = task.module_name
        try:
            proc = subprocess.Popen(
                [f"./{executable}"],
                cwd="",
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True
            )

            task.pid = proc.pid
            task.status = "Running"
            task.workflow.status = "Running"
            db.session.commit()

            print(f"[*] Dispatched {task.module_name} (PID: {task.pid}, Weight: {task.weight})")

        except Exception as e:
            task.status = "Failed"
            db.session.commit()
            print(f"[!] Failed to dispatch {task.module_name}: {e}")

