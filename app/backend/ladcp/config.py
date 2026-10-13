"""LADCP configuration: the instruments, the cruise, and the setup sent to
each ADCP — what the old tool kept in ladcp_tool.ini — plus the command
sequence built from it.

The per-role parameters keep the ini's names so a setup can be carried
over by hand. The sequence is the one ladcp_tool v1.10.2 wrote into its
TeraTerm macros (Write_Cmd_Files): factory reset, clock, profiling
setup, synchronisation, deployment name, keep parameters, read back.
"""

import copy
import json
import os
from datetime import datetime, timezone

import jsonschema

ROLE_DEFAULTS = {
    "mode_15": True,                       # WM15: LADCP/surface-track profiling
    "ambiguity_velocity": 300,             # WV, cm/s
    "bin_number": 20,                      # WN
    "bin_length": 1000,                    # WS, cm
    "blank_after_transmit": 0,             # WF, cm
    "bandwidth": 1,                        # WB (ini: broadband) 0 wide, 1 narrow
    "sensor_source": "0011101",            # EZ
    "coordinate_transformation": "00000",  # EX: beam coordinates
    "flow_control": "11101",               # CF; the serial-output digit follows live_output
    "pings_per_ensemble": 1,               # WP
    "time_between_pings": 0.1,             # TP, s
    "time_per_ensemble": 1.6,              # TE, s
    "power_output": 255,                   # CQ
    "when_to_sync": "001",                 # SA (ini: primary_secondary_when_to_sync)
    "extra_commands": [],                  # sent after the setup, before RN
}

DEFAULT_CONFIG = {
    "cruise_id": "MSM147",
    "data_dir": "",
    "backup_dir": "",
    "ntp_server": "",
    "max_clock_offset_s": 2.0,
    "primary_direction": "DOWN",
    "live_output": True,
    "start_command": "CS",
    "memory_warning_bytes": 10000000,
    "voltage_scale": 0.592157,
    "current_scale": 0.011451,
    "bottom_threshold": 25,
    "reattach_on_restart": True,
    "download": {"files": "last", "simultaneous": True, "baud": 115200},
    "instruments": [
        # The ship's serial server (Raspberry Pi, ser2net with RFC 2217):
        # the LADCP deck box on its ports 3001 and 3002. ser2net answers a
        # break request differently from what pyserial expects ("remote
        # rejected value for option 'control'"); ign_set_control makes
        # pyserial not wait for that answer.
        {"name": "ADCP-A", "port": "rfc2217://10.11.15.10:3001?ign_set_control", "baud": 115200,
         "break": "hardware", "enabled": True},
        {"name": "ADCP-B", "port": "rfc2217://10.11.15.10:3002?ign_set_control", "baud": 115200,
         "break": "hardware", "enabled": True},
    ],
    "primary": {**ROLE_DEFAULTS, "wait_ensembles_before_sync": 0, "wait_time_before_sync": 6500},
    "secondary": {**ROLE_DEFAULTS, "wait_time_before_start_without_sync": 200},
}

_role = {
    "type": "object",
    "properties": {
        "mode_15": {"type": "boolean"},
        "ambiguity_velocity": {"type": "integer", "minimum": 2, "maximum": 700},
        "bin_number": {"type": "integer", "minimum": 1, "maximum": 128},
        "bin_length": {"type": "integer", "minimum": 10, "maximum": 6400},
        "blank_after_transmit": {"type": "integer", "minimum": 0, "maximum": 9999},
        "bandwidth": {"type": "integer", "enum": [0, 1]},
        "sensor_source": {"type": "string", "pattern": "^[0-2]{7}$"},
        "coordinate_transformation": {"type": "string", "pattern": "^[0-1]{5}$"},
        "flow_control": {"type": "string", "pattern": "^[0-1]{5}$"},
        "pings_per_ensemble": {"type": "integer", "minimum": 0, "maximum": 16384},
        "time_between_pings": {"type": "number", "minimum": 0, "maximum": 59.99},
        "time_per_ensemble": {"type": "number", "minimum": 0, "maximum": 59.99},
        "power_output": {"type": "integer", "minimum": 0, "maximum": 255},
        "when_to_sync": {"type": "string", "pattern": "^[0-9]{3}$"},
        "wait_ensembles_before_sync": {"type": "integer", "minimum": 0, "maximum": 65535},
        "wait_time_before_sync": {"type": "integer", "minimum": 0, "maximum": 65535},
        "wait_time_before_start_without_sync": {"type": "integer", "minimum": 0, "maximum": 10800},
        "extra_commands": {"type": "array", "items": {"type": "string", "pattern": "^[A-Za-z?][^\\r\\n]*$"}},
    },
    "additionalProperties": False,
}

