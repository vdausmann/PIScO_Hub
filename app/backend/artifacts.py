"""Output locations and the artifacts tasks produce.

A tool declares its outputs in the manifest and binds each one to a
settings key. That lets the Hub decide *where* results go instead of
making the user invent paths, record what was produced, and hand those
paths to a later task in the same workflow.

Paths are deterministic: the same task always maps to the same location,
so a reference can be resolved even if the artifact row is missing.
"""

import json
import os
import re

from .models import Artifact, GlobalSetting, db


def data_root(app):
    """Root for Hub-allocated outputs.

    Must live on shared storage when tasks run on other machines, since
    the path is handed to the tool verbatim.
    """
    return GlobalSetting.get(
        "DATA_ROOT", default=os.path.join(app.config["HUB_ROOT"], "data"))


def declarations(tool, field):
    """Parse a tool's inputs/outputs declarations."""
    try:
        return json.loads(getattr(tool, field) or "[]")
    except (ValueError, TypeError):
        return []


def find_declaration(tool, field, name):
    for declaration in declarations(tool, field):
        if declaration.get("name") == name:
            return declaration
    return None


def task_workspace(app, task):
    """Everything belonging to one task: its settings file and any output
    the Hub placed for it.

    This lives under DATA_ROOT rather than beside the Hub's own files
    because a task may run on another machine, which reaches it through
    the shared storage at the same path.
    """
    return os.path.join(
        data_root(app),
        "workflows",
        str(task.workflow_id),
        f"{task.workflow_position}_{task.tool.name}",
    )


def allocated_output_path(app, task, output):
    """Where an output goes when the user did not name a location.

    Directories keep a trailing separator. Both forms name the same
    directory, but some tools treat the value as a filename prefix and
    concatenate onto it, so the explicit form is the one that works
    everywhere: "<dir>/" + "file.h5" lands inside, "<dir>" + "file.h5"
    lands beside.
    """
    path = os.path.join(task_workspace(app, task), output["name"])
    if output.get("kind", "directory") == "directory":
        path += os.sep
    return path


def _path_parts(key):
    """'section.items[2].path' -> ['section', 'items', 2, 'path']."""
    return [int(tok[1:-1]) if tok.startswith("[") else tok
            for tok in re.findall(r"\[\d+\]|[^.\[\]]+", key)]


def get_setting(obj, dotted_key):
    """Read a value from parsed settings; 'section.key' walks one level,
    'items[2]' one list position."""
    node = obj
    for part in _path_parts(dotted_key):
        if isinstance(part, int):
            if not isinstance(node, list) or part >= len(node):
                return None
        elif not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def set_setting(obj, dotted_key, value):
    """Write a value into parsed settings, creating intermediate dicts;
    list positions must already exist."""
    parts = _path_parts(dotted_key)
    node = obj
    for part in parts[:-1]:
        if isinstance(part, int):
            node = node[part]
        else:
            if not isinstance(node.get(part), (dict, list)):
                node[part] = {}
            node = node[part]
    node[parts[-1]] = value


def validation_candidate(tool, settings_obj, params=None):
    """A copy of the settings with everything the Hub fills in later stubbed.

    Validation happens when a workflow is built, but references have no
    path until their upstream task runs, and outputs left blank are only
    assigned a location at dispatch. Both would otherwise fail a schema
    that marks them required. Workflow params are known already, so
    those references are filled with their real values and validated.
    """
    from . import settings_engine

    candidate = settings_engine.resolve_params(settings_obj, params)
    candidate = settings_engine.substitute_from_refs(candidate)
    for output in declarations(tool, "outputs"):
        key = output.get("settings_key")
        if key and not get_setting(candidate, key):
            set_setting(candidate, key, "__hub_allocated__")
    return candidate


def record_artifact(task, output, path):
    """Register (or update) an artifact row for a task's declared output.

    Called at dispatch, when the path is finally known, so that a later
    task can resolve a reference to it by lookup instead of guesswork.
    """
    artifact = Artifact.query.filter_by(task_id=task.id,
                                        name=output["name"]).first()
    if artifact is None:
        artifact = Artifact()
        artifact.task_id = task.id
        artifact.name = output["name"]
        db.session.add(artifact)

    artifact.kind = output.get("kind", "directory")
    artifact.path = path
    artifact.exists = None
    artifact.size_bytes = None
    return artifact


def finalize_artifacts(task):
    """After a task finishes, check whether its declared outputs appeared.

    A missing output is recorded rather than hidden: a downstream task
    referencing it should fail with a precise reason.
    """
    for artifact in task.artifacts:
        artifact.exists = os.path.exists(artifact.path)
        if artifact.exists and artifact.kind == "file":
            try:
                artifact.size_bytes = os.path.getsize(artifact.path)
            except OSError:
                artifact.size_bytes = None
        if not artifact.exists:
            print(f"[!] Task {task.id}: declared output '{artifact.name}' "
                  f"was not produced at {artifact.path}")
