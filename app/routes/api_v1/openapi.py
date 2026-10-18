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

_IO_DECLARATION = {
    "type": "object",
    "description": "A path the tool reads or writes. 'settings_key' binds it "
                   "to the settings property carrying that path, which is "
                   "what lets the Hub allocate, record and chain it.",
    "properties": {
        "name": {"type": "string"},
        "kind": {"type": "string", "enum": ["directory", "file"]},
        "description": {"type": ["string", "null"]},
        "settings_key": {"type": ["string", "null"]},
        "pattern": {"type": ["string", "null"]},
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
            "inputs": {"type": "array", "items": _IO_DECLARATION},
            "outputs": {"type": "array", "items": _IO_DECLARATION},
            "requires": {"type": "array", "items": {"type": "string"},
                         "description": "Machine tags this tool needs"},
            "timeout_seconds": {"type": ["integer", "null"],
                                "description": "Seconds a task may run before "
                                               "it is stopped; null uses the "
                                               "TASK_TIMEOUT setting"},
        },
    }],
}

_ARTIFACT = {
    "type": "object",
    "description": "A path a task declared as an output.",
    "properties": {
        "name": {"type": "string"},
        "kind": {"type": "string", "enum": ["directory", "file"]},
        "path": {"type": "string"},
        "exists": {"type": ["boolean", "null"],
                   "description": "Checked once the task finishes; null before that"},
        "size_bytes": {"type": ["integer", "null"]},
        "created_at": {"type": ["string", "null"]},
    },
}

_MACHINE = {
    "type": "object",
    "properties": {
        "id": {"type": "integer"},
        "name": {"type": "string"},
        "host": {"type": ["string", "null"], "description": "null on the local machine"},
        "ssh_user": {"type": ["string", "null"]},
        "is_local": {"type": "boolean"},
        "enabled": {"type": "boolean"},
        "status": {"type": "string", "enum": ["online", "offline", "unknown"]},
        "tags": {"type": "array", "items": {"type": "string"},
                 "description": "Capabilities a tool's 'requires' is matched against"},
        "dedicated": {"type": "boolean",
                      "description": "Takes only tools that require one of its tags"},
        "max_weight": {"type": "integer"},
        "load": {"type": "integer"},
        "remaining_capacity": {"type": "integer"},
        "last_seen_at": {"type": ["string", "null"]},
        "note": {"type": ["string", "null"]},
    },
}

_TASK = {
    "type": "object",
    "properties": {
        "id": {"type": "string"},
        "workflow_id": {"type": "string"},
        "workflow_position": {"type": "integer"},
        "tool": {"type": "string"},
        "status": {"type": "string",
                   "enum": ["Pending", "Running", "Finished", "Failed", "Cancelled"]},
        "priority": {"type": "integer"},
        "weight": {"type": "integer"},
        "machine": {"type": ["string", "null"]},
        "exit_code": {"type": ["integer", "null"]},
        "started_at": {"type": ["string", "null"]},
        "completed_at": {"type": ["string", "null"]},
        "artifacts": {"type": "array", "items": _ARTIFACT},
    },
}

_WORKFLOW = {
    "type": "object",
    "properties": {
        "id": {"type": "string"},
        "name": {"type": "string"},
        "status": {"type": "string",
                   "enum": ["Pending", "Running", "Finished", "Failed", "Cancelled"]},
        "created_at": {"type": ["string", "null"]},
        "task_count": {"type": "integer"},
        "params": {"type": "object",
                   "description": "Workflow-level values task settings "
                                  "reference with $param / $format"},
        "template": {"type": ["string", "null"],
                     "description": "Name of the template it was made from"},
        "tasks": {"type": "array", "items": _TASK},
    },
}

