import time
import subprocess
import os
import signal
import json
import threading
from datetime import datetime
from ...models import db, Task, Workflow
from .tools import Tool, Tools
from flask import Blueprint, render_template, request, redirect, url_for, jsonify
from flask_login import login_required

task_manager_bp = Blueprint('task_manager', __name__)



class TaskManager:
    MAX_WEIGHT = 10

    def __init__(self, app) -> None:
        self.app = app

        self.tools = Tools()
        self.tools.register_tools()


        worker_thread = threading.Thread(target=self.start_worker, daemon=True)
        worker_thread.start()


    def create_new_workflow(self, workflow_name: str, tools: list[str], settings:
                            list[str]):
        # Load workflow config and create tasks from tools
        # 1. Create workflow in db
        # 2. Create a task in the db for each tool
        new_workflow = Workflow()
        new_workflow.name = workflow_name

        db.session.add(new_workflow)
        db.session.flush() # This gets us the task.id before committing


        return new_workflow.id




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

    def start_watcher(self):
        ...

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
        tool = self.tools.get_tool(task.tool_name)
        try:
            res = tool.run_tool(task.settings_file_path)
            if res == None:
                return

            pid, log_path = res

            task.pid = pid
            task.log_path = log_path
            task.status = "Running"
            task.workflow.status = "Running"
            db.session.commit()
            print(f"[*] Dispatched {task.tool_name} (PID: {task.pid}, Weight: {task.weight})")

        except Exception as e:
            task.status = "Failed"
            db.session.commit()
            print(f"[!] Failed to dispatch {task.tool_name}: {e}")


    @task_manager_bp.route('/workflow/create', methods=['POST'])
    @login_required
    def api_create_workflow(self):
        """
        Expects JSON:
        {
            "name": "Gamma Ray Analysis",
            "tools": ["segmenter", "c_analyzer"],
            "settings": ["/path/to/seg_config.json", "/path/to/ana_config.json"]
        }
        """
        print("creating workflow")
        data = request.get_json()

        if not data:
            return jsonify({"error": "No data provided"}), 400

        print(data)
        return jsonify({
            "status": "success",
        }), 201
        # workflow_name = data.get('name')
        # tools = data.get('tools', [])
        # settings = data.get('settings', [])
        #
        # # 1. Basic Validation
        # if not workflow_name or not tools:
        #     return jsonify({"error": "Workflow name and tools are required"}), 400
        #
        # if len(tools) != len(settings):
        #     return jsonify({"error": "Each tool must have a corresponding settings file path"}), 400
        #
        # # 2. Path Validation (Optional but recommended)
        # # This ensures the files exist on the NixOS filesystem before we queue the tasks
        # for path in settings:
        #     if not os.path.exists(path):
        #         return jsonify({"error": f"Settings file not found at: {path}"}), 400
        #
        # try:
        #     # 3. Call your manager function
        #     new_workflow = self.create_new_workflow(
        #         workflow_name=workflow_name,
        #         tools=tools,
        #         settings=settings
        #     )
        #
        #     return jsonify({
        #         "status": "success",
        #         "workflow_id": new_workflow.id,
        #         "message": f"Workflow '{workflow_name}' queued with {len(tools)} tasks."
        #     }), 201
        #
        # except Exception as e:
        #     print(f"[!] Error in create_new_workflow: {e}")
        #     return jsonify({"error": str(e)}), 500
