"""The status display's formatting and alarms, and how values from several
controllers are merged into one device. Run: python -m pytest tests/status_display"""

from datetime import datetime, timezone

from app.backend.hardware_poller import device_time, parse_line
from app.backend.hardware_sensor_display import (Formatter, display_values, load_sensors,
                                                 merge_sources)


def sensor(shorthand, type_="float", fmt=None, min_=None, max_=None, condition=None):
    return {"shorthand": shorthand, "name": shorthand, "group": "g", "type": type_,
            "format": fmt or {"formatter": "xx.xx", "unit": ""}, "min": min_, "max": max_,
            "alarm condition": condition}


CONFIG = {"PIScO": {"sensors": [
    sensor("Pi-ExPres", fmt={"formatter": "xxxx.xx", "unit": "dbar"}, max_="6000"),
    sensor("Pi-TeLED"),
    sensor("Pi-HuCam", max_="75"),
    sensor("Pi-Record", "bool", {"formatter": "button", "colors": ["red", "green", "gray"]}),
    sensor("Pi-TAGLock", "bool", {"formatter": "button", "colors": ["red", "green", "gray"]},
           min_="1", condition="Pi-Record==1"),
    sensor("Pi-FramWr", "int"),
]}, "Sea-Through": {"commands": []}}


def meta(stale, changed="2026-10-27T13:09:45.000+00:00", device=None):
    return {"enabled": True, "host": "h", "stale": stale, "changed": changed,
            "error": None, "config_device": device}


def test_float_keeps_the_sign_below_one():
    f = Formatter({"formatter": "xx.xx", "unit": ""}, "PIScO")
    assert f.format_value(-0.5, None, None, float, True).text == "-00.50"
    assert f.format_value(-15.26, None, None, float, True).text == "-15.26"
    assert f.format_value(1848.594, None, None, float, True).text == "1848.59"


def test_nan_is_not_available_and_raises_no_alarm():
    shown = display_values(load_sensors(CONFIG), {"Pi-HuCam": ("nan", False, "PIScO")})
    assert shown["Pi-HuCam"]["text"] == "N/A"
    assert not shown["Pi-HuCam"]["alarm"]


def test_each_sensor_gets_its_own_alarm_condition():
    # Pi-TAGLock is out of range, but its condition (recording) is false:
    # no alarm, while Pi-HuCam without a condition alarms on its own.
    values = {"Pi-TAGLock": ("0", False, "PIScO"), "Pi-Record": ("0", False, "PIScO"), "Pi-HuCam": ("80", False, "PIScO")}
    shown = display_values(load_sensors(CONFIG), values)
    assert not shown["Pi-TAGLock"]["alarm"] and not shown["Pi-TAGLock"]["valid"]
    assert shown["Pi-HuCam"]["alarm"]
    values["Pi-Record"] = ("1", False, "PIScO")
    assert display_values(load_sensors(CONFIG), values)["Pi-TAGLock"]["alarm"]


def test_stale_value_is_shown_without_alarm():
    shown = display_values(load_sensors(CONFIG), {"Pi-HuCam": ("80", True, "PIScO")})
    assert shown["Pi-HuCam"]["text"] == "80.00" and shown["Pi-HuCam"]["stale"]
    assert not shown["Pi-HuCam"]["alarm"]


def test_int_sensor_takes_a_float_spelling():
    shown = display_values(load_sensors(CONFIG), {"Pi-FramWr": ("46288.0", False, "PIScO")})
    assert shown["Pi-FramWr"]["value"] == 46288


def test_two_controllers_fill_one_device():
    live = {"PIScO": {"Pi-ExPres": "1848.59", "Pi-TeLED": "nan"},
            "PIScO-NUC": {"Pi-ExPres": "1700.00", "Pi-TeLED": "-0.5"}}
    values, sources = merge_sources(live, {"PIScO": meta(False),
                                           "PIScO-NUC": meta(True, device="PIScO")})
    assert values["Pi-ExPres"][:3] == ("1848.59", False, "PIScO")   # the live source wins
    assert values["Pi-TeLED"][:3] == ("-0.5", True, "PIScO")        # a value beats nan
    assert [s["name"] for s in sources["PIScO"]] == ["PIScO", "PIScO-NUC"]


def test_unconfigured_field_comes_along_as_extra():
    shown = display_values(load_sensors(CONFIG), {"Pi-FramQu": ("0", False, "PIScO"),
                                                  "Bl-FramWr": ("113858", False, None),
                                                  "Pi-TAGOn": ("nan", False, "PIScO")})
    assert shown["Pi-FramQu"] == {"value": "0", "text": "0", "valid": True, "alarm": False,
                                  "color": None, "stale": False, "extra": True,
                                  "device_name": "PIScO"}
    assert shown["Bl-FramWr"]["device_name"] is None            # no Bl- sensor configured here
    assert shown["Pi-TAGOn"]["value"] is None


def test_device_time_field():
    _, values = parse_line("Bl-Time_2026102713094563_Bl-Storage_4.9_Bl-FramWr_113858")
    expected = datetime(2026, 10, 27, 13, 9, 45, 630000, timezone.utc).timestamp()
    assert abs(device_time(values) - expected) < 1e-6
    assert device_time({"Pi-ExPres": "1848.59"}) is None


def test_parse_line_keeps_underscored_keys():
    _, values = parse_line("Pi-LED_DCDC_con_1_Pi-LED_DCDC_0_Pi-SetLEDV_1.75")
    assert values == {"Pi-LED_DCDC_con": "1", "Pi-LED_DCDC": "0", "Pi-SetLEDV": "1.75"}


def test_parse_line_propel_camera():
    # Prefix with a digit, and plain lower-case pairs after the last field.
    _, values = parse_line(
        "Pk1-Hu_12.580_Pk1-InPres_1100.700_Pk1-FramDr_nan_recording_1_live-preview_1"
        "_h5-frames-written_000094_h5-frames-dropped_000")
    assert values == {"Pk1-Hu": "12.580", "Pk1-InPres": "1100.700", "Pk1-FramDr": "nan",
                      "recording": "1", "live-preview": "1",
                      "h5-frames-written": "000094", "h5-frames-dropped": "000"}
    _, commands = parse_line("Pk1-Shutdown_0_Pk1-SetGain_0.0_Pk1-AcqTime_200_Pk1-ResCam_0")
    assert commands == {"Pk1-Shutdown": "0", "Pk1-SetGain": "0.0",
                        "Pk1-AcqTime": "200", "Pk1-ResCam": "0"}
