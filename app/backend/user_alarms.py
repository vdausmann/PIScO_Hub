"""Alarms set up on the Status Display, on top of the configured limits.

Anyone on the page can add one: a field (a shorthand such as `Pi-ExPres`)
and a condition. Kept in one JSON file on the Hub and evaluated there with
the values the status endpoint already serves, so every viewer sees the
same alarms.

Kinds:
  threshold   field <op> value             (op: > >= < <= == !=)
  outside     field below min or above max
  on / off    field is 1 / is 0
  unchanged   field has not changed for `seconds`
  missing     field has no value (nan or not sent)

`only_while` (optional) is a threshold on another field, e.g. Pi-Record == 1:
the alarm is checked only while it holds. As with the configured limits,
a field from a stale controller raises no alarm: the device heading
already says the controller went quiet.
"""

import json
import os
import threading
import time
import uuid

OPERATORS = {
    ">": lambda a, b: a > b, ">=": lambda a, b: a >= b,
    "<": lambda a, b: a < b, "<=": lambda a, b: a <= b,
    "==": lambda a, b: a == b, "!=": lambda a, b: a != b,
}
KINDS = ("threshold", "outside", "on", "off", "unchanged", "missing")

_lock = threading.Lock()


def _number(text):
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def _has_value(raw):
    return raw is not None and str(raw).strip().lower() not in ("", "nan")


def _compare(raw, op, target):
    """`raw op target`, numerically when both are numbers."""
    a, b = _number(raw), _number(target)
    if a is None or b is None:
        a, b = str(raw).strip(), str(target).strip()
        if op not in ("==", "!="):
            return False
    return OPERATORS[op](a, b)


def validate(spec):
    """A clean alarm dict from a request body, or ValueError."""
    if not isinstance(spec, dict):
        raise ValueError("body must be a JSON object")
    field = str(spec.get("field") or "").strip()
    kind = spec.get("kind")
    if not field:
        raise ValueError("pick a field")
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {', '.join(KINDS)}")
    alarm = {"field": field, "kind": kind, "name": str(spec.get("name") or "").strip()}
    if kind == "threshold":
        if spec.get("op") not in OPERATORS:
            raise ValueError(f"op must be one of {' '.join(OPERATORS)}")
        if str(spec.get("value", "")).strip() == "":
            raise ValueError("give a value to compare with")
        alarm.update(op=spec["op"], value=str(spec["value"]).strip())
    elif kind == "outside":
        low, high = _number(spec.get("min")), _number(spec.get("max"))
        if low is None and high is None:
            raise ValueError("give a min, a max, or both")
        if low is not None and high is not None and low > high:
            raise ValueError("min is above max")
        alarm.update(min=low, max=high)
    elif kind == "unchanged":
        seconds = _number(spec.get("seconds"))
        if seconds is None or seconds <= 0:
            raise ValueError("seconds must be a positive number")
        alarm["seconds"] = seconds
    guard = spec.get("only_while")
    if guard:
        if not isinstance(guard, dict) or not str(guard.get("field") or "").strip() \
                or guard.get("op") not in OPERATORS or str(guard.get("value", "")).strip() == "":
            raise ValueError("only_while needs a field, an op and a value")
        alarm["only_while"] = {"field": guard["field"].strip(), "op": guard["op"],
                               "value": str(guard["value"]).strip()}
    if not alarm["name"]:
        alarm["name"] = describe(alarm)
    return alarm


def describe(alarm):
    """`Pi-ExPres > 1500`, for names and messages."""
    field, kind = alarm["field"], alarm["kind"]
    text = {
        "threshold": lambda: f"{field} {alarm.get('op')} {alarm.get('value')}",
        "outside": lambda: f"{field} outside {_bound(alarm.get('min'))}…{_bound(alarm.get('max'))}",
        "on": lambda: f"{field} is on",
        "off": lambda: f"{field} is off",
        "unchanged": lambda: f"{field} unchanged for {alarm.get('seconds'):g} s",
        "missing": lambda: f"{field} has no value",
    }[kind]()
    guard = alarm.get("only_while")
    if guard:
        text += f" while {guard['field']} {guard['op']} {guard['value']}"
    return text


def _bound(value):
    return "" if value is None else f"{value:g}"


def evaluate(alarm, values, since, now=None):
    """(active, current value) for one alarm. `values` is the status
    endpoint's {shorthand: (raw, stale, device, ...)}, `since` the epoch
    time each field last changed."""
    now = time.time() if now is None else now
    field = values.get(alarm["field"])
    raw, stale = (field[0], field[1]) if field else (None, False)
    if stale or not alarm.get("enabled", True):
        return False, raw
    guard = alarm.get("only_while")
    if guard:
        held = values.get(guard["field"])
        if not held or held[1] or not _has_value(held[0]) \
                or not _compare(held[0], guard["op"], guard["value"]):
            return False, raw
    kind = alarm["kind"]
    if kind == "missing":
        return not _has_value(raw), raw
    if not _has_value(raw):
        return False, raw
    if kind == "threshold":
        return _compare(raw, alarm["op"], alarm["value"]), raw
    if kind == "outside":
        number = _number(raw)
        if number is None:
            return False, raw
        return ((alarm.get("min") is not None and number < alarm["min"])
                or (alarm.get("max") is not None and number > alarm["max"])), raw
    if kind in ("on", "off"):
        number = _number(raw)
        if number is None:
            return False, raw
        return (number != 0) == (kind == "on"), raw
    if kind == "unchanged":
        changed = since.get(alarm["field"])
        return changed is not None and now - changed >= alarm["seconds"], raw
    return False, raw


def store_path(app):
    # Next to the poller's live state: runtime data, not configuration.
    return os.path.join(app.config["HUB_ROOT"], "hardware_control", "state", "user_alarms.json")


class AlarmStore:
    """The alarms in one JSON file, written atomically."""

    def __init__(self, path):
        self.path = path

    def load(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                alarms = json.load(f)
        except FileNotFoundError:
            return []
        return alarms if isinstance(alarms, list) else []

    def _save(self, alarms):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(alarms, f, indent=1)
        os.replace(tmp, self.path)

    def add(self, spec, user):
        alarm = validate(spec)
        alarm.update(id=uuid.uuid4().hex[:8], enabled=True, created_by=user,
                     created=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
        with _lock:
            alarms = self.load()
            alarms.append(alarm)
            self._save(alarms)
        return alarm

    def update(self, alarm_id, changes):
        """Only `enabled` changes in place; anything else is a new alarm."""
        with _lock:
            alarms = self.load()
            for alarm in alarms:
                if alarm.get("id") == alarm_id:
                    if "enabled" in changes:
                        alarm["enabled"] = bool(changes["enabled"])
                    self._save(alarms)
                    return alarm
        raise KeyError(alarm_id)

    def delete(self, alarm_id):
        with _lock:
            alarms = self.load()
            kept = [a for a in alarms if a.get("id") != alarm_id]
            if len(kept) == len(alarms):
                raise KeyError(alarm_id)
            self._save(kept)
