import os
import signal
from threading import Thread
import time
from datetime import datetime
from flask_sock import Sock
from . import artifacts, process_registry
from .models import db, Task, Workflow, GlobalSetting, Tool

# How long a terminated task gets to exit before it is killed outright.
TERMINATE_GRACE_SECONDS = 30



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
                        self.enforce_timeout(task)
                        self.check_task_status(task)

                sleep_time = GlobalSetting.get("POLL_INTERVAL", default=5)
                time.sleep(sleep_time)

    def timeout_for(self, task):
        """Seconds this task may run, or 0 for no limit."""
        if task.tool is not None and task.tool.timeout_seconds:
            return task.tool.timeout_seconds
        return GlobalSetting.get("TASK_TIMEOUT", default=0)

    def enforce_timeout(self, task):
        """Stop a task that has outstayed its limit.

        A tool that hangs holds its machine's capacity indefinitely, and
        without this the only way out is finding the process by hand. The
        kill makes the task fail like any other, so the workflow stops and
        the reason is in the task's own error log.
        """
        limit = self.timeout_for(task)
        if not limit or not task.started_at:
            return

        now = datetime.now()
        elapsed = (now - task.started_at).total_seconds()
        if elapsed < limit:
            return

        if task.terminated_at is None:
            sig, name = signal.SIGTERM, "SIGTERM"
        elif (now - task.terminated_at).total_seconds() > TERMINATE_GRACE_SECONDS:
            # It ignored the polite request; stop asking.
            sig, name = signal.SIGKILL, "SIGKILL"
        else:
            return  # already signalled, still winding down

        note = (f"[hub] Exceeded its {limit}s limit after {int(elapsed)}s; "
                f"sending {name}.")
        if task.machine is not None and not task.machine.is_local:
            note += (f" Running on {task.machine.name}: only the local ssh"
                     " process can be signalled, so the remote program may"
                     " keep going and need killing there.")

        self._append_error_note(task, note)
        print(f"[!] Task {task.id}: {note}")

        try:
            os.killpg(os.getpgid(task.pid), sig)
        except (ProcessLookupError, PermissionError, OSError) as e:
            print(f"[!] Could not signal task {task.id}: {e}")

        task.terminated_at = now
        db.session.commit()

    def _append_error_note(self, task, note):
        """Put the reason where whoever reads the logs will find it."""
        if not task.log_prefix:
            return
        try:
            with open(task.log_prefix + ".err", "a") as f:
                f.write(note + "\n")
        except OSError as e:
            print(f"[!] Could not annotate log for task {task.id}: {e}")

    def check_task_status(self, task):
        """Checks a specific PID and updates the DB if the process ended."""
        process = process_registry.get(task.pid)
        if process is not None:
            # We started it, so poll the handle. This reports the true exit
            # status and cannot lose the child to subprocess's cleanup.
            exit_code = process.poll()
            if exit_code is not None:
                self.finalize_task(task, exit_code)
            return

        # No handle: the task belongs to an earlier Hub process, since a
        # restart leaves tasks marked Running in the database. It is not our
        # child, so it cannot be reaped -- all we can do is see whether it is
        # still there, and report that we do not know how it ended.
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
        process_registry.forget(task.pid)
        task.pid = None

        if task.status == 'Finished':
            artifacts.finalize_artifacts(task)

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

