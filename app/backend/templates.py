"""Workflow templates: a list of steps plus the parameters a run needs.

A template comes from one of two places. Users save one on this Hub from
a workflow they built; a toolbox ships one in its manifest, in which case
the row belongs to the provider and is rewritten on every scan. Both are
instantiated the same way: the caller supplies params, they are checked
against the template's schema, and the steps become a workflow whose
settings keep their $param/$format references for the dispatcher.
"""

import hashlib
import json
from datetime import datetime

try:
    import jsonschema
except ImportError:  # pragma: no cover
    jsonschema = None

from . import settings_engine
from .models import TemplateTask, TemplateWorkflow, Tool, db


class TemplateError(Exception):
    pass


def find(id_or_name):
    """A template by id, else by name (provider-shipped first)."""
    template = TemplateWorkflow.query.get(id_or_name)
    if template is None:
        template = (TemplateWorkflow.query.filter_by(name=id_or_name)
                    .order_by(TemplateWorkflow.provider_id.is_(None),
                              TemplateWorkflow.created_at)
                    .first())
    return template


def _task_settings(template_task):
    """Settings as a creation spec expects: an object for structured
    tools (so references stay references), text for opaque ones."""
    tool = template_task.tool
    if (tool.settings_format or "opaque") == "opaque":
        return template_task.settings
    try:
        return json.loads(template_task.settings) if template_task.settings else {}
    except ValueError:
        return template_task.settings


def task_specs(template):
    specs = []
    for task in template.tasks:
        spec = {"tool": task.tool.name, "settings": _task_settings(task),
                "priority": task.priority, "weight": task.weight}
        specs.append(spec)
    return specs


def instantiate(template, params, name=None):
    """Turn a template plus params into (name, params, task_specs) ready
    for create_new_workflow. Raises TemplateError on bad params."""
    params, errors = settings_engine.validate_params(template.params_schema, params)
    if errors:
        raise TemplateError("Invalid parameters: " + "; ".join(errors))
    unavailable = [t.tool.name for t in template.tasks if t.tool.available is False]
    if unavailable:
        raise TemplateError(
            f"Template uses tools that are no longer available: "
            f"{', '.join(unavailable)}")
    name = name or settings_engine.format_name(template.name_format, params)
    if not name:
        name = f"{template.name} {datetime.now():%Y-%m-%d %H:%M}"
    return name, params, task_specs(template)


def _store_tasks(template, specs):
    """Replace a template's steps from creation-style task specs."""
    for old in list(template.tasks):
        db.session.delete(old)
    for position, spec in enumerate(specs):
        tool = Tool.query.filter_by(name=spec.get("tool")).first()
        if tool is None:
            raise TemplateError(f"Tool '{spec.get('tool')}' is not registered.")
        task = TemplateTask()
        task.workflow = template
        task.tool_id = tool.id
        task.workflow_position = position
        settings = spec.get("settings")
        if settings is None:
            settings = settings_engine.default_settings(tool)
        elif not isinstance(settings, str):
            settings = json.dumps(settings, indent=2)
        task.settings = settings
        task.priority = spec.get("priority", tool.default_priority)
        task.weight = spec.get("weight", tool.default_weight)
        db.session.add(task)


def _check_schema(params_schema):
    if params_schema is None:
        return None
    if not isinstance(params_schema, dict):
        raise TemplateError("'params_schema' must be a JSON Schema object")
    if jsonschema is not None:
        try:
            jsonschema.Draft202012Validator.check_schema(params_schema)
        except jsonschema.SchemaError as e:
            raise TemplateError(f"'params_schema' is not a valid JSON Schema: {e.message}")
    return json.dumps(params_schema, indent=2)


def _check_name(name, exclude=None):
    name = (name or "").strip()
    if not name:
        raise TemplateError("'name' is required")
    clash = TemplateWorkflow.query.filter_by(name=name).first()
    if clash is not None and clash is not exclude:
        raise TemplateError(f"A template named '{name}' already exists")
    return name


