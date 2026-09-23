import json
import random
from time import sleep



# read config:
with open("hardware_control/hardware_control_config.json", "r") as f:
    config = json.load(f)

def simulate_bool():
    return random.random() > 0.5

def simulate_int(min_val: int | None, max_val: int | None):
    if min_val is None:
        min_val = 0
    if max_val is None:
        max_val = 100
    # oout of bounds with small probability:
    r = random.random()
    if r < 0.05:
        interval = max_val - min_val
        return random.randint(min_val - interval // 10, min_val)
    elif r > 0.95:
        interval = max_val - min_val
        return random.randint(max_val, max_val + interval // 10)

    return random.randint(min_val, max_val)

def simulate_float(min_val: float | None, max_val: float | None):
    if min_val is None:
        min_val = 0
    if max_val is None:
        max_val = 100

    r = random.random()
    if r < 0.05:
        interval = max_val - min_val
        return random.uniform(min_val - interval // 10, min_val)
    elif r > 0.95:
        interval = max_val - min_val
        return random.uniform(max_val, max_val + interval // 10)

    return random.uniform(min_val, max_val)


def parse_min_max(min_value: str | None, max_value: str | None, parser):
    if not min_value is None:
        min_value = parser(min_value)
    if not max_value is None:
        max_value = parser(max_value)
    return min_value, max_value


def simulate_sensor(config):
    data_str = ""
    for name, device in config.items():
        if not "sensors" in device:
            continue

        sensors = device["sensors"]

        for sensor in sensors:
            value_type = sensor["type"]
            min_value = sensor["min"]
            max_value = sensor["max"]

            if value_type == "bool":
                min_value, max_value = parse_min_max(min_value, max_value, bool)
                value = simulate_bool()
            elif value_type == "int":
                min_value, max_value = parse_min_max(min_value, max_value, int)
                value = simulate_int(min_value, max_value)
            elif value_type == "float":
                min_value, max_value = parse_min_max(min_value, max_value, float)
                value = simulate_float(min_value, max_value)

            data_str += sensor["shorthand"] + "_" + str(value) + "_"
    return data_str[:-1]

while True:
    with open("hardware_control/test.txt", "w") as f:
        f.write(simulate_sensor(config))
    sleep(1)
