"""hub — command-line client for the PIScO Hub API.

Examples:
    hub tools list
    hub tools describe diplo_max_bg_calc --json
    hub providers add ~/PIScO_dev/DIPLO
    hub workflow create --name run1 --task tool=diplo_max_bg_calc settings=@s.json
    hub workflow create --template process_profile --param cruise=M203 --param station=012-01
    hub workflow status <id> --watch
    hub templates describe process_profile
    hub task logs <task-id> --stderr --tail 50

Exit codes: 0 success, 1 API/workflow failure, 2 usage error.
"""

import argparse
import csv
import json
import sys
import time

from .client import ApiError, HubClient
from .output import print_json, print_table

TERMINAL_STATUSES = ("Finished", "Failed", "Cancelled")


def _parse_task_arg(raw):
    """Parse a --task argument of the form key=value[,...]:
    tool=NAME settings=@file.json|'{...}'|raw-text priority=N weight=N."""
    spec = {}
    for part in raw:
        if "=" not in part:
            raise SystemExit(f"Invalid --task component '{part}' (expected key=value)")
        key, value = part.split("=", 1)
        if key == "settings":
            if value.startswith("@"):
                with open(value[1:]) as f:
                    value = f.read()
            try:
                spec["settings"] = json.loads(value)
            except ValueError:
                spec["settings"] = value
        elif key in ("priority", "weight"):
            spec[key] = int(value)
        elif key in ("tool", "machine"):
            spec[key] = value
        else:
            raise SystemExit(f"Unknown --task key '{key}'")
    if "tool" not in spec:
        raise SystemExit("Each --task needs tool=<name>")
    return spec


# ---- tools ----------------------------------------------------------------

def cmd_tools_list(client, args):
    params = {} if args.all else {"available": "true"}
    data = client.get("/tools", params=params)
    if args.json:
        print_json(data)
        return
    print_table(data["tools"], [
        ("NAME", "name"), ("PROVIDER", "provider"), ("FORMAT", "settings_format"),
        ("AVAILABLE", "available"), ("TAGS", "tags"), ("DESCRIPTION", "description"),
    ])


def cmd_tools_describe(client, args):
    data = client.get(f"/tools/{args.name}")
    if args.json:
        print_json(data)
        return
    for key in ("name", "provider", "version", "description", "program_type",
                "settings_format", "default_priority", "default_weight",
                "failed_ok", "available"):
        print(f"{key:18} {data.get(key)}")
    if data.get("settings_schema"):
        print("settings_schema:")
        print(json.dumps(data["settings_schema"], indent=2))
    elif data.get("settings_template"):
        print("settings_template:")
        print(data["settings_template"])


def cmd_tools_refresh(client, args):
    data = client.post("/tools/refresh")
    if args.json:
        print_json(data)
        return
    for name, result in data.get("providers", {}).items():
        print(f"[{name}] added: {len(result.get('added', []))}, "
              f"updated: {len(result.get('updated', []))}, "
              f"unchanged: {len(result.get('unchanged', []))}, "
              f"errors: {len(result.get('errors', []))}")
        for error in result.get("errors", []):
            print(f"  ! {error}")
    if data.get("deactivated"):
        print(f"deactivated: {', '.join(data['deactivated'])}")


# ---- providers ------------------------------------------------------------

def cmd_providers_list(client, args):
    data = client.get("/providers")
    if args.json:
        print_json(data)
        return
    print_table(data["providers"], [
        ("ID", "id"), ("NAME", "name"), ("TOOLS", "tool_count"),
        ("BUILTIN", "builtin"), ("PATH", "path"),
    ])


def cmd_providers_add(client, args):
    payload = {"path": args.path}
    if args.name:
        payload["name"] = args.name
    data = client.post("/providers", payload)
    if args.json:
        print_json(data)
        return
    provider = data["provider"]
    scan = data.get("scan", {})
    print(f"Registered provider '{provider['name']}' ({provider['path']})")
    print(f"Tools added: {', '.join(scan.get('added', [])) or '(none)'}")
    for error in scan.get("errors", []):
        print(f"  ! {error}")


def cmd_providers_remove(client, args):
    data = client.delete(f"/providers/{args.id}")
    if args.json:
        print_json(data)
        return
    print(f"Removed. Deactivated tools: "
          f"{', '.join(data.get('deactivated_tools', [])) or '(none)'}")


