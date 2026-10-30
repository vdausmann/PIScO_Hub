"""The configured limits as the alarm list shows them, changed on the Hub
on top of the config file, and editing an alarm of your own.
Run: python -m pytest tests/status_display"""

import pytest

from app.backend.hardware_sensor_display import build_status
from app.backend.user_alarms import AlarmStore, LimitStore, validate_limit

from test_status_display import CONFIG, sensor

VALUES = {"Pi-HuCam": ("80", False, "PIScO", None), "Pi-ExPres": ("12", False, "PIScO", None)}


def limits(status):
    return {limit["field"]: limit for limit in status["limits"]}


def test_only_sensors_with_a_limit_are_listed():
    listed = limits(build_status(CONFIG, VALUES, {}, []))
    assert set(listed) == {"Pi-ExPres", "Pi-HuCam", "Pi-TAGLock"}
    assert listed["Pi-HuCam"]["active"] and not listed["Pi-HuCam"]["changed"]
    assert listed["Pi-TAGLock"]["condition"] == "Pi-Record==1"


def test_override_replaces_the_configured_limit():
    override = {"Pi-HuCam": {"min": None, "max": "90", "condition": None, "enabled": True,
                             "changed_by": "veit"}}
    status = build_status(CONFIG, VALUES, {}, [], override)
    assert not status["sensors"]["Pi-HuCam"]["alarm"]
    hu = limits(status)["Pi-HuCam"]
    assert hu["max"] == "90" and hu["changed"] and hu["configured"]["max"] == "75"


def test_switched_off_limit_raises_no_alarm():
    override = {"Pi-HuCam": {"min": None, "max": "75", "condition": None, "enabled": False}}
    status = build_status(CONFIG, VALUES, {}, [], override)
    assert not status["sensors"]["Pi-HuCam"]["alarm"]
    assert not status["sensors"]["Pi-HuCam"]["valid"]
    assert limits(status)["Pi-HuCam"]["enabled"] is False


def test_a_limit_can_be_added_to_a_sensor_without_one():
    override = {"Pi-TeLED": {"min": None, "max": "40", "condition": None, "enabled": True}}
    status = build_status(CONFIG, {"Pi-TeLED": ("50", False, "PIScO", None)}, {}, [], override)
    assert status["sensors"]["Pi-TeLED"]["alarm"]
    assert "Pi-TeLED" in limits(status)


@pytest.mark.parametrize("spec", [
    {"min": None, "max": None},
    {"max": "warm"},
    {"min": "5", "max": "1"},
    {"max": "5", "condition": "Pi-Record"},
])
def test_invalid_limits(spec):
    with pytest.raises(ValueError):
        validate_limit(spec, sensor("Pi-HuCam", max_="75"))


def test_int_sensor_refuses_a_fraction():
    with pytest.raises(ValueError):
        validate_limit({"max": "11.5"}, sensor("Pi-FramWr", "int"))


def test_bool_limit_of_zero_stays_zero():
    status = build_status({"P": {"sensors": [sensor("Pi-Rec", "bool",
                           {"formatter": "button", "colors": ["red", "green", "gray"]}, max_="0")]}},
                          {"Pi-Rec": ("0", False, "P", None)}, {}, [])
    assert status["sensors"]["Pi-Rec"]["valid"]


def test_limit_store_set_and_reset(tmp_path):
    store = LimitStore(str(tmp_path / "limit_overrides.json"))
    store.set("Pi-HuCam", {"max": 90, "enabled": True}, sensor("Pi-HuCam", max_="75"), "veit")
    assert store.load()["Pi-HuCam"]["max"] == "90"
    store.reset("Pi-HuCam")
    assert store.load() == {}
    with pytest.raises(KeyError):
        store.reset("Pi-HuCam")


def test_editing_an_alarm_keeps_its_id_author_and_state(tmp_path):
    store = AlarmStore(str(tmp_path / "user_alarms.json"))
    alarm = store.add({"field": "Pi-Record", "kind": "off"}, "veit")
    store.update(alarm["id"], {"enabled": False})
    edited = store.update(alarm["id"], {"field": "Pi-HuCam", "kind": "threshold",
                                        "op": ">", "value": "70"}, "anton")
    assert edited["id"] == alarm["id"] and edited["created_by"] == "veit"
    assert edited["enabled"] is False and edited["changed_by"] == "anton"
    assert edited["name"] == "Pi-HuCam > 70"
    with pytest.raises(ValueError):
        store.update(alarm["id"], {"field": "Pi-HuCam", "kind": "sometimes"})
