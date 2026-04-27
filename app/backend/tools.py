import json
import os
from .models import Tool, db



def register_tools(app):
    path = "./tools"
    if not os.path.isdir(path):
        raise Exception(f"Tools directory '{path}' does not exist.")

    with app.app_context():
        for file in os.listdir(path):
            tool_path = os.path.join(path, file)
            if not os.path.isdir(tool_path):
                continue

            config_path = os.path.join(tool_path, "config.json")
            if not os.path.isfile(config_path):
                continue

            with open(config_path, "r") as f:
                config = json.load(f)

            try:
                name = config["name"]
                program_path = config["program_path"]
                program_type = config["program_type"]
                working_directory = config["working_directory"]
                description = config["description"]
                priority = config["default_priority"]
                weight = config["default_weight"]
                failed_ok = config["failed_ok"]
                settings_template_path = config["settings_template_path"]

                existing_tool = Tool.query.filter_by(name=name).first()
                if existing_tool:
                    return

                # load settings_template:
                settings_template = ""
                if settings_template_path:
                    settings_template_path = os.path.join(tool_path, settings_template_path)
                    if os.path.exists(settings_template_path):
                        with open(settings_template_path, "r") as f:
                            settings_template = f.read()

                tool = Tool()
                tool.name = name
                tool.program_path = program_path
                tool.program_type = program_type
                tool.working_directory = working_directory
                tool.settings_template = settings_template
                tool.description = description
                tool.default_priority = priority
                tool.default_weight = weight
                tool.failed_ok = failed_ok

                db.session.add(tool)

            except KeyError as e:
                raise Exception(f"The config at {config_path} is not valid: {e}")

            db.session.commit()
        