# ---- templates ------------------------------------------------------------

def cmd_templates_list(client, args):
    data = client.get("/templates")
    if args.json:
        print_json(data)
        return
    rows = [dict(t, tools=" > ".join(t["tools"])) for t in data["templates"]]
    print_table(rows, [
        ("NAME", "name"), ("PROVIDER", "provider"), ("STEPS", "tools"),
        ("DESCRIPTION", "description"),
    ])


def cmd_templates_describe(client, args):
    data = client.get(f"/templates/{args.name}")
    if args.json:
        print_json(data)
        return
    for key in ("id", "name", "provider", "description", "name_format"):
        print(f"{key:12} {data.get(key)}")
    schema = data.get("params_schema") or {}
    required = set(schema.get("required", []))
    print("parameters:")
    for name, prop in schema.get("properties", {}).items():
        flags = []
        if name in required:
            flags.append("required")
        if "default" in prop:
            flags.append(f"default={prop['default']!r}")
        if prop.get("enum"):
            flags.append("one of " + ", ".join(map(str, prop["enum"])))
        detail = f"  ({', '.join(flags)})" if flags else ""
        print(f"  {name:14} {prop.get('type', 'any'):8} "
              f"{prop.get('description') or prop.get('title') or ''}{detail}")
    if not schema.get("properties"):
        print("  (none)")
    print("steps:")
    for i, task in enumerate(data.get("tasks", [])):
        print(f"  {i}  {task['tool']}")


def cmd_templates_delete(client, args):
    data = client.delete(f"/templates/{args.id}")
    print_json(data) if args.json else print("Deleted.")


# ---- workflows ------------------------------------------------------------

def _parse_params(args):
    """--params @file.json plus any --param key=value (text; the server
    coerces to the template's declared types)."""
    params = {}
    if args.params:
        source = args.params
        if source.startswith("@"):
            with open(source[1:]) as f:
                source = f.read()
        params.update(json.loads(source))
    for item in args.param or []:
        if "=" not in item:
            raise SystemExit(f"--param expects key=value, got '{item}'")
        key, value = item.split("=", 1)
        params[key] = value
    return params


def cmd_workflow_create(client, args):
    if args.file:
        with open(args.file) as f:
            payload = json.load(f)
    elif args.template:
        payload = {"template": args.template}
        if args.name:
            payload["name"] = args.name
    else:
        if not args.name or not args.task:
            raise SystemExit("Either --file, --template, or both --name and "
                             "--task are required")
        payload = {"name": args.name,
                   "tasks": [_parse_task_arg(t) for t in args.task]}
    params = _parse_params(args)
    if params:
        payload["params"] = {**payload.get("params", {}), **params}

    data = client.post("/workflows", payload)
    if args.json and not args.watch:
        print_json(data)
        return
    print(f"Created workflow '{data['name']}' ({data['id']}) "
          f"with {data['task_count']} task(s).")
    if args.watch:
        _watch(client, data["id"], as_json=args.json)


def cmd_workflow_list(client, args):
    params = {}
    if args.status:
        params["status"] = args.status
    if args.template:
        params["template"] = args.template
    data = client.get("/workflows", params=params)
    if args.json:
        print_json(data)
        return
    columns = [("ID", "id"), ("NAME", "name"), ("STATUS", "status"),
               ("TASKS", "task_count"), ("CREATED", "created_at")]
    rows = data["workflows"]
    if args.params or args.template:
        # The protocol view: one column per parameter, in order of first
        # appearance across the runs.
        keys = []
        for row in rows:
            for key in row.get("params", {}):
                if key not in keys:
                    keys.append(key)
        columns = [("NAME", "name"), ("STATUS", "status"), ("CREATED", "created_at")]
        columns += [(key, key) for key in keys]
        rows = [dict(row, **{k: row.get("params", {}).get(k, "") for k in keys})
                for row in rows]
    if args.csv:
        writer = csv.writer(sys.stdout)
        writer.writerow([label for label, _ in columns])
        for row in rows:
            writer.writerow([row.get(field, "") for _, field in columns])
        return
    print_table(rows, columns)