_TEMPLATE = {
    "type": "object",
    "description": "A reusable workflow: steps whose settings reference "
                   "parameters, plus a JSON Schema for those parameters. "
                   "Shipped by a toolbox manifest (provider set, read-only) "
                   "or saved by a user.",
    "properties": {
        "id": {"type": "string"},
        "name": {"type": "string"},
        "description": {"type": ["string", "null"]},
        "provider": {"type": ["string", "null"]},
        "task_count": {"type": "integer"},
        "tools": {"type": "array", "items": {"type": "string"}},
        "name_format": {"type": ["string", "null"],
                        "description": "Default workflow name, e.g. "
                                       "\"{cruise}_{station}\""},
        "params_schema": {"type": ["object", "null"],
                          "description": "JSON Schema the params must satisfy; "
                                         "detail view only"},
        "tasks": {"type": "array", "description": "Creation-style task specs; "
                                                  "detail view only"},
        "created_at": {"type": ["string", "null"]},
    },
}

_FROM_REF = {
    "type": "object",
    "description": "Consume an earlier task's output. Resolved to a real "
                   "path at dispatch; must reference an earlier position "
                   "and an output that tool declares with a settings_key.",
    "required": ["$from"],
    "properties": {
        "$from": {
            "type": "object",
            "required": ["task", "output"],
            "properties": {
                "task": {"description": "Workflow position, or \"prev\""},
                "output": {"type": "string"},
            },
        },
    },
}

_PARAM_REF = {
    "type": "object",
    "description": "Take a workflow parameter as the value ({\"$param\": "
                   "\"cruise\"}) or compose a string from several "
                   "({\"$format\": \"{cruise}_{station}\"}). Checked "
                   "against the params when the workflow is created.",
    "properties": {"$param": {"type": "string"}, "$format": {"type": "string"}},
}

_WORKFLOW_CREATE = {
    "type": "object",
    "description": "Either 'name' + 'tasks', or 'template' + 'params' "
                   "(name optional, defaulting to the template's "
                   "name_format). An exported workflow carries both; then "
                   "the tasks are used and the template records provenance.",
    "properties": {
        "name": {"type": "string"},
        "template": {"type": "string", "description": "Template name or id"},
        "params": {"type": "object",
                   "description": "Values for $param/$format references. "
                                  "Strings are coerced to the template "
                                  "schema's declared types."},
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
                                       "string for opaque tools, omit for defaults. "
                                       "Any value may be a $from, $param or $format "
                                       "reference; declared outputs left blank get "
                                       "an allocated path.",
                    },
                    "priority": {"type": "integer"},
                    "weight": {"type": "integer"},
                    "machine": {"type": "string",
                                "description": "Pin to a machine by name; "
                                               "omit to let the scheduler choose"},
                },
            },
        },
    },
}


