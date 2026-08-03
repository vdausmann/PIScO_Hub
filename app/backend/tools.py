"""Tool registry: discovers tools from providers and syncs them into the DB.

Two provider layouts are supported:

* builtin (legacy): a directory of ``<tools>/<name>/config.json`` entries,
  one tool per subdirectory. This is the Hub's own ``tools/`` directory.
* toolbox: an external repository with a single ``toolbox.json`` manifest
  at its root declaring provider metadata and a list of tools.

Tools are upserted by unique name: new tools are inserted, changed
manifest entries update the existing row (detected via a content hash),
and tools missing from every provider are marked unavailable but never
deleted, because historical tasks reference them.
"""

import hashlib
import json
import os
from datetime import datetime

from .models import Tool, ToolProvider, db

MANIFEST_NAME = "toolbox.json"

_REQUIRED_KEYS = ("name", "program_path", "program_type", "working_directory",
                  "description")
_SETTINGS_FORMATS = ("opaque", "json", "ini")


class ManifestError(Exception):
    pass


def load_toolbox_manifest(path):
    """Read and validate a toolbox.json at the given provider root.

    Returns (provider_info, tool_descriptors). Raises ManifestError on
    structural problems so callers can reject a provider before insert.
    """
    manifest_path = os.path.join(path, MANIFEST_NAME)
    if not os.path.isfile(manifest_path):
        raise ManifestError(f"No {MANIFEST_NAME} found in {path}")

    try:
        with open(manifest_path, "r") as f:
            manifest = json.load(f)
    except json.JSONDecodeError as e:
        raise ManifestError(f"{manifest_path} is not valid JSON: {e}")

    provider_info = manifest.get("provider", {})
    if not provider_info.get("name"):
        raise ManifestError(f"{manifest_path}: provider.name is required")

    tools = manifest.get("tools", [])
    if not isinstance(tools, list) or not tools:
        raise ManifestError(f"{manifest_path}: 'tools' must be a non-empty list")

    descriptors = [_normalize_entry(entry, path) for entry in tools]
    return provider_info, descriptors


def _normalize_entry(entry, root):
    """Validate a manifest tool entry and resolve it into a flat descriptor.

    Relative paths resolve against the provider root so a toolbox checkout
    works from any location on disk.
    """
    for key in _REQUIRED_KEYS:
        if key not in entry:
            raise ManifestError(
                f"Tool entry {entry.get('name', '?')!r} is missing key '{key}'")

    settings_format = entry.get("settings_format", "opaque")
    if settings_format not in _SETTINGS_FORMATS:
        raise ManifestError(
            f"Tool {entry['name']!r}: unknown settings_format '{settings_format}'")

    def resolve(p):
        return p if os.path.isabs(p) else os.path.normpath(os.path.join(root, p))

    settings_template = ""
    template_path = entry.get("settings_template")
    if template_path:
        template_path = resolve(template_path)
        if not os.path.isfile(template_path):
            raise ManifestError(
                f"Tool {entry['name']!r}: settings_template '{template_path}' not found")
        with open(template_path, "r") as f:
            settings_template = f.read()

    schema = entry.get("settings_schema")
    if schema is not None and not isinstance(schema, dict):
        raise ManifestError(f"Tool {entry['name']!r}: settings_schema must be an object")

    return {
        "name": entry["name"],
        "description": entry["description"],
        "program_path": resolve(entry["program_path"]),
        "program_type": entry["program_type"],
        "working_directory": resolve(entry["working_directory"]),
        "default_priority": entry.get("default_priority", 1),
        "default_weight": entry.get("default_weight", 1),
        "failed_ok": entry.get("failed_ok", False),
        "settings_format": settings_format,
        "settings_schema": json.dumps(schema) if schema is not None else None,
        "settings_template": settings_template,
        "inputs": json.dumps(entry.get("inputs", [])),
        "outputs": json.dumps(entry.get("outputs", [])),
        "tags": json.dumps(entry.get("tags", [])),
        "version": entry.get("version"),
    }