def _print_workflow(data):
    print(f"{data['name']} ({data['id']}): {data['status']}")
    if data.get("template"):
        print(f"template: {data['template']}")
    for key, value in (data.get("params") or {}).items():
        print(f"  {key} = {value}")
    print_table(data["tasks"], [
        ("POS", "workflow_position"), ("TOOL", "tool"), ("STATUS", "status"),
        ("MACHINE", "machine"), ("EXIT", "exit_code"), ("TASK ID", "id"),
    ])
    for task in data["tasks"]:
        for artifact in task.get("artifacts", []):
            flag = "" if artifact.get("exists") is not False else "  (MISSING)"
            print(f"  {task['tool']} -> {artifact['name']}: "
                  f"{artifact['path']}{flag}")


def _watch(client, workflow_id, as_json=False, interval=3):
    last_line = ""
    while True:
        data = client.get(f"/workflows/{workflow_id}")
        statuses = ", ".join(f"{t['tool']}:{t['status']}" for t in data["tasks"])
        line = f"[{data['status']}] {statuses}"
        if line != last_line:
            print(line)
            last_line = line
        if data["status"] in TERMINAL_STATUSES:
            if as_json:
                print_json(data)
            sys.exit(0 if data["status"] == "Finished" else 1)
        time.sleep(interval)


def cmd_workflow_status(client, args):
    if args.watch:
        _watch(client, args.id, as_json=args.json)
    data = client.get(f"/workflows/{args.id}")
    if args.json:
        print_json(data)
        return
    _print_workflow(data)


def cmd_workflow_retry(client, args):
    data = client.post(f"/workflows/{args.id}/retry")
    if args.json and not args.watch:
        print_json(data)
        return
    print(f"Requeued '{data['name']}' ({data['id']}).")
    if args.watch:
        _watch(client, data["id"], as_json=args.json)


def cmd_workflow_cancel(client, args):
    data = client.post(f"/workflows/{args.id}/cancel")
    if args.json and not args.watch:
        print_json(data)
        return
    winding = data.get("winding_down", [])
    print(f"Cancelled '{data['name']}' ({data['id']})."
          + (f" {len(winding)} task(s) still stopping." if winding else ""))
    if args.watch and winding:
        _watch_tasks_stopped(client, data["id"])


def _watch_tasks_stopped(client, workflow_id, interval=3):
    """Wait until no task of the workflow is Running any more."""
    while True:
        data = client.get(f"/workflows/{workflow_id}")
        running = [t["tool"] for t in data["tasks"] if t["status"] == "Running"]
        if not running:
            print("Stopped.")
            return
        print(f"waiting for {', '.join(running)} to stop…")
        time.sleep(interval)


def cmd_workflow_export(client, args):
    """Always JSON: the point is a file for `workflow create --file`."""
    print_json(client.get(f"/workflows/{args.id}/spec"))


def cmd_workflow_delete(client, args):
    data = client.delete(f"/workflows/{args.id}")
    print_json(data) if args.json else print("Deleted.")


# ---- tasks ----------------------------------------------------------------

def cmd_task_status(client, args):
    data = client.get(f"/tasks/{args.id}")
    if args.json:
        print_json(data)
        return
    for key in ("id", "workflow_id", "tool", "status", "exit_code",
                "started_at", "completed_at"):
        print(f"{key:14} {data.get(key)}")


def cmd_task_artifacts(client, args):
    data = client.get(f"/tasks/{args.id}/artifacts")
    if args.json:
        print_json(data)
        return
    print_table(data["artifacts"], [
        ("NAME", "name"), ("KIND", "kind"), ("EXISTS", "exists"),
        ("SIZE", "size_bytes"), ("PATH", "path"),
    ])


def cmd_task_logs(client, args):
    params = {"stream": "stderr" if args.stderr else "stdout"}
    if args.tail is not None:
        params["tail"] = args.tail

    if not args.follow:
        data = client.get(f"/tasks/{args.id}/logs", params=params)
        if args.json:
            print_json(data)
        else:
            print(data["content"], end="")
        return

    printed = 0
    while True:
        data = client.get(f"/tasks/{args.id}/logs",
                          params={"stream": params["stream"]})
        content = data["content"]
        if len(content) > printed:
            print(content[printed:], end="", flush=True)
            printed = len(content)
        status = client.get(f"/tasks/{args.id}")["status"]
        if status in TERMINAL_STATUSES:
            return
        time.sleep(2)


# ---- system ---------------------------------------------------------------

