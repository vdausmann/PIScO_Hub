"""Hand-maintained OpenAPI 3.1 description of /api/v1.

Update this file whenever an api_v1 route is added or changed.
Kept as a python dict so the server URL/version can be injected live.
"""

from .system import API_VERSION

_ERROR = {"type": "object", "properties": {"error": {"type": "string"}}}

_TOOL_SUMMARY = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "description": {"type": "string"},
        "tags": {"type": "array", "items": {"type": "string"}},
        "provider": {"type": ["string", "null"]},
        "version": {"type": ["string", "null"]},
        "available": {"type": "boolean"},
        "settings_format": {"type": "string", "enum": ["opaque", "json", "ini"]},
    },
}

_TOOL_DETAIL = {
    "allOf": [_TOOL_SUMMARY, {
        "type": "object",
        "properties": {
            "program_type": {"type": "string"},
            "default_priority": {"type": "integer"},
            "default_weight": {"type": "integer"},
            "failed_ok": {"type": "boolean"},
            "settings_schema": {
                "type": ["object", "null"],
                "description": "JSON Schema for this tool's settings object",
            },
            "settings_template": {"type": ["string", "null"]},
            "inputs": {"type": "array"},
            "outputs": {"type": "array"},
        },
    }],
}

_TASK = {
    "type": "object",
    "properties": {
        "id": {"type": "string"},
        "workflow_id": {"type": "string"},
        "workflow_position": {"type": "integer"},
        "tool": {"type": "string"},
        "status": {"type": "string",
                   "enum": ["Pending", "Running", "Finished", "Failed"]},
        "priority": {"type": "integer"},
        "weight": {"type": "integer"},
        "exit_code": {"type": ["integer", "null"]},
        "started_at": {"type": ["string", "null"]},
        "completed_at": {"type": ["string", "null"]},
    },
}

_WORKFLOW = {
    "type": "object",
    "properties": {
        "id": {"type": "string"},
        "name": {"type": "string"},
        "status": {"type": "string",
                   "enum": ["Pending", "Running", "Finished", "Failed"]},
        "created_at": {"type": ["string", "null"]},
        "task_count": {"type": "integer"},
        "tasks": {"type": "array", "items": _TASK},
    },
}

_WORKFLOW_CREATE = {
    "type": "object",
    "required": ["name", "tasks"],
    "properties": {
        "name": {"type": "string"},
        "tasks": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "required": ["tool"],
                "properties": {
                    "tool": {"type": "string"},
                    "settings": {
                        "description": "Object for schema'd tools (validated), "
                                       "string for opaque tools, omit for defaults",
                    },
                    "priority": {"type": "integer"},
                    "weight": {"type": "integer"},
                },
            },
        },
    },
}


def _op(summary, responses=None, body=None, params=None, no_auth=False):
    op = {"summary": summary, "responses": responses or {"200": {"description": "OK"}}}
    if body is not None:
        op["requestBody"] = {"required": True, "content": {
            "application/json": {"schema": body}}}
    if params:
        op["parameters"] = params
    if not no_auth:
        op["security"] = [{"bearerToken": []}, {"session": []}]
    return op


def _q(name, description, schema=None):
    return {"name": name, "in": "query", "description": description,
            "schema": schema or {"type": "string"}}


def _p(name):
    return {"name": name, "in": "path", "required": True,
            "schema": {"type": "string"}}


def _json_resp(schema, description="OK"):
    return {"description": description,
            "content": {"application/json": {"schema": schema}}}


def build_spec(server_url):
    return {
        "openapi": "3.1.0",
        "info": {
            "title": "PIScO Hub API",
            "version": API_VERSION,
            "description": "Control the PIScO Hub: discover tools provided by "
                           "registered toolboxes, create and monitor workflows, "
                           "and fetch task logs. See /api/v1/docs for the "
                           "agent guide and the toolbox.json authoring spec.",
        },
        "servers": [{"url": server_url}],
        "components": {
            "securitySchemes": {
                "bearerToken": {"type": "http", "scheme": "bearer",
                                "description": "API token from POST /api/v1/tokens"},
                "session": {"type": "apiKey", "in": "cookie", "name": "session"},
            },
            "schemas": {
                "Error": _ERROR,
                "ToolSummary": _TOOL_SUMMARY,
                "ToolDetail": _TOOL_DETAIL,
                "Task": _TASK,
                "Workflow": _WORKFLOW,
                "WorkflowCreate": _WORKFLOW_CREATE,
            },
        },
        "paths": {
            "/health": {"get": _op("Liveness check", no_auth=True)},
            "/tools": {"get": _op(
                "List tools",
                params=[_q("available", "Filter by availability (true/false)"),
                        _q("tag", "Only tools carrying this tag")],
                responses={"200": _json_resp({
                    "type": "object",
                    "properties": {"tools": {"type": "array",
                                             "items": _TOOL_SUMMARY}}})})},
            "/tools/{name}": {"get": _op(
                "Describe a tool (includes its settings JSON Schema)",
                params=[_p("name")],
                responses={"200": _json_resp(_TOOL_DETAIL),
                           "404": _json_resp(_ERROR, "Unknown tool")})},
            "/tools/refresh": {"post": _op(
                "Rescan all providers and sync tools (upsert)")},
            "/providers": {
                "get": _op("List tool providers"),
                "post": _op(
                    "Register a toolbox directory (validates toolbox.json first)",
                    body={"type": "object", "required": ["path"],
                          "properties": {"path": {"type": "string"},
                                         "name": {"type": "string"}}},
                    responses={"201": {"description": "Registered and scanned"},
                               "400": _json_resp(_ERROR, "Invalid manifest"),
                               "409": _json_resp(_ERROR, "Already registered")}),
            },
            "/providers/{id}": {"delete": _op(
                "Remove a provider; its tools become unavailable",
                params=[_p("id")])},
            "/workflows": {
                "get": _op("List workflows",
                           params=[_q("status", "Pending|Running|Finished|Failed")]),
                "post": _op(
                    "Create and queue a workflow",
                    body=_WORKFLOW_CREATE,
                    responses={"201": _json_resp(_WORKFLOW, "Created"),
                               "400": _json_resp(_ERROR, "Validation failed"),
                               "404": _json_resp(_ERROR, "Unknown tool")}),
            },
            "/workflows/{id}": {
                "get": _op("Workflow status incl. per-task detail",
                           params=[_p("id")],
                           responses={"200": _json_resp(_WORKFLOW)}),
                "delete": _op("Delete a workflow (409 while Running)",
                              params=[_p("id")]),
            },
            "/tasks/{id}": {"get": _op("Task detail incl. stored settings",
                                       params=[_p("id")])},
            "/tasks/{id}/logs": {"get": _op(
                "Task log content",
                params=[_p("id"),
                        _q("stream", "stdout (default) or stderr"),
                        _q("tail", "Return only the last N lines",
                           {"type": "integer"})])},
            "/system/load": {"get": _op("Current scheduler load vs capacity")},
            "/tokens": {
                "get": _op("List your API tokens (session auth only)"),
                "post": _op("Mint an API token; plaintext returned once "
                            "(session auth only)",
                            body={"type": "object", "required": ["name"],
                                  "properties": {"name": {"type": "string"}}}),
            },
            "/tokens/{id}": {"delete": _op("Revoke an API token",
                                           params=[_p("id")])},
        },
    }
