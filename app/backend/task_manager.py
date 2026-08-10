from .models import db, Workflow, Task, Tool, GlobalSetting, Machine
from . import artifacts, machines, settings_engine
import json
import shlex
import time
import subprocess
import os
import threading
from datetime import datetime


def _validate_refs(tool, settings_obj, position, tools_so_far):
    """Check every $from reference against the workflow built so far.

    Workflows run in order, so a reference may only point backwards; a
    forward reference could never be resolved. The referenced tool must
    also declare that output *with a settings_key*, since only bound
    outputs get a recorded path.
    """
    for path, ref in settings_engine.extract_from_refs(settings_obj):
        if not isinstance(ref, dict) or "output" not in ref or "task" not in ref:
            raise ValueError(
                f"'{path}': $from needs 'task' and 'output' keys")

        target = ref["task"]
        if target == "prev":
            target = position - 1
        if not isinstance(target, int):
            raise ValueError(
                f"'{path}': $from task must be a position or \"prev\"")
        if target < 0:
            raise ValueError(
                f"'{path}': there is no task before position {position}")
        if target >= position:
            raise ValueError(
                f"'{path}': $from must reference an earlier task "
                f"(referenced {target} from position {position})")

        upstream_tool = tools_so_far[target]
        declaration = artifacts.find_declaration(
            upstream_tool, "outputs", ref["output"])
        if declaration is None:
            available = [d.get("name") for d
                         in artifacts.declarations(upstream_tool, "outputs")]
            raise ValueError(
                f"'{path}': tool '{upstream_tool.name}' at position {target} "
                f"declares no output '{ref['output']}' (has: "
                f"{', '.join(available) or 'none'})")
        if not declaration.get("settings_key"):
            raise ValueError(
                f"'{path}': output '{ref['output']}' of '{upstream_tool.name}' "
                "has no settings_key, so its location is not tracked")