def cmd_system_load(client, args):
    data = client.get("/system/load")
    if args.json:
        print_json(data)
        return
    print(f"total load {data['load']}/{data['max_weight']} "
          f"(remaining {data['remaining_capacity']})")
    print_table(data.get("machines", []), [
        ("MACHINE", "name"), ("STATUS", "status"), ("ENABLED", "enabled"),
        ("LOAD", "load"), ("CAPACITY", "max_weight"), ("TAGS", "tags"),
    ])


# ---- machines --------------------------------------------------------------

def cmd_machines_list(client, args):
    data = client.get("/machines")
    if args.json:
        print_json(data)
        return
    print_table(data["machines"], [
        ("ID", "id"), ("NAME", "name"), ("HOST", "host"), ("STATUS", "status"),
        ("ENABLED", "enabled"), ("LOAD", "load"), ("CAPACITY", "max_weight"),
        ("TAGS", "tags"), ("DEDICATED", "dedicated"),
    ])
    for machine in data["machines"]:
        if machine.get("last_probe_error"):
            print(f"  {machine['name']}: {machine['last_probe_error']}")


def cmd_machines_add(client, args):
    payload = {"name": args.name, "host": args.host}
    if args.user:
        payload["ssh_user"] = args.user
    if args.max_weight is not None:
        payload["max_weight"] = args.max_weight
    if args.tag:
        payload["tags"] = args.tag
    if args.dedicated:
        payload["dedicated"] = True
    data = client.post("/machines", payload)
    if args.json:
        print_json(data)
        return
    print(f"Registered '{data['name']}' ({data['host']}) - {data['status']}")
    if data.get("last_probe_error"):
        print(f"  {data['last_probe_error']}")


def cmd_machines_remove(client, args):
    data = client.delete(f"/machines/{args.id}")
    print_json(data) if args.json else print("Removed.")


def cmd_machines_set(client, args):
    payload = {"enabled": args.cmd == "enable"}
    data = client.request("PATCH", f"/machines/{args.id}", json=payload)
    if args.json:
        print_json(data)
        return
    print(f"'{data['name']}' is now "
          f"{'enabled' if data['enabled'] else 'disabled'}.")


