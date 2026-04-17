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
                settings_template_path = config["settings_template_path"]
                description = config["description"]
                priority = config["default_priority"]
                weight = config["default_weight"]

                existing_tool = Tool.query.filter_by(name=name).first()
                if existing_tool:
                    return


                tool = Tool()
                tool.name = name
                tool.program_path = program_path
                tool.program_type = program_type
                tool.settings_template_path = settings_template_path
                tool.description = description
                tool.default_priority = priority
                tool.default_weight = weight

                db.session.add(tool)

            except KeyError as e:
                raise Exception(f"The config at {config_path} is not valid: {e}")

            db.session.commit()
        
