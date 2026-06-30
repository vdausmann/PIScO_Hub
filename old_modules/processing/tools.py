import subprocess
import os
import uuid



class Tool:
    def __init__(self, name: str, execution_call: str, settings_template_path: str,
                 description: str) -> None:
        self.name = name
        self.execution_call = execution_call
        self.settings_template_path = settings_template_path
        self.description = description

    def run_tool(self, settings_file_path: str):
        log_dir = os.path.join("logs", self.name)
        os.makedirs(log_dir, exist_ok=True)

        log_name = str(uuid.uuid4()) + ".log"
        log_path = os.path.join(log_dir, log_name)

        try:
            with open(log_path, "w") as log_file:
                proc = subprocess.Popen(
                    [f"{self.execution_call} {settings_file_path}"],
                    stdout=log_file,
                    stderr=log_file,
                    start_new_session=True
                )

            pid = proc.pid
            print(f"[*] Dispatched {self.name} (PID: {pid})")
            return pid, log_path

        except Exception as e:
            print(f"[!] Failed to dispatch {self.name}: {e}")


class Tools:

    def __init__(self):
        self.tools: dict[str, Tool] = {}

    def get_tool(self, tool_name: str):
        if tool_name in self.tools:
            return self.tools[tool_name]
        raise KeyError(f"Tool '{tool_name}' not registered.")

    def register_tools(self):
        ...

    def _check_tool_config(self, config: dict):
        ...




# def load_tools():
#     path = "./tools"
#     objects = os.listdir(path)
#     tools = {}
#     for obj in objects:
#         dir_path = os.path.join(path, obj)
#         if not os.path.isdir(path):
#             continue
#         config_path = os.path.join(dir_path, "config.json")
#         if not os.path.isfile(config_path):
#             continue
#         with open(config_path, "r") as f:
#             config = json.load(f)
#             config["tool_dir"] = dir_path
#
#             if check_config(config):
#                 tools[config["name"]] = config
#
#     return tools
