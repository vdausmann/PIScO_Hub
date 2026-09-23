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
PARAM_KEY = "$param"
FORMAT_KEY = "$format"
REF_KEYS = (FROM_KEY, PARAM_KEY, FORMAT_KEY)
_PLACEHOLDER = "__hub_pending__"


def ref_kind(value):
    """Which kind of reference a settings value is, or None.

    Three kinds: {"$from": {...}} consumes an earlier task's output,
    {"$param": "name"} takes a workflow parameter as it is, and
    {"$format": "{cruise}_{station}"} composes a string from several.
    """
    if isinstance(value, dict) and len(value) == 1:
        key = next(iter(value))
        if key in REF_KEYS:
            return key
    return None


def _is_ref(value):
    return ref_kind(value) == FROM_KEY


def extract_refs(obj, prefix=""):
    """Find every reference in a settings object as [(path, kind, ref)].
    Paths are dotted, with list positions as `[i]`: `inputs[2].path`."""
    found = []
    if isinstance(obj, dict):
        for key, value in obj.items():
            path = f"{prefix}.{key}" if prefix else key
            kind = ref_kind(value)
            if kind is not None:
                found.append((path, kind, value[kind]))
            else:
                found.extend(extract_refs(value, path))
    elif isinstance(obj, list):
        for i, value in enumerate(obj):
            path = f"{prefix}[{i}]"
            kind = ref_kind(value)
            if kind is not None:
                found.append((path, kind, value[kind]))
            else:
                found.extend(extract_refs(value, path))
    return found


def extract_from_refs(obj, prefix=""):
    """Find every {"$from": {...}} reference in a settings object.

    Returns [(dotted_path, ref)] so callers can validate the references
    up front and substitute resolved paths later.
    """
    return [(path, ref) for path, kind, ref in extract_refs(obj, prefix)
            if kind == FROM_KEY]


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


class _Params(dict):
    def __missing__(self, key):
        raise KeyError(key)


def resolve_params(obj, params):
    """Copy of the settings with $param and $format references filled in
    from the workflow's params. $from references are left alone.

    Raises ValueError naming the setting and the parameter it misses, so
    a workflow with a typo is rejected when it is created.
    """
    params = _Params(params or {})

    def walk(value, path):
        kind = ref_kind(value)
        if kind == PARAM_KEY:
            name = value[PARAM_KEY]
            if not isinstance(name, str):
                raise ValueError(f"'{path}': $param must name a parameter")
            if name not in params:
                raise ValueError(
                    f"'{path}': no workflow parameter '{name}'")
            return params[name]
        if kind == FORMAT_KEY:
            template = value[FORMAT_KEY]
            if not isinstance(template, str):
                raise ValueError(f"'{path}': $format must be a string")
            try:
                return template.format_map(params)
            except KeyError as e:
                raise ValueError(
                    f"'{path}': $format uses unknown parameter {e}")
            except (ValueError, IndexError) as e:
                raise ValueError(f"'{path}': bad $format string: {e}")
        if isinstance(value, dict):
            return {key: walk(inner, f"{path}.{key}" if path else key)
                    for key, inner in value.items()}
        if isinstance(value, list):
            return [walk(inner, path) for inner in value]
        return value

    return walk(obj, "")


def format_name(name_format, params):
    """A workflow name from a template's name_format, or None if the
    format needs a parameter that is missing."""
    if not name_format:
        return None
    try:
        return name_format.format_map(_Params(params or {}))
    except (KeyError, ValueError, IndexError):
        return None


def validate_params(schema_text, params):
    """Check workflow params against a template's JSON Schema.

    Values that arrive as text (CLI flags, HTML forms) are coerced to the
    declared type first, so "38.5" satisfies a number property. Defaults
    are filled in. Returns (params, errors)."""
    params = dict(params or {})
    if not schema_text:
        return params, []
    schema = json.loads(schema_text) if isinstance(schema_text, str) else schema_text
    properties = schema.get("properties", {})
    for key, prop in properties.items():
        if key not in params and "default" in prop:
            params[key] = prop["default"]
        elif key in params and isinstance(params[key], str):
            params[key] = _coerce(prop.get("type"), params[key])
    if jsonschema is None:
        return params, []
    errors = []
    for error in jsonschema.Draft202012Validator(schema).iter_errors(params):
        location = "/".join(str(p) for p in error.absolute_path) or "(root)"
        errors.append(f"{location}: {error.message}")
    return params, errors


def _coerce(prop_type, text):
    try:
        if prop_type == "integer":
            return int(text)
        if prop_type == "number":
            return float(text)
        if prop_type == "boolean":
            if text.lower() in ("true", "1", "yes", "on"):
                return True
            if text.lower() in ("false", "0", "no", "off", ""):
                return False
    except ValueError:
        pass
    return text


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
    # Tools look their keys up verbatim, and configparser lowercases option
    # names by default - which would turn sourcePath into sourcepath and
    # leave the tool silently using its own defaults.
    parser.optionxform = str
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
        # 1/0 rather than true/false: C and C++ readers parse booleans with
        # stream extraction, which only understands 1/0 unless they opt into
        # boolalpha, while python's configparser accepts both.
        return "1" if value else "0"
    if isinstance(value, (list, dict)):
        return json.dumps(value)
    return str(value)