def _op(summary, responses=None, body=None, params=None, no_auth=False,
        scope="read"):
    responses = dict(responses or {"200": {"description": "OK"}})
    op = {"summary": summary, "responses": responses}
    if body is not None:
        op["requestBody"] = {"required": True, "content": {
            "application/json": {"schema": body}}}
    if params:
        op["parameters"] = params
    if not no_auth:
        op["security"] = [{"bearerToken": []}, {"session": []}]
        op["description"] = f"Requires token scope '{scope}' or a session."
        responses.setdefault("401", _json_resp(_ERROR, "Authentication required"))
        responses.setdefault("403", _json_resp(_ERROR, "Token scope insufficient"))
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
                "Artifact": _ARTIFACT,
                "Machine": _MACHINE,
                "Workflow": _WORKFLOW,
                "WorkflowCreate": _WORKFLOW_CREATE,
                "FromRef": _FROM_REF,
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
                "Rescan all providers and sync tools (upsert)",
                scope="operate")},
            "/providers": {
                "get": _op("List tool providers"),
                "post": _op(
                    "Register a toolbox directory (validates toolbox.json first)",
                    scope="full",
                    body={"type": "object", "required": ["path"],
                          "properties": {"path": {"type": "string"},
                                         "name": {"type": "string"}}},
                    responses={"201": {"description": "Registered and scanned"},
                               "400": _json_resp(_ERROR, "Invalid manifest"),
                               "409": _json_resp(_ERROR, "Already registered")}),
            },
            "/providers/{id}": {"delete": _op(
                "Remove a provider; its tools become unavailable",
                scope="full", params=[_p("id")])},
            "/machines": {
                "get": _op("List machines with live load and reachability",
                           responses={"200": _json_resp({
                               "type": "object",
                               "properties": {"machines": {
                                   "type": "array", "items": _MACHINE}}})}),
                "post": _op(
                    "Register a worker reachable over SSH (shared storage assumed)",
                    scope="full",
                    body={"type": "object", "required": ["name", "host"],
                          "properties": {
                              "name": {"type": "string"},
                              "host": {"type": "string"},
                              "ssh_user": {"type": "string"},
                              "max_weight": {"type": "integer"},
                              "tags": {"type": "array", "items": {"type": "string"}},
                              "dedicated": {"type": "boolean"},
                              "note": {"type": "string"}}},
                    responses={"201": _json_resp(_MACHINE, "Registered and probed"),
                               "409": _json_resp(_ERROR, "Name already used")}),
            },
            "/machines/{id}": {
                "patch": _op("Update a machine, or enable/disable it",
                             scope="full", params=[_p("id")],
                             body={"type": "object", "properties": {
                                 "name": {"type": "string"},
                                 "host": {"type": "string"},
                                 "ssh_user": {"type": "string"},
                                 "max_weight": {"type": "integer"},
                                 "enabled": {"type": "boolean"},
                                 "tags": {"type": "array",
                                          "items": {"type": "string"}},
                                 "dedicated": {"type": "boolean"},
                                 "note": {"type": "string"}}},
                             responses={"200": _json_resp(_MACHINE)}),
                "delete": _op("Remove a machine", scope="full", params=[_p("id")],
                              responses={"200": {"description": "Deleted"},
                                         "400": _json_resp(_ERROR, "Local machine"),
                                         "409": _json_resp(_ERROR, "Tasks running")}),
            },
            "/workflows": {
                "get": _op("List workflows, each with its params — one row per "
                           "run for a processing protocol",
                           params=[_q("status", "Pending|Running|Finished|Failed|Cancelled"),
                                   _q("template", "Only runs made from this template")]),
                "post": _op(
                    "Create and queue a workflow",
                    scope="operate",
                    body=_WORKFLOW_CREATE,
                    responses={"201": _json_resp(_WORKFLOW, "Created"),
                               "400": _json_resp(_ERROR, "Validation failed"),
                               "404": _json_resp(_ERROR, "Unknown tool or machine")}),
            },
            "/workflows/{id}": {
                "get": _op("Workflow status incl. per-task detail and artifacts",
                           params=[_p("id")],
                           responses={"200": _json_resp(_WORKFLOW)}),
                "delete": _op("Delete a workflow (409 while Running)",
                              scope="operate", params=[_p("id")]),
            },
            "/workflows/{id}/spec": {"get": _op(
                "The workflow as a creation request (name, params, template, "
                "tasks with stored settings) for resubmitting to POST /workflows",
                params=[_p("id")],
                responses={"200": _json_resp(_WORKFLOW_CREATE)})},
            "/workflows/{id}/cancel": {"post": _op(
                "Stop a pending or running workflow: pending tasks are cancelled, "
                "the running one is signalled (SIGTERM, then SIGKILL) on its "
                "machine and ends up Cancelled",
                scope="operate", params=[_p("id")],
                responses={"200": _json_resp(_WORKFLOW, "Cancelling; winding_down lists "
                                                        "tasks still stopping"),
                           "409": _json_resp(_ERROR, "Nothing to cancel")})},
            "/workflows/{id}/retry": {"post": _op(
                "Requeue from the first failed or cancelled task, discarding the "
                "artifacts recorded from that point on",
                scope="operate", params=[_p("id")],
                responses={"200": _json_resp(_WORKFLOW, "Requeued"),
                           "409": _json_resp(_ERROR, "Nothing to retry")})},
            "/templates": {
                "get": _op("List templates",
                           responses={"200": _json_resp({
                               "type": "object",
                               "properties": {"templates": {"type": "array",
                                                            "items": _TEMPLATE}}})}),
                "post": _op("Save a template", scope="operate",
                            body={"type": "object",
                                  "required": ["name"],
                                  "properties": {
                                      "name": {"type": "string"},
                                      "tasks": {"type": "array",
                                                "description": "Creation-style task specs"},
                                      "from_workflow": {"type": "string",
                                                        "description": "Copy this workflow's steps instead of giving tasks"},
                                      "params_schema": {"type": "object"},
                                      "description": {"type": "string"},
                                      "name_format": {"type": "string"}}},
                            responses={"201": _json_resp(_TEMPLATE, "Created"),
                                       "400": _json_resp(_ERROR, "Invalid")}),
            },
            "/templates/{id}": {
                "get": _op("Template detail incl. params_schema and tasks; "
                           "id or name", params=[_p("id")],
                           responses={"200": _json_resp(_TEMPLATE)}),
                "delete": _op("Remove a user-saved template (400 for a "
                              "provider-shipped one)", scope="operate",
                              params=[_p("id")]),
            },
            "/tasks/{id}": {"get": _op("Task detail incl. stored settings",
                                       params=[_p("id")])},
            "/tasks/{id}/artifacts": {"get": _op(
                "Paths this task declared as outputs, and whether they appeared",
                params=[_p("id")],
                responses={"200": _json_resp({
                    "type": "object",
                    "properties": {"task_id": {"type": "string"},
                                   "artifacts": {"type": "array",
                                                 "items": _ARTIFACT}}})})},
            "/tasks/{id}/logs": {"get": _op(
                "Task log content",
                params=[_p("id"),
                        _q("stream", "stdout (default) or stderr"),
                        _q("tail", "Return only the last N lines",
                           {"type": "integer"})])},
            "/system/load": {"get": _op(
                "Scheduler load vs capacity, broken down per machine")},
            "/dship/status": {"get": _op("DSHIP connection: expedition, last read, error")},
            "/dship/operations": {"get": _op(
                "Device operations from DSHIP's action log, with station, cast number and events",
                params=[{"name": "device", "in": "query", "required": False,
                         "schema": {"type": "string"}}])},
            "/dship/next-cast": {"get": _op(
                "The CTD cast being set up or under way, or the number the next one will get",
                params=[{"name": "refresh", "in": "query", "required": False, "schema": {"type": "string"}}])},
            "/dship/at": {"get": _op(
                "The cast-device operation a time belongs to (recording may start up to 45 min before it goes in)",
                params=[{"name": "time", "in": "query", "required": True, "schema": {"type": "string"}},
                        {"name": "device", "in": "query", "required": False, "schema": {"type": "string"}}])},
            "/nav/latest": {"get": _op(
                "The ship's latest NMEA fix and the state of each feed")},
            "/nav/at": {"get": _op(
                "Ship position at a time, interpolated from the track log",
                params=[{"name": "time", "in": "query", "required": True,
                         "description": "ISO 8601 (UTC unless it says otherwise) or epoch seconds",
                         "schema": {"type": "string"}}])},
            "/tokens": {
                "get": _op("List your API tokens (session auth only)"),
                "post": _op("Mint an API token; plaintext returned once "
                            "(session auth only)",
                            body={"type": "object", "required": ["name"],
                                  "properties": {
                                      "name": {"type": "string"},
                                      "scope": {"type": "string",
                                                "enum": ["read", "operate", "full"],
                                                "default": "full"}}}),
            },
            "/tokens/{id}": {"delete": _op("Revoke an API token",
                                           params=[_p("id")])},
        },
    }