SCHEMA = {
    "type": "object",
    "required": ["cruise_id", "instruments", "primary", "secondary"],
    "properties": {
        "cruise_id": {"type": "string", "pattern": "^[A-Za-z0-9_\\-]+$"},
        "data_dir": {"type": "string"},
        "backup_dir": {"type": "string"},
        "ntp_server": {"type": "string"},
        "max_clock_offset_s": {"type": "number", "minimum": 0},
        "primary_direction": {"enum": ["DOWN", "UP"]},
        "live_output": {"type": "boolean"},
        "start_command": {"type": "string", "enum": ["CS", "$"]},
        "memory_warning_bytes": {"type": "integer", "minimum": 0},
        "voltage_scale": {"type": ["number", "null"]},
        "current_scale": {"type": ["number", "null"]},
        "bottom_threshold": {"type": "number", "minimum": 1},
        "reattach_on_restart": {"type": "boolean"},
        "download": {
            "type": "object",
            "properties": {
                "files": {"enum": ["last", "all"]},
                "simultaneous": {"type": "boolean"},
                "baud": {"type": ["integer", "null"]},
            },
            "additionalProperties": False,
        },
        "instruments": {
            "type": "array", "minItems": 1, "maxItems": 2,
            "items": {
                "type": "object",
                "required": ["name", "port"],
                "properties": {
                    "name": {"type": "string", "pattern": "^[A-Za-z0-9_\\-]+$"},
                    "port": {"type": "string"},
                    "baud": {"type": "integer", "enum": [1200, 2400, 4800, 9600, 19200, 38400, 57600, 115200]},
                    "break": {"type": "string", "minLength": 1},
                    "enabled": {"type": "boolean"},
                },
                "additionalProperties": False,
            },
        },
        "primary": _role,
        "secondary": _role,
    },
    "additionalProperties": False,
}

BAUD_CODES = {1200: 1, 2400: 2, 4800: 3, 9600: 4, 19200: 5, 38400: 6, 57600: 7, 115200: 8}


def merged(config):
    """The config with every default filled in."""
    out = copy.deepcopy(DEFAULT_CONFIG)
    for key, value in (config or {}).items():
        if key in ("primary", "secondary", "download") and isinstance(value, dict):
            out[key].update(value)
        else:
            out[key] = value
    return out


def validate(config):
    """Raises ValueError naming the first problem."""
    try:
        jsonschema.validate(config, SCHEMA)
    except jsonschema.ValidationError as e:
        where = "/".join(str(p) for p in e.absolute_path) or "config"
        raise ValueError(f"{where}: {e.message}") from None
    names = [i["name"] for i in config["instruments"]]
    if len(set(names)) != len(names):
        raise ValueError("instruments: names must differ")
    ports = [i["port"] for i in config["instruments"] if i.get("enabled", True)]
    if len(set(ports)) != len(ports):
        raise ValueError("instruments: two enabled instruments share a port")


def load(path):
    if os.path.exists(path):
        with open(path) as f:
            return merged(json.load(f))
    return merged({})


def save(path, config):
    config = merged(config)
    validate(config)
    tmp = path + ".tmp"
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(tmp, "w") as f:
        json.dump(config, f, indent=2)
        f.write("\n")
    os.replace(tmp, path)
    return config


# ---- the command sequence ---------------------------------------------------------

def deployment_name(profile, orientation):
    return f"{int(profile):03d}{'UP' if orientation == 'UP' else 'DN'}"


def _flow_control(value, live):
    digits = list(value)
    digits[3] = "1" if live else "0"
    return "".join(digits)


def setup_commands(config, role, profile, orientation, now=None):
    """[(command, what it does)] to program one instrument for a profile.
    role: "primary" | "secondary" | "single" — single is a lone
    instrument, programmed like the primary without synchronisation."""
    p = config["secondary" if role == "secondary" else "primary"]
    now = now or datetime.now(timezone.utc)
    seq = [
        ("CR1", "factory defaults"),
        ("SB0", "ignore breaks on channel B (RS-422 noise)"),
        ("TS " + now.strftime("%y/%m/%d,%H:%M:%S"), "set the clock (UTC)"),
    ]
    if p["mode_15"]:
        seq.append(("WM15", "LADCP / surface-track mode"))
    seq += [
        (f"WV{p['ambiguity_velocity']}", "ambiguity velocity, cm/s"),
        (f"WN{p['bin_number']}", "number of depth cells"),
        (f"WS{p['bin_length']}", "depth cell size, cm"),
        (f"WF{p['blank_after_transmit']}", "blank after transmit, cm"),
        (f"WB{p['bandwidth']}", "bandwidth (0 wide, 1 narrow)"),
        (f"EZ{p['sensor_source']}", "sensor source"),
        (f"EX{p['coordinate_transformation']}", "coordinate transformation"),
        (f"CF{_flow_control(p['flow_control'], config['live_output'])}",
         "flow control" + (" (serial output on: live view)" if config["live_output"] else "")),
        (f"WP{p['pings_per_ensemble']}", "pings per ensemble"),
        (f"TP 00:{p['time_between_pings']:05.2f}", "time between pings"),
        (f"TE 00:00:{p['time_per_ensemble']:05.2f}", "time per ensemble"),
    ]
    if role == "primary":
        seq += [
            ("SM1", "synchronisation: primary (RDS3 master)"),
            (f"SI{p['wait_ensembles_before_sync']}", "ensembles between sync pulses"),
            (f"SA{p['when_to_sync']}", "sync before each ping"),
            (f"SW{p['wait_time_before_sync']}", "delay after the sync pulse, 0.1 ms"),
        ]
    elif role == "secondary":
        seq += [
            ("SM2", "synchronisation: secondary (RDS3 slave)"),
            (f"SA{p['when_to_sync']}", "wait for the pulse before each ping"),
            (f"ST{p['wait_time_before_start_without_sync']}", "start on its own after this many s without a pulse"),
        ]
    seq.append((f"CQ{p['power_output']}", "transmit power"))
    seq += [(c, "extra command") for c in p.get("extra_commands", [])]
    seq += [
        (f"RN {deployment_name(profile, orientation)}", "deployment name"),
        ("CK", "keep parameters"),
    ]
    return seq


READBACK = ["S?", "T?", "W?", "E?"]
