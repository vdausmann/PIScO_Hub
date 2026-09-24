import json
import operator
from pathlib import Path
from dataclasses import dataclass
from typing import Any
import re



sensor_config = {}




@dataclass
class DisplayValue:
    value: Any
    text: str
    valid: bool
    alarm: bool
    device_name: str
    color: str | None = None


    def to_dict(self):
        return {
            "value": self.value,
            "text": self.text,
            "valid": self.valid,
            "alarm": self.alarm,
            "device_name": self.device_name,
            "color": self.color
        }


class Formatter:

    def __init__(self, config: dict, device: str) -> None:
        self.formatter_str = config.get("formatter", "")
        self.unit = config.get("unit", "")
        self.colors = config.get("colors", [])
        self.device = device

        if self.formatter_str.lower() == "button" and not self.colors:
            self.colors = ["red", "green", "gray"]
            print("Warning: No colors specified for button formatter. Defaulting to red, green, and gray.")

    def _format_bool(self, value: bool | None, min_value: bool | None, max_value: bool |
                     None, triggered: bool):

        if value is None:
            text = "N/A"
            valid = True    # No value given, so no alarm should be raised.
            color = self.colors[-1] if self.colors else "gray"

            return DisplayValue(value, text, valid, False,  self.device, color)

        alarm = False
        valid = True
        if not min_value is None and int(value) < int(min_value):
            valid = False

        if not max_value is None and int(value) > int(max_value):
            valid = False

        if not valid and triggered:
            alarm = True

        text = str(value)
        color = self.colors[int(value)]

        if self.unit:
            text += " " + self.unit

        return DisplayValue(value, text, valid, alarm, self.device, color)



    def _format_int(self, value: int | None, min_value: int | None, max_value: int | None,
                    triggered: bool):
        if value is None:
            text = "N/A"
            valid = True    # No value given, so no alarm should be raised.
            color = None

            return DisplayValue(value, text, valid, False, self.device, color)

        valid = True
        alarm = False
        if not min_value is None and value < min_value:
            valid = False
        if not max_value is None and value > max_value:
            valid = False

        if not valid and triggered:
            alarm = True

        if not self.formatter_str:
            text = str(value)
        else:
            places = self.formatter_str.lower().count("x")
            text = str(value)
            text = text.zfill(places)

        if self.unit:
            text += " " + self.unit
        return DisplayValue(value, text, valid, alarm, self.device)


    def _format_float(self, value: float | None, min_value: float | None, max_value: float
                      | None, triggered: bool):
        if value is None:
            text = "N/A"
            valid = True    # No value given, so no alarm should be raised.
            color = None

            return DisplayValue(value, text, valid, False, self.device, color)

        alarm = False
        valid = True
        if not min_value is None and value < min_value:
            valid = False
        if not max_value is None and value > max_value:
            valid = False

        if not valid and triggered:
            alarm = True

        if not self.formatter_str:
            text = str(value)
        else:
            split = self.formatter_str.split(".")

            integer_places = len(split[0])
            decimal_places = len(split[1])

            rounded_value = round(float(value), decimal_places)

            integer_text = str(int(rounded_value)).zfill(integer_places)

            decimal_value = round(
                abs(rounded_value - int(rounded_value)),
                decimal_places
            )

            decimal_text = str(
                int(decimal_value * 10**decimal_places)
            ).zfill(decimal_places)

            text = f"{integer_text}.{decimal_text}"

        if self.unit:
            text += " " + self.unit

        return DisplayValue(value, text, valid, alarm, self.device, None)


    def format_value(self, value, min_value, max_value, value_type, triggered):
        if value_type == int:
            return self._format_int(value, min_value, max_value, triggered)
        elif value_type == float:
            return self._format_float(value, min_value, max_value, triggered)
        elif value_type == bool:
            return self._format_bool(value, min_value, max_value, triggered)
        else:
            return DisplayValue(0, "", False, False, self.device, None)

OPERATORS = {
    "==": operator.eq,
    "!=": operator.ne,
    ">=": operator.ge,
    "<=": operator.le,
    ">": operator.gt,
    "<": operator.lt,
}