def create_new_workflow(workflow_name: str, task_specs: list[dict]):
    """Create a workflow from task specs.

    Each spec: {"tool": <name>, "settings": <object or text, optional>,
    "priority": <int, optional>, "weight": <int, optional>}.
    Settings are stored as text (JSON text for structured formats) and
    validated against the tool's schema when one exists. Settings values
    may be {"$from": {"task": <position>|"prev", "output": <name>}} to
    consume an earlier task's output; those are resolved at dispatch.
    Raises KeyError for unknown/unavailable tools and ValueError for
    invalid settings.
    """
    new_workflow = Workflow()
    new_workflow.name = workflow_name

    db.session.add(new_workflow)
    db.session.flush() # This gets us the workflow.id before committing

    tools_so_far = []
    for i, spec in enumerate(task_specs):
        tool_name = spec.get("tool")
        tool = Tool.query.filter_by(name=tool_name).first()

        if tool is None:
            raise KeyError(f"Tool '{tool_name}' not registered.")
        if tool.available is False:
            raise KeyError(f"Tool '{tool_name}' is not available anymore.")
        tools_so_far.append(tool)

        settings = spec.get("settings")
        if settings is None:
            settings_text = settings_engine.default_settings(tool)
        elif isinstance(settings, str):
            settings_text = settings
        else:
            _validate_refs(tool, settings, i, tools_so_far)

            errors = settings_engine.validate(
                tool, artifacts.validation_candidate(tool, settings))
            if errors:
                raise ValueError(
                    f"Invalid settings for tool '{tool_name}': " + "; ".join(errors))
            settings_text = json.dumps(settings, indent=2)

        machine_name = spec.get("machine")
        machine = None
        if machine_name:
            machine = Machine.query.filter_by(name=machine_name).first()
            if machine is None:
                raise KeyError(f"Machine '{machine_name}' is not registered.")
            if not machine.enabled:
                raise KeyError(f"Machine '{machine_name}' is disabled.")

        task = Task()
        task.workflow_id = new_workflow.id
        task.tool_id = tool.id
        task.workflow_position = i
        task.priority = spec.get("priority", tool.default_priority)
        task.weight = spec.get("weight", tool.default_weight)
        task.machine_id = machine.id if machine else None
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


    def _candidate_machines(self, task):
        """Machines that could run this task right now.

        A task pinned to a machine waits for that one; otherwise any
        enabled, reachable machine whose tags cover the tool's
        requirements and that still has capacity will do.
        """
        if task.machine_id is not None:
            candidates = [Machine.query.get(task.machine_id)]
            candidates = [m for m in candidates if m is not None and m.enabled]
        else:
            required = machines.tool_requirements(task.tool)
            candidates = [
                m for m in Machine.query.filter_by(enabled=True).all()
                if required <= machines.machine_tags(m)
            ]

        return [
            m for m in candidates
            if (m.is_local or m.status == "online")
            and task.weight <= (m.max_weight or 0) - machines.machine_load(m.id)
        ]


    def _process_next_step(self):
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

        # sort tasks by priority and workflow creation date
        next_tasks.sort(key=lambda t: (t.priority, t.workflow.created_at))

        # Dispatch the most important task that some machine can take. A
        # task nobody can host yet simply waits for the next cycle.
        for task in next_tasks:
            candidates = self._candidate_machines(task)
            if not candidates:
                continue
            machine = min(candidates, key=lambda m: machines.machine_load(m.id))
            task.machine_id = machine.id
            self._dispatch_task(task, machine)
            return


    def _resolve_settings(self, tool, task):
        """Fill in the paths the Hub owns, just before the tool runs.

        Two kinds: references to an earlier task's output, and declared
        outputs the user left blank, which get an allocated location.
        The task's stored settings keep the user's intent untouched, so
        re-running resolves against the state at that time.
        """
        if (tool.settings_format or "opaque") == "opaque":
            return task.settings

        obj = settings_engine.parse_settings(tool, task.settings)
        if not isinstance(obj, dict):
            return task.settings

        for path, ref in settings_engine.extract_from_refs(obj):
            artifacts.set_setting(obj, path, self._resolve_ref(task, path, ref))

        for output in artifacts.declarations(tool, "outputs"):
            key = output.get("settings_key")
            if not key:
                continue
            location = artifacts.get_setting(obj, key)
            if not location:
                location = artifacts.allocated_output_path(self.app, task, output)
                artifacts.set_setting(obj, key, location)

            parent = location if output.get("kind", "directory") == "directory" \
                else os.path.dirname(location)
            if parent:
                os.makedirs(parent, exist_ok=True)
            artifacts.record_artifact(task, output, location)

        return json.dumps(obj)


    def _resolve_ref(self, task, path, ref):
        """Turn a $from reference into the upstream task's actual path."""
        position = task.workflow_position - 1 if ref["task"] == "prev" else ref["task"]
        upstream = Task.query.filter_by(workflow_id=task.workflow_id,
                                        workflow_position=position).first()
        if upstream is None:
            raise ValueError(f"'{path}': no task at position {position}")
        if upstream.status != "Finished":
            raise ValueError(
                f"'{path}': task at position {position} is {upstream.status}, "
                "so its output cannot be used")

        artifact = next((a for a in upstream.artifacts
                         if a.name == ref["output"]), None)
        if artifact is None:
            # Tasks dispatched before outputs were tracked have no rows;
            # the allocation is deterministic, so recompute it.
            declaration = artifacts.find_declaration(
                upstream.tool, "outputs", ref["output"])
            if declaration is None:
                raise ValueError(
                    f"'{path}': '{upstream.tool.name}' declares no output "
                    f"'{ref['output']}'")
            print(f"[!] Task {task.id}: no recorded artifact for "
                  f"'{ref['output']}'; recomputing its location.")
            return artifacts.allocated_output_path(self.app, upstream, declaration)

        if artifact.exists is False:
            raise ValueError(
                f"'{path}': upstream output '{ref['output']}' was never "
                f"produced at {artifact.path}")
        return artifact.path


    def _dispatch_task(self, task: Task, machine=None):
        tool = Tool.query.filter_by(id=task.tool_id).first()
        if tool is None:
            raise KeyError(f"Could not find tool with id {task.tool_id} in database.")
        try:
            # The settings file goes in the task's workspace on shared
            # storage, not beside the Hub's own files: a task running on
            # another machine is handed this path verbatim and has to be
            # able to read it.
            workspace = artifacts.task_workspace(self.app, task)
            os.makedirs(workspace, exist_ok=True)
            extension = settings_engine.settings_file_extension(tool)
            settings_file_path = os.path.join(workspace, "settings" + extension)

            resolved_settings = self._resolve_settings(tool, task)

            with open(settings_file_path, "w") as f:
                f.write(settings_engine.render_settings_file(tool, resolved_settings))

            res = self._run_tool(tool, task, settings_file_path, machine)
            if res == None:
                raise RuntimeError("could not start the program")

            pid, log_prefix = res

            task.pid = pid
            task.log_prefix = log_prefix
            task.status = "Running"
            task.workflow.status = "Running"
            task.started_at = datetime.now()
            db.session.commit()
            where = machine.name if machine else "local"
            print(f"[*] Dispatched {tool.name} on {where} "
                  f"(PID: {task.pid}, Weight: {task.weight})")

        except Exception as e:
            # A task that never started must stop its workflow the same way
            # a failed one does, or the next task would run against results
            # that were never produced.
            db.session.rollback()
            task.status = "Failed"
            task.completed_at = datetime.now()
            if not tool.failed_ok:
                task.workflow.status = "Failed"
            else:
                task.workflow.status = "Pending"
            db.session.commit()
            print(f"[!] Failed to dispatch {tool.name}: {e}")


    def _run_tool(self, tool: Tool, task: Task, settings_file_path: str,
                  machine=None):
        if tool.program_type == "python":
            execution_call = ["python3",  tool.program_path]
        else:
            execution_call = [tool.program_path]

        log_dir = os.path.join(self.app.config["LOGS_DIR"], tool.name)
        os.makedirs(log_dir, exist_ok=True)

        prefix = os.path.join(log_dir, task.id)
        log_path = prefix + ".log"
        error_path = prefix + ".err"

        remote = machine is not None and not machine.is_local
        if remote:
            # ssh is the child process we track: it forwards the remote
            # program's output to these same log files and exits with its
            # exit code, so nothing downstream needs to know the
            # difference. Paths are valid remotely because storage is
            # shared. Note that killing this ssh would leave the remote
            # program running; the Hub has no cancel feature today, and a
            # worker daemon is the proper fix when it does.
            remote_command = "cd {} && exec {}".format(
                shlex.quote(tool.working_directory),
                " ".join(shlex.quote(part) for part
                         in execution_call + [settings_file_path]))
            popen_args = machines.ssh_command(machine, remote_command)
            popen_kwargs = {}
        else:
            popen_args = execution_call + [settings_file_path]
            popen_kwargs = {"cwd": tool.working_directory}

        try:
            with open(log_path, "w") as f, open(error_path, "w") as e:
                process = subprocess.Popen(
                    popen_args,
                    stdout=f,
                    stderr=e,
                    start_new_session=True,
                    **popen_kwargs
                )
            print(f"[*] Started {tool.name} (PID: {process.pid})")
            return (process.pid, prefix)
        except Exception as e:
            print(f"[!] Failed to start {tool.name}: {e}")
            return None
        