def build_parser():
    parser = argparse.ArgumentParser(
        prog="hub", description="Command-line client for the PIScO Hub API.")
    parser.add_argument("--url", help="Hub base URL (default: $PISCO_HUB_URL "
                                      "or http://localhost:5000)")
    parser.add_argument("--token", help="API token (default: $PISCO_HUB_TOKEN)")
    parser.add_argument("--json", action="store_true",
                        help="Print raw JSON responses")
    sub = parser.add_subparsers(dest="group", required=True)

    tools = sub.add_parser("tools", help="List and inspect tools").add_subparsers(
        dest="cmd", required=True)
    p = tools.add_parser("list", help="List tools")
    p.add_argument("--all", action="store_true", help="Include unavailable tools")
    p.set_defaults(func=cmd_tools_list)
    p = tools.add_parser("describe", help="Show a tool incl. its settings schema")
    p.add_argument("name")
    p.set_defaults(func=cmd_tools_describe)
    p = tools.add_parser("refresh", help="Rescan all providers")
    p.set_defaults(func=cmd_tools_refresh)

    providers = sub.add_parser("providers", help="Manage tool providers")\
        .add_subparsers(dest="cmd", required=True)
    p = providers.add_parser("list")
    p.set_defaults(func=cmd_providers_list)
    p = providers.add_parser("add", help="Register a toolbox directory")
    p.add_argument("path")
    p.add_argument("--name")
    p.set_defaults(func=cmd_providers_add)
    p = providers.add_parser("remove")
    p.add_argument("id", type=int)
    p.set_defaults(func=cmd_providers_remove)

    workflow = sub.add_parser("workflow", help="Create and monitor workflows")\
        .add_subparsers(dest="cmd", required=True)
    p = workflow.add_parser("create")
    p.add_argument("--file", help="JSON file with {name, params?, tasks:[...]} "
                                  "(what 'workflow export' writes)")
    p.add_argument("--template", help="Instantiate a template by name or id")
    p.add_argument("--name", help="Workflow name (a template supplies a "
                                  "default from its name_format)")
    p.add_argument("--task", action="append", nargs="+", metavar="KEY=VALUE",
                   help="tool=NAME [settings=@file|json|text] [priority=N] "
                        "[weight=N] [machine=NAME]; repeat for multiple tasks")
    p.add_argument("--param", action="append", metavar="KEY=VALUE",
                   help="Workflow parameter; repeat for several")
    p.add_argument("--params", metavar="@FILE|JSON",
                   help="Workflow parameters as a JSON object")
    p.add_argument("--watch", action="store_true",
                   help="Wait until the workflow finishes")
    p.set_defaults(func=cmd_workflow_create)
    p = workflow.add_parser("list")
    p.add_argument("--status", choices=["Pending", "Running", "Finished", "Failed", "Cancelled"])
    p.add_argument("--template", help="Only runs made from this template; "
                                      "shows their parameters as columns")
    p.add_argument("--params", action="store_true",
                   help="Show workflow parameters as columns (the protocol)")
    p.add_argument("--csv", action="store_true", help="Write CSV instead of a table")
    p.set_defaults(func=cmd_workflow_list)
    p = workflow.add_parser("status")
    p.add_argument("id")
    p.add_argument("--watch", action="store_true")
    p.set_defaults(func=cmd_workflow_status)
    p = workflow.add_parser("cancel", help="Stop a pending or running workflow "
                                           "(SIGTERM, then SIGKILL after the grace period)")
    p.add_argument("id")
    p.add_argument("--watch", action="store_true",
                   help="Wait until the running task has stopped")
    p.set_defaults(func=cmd_workflow_cancel)
    p = workflow.add_parser("retry", help="Requeue from the first failed or cancelled task")
    p.add_argument("id")
    p.add_argument("--watch", action="store_true")
    p.set_defaults(func=cmd_workflow_retry)
    p = workflow.add_parser("export", help="Print the workflow as JSON that "
                                           "'create --file' accepts")
    p.add_argument("id")
    p.set_defaults(func=cmd_workflow_export)
    p = workflow.add_parser("delete")
    p.add_argument("id")
    p.set_defaults(func=cmd_workflow_delete)

    templates = sub.add_parser("templates", help="Reusable workflows and "
                               "the parameters they take").add_subparsers(
        dest="cmd", required=True)
    p = templates.add_parser("list")
    p.set_defaults(func=cmd_templates_list)
    p = templates.add_parser("describe", help="Parameters and steps of a template")
    p.add_argument("name")
    p.set_defaults(func=cmd_templates_describe)
    p = templates.add_parser("delete", help="Remove a user-saved template")
    p.add_argument("id")
    p.set_defaults(func=cmd_templates_delete)

    task = sub.add_parser("task", help="Inspect tasks").add_subparsers(
        dest="cmd", required=True)
    p = task.add_parser("status")
    p.add_argument("id")
    p.set_defaults(func=cmd_task_status)
    p = task.add_parser("artifacts", help="Outputs this task declared")
    p.add_argument("id")
    p.set_defaults(func=cmd_task_artifacts)
    p = task.add_parser("logs")
    p.add_argument("id")
    p.add_argument("--stderr", action="store_true")
    p.add_argument("--tail", type=int)
    p.add_argument("--follow", action="store_true")
    p.set_defaults(func=cmd_task_logs)

    machines = sub.add_parser("machines", help="Machines tasks can run on")\
        .add_subparsers(dest="cmd", required=True)
    p = machines.add_parser("list")
    p.set_defaults(func=cmd_machines_list)
    p = machines.add_parser("add", help="Register a worker reachable over SSH")
    p.add_argument("name")
    p.add_argument("--host", required=True)
    p.add_argument("--user", help="SSH user")
    p.add_argument("--max-weight", type=int, dest="max_weight")
    p.add_argument("--tag", action="append",
                   help="Capability tag, e.g. gpu; repeat for several")
    p.add_argument("--dedicated", action="store_true",
                   help="Take only tools that require one of its tags")
    p.set_defaults(func=cmd_machines_add)
    p = machines.add_parser("remove")
    p.add_argument("id", type=int)
    p.set_defaults(func=cmd_machines_remove)
    for action in ("enable", "disable"):
        p = machines.add_parser(action)
        p.add_argument("id", type=int)
        p.set_defaults(func=cmd_machines_set)

    system = sub.add_parser("system", help="Server status").add_subparsers(
        dest="cmd", required=True)
    p = system.add_parser("load")
    p.set_defaults(func=cmd_system_load)

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()
    client = HubClient(url=args.url, token=args.token)
    try:
        args.func(client, args)
    except ApiError as e:
        print(f"Error: {e.message}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