def save(name, specs=None, params_schema=None, description=None,
         name_format=None, user_id=None):
    """Store a user-defined template. `specs` may be empty: a template can
    start as a set of parameters and get its steps later."""
    template = TemplateWorkflow()
    template.name = _check_name(name)
    template.description = description
    template.params_schema = _check_schema(params_schema)
    template.name_format = name_format
    template.user_id = user_id
    db.session.add(template)
    _store_tasks(template, specs or [])
    db.session.commit()
    return template


def _editable(template):
    if template.provider_id is not None:
        raise TemplateError(
            f"'{template.name}' is shipped by provider "
            f"'{template.provider.name}'; edit its toolbox.json instead.")


def update(template, name=None, description=None, name_format=None,
           params_schema=None):
    """Change a user-saved template's parameters and metadata."""
    _editable(template)
    if name is not None:
        template.name = _check_name(name, exclude=template)
    template.description = description
    template.name_format = name_format
    template.params_schema = _check_schema(params_schema)
    db.session.commit()
    return template


def update_tasks(template, specs):
    """Replace a user-saved template's steps (from a builder draft)."""
    _editable(template)
    _store_tasks(template, specs)
    db.session.commit()
    return template


def delete(template):
    _editable(template)
    db.session.delete(template)
    db.session.commit()


# ---- manifest-shipped templates ------------------------------------------

def _entry_hash(entry):
    return hashlib.sha256(json.dumps(entry, sort_keys=True).encode()).hexdigest()


def sync_provider(provider, entries, result):
    """Make the provider's templates match the `workflows` list of its
    manifest. Reports into the scan result like tools do."""
    seen = set()
    for entry in entries:
        name = entry.get("name")
        try:
            if not name:
                raise TemplateError("workflow entry without a name")
            if name in seen:
                raise TemplateError(f"workflow '{name}' listed twice")
            seen.add(name)
            specs = entry.get("tasks", [])
            if not isinstance(specs, list):
                raise TemplateError(f"workflow '{name}': 'tasks' must be a list")
            entry_hash = _entry_hash(entry)
            template = TemplateWorkflow.query.filter_by(
                provider_id=provider.id, name=name).first()
            if template is not None and template.manifest_hash == entry_hash:
                continue
            # A savepoint, so one bad entry does not take the others with it.
            with db.session.begin_nested():
                if template is None:
                    template = TemplateWorkflow()
                    template.name = name
                    template.provider_id = provider.id
                    db.session.add(template)
                    result["added"].append(f"workflow:{name}")
                else:
                    result["updated"].append(f"workflow:{name}")
                template.description = entry.get("description")
                template.params_schema = _check_schema(entry.get("params_schema"))
                template.name_format = entry.get("name_format")
                _store_tasks(template, specs)
                template.manifest_hash = entry_hash
        except TemplateError as e:
            result["errors"].append(f"workflow '{name}': {e}")
            print(f"[!] Provider '{provider.name}': workflow '{name}': {e}")

    for template in TemplateWorkflow.query.filter_by(provider_id=provider.id).all():
        if template.name not in seen:
            db.session.delete(template)
            print(f"[*] Template '{template.name}' no longer in "
                  f"'{provider.name}'; removed.")
    db.session.commit()


def template_dict(template, detail=False):
    data = {
        "id": template.id,
        "name": template.name,
        "description": template.description,
        "provider": template.provider.name if template.provider else None,
        "task_count": len(template.tasks),
        "tools": [t.tool.name for t in template.tasks],
        "name_format": template.name_format,
        "created_at": template.created_at.isoformat() if template.created_at else None,
    }
    if detail:
        schema = None
        if template.params_schema:
            try:
                schema = json.loads(template.params_schema)
            except ValueError:
                schema = None
        data["params_schema"] = schema
        data["tasks"] = task_specs(template)
    return data
