import json


def _json_or_default(text, default):
    if not text:
        return default
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return default


def tool_summary(tool):
    return {
        "name": tool.name,
        "description": tool.description,
        "tags": _json_or_default(tool.tags, []),
        "provider": tool.provider.name if tool.provider else None,
        "version": tool.version,
        "available": tool.available if tool.available is not None else True,
        "settings_format": tool.settings_format or "opaque",
    }


def tool_detail(tool):
    data = tool_summary(tool)
    data.update({
        "program_type": tool.program_type,
        "default_priority": tool.default_priority,
        "default_weight": tool.default_weight,
        "failed_ok": tool.failed_ok,
        "settings_schema": _json_or_default(tool.settings_schema, None),
        "settings_template": tool.settings_template,
        "inputs": _json_or_default(tool.inputs, []),
        "outputs": _json_or_default(tool.outputs, []),
        "requires": _json_or_default(tool.requires, []),
    })
    return data


def provider_dict(provider):
    return {
        "id": provider.id,
        "name": provider.name,
        "path": provider.path,
        "description": provider.description,
        "version": provider.version,
        "builtin": bool(provider.builtin),
        "last_scanned_at": provider.last_scanned_at.isoformat()
            if provider.last_scanned_at else None,
        "tool_count": len(provider.tools),
    }


def machine_dict(machine):
    from ...backend.machines import machine_load
    load = machine_load(machine.id)
    return {
        "id": machine.id,
        "name": machine.name,
        "host": machine.host,
        "ssh_user": machine.ssh_user,
        "is_local": bool(machine.is_local),
        "enabled": bool(machine.enabled),
        "status": "online" if machine.is_local else machine.status,
        "tags": _json_or_default(machine.tags, []),
        "max_weight": machine.max_weight,
        "load": load,
        "remaining_capacity": (machine.max_weight or 0) - load,
        "last_seen_at": machine.last_seen_at.isoformat()
            if machine.last_seen_at else None,
        "note": machine.note,
    }


def artifact_dict(artifact):
    return {
        "name": artifact.name,
        "kind": artifact.kind,
        "path": artifact.path,
        "exists": artifact.exists,
        "size_bytes": artifact.size_bytes,
        "created_at": artifact.created_at.isoformat() if artifact.created_at else None,
    }


def task_dict(task):
    return {
        "id": task.id,
        "workflow_id": task.workflow_id,
        "workflow_position": task.workflow_position,
        "tool": task.tool.name if task.tool else None,
        "status": task.status,
        "priority": task.priority,
        "weight": task.weight,
        "exit_code": task.exit_code,
        "started_at": task.started_at.isoformat() if task.started_at else None,
        "completed_at": task.completed_at.isoformat() if task.completed_at else None,
        "machine": task.machine.name if task.machine else None,
        "artifacts": [artifact_dict(a) for a in task.artifacts],
    }


def workflow_summary(workflow):
    return {
        "id": workflow.id,
        "name": workflow.name,
        "status": workflow.status,
        "created_at": workflow.created_at.isoformat() if workflow.created_at else None,
        "task_count": len(workflow.tasks),
    }


def workflow_detail(workflow):
    data = workflow_summary(workflow)
    tasks = sorted(workflow.tasks, key=lambda t: t.workflow_position)
    data["tasks"] = [task_dict(t) for t in tasks]
    return data
