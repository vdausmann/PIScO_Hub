"""Predefined workflow parameters the template editor offers.

Each entry is a JSON Schema property the way a template's params_schema
holds it, plus the key it is stored under. The editor lets a user add
these with one click and adjust them; anything not listed can still be
defined by hand there. Keep the keys stable: templates and the tools
that consume them refer to parameters by key.
"""

CATALOG = [
    {"key": "cruise", "required": True,
     "schema": {"type": "string", "title": "Cruise", "sticky": True,
                "description": "Cruise identifier, e.g. M203"}},
    {"key": "station", "required": True,
     "schema": {"type": "string", "title": "Station",
                "description": "DSHIP station id, e.g. 012-01"}},
    {"key": "instrument", "required": False,
     "schema": {"type": "string", "title": "Instrument", "sticky": True,
                "enum": ["PISCO1", "PISCO2"], "default": "PISCO2"}},
    {"key": "lat", "required": False,
     "schema": {"type": "number", "title": "Latitude", "minimum": -90, "maximum": 90,
                "description": "Decimal degrees, south negative"}},
    {"key": "lon", "required": False,
     "schema": {"type": "number", "title": "Longitude", "minimum": -180, "maximum": 180,
                "description": "Decimal degrees, west negative"}},
    {"key": "frames", "required": True,
     "schema": {"type": "string", "title": "Frame directory", "format": "directory",
                "description": "Raw frames of this profile, on shared storage"}},
    {"key": "sensor_log", "required": False,
     "schema": {"type": "string", "title": "Sensor log", "format": "file",
                "description": "Pressure/temperature log matched to frames by time"}},
    {"key": "date", "required": False,
     "schema": {"type": "string", "title": "Deployment date", "format": "date"}},
    {"key": "operator", "required": False,
     "schema": {"type": "string", "title": "Operator", "sticky": True}},
    {"key": "comment", "required": False,
     "schema": {"type": "string", "title": "Comment"}},
    {"key": "deconvolve", "required": False,
     "schema": {"type": "boolean", "title": "Deconvolve crops", "default": True}},
]

# The editor's type menu: label -> the schema fragment it stands for.
TYPES = [
    {"id": "text", "label": "Text", "schema": {"type": "string"}},
    {"id": "choice", "label": "Choice", "schema": {"type": "string"}, "extra": "enum"},
    {"id": "number", "label": "Number", "schema": {"type": "number"}, "extra": "range"},
    {"id": "integer", "label": "Whole number", "schema": {"type": "integer"}, "extra": "range"},
    {"id": "boolean", "label": "Yes / no", "schema": {"type": "boolean"}},
    {"id": "directory", "label": "Directory", "schema": {"type": "string", "format": "directory"}},
    {"id": "file", "label": "File", "schema": {"type": "string", "format": "file"}},
    {"id": "date", "label": "Date", "schema": {"type": "string", "format": "date"}},
]
