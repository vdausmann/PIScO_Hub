"""Validation and serialization of task settings.

A tool declares a ``settings_format``:

* ``opaque`` — settings are a free-text blob, written verbatim to a
  ``.cfg`` file at dispatch (the original Hub behavior).
* ``json`` — settings are a JSON object, optionally validated against the
  tool's JSON Schema, written as a ``.json`` file.
* ``ini`` — settings are a JSON object whose top-level keys are INI
  sections, rendered with configparser to a ``.ini`` file.

Task rows store settings as text in all cases (JSON text for structured
formats), so the Temporary/Template task tables need no schema changes.
"""

import configparser
import io
import json

try:
    import jsonschema
except ImportError:  # pragma: no cover - optional until flake update is applied
    jsonschema = None

_EXTENSIONS = {"opaque": ".cfg", "json": ".json", "ini": ".ini"}


def settings_file_extension(tool):
    return _EXTENSIONS.get(tool.settings_format or "opaque", ".cfg")


def parse_settings(tool, settings_text):
    """Parse stored settings text into an object for structured formats.

    Returns the raw text for opaque tools. Raises ValueError on bad JSON.
    """
    if (tool.settings_format or "opaque") == "opaque":
        return settings_text
    try:
        return json.loads(settings_text) if settings_text else {}
    except json.JSONDecodeError as e:
        raise ValueError(f"Settings are not valid JSON: {e}")


FROM_KEY = "$from"
_PLACEHOLDER = "__hub_pending__"


def _is_ref(value):
    return isinstance(value, dict) and set(value.keys()) == {FROM_KEY}


def extract_from_refs(obj, prefix=""):
    """Find every {"$from": {...}} reference in a settings object.

    Returns [(dotted_path, ref)] so callers can validate the references
    up front and substitute resolved paths later.
    """
    found = []
    if isinstance(obj, dict):
        for key, value in obj.items():
            path = f"{prefix}.{key}" if prefix else key
            if _is_ref(value):
                found.append((path, value[FROM_KEY]))
            else:
                found.extend(extract_from_refs(value, path))
    return found


def substitute_from_refs(obj):
    """Copy of the settings with references replaced by a placeholder.

    References point at paths that only exist once an upstream task has
    run, so schema validation at creation time checks the shape around
    them rather than the value itself. Bound path properties should
    therefore be plain strings, without pattern/enum constraints.
    """
    if isinstance(obj, dict):
        if _is_ref(obj):
            return _PLACEHOLDER
        return {key: substitute_from_refs(value) for key, value in obj.items()}
    if isinstance(obj, list):
        return [substitute_from_refs(value) for value in obj]
    return obj


def validate(tool, settings_obj):
    """Validate a settings object against the tool's schema.

    Returns a list of human-readable error strings (empty = valid).
    """
    if (tool.settings_format or "opaque") == "opaque" or not tool.settings_schema:
        return []
    if not isinstance(settings_obj, dict):
        return ["Settings must be a JSON object for this tool."]
    if jsonschema is None:
        print("[!] jsonschema not installed; skipping settings validation.")
        return []

    schema = json.loads(tool.settings_schema)
    validator = jsonschema.Draft202012Validator(schema)
    errors = []
    for error in validator.iter_errors(settings_obj):
        location = "/".join(str(p) for p in error.absolute_path) or "(root)"
        errors.append(f"{location}: {error.message}")
    return errors


def apply_defaults(schema_node, obj):
    """Recursively fill missing keys of obj with schema defaults."""
    if schema_node.get("type") != "object":
        return obj
    for key, prop in schema_node.get("properties", {}).items():
        if key not in obj and "default" in prop:
            obj[key] = prop["default"]
        elif prop.get("type") == "object":
            obj.setdefault(key, {})
            apply_defaults(prop, obj[key])
    return obj


def default_settings(tool):
    """The settings text a new task is pre-filled with."""
    fmt = tool.settings_format or "opaque"
    if fmt == "opaque":
        return tool.settings_template or ""
    obj = {}
    if tool.settings_schema:
        apply_defaults(json.loads(tool.settings_schema), obj)
    if not obj and tool.settings_template:
        return tool.settings_template
    return json.dumps(obj, indent=2)


def render_settings_file(tool, settings_text):
    """Serialize stored settings text into the file content the tool reads."""
    fmt = tool.settings_format or "opaque"
    if fmt == "opaque":
        return settings_text or ""

    obj = parse_settings(tool, settings_text)
    if fmt == "json":
        return json.dumps(obj, indent=2)

    # ini: top-level keys are sections; scalar top-level values go to [DEFAULT]
    parser = configparser.ConfigParser()
    for key, value in obj.items():
        if isinstance(value, dict):
            parser[key] = {k: _ini_value(v) for k, v in value.items()}
        else:
            parser["DEFAULT"][key] = _ini_value(value)
    out = io.StringIO()
    parser.write(out)
    return out.getvalue()


def _ini_value(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, dict)):
        return json.dumps(value)
    return str(value)
