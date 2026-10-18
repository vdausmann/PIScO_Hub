# The `hub` command line

A thin client over the REST API (`hub_cli/`): every command is one or a
few HTTP calls, nothing runs locally. It can be used from any machine
that reaches the Hub. `hub -h` and `hub <group> <command> -h` are the
authoritative help; this page adds what the help cannot say.

## Install

Inside the Hub's checkout the CLI is part of the server's venv
(`pip install -e .`), or `hub` in the nix shell. On another machine:

```bash
pip install git+ssh://git@git.geomar.de/team-optics/plankton/PIScO_Hub.git@toolbox-framework
```

or `pip install /path/to/PIScO_Hub`. The only dependency is `requests`.
On the cluster it is installed at `/usr/local/bin/hub` on the Hub
container; under `pct exec` call it by that full path, since that PATH
has no `/usr/local/bin`.

## Connecting

Resolution order for the URL and the token: flag, environment, config
file, default.

| | flag | environment | config file key | default |
|---|---|---|---|---|
| Hub | `--url` | `PISCO_HUB_URL` | `url` | `http://localhost:5000` |
| token | `--token` | `PISCO_HUB_TOKEN` | `token` | none |

The config file is `~/.config/pisco-hub/config.json`:
`{"url": "http://10.11.11.110:5000", "token": "…"}`.

Tokens come from `scripts/create_api_token.py <user> <name> [scope]` on
the Hub or `POST /api/v1/tokens` from a browser session. What a token may
do depends on its scope (`read` → `operate` → `full`); a command the
token cannot run fails with the scope it needed.

## Global flags

`--json` prints the API's raw JSON instead of a table and goes **before
the command**: `hub --json workflow status <id>`, not
`hub workflow status --json <id>`. Every command supports it;
`workflow export` is always JSON.

Exit codes: `0` success; `1` API error or, with `--watch`, a workflow
that ended `Failed`; `2` usage error; `130` interrupted.

## Commands

### tools

```
hub tools list [--all]          available tools; --all includes unavailable ones
hub tools describe <name>       full descriptor: schema (or settings template), inputs/outputs
hub tools refresh               rescan every provider; reports added/updated/errors per provider
```

`describe --json` is what to read before writing settings by hand: the
`settings_schema` has every key, its type, default and description, and
`outputs` names what a later task can reference with `$from`.

### providers

```
hub providers list
hub providers add <path> [--name <name>]    validate toolbox.json, register, scan
hub providers remove <id>                    tools become unavailable; history kept
```

`<path>` is resolved on the Hub's host, not on yours — it is where the
toolbox lives on the Hub (and, for remote tasks, at the same path on the
workers). Needs a `full` token.

### templates

```
hub templates list                     shipped and user-saved, with their steps
hub templates describe <name|id>       parameters (type, required, default, choices) and steps
hub templates delete <id>              user-saved only
```

There is no `templates create` on the CLI; use `POST /api/v1/templates`
(with `tasks` or `from_workflow`) or the web editor.

### workflow

```
hub workflow create --template <name> [--param k=v …] [--params @file|json] [--name <n>] [--watch]
hub workflow create --file wf.json [--param k=v …] [--watch]
hub workflow create --name <n> --task tool=<t> [settings=@s.json|json|text] [priority=N] [weight=N] [machine=<m>] [--task …] [--watch]
```

Three ways to describe the run, in order of preference:

- **a template**: `--param key=value` for each parameter (text; the Hub
  coerces to the schema's types), or `--params @file.json` for all of
  them at once. The name comes from the template's name format unless
  `--name` is given.
- **a file**: the JSON body `POST /workflows` takes — `name`, optional
  `params` and `template`, `tasks` — which is exactly what
  `hub workflow export` writes, so a run can be edited and resubmitted.
  `--param` values are merged on top of the file's `params`.
- **inline tasks**: one `--task` per step, each a list of `key=value`
  words. `settings=@file` reads a file; a value that parses as JSON is
  sent as an object (validated), anything else as text (opaque tools).

`--watch` prints a status line each time any task changes state and
exits when the workflow is terminal — `0` for Finished, `1` for Failed.

```
hub workflow list [--status S] [--template <name>] [--params] [--csv]
```

Plain: id, name, status, task count, created. `--template <name>` or
`--params` turns it into the protocol — one column per workflow
parameter, in order of first appearance — and `--csv` writes that as
CSV to stdout:

```
hub workflow list --template process_profile --csv > M203-protocol.csv
```

```
hub workflow status <id> [--watch]   tasks with machine, status, exit code; artifacts and whether they exist
hub workflow retry <id> [--watch]    requeue from the first failed task
hub workflow export <id>             the run as create --file input (always JSON)
hub workflow delete <id>             refused while running
```

`status` marks an artifact that a finished task did not produce with
`(MISSING)`.

### task

```
hub task status <id>
hub task artifacts <id>
hub task logs <id> [--stderr] [--tail N] [--follow]
```

`logs` prints stdout by default. `--follow` keeps printing new output
every 2 s until the task is terminal — the equivalent of `tail -f` for a
task running on another machine. Task ids are in `workflow status`.

### machines

```
hub machines list                       with status, load, capacity, tags, and the last probe error
hub machines add <name> --host <h> [--user <u>] [--tag <t> …] [--max-weight <n>] [--dedicated]
hub machines enable <id> | disable <id>
hub machines remove <id>                refused while it has running tasks
```

`add` probes the machine straight away and prints the result; `offline`
comes with the reason (ssh failed, or the shared storage is not
writable). Tags gate placement: a tool's `requires` must be a subset of
the machine's tags. Needs a `full` token.

### system

```
hub system load        total running weight vs capacity, and the same per machine
```

## Recipes

Submit a chain from a file and follow it:

```bash
hub workflow create --file wf.json --watch
```

Repeat a run with one parameter changed:

```bash
hub workflow export <id> > run.json     # edit params in run.json
hub workflow create --file run.json --watch
```

Or, if it came from a template, just re-create with different flags:

```bash
hub workflow create --template process_profile \
    --param cruise=M203 --param station=013-01 --param frames=/mnt/pisco/incoming/M203_013-01/
```

Find out why a task failed:

```bash
hub workflow status <id>                 # which task, which machine, exit code
hub task logs <task-id> --stderr --tail 50
```

Everything the Hub knows about a tool, for writing settings:

```bash
hub --json tools describe pisco_segment | jq .settings_schema
```

Run an agent's loop by hand — discover, submit, poll, inspect — see
[AGENTS.md](../AGENTS.md#the-agent-loop); every step there has the CLI
equivalent beside it.
