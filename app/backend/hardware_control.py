import json
from pathlib import Path


def parse_sensor_data(path: Path) -> dict:
    return {}

def parse_config(path: Path):

    with open(path, "r") as f:
        config = json.load(f)

    return config
