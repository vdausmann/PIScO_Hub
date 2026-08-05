"""hub — command-line client for the PIScO Hub API.

Examples:
    hub tools list
    hub tools describe diplo_max_bg_calc --json
    hub providers add ~/PIScO_dev/DIPLO
    hub workflow create --name run1 --task tool=diplo_max_bg_calc settings=@s.json
    hub workflow status <id> --watch
    hub task logs <task-id> --stderr --tail 50

Exit codes: 0 success, 1 API/workflow failure, 2 usage error.
"""

import argparse
import json
import sys
import time

from .client import ApiError, HubClient
from .output import print_json, print_table

TERMINAL_STATUSES = ("Finished", "Failed")


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


# ---- workflows ------------------------------------------------------------

def cmd_workflow_create(client, args):
    if args.file:
        with open(args.file) as f:
            payload = json.load(f)
    else:
        if not args.name or not args.task:
            raise SystemExit("Either --file or both --name and --task are required")
        payload = {"name": args.name,
                   "tasks": [_parse_task_arg(t) for t in args.task]}

    data = client.post("/workflows", payload)
    if args.json and not args.watch:
        print_json(data)
        return
    print(f"Created workflow '{data['name']}' ({data['id']}) "
          f"with {data['task_count']} task(s).")
    if args.watch:
        _watch(client, data["id"], as_json=args.json)


def cmd_workflow_list(client, args):
    params = {"status": args.status} if args.status else {}
    data = client.get("/workflows", params=params)
    if args.json:
        print_json(data)
        return
    print_table(data["workflows"], [
        ("ID", "id"), ("NAME", "name"), ("STATUS", "status"),
        ("TASKS", "task_count"), ("CREATED", "created_at"),
    ])


def _print_workflow(data):
    print(f"{data['name']} ({data['id']}): {data['status']}")
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
        ("TAGS", "tags"),
    ])


def cmd_machines_add(client, args):
    payload = {"name": args.name, "host": args.host}
    if args.user:
        payload["ssh_user"] = args.user
    if args.max_weight is not None:
        payload["max_weight"] = args.max_weight
    if args.tag:
        payload["tags"] = args.tag
    data = client.post("/machines", payload)
    if args.json:
        print_json(data)
        return
    print(f"Registered '{data['name']}' ({data['host']}) - {data['status']}")


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
    p.add_argument("--file", help="JSON file with {name, tasks:[...]}")
    p.add_argument("--name")
    p.add_argument("--task", action="append", nargs="+", metavar="KEY=VALUE",
                   help="tool=NAME [settings=@file|json|text] [priority=N] "
                        "[weight=N] [machine=NAME]; repeat for multiple tasks")
    p.add_argument("--watch", action="store_true",
                   help="Wait until the workflow finishes")
    p.set_defaults(func=cmd_workflow_create)
    p = workflow.add_parser("list")
    p.add_argument("--status", choices=["Pending", "Running", "Finished", "Failed"])
    p.set_defaults(func=cmd_workflow_list)
    p = workflow.add_parser("status")
    p.add_argument("id")
    p.add_argument("--watch", action="store_true")
    p.set_defaults(func=cmd_workflow_status)
    p = workflow.add_parser("retry", help="Requeue from the first failed task")
    p.add_argument("id")
    p.add_argument("--watch", action="store_true")
    p.set_defaults(func=cmd_workflow_retry)
    p = workflow.add_parser("delete")
    p.add_argument("id")
    p.set_defaults(func=cmd_workflow_delete)

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
