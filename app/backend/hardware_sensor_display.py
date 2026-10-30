import json
import operator
from pathlib import Path
from dataclasses import dataclass
from typing import Any
import re
from datetime import datetime



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
            integer_part, _, decimal_part = self.formatter_str.partition(".")

            integer_places = len(integer_part)
            decimal_places = len(decimal_part)

            # Pad the magnitude, then sign it: -0.5 must not lose its sign
            # to int() truncating towards zero.
            width = integer_places + (decimal_places + 1 if decimal_places else 0)
            sign = "-" if value < 0 else ""
            text = sign + f"{abs(value):0{width}.{decimal_places}f}"

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

        self.min_value = self._limit(config["min"])
        self.max_value = self._limit(config["max"])

        # Switched off on the Status Display: shown, but never an alarm.
        self.alarm_enabled = config.get("alarm enabled", True)

        alarm_condition_str = config["alarm condition"]
        self.alarm_condition = None
        if alarm_condition_str:
            self.alarm_condition = AlarmCondition(alarm_condition_str, self.value_type)

        self.formatter = Formatter(config["format"], self.device)

        self.currentValue = None

    def _limit(self, text):
        if text is None:
            return None
        if self.value_type == bool:
            # bool("0") is True; a limit of 0 must stay 0.
            return bool(int(float(text)))
        return self.value_type(text)

    def set_value(self, value: str | None):
        if value is None:
            self.currentValue = None
            return

        value_lower = value.strip().lower()

        if value_lower in ("", "nan"):
            # The controllers write `nan` for a sensor that is not connected.
            self.currentValue = None

        elif self.value_type == bool:
            if value_lower == "0" or value_lower == "false":
                self.currentValue = False
            elif value_lower == "1" or value_lower == "true":
                self.currentValue = True
            else:
                self.currentValue = None

        elif self.value_type == int:
            self.currentValue = int(float(value))

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

    for device in config:
        device_dict = config[device]
        if not "sensors" in device_dict:
            device_dict["sensors"] = None

        if not "commands" in device_dict:
            device_dict["commands"] = None


    return config


def load_sensors(config: dict) -> dict:
    """{shorthand: Sensor} for every configured sensor. Built per request:
    a Sensor holds its current value, so sharing one set between viewers
    would mix their readings."""
    sensors = {}
    for device, device_dict in config.items():
        if not isinstance(device_dict, dict):
            continue
        for sensor in device_dict.get("sensors") or []:
            s = Sensor(sensor, device)
            sensors[s.shorthand] = s
    return sensors


def merge_sources(live: dict, devices_meta: dict) -> tuple[dict, dict]:
    """Values for each configured device from the poller's devices.

    A poller device feeds the configured device named by its
    `config_device` (its own name by default), so two controllers can fill
    one device, e.g. the PIScO camera computer and the NUC. Per shorthand,
    a live source beats a stale one and a real value beats `nan`.

    Returns ({shorthand: (raw value, stale, device, epoch it last changed)},
    {device: [source meta]})."""
    order = sorted(devices_meta.items(),
                   key=lambda item: (item[1].get("stale", True), item[1].get("changed") is None,
                                     -(_epoch(item[1].get("changed")))))
    values, sources = {}, {}
    for name, meta in order:
        device = meta.get("config_device") or name
        sources.setdefault(device, []).append(
            {"name": name, **{k: v for k, v in meta.items() if k != "since"}})
        stale = bool(meta.get("stale", True))
        since = meta.get("since") or {}
        for shorthand, raw in (live.get(name) or {}).items():
            held = values.get(shorthand)
            if held is None or (str(held[0]).lower() == "nan" and str(raw).lower() != "nan"):
                values[shorthand] = (raw, stale, device, since.get(shorthand))
    return values, sources


def _epoch(iso):
    return datetime.fromisoformat(iso).timestamp() if iso else 0.0


def display_values(sensors: dict, values: dict) -> dict:
    """{shorthand: display dict} from {shorthand: (raw value, stale,
    device)}. A value from a stale source is shown but raises no alarm: the
    device status says the source went quiet, the value only says what it
    last was. A field a controller sends but the config does not declare
    comes along as it was sent, marked `extra`, with no limits to check."""
    stale = {}
    for name, sensor in sensors.items():
        raw, stale[name], _ = values.get(name, (None, False, None))[:3]
        try:
            sensor.set_value(None if raw is None else str(raw))
        except ValueError:
            sensor.set_value(None)

    res = {}
    for name, sensor in sensors.items():
        if sensor.alarm_condition is not None:
            try:
                triggered = bool(sensor.alarm_condition.check(sensors))
            except (KeyError, TypeError, ValueError):
                triggered = False
        else:
            # No alarm condition means an alarm as soon as the value is invalid.
            triggered = True

        display = sensor.get_display_value(triggered).to_dict()
        display["stale"] = stale[name]
        if stale[name] or not sensor.alarm_enabled:
            display["alarm"] = False
        res[name] = display

    # The device a field belongs to by its prefix (Pi-, Bl-, ...), for
    # sources that do not say (a local test file).
    by_prefix = {s.shorthand.split("-")[0]: s.device for s in sensors.values()}
    for name, held in values.items():
        if name in res:
            continue
        raw, is_stale, device = held[:3]
        text = "" if raw is None else str(raw).strip()
        missing = text.lower() in ("", "nan")
        res[name] = {
            "value": None if missing else text, "text": "N/A" if missing else text,
            "valid": True, "alarm": False, "color": None, "stale": is_stale, "extra": True,
            "device_name": device or by_prefix.get(name.split("-")[0]),
        }
    return res


