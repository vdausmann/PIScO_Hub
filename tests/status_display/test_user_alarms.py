"""Alarms set up on the Status Display: validation, evaluation and the
store. Run: python -m pytest tests/status_display"""

import pytest

from app.backend import user_alarms
from app.backend.user_alarms import AlarmStore, evaluate, validate

NOW = 1_000_000.0


def values(**fields):
    """{shorthand: (raw, stale, device, since)}; names use _ for -."""
    return {k.replace("_", "-"): (v, False, "PIScO", None) for k, v in fields.items()}


def check(spec, vals, since=None):
    return evaluate(validate(spec), vals, since or {}, now=NOW)[0]


def test_threshold_numeric_and_text():
    spec = {"field": "Pi-ExPres", "kind": "threshold", "op": ">", "value": "1500"}
    assert check(spec, values(Pi_ExPres="1848.59"))
    assert not check(spec, values(Pi_ExPres="0023.45"))
    assert check({"field": "Pi-Mode", "kind": "threshold", "op": "==", "value": "idle"},
                 values(Pi_Mode="idle"))


def test_outside_range_with_one_bound():
    spec = {"field": "Pi-HuCam", "kind": "outside", "min": None, "max": 75}
    assert check(spec, values(Pi_HuCam="80"))
    assert not check(spec, values(Pi_HuCam="5"))


def test_on_and_off():
    assert check({"field": "Pi-Record", "kind": "off"}, values(Pi_Record="0"))
    assert not check({"field": "Pi-Record", "kind": "on"}, values(Pi_Record="0"))


def test_unchanged_for_seconds():
    spec = {"field": "Pi-FramWr", "kind": "unchanged", "seconds": 30}
    vals = values(Pi_FramWr="46288")
    assert check(spec, vals, {"Pi-FramWr": NOW - 31})
    assert not check(spec, vals, {"Pi-FramWr": NOW - 5})
    assert not check(spec, vals, {})                    # never seen change: not judged


def test_missing_value():
    spec = {"field": "Pi-HuCom", "kind": "missing"}
    assert check(spec, values(Pi_HuCom="nan"))
    assert check(spec, {})
    assert not check(spec, values(Pi_HuCom="38.1"))


def test_only_while_and_stale():
    spec = {"field": "Pi-FramWr", "kind": "unchanged", "seconds": 10,
            "only_while": {"field": "Pi-Record", "op": "==", "value": "1"}}
    since = {"Pi-FramWr": NOW - 60}
    assert check(spec, values(Pi_FramWr="5", Pi_Record="1"), since)
    assert not check(spec, values(Pi_FramWr="5", Pi_Record="0"), since)
    stale = {"Pi-FramWr": ("5", True, "PIScO", None), "Pi-Record": ("1", False, "PIScO", None)}
    assert not check(spec, stale, since)


def test_disabled_alarm_is_quiet():
    alarm = {**validate({"field": "Pi-Record", "kind": "off"}), "enabled": False}
    assert not evaluate(alarm, values(Pi_Record="0"), {}, now=NOW)[0]


@pytest.mark.parametrize("spec", [
    {"kind": "on"},
    {"field": "Pi-X", "kind": "sometimes"},
    {"field": "Pi-X", "kind": "threshold", "op": "~", "value": "1"},
    {"field": "Pi-X", "kind": "threshold", "op": ">", "value": ""},
    {"field": "Pi-X", "kind": "outside"},
    {"field": "Pi-X", "kind": "outside", "min": 5, "max": 1},
    {"field": "Pi-X", "kind": "unchanged", "seconds": 0},
    {"field": "Pi-X", "kind": "on", "only_while": {"field": "Pi-Y", "op": "=="}},
])
def test_invalid_specs(spec):
    with pytest.raises(ValueError):
        validate(spec)


def test_name_is_described_when_not_given():
    assert validate({"field": "Pi-FramWr", "kind": "unchanged", "seconds": 30,
                     "only_while": {"field": "Pi-Record", "op": "==", "value": 1}})["name"] \
        == "Pi-FramWr unchanged for 30 s while Pi-Record == 1"


def test_store_add_switch_delete(tmp_path):
    store = AlarmStore(str(tmp_path / "state" / "user_alarms.json"))
    alarm = store.add({"field": "Pi-Record", "kind": "off"}, "veit")
    assert alarm["created_by"] == "veit" and alarm["enabled"]
    assert store.update(alarm["id"], {"enabled": False})["enabled"] is False
    store.delete(alarm["id"])
    assert store.load() == []
    with pytest.raises(KeyError):
        store.delete(alarm["id"])
