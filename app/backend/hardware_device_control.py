import json
import operator
from pathlib import Path
from dataclasses import dataclass
from typing import Any
import re



command_config = {}

def parse_config(path: Path):

    with open(path, "r") as f:
        config = json.load(f)

    parsed_config = {}

    for device in config:
        device_dict = config[device]
        if not "commands" in device_dict:
            device_dict["commands"] = None
        else:
            commands = device_dict["commands"]
            for command in commands:
                print(command)


    return config