def _legacy_descriptors(provider_path):
    """Adapt the legacy tools/<name>/config.json layout to descriptors."""
    descriptors = []
    for name in sorted(os.listdir(provider_path)):
        tool_path = os.path.join(provider_path, name)
        config_path = os.path.join(tool_path, "config.json")
        if not os.path.isdir(tool_path) or not os.path.isfile(config_path):
            continue

        with open(config_path, "r") as f:
            config = json.load(f)

        try:
            entry = {
                "name": config["name"],
                "description": config["description"],
                "program_path": config["program_path"],
                "program_type": config["program_type"],
                "working_directory": config["working_directory"],
                "default_priority": config["default_priority"],
                "default_weight": config["default_weight"],
                "failed_ok": config["failed_ok"],
            }
        except KeyError as e:
            raise ManifestError(f"The config at {config_path} is not valid: {e}")

        if config.get("settings_template_path"):
            entry["settings_template"] = config["settings_template_path"]

        # Newer optional keys work in legacy configs too.
        for key in ("settings_format", "settings_schema", "inputs", "outputs",
                    "tags", "version"):
            if key in config:
                entry[key] = config[key]

        descriptors.append(_normalize_entry(entry, tool_path))
    return descriptors


def _descriptor_hash(desc):
    return hashlib.sha256(
        json.dumps(desc, sort_keys=True).encode()).hexdigest()


class ToolRegistry:

    def __init__(self, app):
        self.app = app

    def ensure_builtin_provider(self):
        """Seed the provider row for the Hub's own tools/ directory."""
        path = os.path.join(self.app.config["HUB_ROOT"], "tools")
        provider = ToolProvider.query.filter_by(builtin=True).first()
        if provider is None:
            provider = ToolProvider()
            provider.name = "builtin"
            provider.description = "Tools shipped inside the PIScO_Hub repository"
            provider.builtin = True
            db.session.add(provider)
        provider.path = path
        db.session.commit()
        return provider

    def scan_all(self):
        """Scan every registered provider and sync tools into the DB.

        Returns a per-provider report plus the list of deactivated tools.
        One broken provider must not prevent the others (or the server)
        from loading, so errors are collected instead of raised.
        """
        report = {"providers": {}, "deactivated": []}
        seen_names = set()

        for provider in ToolProvider.query.all():
            result = {"added": [], "updated": [], "unchanged": [], "errors": []}
            report["providers"][provider.name] = result

            try:
                if provider.builtin:
                    if not os.path.isdir(provider.path):
                        raise ManifestError(
                            f"Tools directory '{provider.path}' does not exist.")
                    descriptors = _legacy_descriptors(provider.path)
                else:
                    provider_info, descriptors = load_toolbox_manifest(provider.path)
                    provider.description = provider_info.get(
                        "description", provider.description)
                    provider.version = provider_info.get("version", provider.version)
            except (ManifestError, OSError, json.JSONDecodeError) as e:
                result["errors"].append(str(e))
                print(f"[!] Provider '{provider.name}': {e}")
                continue

            for desc in descriptors:
                name = desc["name"]
                if name in seen_names:
                    result["errors"].append(
                        f"Tool name '{name}' already provided elsewhere; skipped.")
                    print(f"[!] Provider '{provider.name}': duplicate tool "
                          f"name '{name}' skipped.")
                    continue
                seen_names.add(name)
                self._upsert(provider, desc, result)

            provider.last_scanned_at = datetime.now()
            db.session.commit()

        # Tools that no provider mentions anymore are hidden from new
        # workflows but kept for the history of finished tasks.
        for tool in Tool.query.all():
            if tool.name not in seen_names and tool.available:
                tool.available = False
                report["deactivated"].append(tool.name)
                print(f"[*] Tool '{tool.name}' no longer provided; marked unavailable.")
        db.session.commit()

        return report

    def _upsert(self, provider, desc, result):
        config_hash = _descriptor_hash(desc)
        tool = Tool.query.filter_by(name=desc["name"]).first()

        if tool is None:
            tool = Tool()
            tool.name = desc["name"]
            db.session.add(tool)
            result["added"].append(desc["name"])
        elif tool.config_hash == config_hash and tool.available \
                and tool.provider_id == provider.id:
            result["unchanged"].append(desc["name"])
            return
        else:
            result["updated"].append(desc["name"])

        tool.provider_id = provider.id
        tool.description = desc["description"]
        tool.program_path = desc["program_path"]
        tool.program_type = desc["program_type"]
        tool.working_directory = desc["working_directory"]
        tool.default_priority = desc["default_priority"]
        tool.default_weight = desc["default_weight"]
        tool.failed_ok = desc["failed_ok"]
        tool.settings_format = desc["settings_format"]
        tool.settings_schema = desc["settings_schema"]
        tool.settings_template = desc["settings_template"]
        tool.inputs = desc["inputs"]
        tool.outputs = desc["outputs"]
        tool.tags = desc["tags"]
        tool.version = desc["version"]
        tool.available = True
        tool.config_hash = config_hash