class AlarmCondition:

    def __init__(self, expression, value_type):
        match = re.fullmatch(
            r"\s*(.+?)\s*(==|!=|>=|<=|>|<)\s*(.+?)\s*",
            expression,
        )

        if not match:
            raise ValueError(f"Invalid condition: {expression}")

        self.name = match.group(1)
        self.operator = OPERATORS[match.group(2)]
        self.threshold = match.group(3)


    def check(self, sensors):
        if not self.name in sensors:
            raise KeyError(f"{self.name} is not in SensorConfig")
        return self.operator(sensors[self.name].currentValue,
                             sensors[self.name].parse_value(self.threshold))

class Sensor:

    def __init__(self, config, device: str) -> None:
        self.name: str = config["name"]

        # Type parsing: Supported types are currently int, float, bool
        self.value_type_str = config["type"].lower()
        self.value_type = int
        if self.value_type_str == "float":
            self.value_type = float
        elif self.value_type_str == "bool":
            self.value_type = bool

        self.shorthand: str = config["shorthand"]
        self.group: str = config["group"]
        self.device: str = device

        self.min_value = config["min"]
        if not self.min_value is None:
            self.min_value = self.value_type(self.min_value)

        self.max_value = config["max"]
        if not self.max_value is None:
            self.max_value = self.value_type(self.max_value)

        alarm_condition_str = config["alarm condition"]
        self.alarm_condition = None
        if alarm_condition_str:
            self.alarm_condition = AlarmCondition(alarm_condition_str, self.value_type)

        self.formatter = Formatter(config["format"], self.device)

        self.currentValue = None

    def set_value(self, value: str | None):
        if value is None:
            self.currentValue = None
            return

        value_lower = value.strip().lower()

        if self.value_type == bool:
            if value_lower == "0" or value_lower == "false":
                self.currentValue = False
            elif value_lower == "1" or value_lower == "true":
                self.currentValue = True
            else:
                self.currentValue = None

        else:
            self.currentValue = self.value_type(value)


    def get_display_value(self, triggered: bool):
        return self.formatter.format_value(self.currentValue, self.min_value,
                                    self.max_value, self.value_type, triggered)

    def parse_value(self, value: str):
        return self.value_type(value)

    def compare_value(self, value: Any) -> bool:
        return self.currentValue == value



def parse_config(path: Path):

    with open(path, "r") as f:
        config = json.load(f)

    parsed_config = {}

    for device in config:
        device_dict = config[device]
        if not "sensors" in device_dict:
            device_dict["sensors"] = None
        else:
            sensors = device_dict["sensors"]
            for sensor in sensors:
                s = Sensor(sensor, device)
                sensor_config[s.shorthand] = s


        if not "commands" in device_dict:
            device_dict["commands"] = None


    return config


def parse_sensor_data(filepath: Path):
    try:
        # get last row of file:
        with open(filepath, "r") as f:
            # Assumes the data is written in the format shorthand1_data1_shorthand2_data2...
            data = f.readlines()[-1].strip().split("_")

        if len(data) % 2 != 0:
            raise Exception("Invalid data file")

        data_dict = {data[2 * i]: data[2 * i + 1] for i in range(len(data) // 2)}

        sensors = {}
        for name, sensor in sensor_config.items():
            try:
                value = data_dict.get(name, None)
                # if not value is None:
                #     value = sensor.parse_value(value)
                sensor.set_value(value)

                sensors[name] = sensor
            except Exception as e:
                print("#" * 60)
                print(name, value, type(value), e)

        res = {}
        # check all alarm conditions:
        for name, sensor in sensor_config.items():
            triggered = False
            if sensor.alarm_condition is not None:
                try:
                    if sensor.alarm_condition.check(sensors):
                        triggered = True
                except Exception as exception:
                    # print(name, exception)
                    ...
            else:
                # Assume that no-alarm condition means that an alarm should be triggered
                # as soon as the value is invalid
                triggered = True

            res = {name: sensors[name].get_display_value(triggered).to_dict() for name in sensors}
            print(name, sensors[name].get_display_value(triggered).to_dict())

                
        return res
    except Exception as e:
        print("parse_sensor_data:", e)
        return {}