def apply_limit_overrides(config: dict, overrides: dict) -> dict:
    """The config with the limits changed on the Status Display put in
    place of the configured ones. The config file itself is left alone: it
    is shared with the controllers' repository."""
    if not overrides:
        return config
    changed = {}
    for device, device_dict in config.items():
        if not isinstance(device_dict, dict) or not device_dict.get("sensors"):
            changed[device] = device_dict
            continue
        sensors = []
        for sensor in device_dict["sensors"]:
            override = overrides.get(sensor["shorthand"])
            if override:
                sensor = {**sensor, "min": override.get("min"), "max": override.get("max"),
                          "alarm condition": override.get("condition"),
                          "alarm enabled": override.get("enabled", True)}
            sensors.append(sensor)
        changed[device] = {**device_dict, "sensors": sensors}
    return changed


def limit_list(config: dict, shown: dict, overrides: dict) -> list:
    """Every configured sensor with a limit, as the page lists them: the
    limit in force, what the config file says, and whether it is on now."""
    limits = []
    for device, device_dict in config.items():
        if not isinstance(device_dict, dict):
            continue
        for sensor in device_dict.get("sensors") or []:
            shorthand = sensor["shorthand"]
            override = overrides.get(shorthand)
            configured = {"min": sensor.get("min"), "max": sensor.get("max"),
                          "condition": sensor.get("alarm condition")}
            current = ({k: override.get(k) for k in ("min", "max", "condition")}
                       if override else configured)
            if not override and configured["min"] is None and configured["max"] is None:
                continue
            display = shown.get(shorthand, {})
            limits.append({
                "field": shorthand, "name": sensor.get("name") or shorthand, "device": device,
                "type": sensor.get("type"), "unit": (sensor.get("format") or {}).get("unit", ""),
                **current, "enabled": override.get("enabled", True) if override else True,
                "configured": configured, "changed": bool(override),
                "changed_by": override.get("changed_by") if override else None,
                "active": bool(display.get("alarm")), "valid": display.get("valid", True),
                "current": display.get("text"), "stale": display.get("stale", False),
            })
    return limits


def build_status(config: dict, values: dict, sources: dict, alarms: list,
                 limit_overrides: dict | None = None) -> dict:
    """What the Status Display shows, from merged values: the formatted
    sensors, the devices' sources, the configured limits and the page's own
    alarms evaluated. Shared by the status endpoint and the alarm monitor,
    so both judge the same way."""
    from . import user_alarms
    limit_overrides = limit_overrides or {}
    since = {k: v[3] for k, v in values.items() if len(v) > 3 and v[3] is not None}
    evaluated = []
    for alarm in alarms:
        active, value = user_alarms.evaluate(alarm, values, since)
        evaluated.append({**alarm, "active": active, "current": value})
    sensors = load_sensors(apply_limit_overrides(config, limit_overrides))
    shown = display_values(sensors, values)
    for name, display in shown.items():
        display["name"] = sensors[name].name if name in sensors else name
    return {"sensors": shown, "devices": sources, "alarms": evaluated,
            "limits": limit_list(config, shown, limit_overrides)}


def active_alarms(status: dict) -> dict:
    """{alarm id: {...}} for every alarm that is on: a configured limit
    (`limit:<shorthand>`) or one set up on the page (`user:<id>`)."""
    active = {}
    for shorthand, display in status["sensors"].items():
        if display.get("alarm"):
            active[f"limit:{shorthand}"] = {
                "kind": "limit", "device": display.get("device_name"), "field": shorthand,
                "name": f"{display.get('name') or shorthand} out of range", "value": display.get("text")}
    for alarm in status["alarms"]:
        if alarm["active"]:
            current = alarm.get("current")
            active[f"user:{alarm['id']}"] = {
                "kind": "user", "device": None, "field": alarm["field"], "name": alarm["name"],
                "value": "no value" if current is None or str(current).lower() == "nan" else str(current)}
    return active
