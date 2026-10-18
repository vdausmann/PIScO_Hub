# PIScO Hub — Agent & Integration Guide

The PIScO Hub is a Flask server that schedules and runs data-processing
**tools** (external programs) as **workflows** (ordered task lists). Tools are
provided by **toolbox repositories** registered as providers. This document is
the contract for driving the Hub programmatically (REST API / `hub` CLI) and
for authoring a toolbox.

Machine-readable API description: `GET /api/v1/openapi.json`.

## Concept model

- **Tool** — an external program. Contract: the Hub runs
  `program <settings_file>` (prefixed with `python3` when
  `program_type == "python"`) with `cwd = working_directory`; exit code 0
  means success; stdout/stderr are captured to per-task log files.
- **Settings** — every task carries settings for its tool.
  `settings_format` decides their shape:
  - `opaque`: free text, written verbatim to a `.cfg` file (legacy tools);
  - `json`: a JSON object, optionally validated against the tool's
    `settings_schema` (JSON Schema), written as `.json`;
  - `ini`: a JSON object whose top-level keys are INI sections, rendered
    with configparser to `.ini`.
- **Workflow** — a named, ordered list of tasks. Execution is linear per
  workflow; tasks from different workflows run in parallel subject to
  capacity.
- **Outputs and artifacts** — a tool declares its `inputs`/`outputs` and
  binds each to the settings key carrying its path. The Hub allocates a
  location for any declared output the caller leaves blank, records what
  was produced as an *artifact*, and can hand that path to a later task
  (see `$from` below). Outputs without a `settings_key` are not tracked.
- **Machine** — where a task runs. The `local` machine is the Hub's own
  host; others are reached over SSH and require shared storage mounted at
  identical paths. A tool may declare `requires` tags (e.g. `["gpu"]`)
  that a machine's `tags` must cover. A machine marked `dedicated` takes
  only tools that require one of its tags, so a server set up for one job
  (EcoTaxa) does not also catch every tool that requires nothing.
- **Scheduling** — each task has a `weight`; the running weights on a
  machine stay below that machine's `max_weight`. Lower `priority` numbers
  are scheduled first. A task waits until some enabled, reachable machine
  satisfies its requirements and has room. A failed task aborts its
  workflow unless the tool is marked `failed_ok`.
- **Provider** — a directory registered with the Hub that supplies tools:
  either the builtin `tools/` directory (one `config.json` per subdirectory)
  or an external toolbox repo with a `toolbox.json` manifest.

## Authentication

Browser sessions (flask-login) and bearer tokens are both accepted:

```
Authorization: Bearer <token>
```

Mint a token with `POST /api/v1/tokens` (session required, plaintext is
returned once) or bootstrap one on the server:
`python scripts/create_api_token.py <username> <token-name> [scope]`.
Tokens travel unencrypted over plain HTTP — the Hub is designed for
trusted-LAN deployments.

Tokens carry a **scope**, and a request with too little access gets a
`403` naming what it needed:

| Scope | Can do |
|---|---|
| `read` | list and inspect everything: tools, workflows, tasks, logs, artifacts, machines |
| `operate` | also create, retry and delete workflows, and refresh tools |
| `full` | also register providers and machines (default for new tokens) |

A browser session always has full access. Give dashboards `read` and
monitoring agents `operate`, so neither can reconfigure the Hub.

## The agent loop

1. `GET /api/v1/tools` — discover available tools (names, tags, formats).
2. `GET /api/v1/tools/<name>` — read the full descriptor. For schema'd
   tools, `settings_schema` tells you exactly which settings object to
   build (types, defaults, ranges, required keys).
3. `POST /api/v1/workflows` — create and queue work:

   ```json
   {
     "name": "background run 7",
     "tasks": [
       {"tool": "diplo_max_bg_calc",
        "settings": {"input_dir": "/data/frames", "output_dir": "/data/out"}}
     ]
   }
   ```

   Settings objects are validated server-side; errors come back as
   `400 {"error": "..."}`. A string is stored verbatim (opaque tools);
   omitting `settings` uses the tool's defaults.
4. Poll `GET /api/v1/workflows/<id>` until `status` is `Finished` or
   `Failed`.
5. `GET /api/v1/tasks/<id>/logs?stream=stderr&tail=100` for diagnostics,
   and `GET /api/v1/tasks/<id>/artifacts` for what it produced.

### Chaining tasks

Instead of naming the same path twice, a settings value may reference an
earlier task's output:

```json
{"tool": "eval_compare",
 "settings": {"input_dir": {"$from": {"task": "prev", "output": "results"}}}}
```

`task` is a workflow position or `"prev"`, and must point *backwards*;
`output` must be an output that tool declares with a `settings_key`.
References are checked when the workflow is created and resolved to real
paths at dispatch, so the stored settings keep the reference and a retry
re-resolves it. Leave a declared output out of your settings entirely and
the Hub allocates a location for it under `DATA_ROOT`.

Because the value is filled in later, a schema property bound to a path
should be a plain `"type": "string"` without `pattern`/`enum` constraints.

### Workflow parameters

Facts that several tasks share — a profile's cruise and station, the
frame directory, a model path — belong to the workflow, not to each
task. Give them once as `params` and reference them from settings:

```json
{"name": "M203_012-01",
 "params": {"cruise": "M203", "station": "012-01", "frames": "/mnt/pisco/incoming/M203_012-01/"},
 "tasks": [
   {"tool": "pisco_segment", "settings": {"sourcePath": {"$param": "frames"}, "targetPath": ""}},
   {"tool": "pisco_ecotaxa_export", "settings": {"profileName": {"$format": "{cruise}_{station}"}}}
 ]}
```

`{"$param": "name"}` takes the parameter's value as it is (so a number
stays a number); `{"$format": "…{name}…"}` composes a string from
several. Like `$from`, they stay in the stored settings and are resolved
at dispatch — but because the values are known at creation, they are
validated then, with the real value substituted: an unknown parameter or
a type mismatch is a `400` naming the setting. In a run made from a
template, a setting that is exactly `{"$param": name}` for a parameter the
template declares but the run leaves out is dropped, so the tool's
default applies — that is what makes an optional parameter optional. `GET /workflows/<id>`
returns `params`; the workflow page and the export carry them too.

### Templates

A template is a task list whose settings reference parameters, plus a
JSON Schema for those parameters. Toolboxes ship them in their manifest
(`workflows`, see below); users save their own with `POST /templates`.
Instantiate one instead of writing the task list:

```json
{"template": "process_profile",
 "params": {"cruise": "M203", "station": "012-01", "lat": 38.58, "lon": -27.32}}
```

The params are checked against the template's `params_schema` (text
values are coerced to the declared types first, so `"lat": "38.58"` from a
form or a CLI flag is fine, and a `lat`/`lon` parameter — or one with
`"format": "latitude"|"longitude"` — also takes written forms such as
`"37° 20.198' N"`; defaults are filled in), the name comes from
the template's `name_format` unless `name` is given, and the response is
the usual workflow detail. `GET /templates/<name>` shows the schema, so an
agent can work out what to ask for. CLI: `hub templates describe <name>`,
`hub workflow create --template <name> --param k=v … [--params @file]`.

### Monitoring agents

Give a monitoring agent an `operate` token. A reasonable loop: poll the
workflow; on `Failed`, fetch the failed task's `stderr` tail; decide
whether the cause is transient (an SSH connection failure, exit code 255,
a timeout) or deterministic (a traceback, invalid settings). Retry once
with `POST /api/v1/workflows/<id>/retry` for the transient case, and
otherwise report the failure with its logs rather than retrying. Retry
requeues from the first failed task and discards the artifacts recorded
from that point on, so downstream tasks cannot consume results of a run
that did not complete.

`POST /api/v1/workflows/<id>/cancel` stops a workflow: pending tasks are
marked `Cancelled` at once; the running task gets SIGTERM — on its machine
too, found by the settings file only it was given — and SIGKILL if it is
still there 30 s later, and shows as `Cancelled` once it has stopped (the
response's `winding_down` lists it until then). Retry works on a cancelled
workflow the same way as on a failed one. Statuses are therefore
`Pending`, `Running`, `Finished`, `Failed`, `Cancelled`.

Equivalent CLI: `hub tools list`, `hub --json tools describe <name>`,
`hub workflow create --file wf.json --watch`, `hub task logs <id>`, and
`hub workflow list --template <name> --csv` for the protocol. `--json` is
a global flag that every command supports; configuration comes from
`--url/--token`, `$PISCO_HUB_URL`/`$PISCO_HUB_TOKEN`, or
`~/.config/pisco-hub/config.json`. Full reference: `docs/cli.md`.

## Authoring a toolbox (`toolbox.json`)

Place a `toolbox.json` at the root of your repository and register the
directory: `hub providers add /path/to/repo` (or `POST /api/v1/providers`).
The Hub re-reads it on every `POST /api/v1/tools/refresh`; changed entries
update in place, removed tools become unavailable (history is preserved).

```json
{
  "manifest_version": 1,
  "provider": {
    "name": "diplo",
    "description": "DIPLO image-processing toolbox",
    "version": "0.1.0"
  },
  "tools": [
    {
      "name": "diplo_max_bg_calc",
      "description": "Estimate a static background as the per-pixel maximum over frames.",
      "tags": ["background", "preprocessing"],
      "program_path": "run.sh",
      "program_type": "binary",
      "working_directory": ".",
      "default_priority": 2,
      "default_weight": 2,
      "failed_ok": false,
      "settings_format": "json",
      "requires": [],
      "settings_schema": {
        "type": "object",
        "required": ["input_dir", "output_dir"],
        "properties": {
          "input_dir":  {"type": "string", "description": "Directory of input frames"},
          "output_dir": {"type": "string", "description": "Where results are written"},
          "n_frames":   {"type": "integer", "minimum": 1, "default": 4}
        }
      },
      "inputs":  [{"name": "frames", "kind": "directory", "settings_key": "input_dir"}],
      "outputs": [{"name": "results", "kind": "directory", "settings_key": "output_dir"}]
    }
  ]
}
```

Rules:

- `name` must be globally unique across all providers; prefix with your
  toolbox name (`diplo_*`). Duplicates are skipped with an error.
- Required per tool: `name`, `description`, `program_path`,
  `program_type` (`python` | `binary`), `working_directory`.
- Relative paths (`program_path`, `working_directory`,
  `settings_template`) resolve against the toolbox repo root, so a
  checkout works from any location.
- Your program receives exactly one argument: the path to the rendered
  settings file. Exit 0 only if the work actually succeeded — the Hub
  trusts the exit code.
- If your tool needs its own environment (conda, venv, GPU stack), use
  `program_type: "binary"` with a `run.sh` wrapper that activates it.
- `settings_schema` is optional but strongly recommended: it gives you
  server-side validation, auto-generated web forms, and lets agents
  construct valid settings without reading your source.
- Give every input and output a `settings_key`. That is what lets the Hub
  place your results, record them, and feed them to the next tool; a
  declaration without one is documentation only. It requires structured
  settings (`json` or `ini`), and the key must exist in your schema.
- `requires` lists machine tags your tool needs (e.g. `["gpu"]`). It is
  matched against each machine's `tags`; an empty list runs anywhere.
- `timeout` is how many seconds a task may run before the Hub stops it.
  Without one, a tool that hangs holds its machine's capacity until
  somebody notices. The `TASK_TIMEOUT` setting applies to tools that
  declare none; 0 there means no limit. A stopped task fails like any
  other, with the reason appended to its error log.

### Shipping workflow templates

A manifest may also carry a `workflows` list: the standard chains built
from its tools, versioned with them. Each entry is a template — the same
shape `POST /workflows` accepts, plus a schema for its parameters:

```json
"workflows": [
  {
    "name": "process_profile",
    "description": "Correct, deconvolve, extract, classify and export one profile.",
    "name_format": "{cruise}_{station}",
    "params_schema": {
      "type": "object",
      "required": ["cruise", "station", "frames"],
      "properties": {
        "cruise":  {"type": "string", "title": "Cruise"},
        "station": {"type": "string", "title": "Station (DSHIP id)"},
        "frames":  {"type": "string", "title": "Frame directory"},
        "lat":     {"type": "number", "minimum": -90, "maximum": 90}
      }
    },
    "tasks": [
      {"tool": "pisco_segment", "settings": {"sourcePath": {"$param": "frames"}, "targetPath": ""}},
      {"tool": "pisco_ecotaxa_export", "settings": {"profileName": {"$format": "{cruise}_{station}"}}}
    ]
  }
]
```

Tools are named as usual and may come from any registered provider.
Templates are registered on every scan (`hub tools refresh`), show their
provider in `GET /templates`, and cannot be edited or deleted through
the Hub — change the manifest and refresh. An entry that fails to load
is reported in the scan result and skipped; the others still load.

## Running on other machines

The Hub can dispatch tasks to other machines over SSH. This assumes
**shared storage mounted at identical paths** on the Hub and every
worker: the Hub writes the settings file locally and passes that path to
the remote tool, and nothing is copied. Key-based, non-interactive SSH
from the Hub's account to each worker is required.

Register one on the Processing Hub's **Machines** page, with
`POST /api/v1/machines`, or
`hub machines add gpu-box --host 10.0.0.5 --user pisco --tag gpu`. The
page also edits tags, capacity and the `dedicated` flag, and lists for
every tool the machines it can run on. The
Hub probes reachability periodically; an unreachable machine is skipped
rather than failing the tasks queued for it. Pin a task to a machine with
`"machine": "<name>"` in its task spec, or leave it out and let the
scheduler pick the least-loaded machine that fits.

`POST /workflows/{id}/cancel` stops a workflow: pending tasks are
cancelled outright, a running one is signalled on its machine (SIGTERM,
then SIGKILL after a grace period) — the program on a remote worker
included, not just the local SSH.

## Ship navigation

The Hub can listen to the ship's NMEA 0183 feed (`NAV_SOURCES`, e.g.
`udp:30000` for a broadcast port, `tcp:host:port` for a server; read at
start) and keeps a track log, one CSV per UTC day under `<DATA_ROOT>/nav/`
(`NAV_LOG_DIR` to move it), a row per position fix with heading, course
and speed. `GET /nav/at?time=…` interpolates it between fixes at most
30 s apart, which is how a profile gets its position: the new-workflow
form's *Fill from ship nav* asks for the time of the first kept frame.

## DSHIP

With `DSHIP_URL` set (MSM147: `http://192.168.100.10:8080`), the Hub reads
the ship's DSHIP action log read-only, as the anonymous user its web
interface uses, every `DSHIP_POLL_INTERVAL` seconds, and keeps a snapshot
under `<DATA_ROOT>/dship/`. Device operations (`MSM147_2-9`) become a
station written `002-09`; operations of `DSHIP_CAST_DEVICE` (CTD) are
numbered in time order as CTD casts, aborted ones included.
`GET /dship/at?time=…` returns the operation a recording belongs to — it
may start up to 45 min before the device goes in — with its events
(in the water, max depth, on deck: time, position, water depth, rope
length). The new-workflow form's *Fill from DSHIP* uses it for station,
`ctd_cast`, position and date; the EcoTaxa export writes `ctd_cast` as
`sample_ctd_cast`.

## DSHIP

With `DSHIP_URL` set (MSM147: `http://192.168.100.10:8080`), the Hub reads
the ship's DSHIP action log read-only, as the anonymous user its web
interface uses, every `DSHIP_POLL_INTERVAL` seconds, and keeps a snapshot
under `<DATA_ROOT>/dship/`. Device operations (`MSM147_2-9`) become a
station written `002-09`; operations of `DSHIP_CAST_DEVICE` (CTD) are
numbered in time order as CTD casts, aborted ones included.
`GET /dship/at?time=…` returns the operation a recording belongs to — it
may start up to 45 min before the device goes in — with its events
(in the water, max depth, on deck: time, position, water depth, rope
length). The new-workflow form's *Fill from DSHIP* uses it for station,
`ctd_cast`, position and date; the EcoTaxa export writes `ctd_cast` as
`sample_ctd_cast`.

## Endpoint summary

| Method | Path | Scope | Purpose |
|---|---|---|---|
| GET | `/api/v1/health` | — | liveness (no auth) |
| GET | `/api/v1/tools` | read | list tools (`?available=`, `?tag=`) |
| GET | `/api/v1/tools/<name>` | read | full descriptor incl. schema |
| POST | `/api/v1/tools/refresh` | operate | rescan providers (upsert) |
| GET | `/api/v1/providers` | read | list toolboxes |
| POST | `/api/v1/providers` | full | register a toolbox |
| DELETE | `/api/v1/providers/<id>` | full | remove (tools become unavailable) |
| GET | `/api/v1/workflows` | read | list, each with `params` (`?status=`, `?template=`) |
| POST | `/api/v1/workflows` | operate | create + queue (`tasks`, or `template` + `params`) |
| GET | `/api/v1/workflows/<id>` | read | status detail incl. artifacts |
| GET | `/api/v1/workflows/<id>/spec` | read | the run as a creation request, for resubmitting |
| POST | `/api/v1/workflows/<id>/cancel` | operate | stop: pending tasks cancelled, the running one signalled on its machine |
| POST | `/api/v1/workflows/<id>/retry` | operate | requeue from the first failed or cancelled task |
| DELETE | `/api/v1/workflows/<id>` | operate | delete (409 while Running) |
| GET | `/api/v1/templates` | read | list templates |
| GET | `/api/v1/templates/<id or name>` | read | params schema and steps |
| POST | `/api/v1/templates` | operate | save a template (`tasks` or `from_workflow`) |
| PATCH | `/api/v1/templates/<id>` | operate | change a user-saved template: `name`, `description`, `name_format`, `params_schema`, `tasks` |
| DELETE | `/api/v1/templates/<id>` | operate | remove a user-saved template |
| GET | `/api/v1/tasks/<id>` | read | task detail incl. stored settings |
| GET | `/api/v1/tasks/<id>/artifacts` | read | what the task produced |
| GET | `/api/v1/tasks/<id>/logs` | read | `?stream=stdout|stderr&tail=N` |
| GET | `/api/v1/machines` | read | machines, load and reachability |
| POST | `/api/v1/machines` | full | register a worker |
| PATCH | `/api/v1/machines/<id>` | full | update, enable or disable |
| DELETE | `/api/v1/machines/<id>` | full | remove (409 with running tasks) |
| GET | `/api/v1/system/load` | read | load vs capacity, per machine |
| GET | `/api/v1/dship/status` | read | DSHIP connection: expedition, last read, error |
| GET | `/api/v1/dship/operations` | read | device operations with station, cast number and events (`?device=`) |
| GET | `/api/v1/dship/at?time=` | read | the cast-device operation a time belongs to |
| GET | `/api/v1/dship/status` | read | DSHIP connection: expedition, last read, error |
| GET | `/api/v1/dship/operations` | read | device operations with station, cast number and events (`?device=`) |
| GET | `/api/v1/dship/at?time=` | read | the cast-device operation a time belongs to |
| GET | `/api/v1/nav/latest` | read | the ship's latest NMEA fix and the state of each feed |
| GET | `/api/v1/nav/at?time=` | read | ship position at a time (ISO 8601 or epoch), interpolated from the track log |
| GET/POST | `/api/v1/tokens` | session | list / mint API tokens (`name`, `scope`) |
| DELETE | `/api/v1/tokens/<id>` | session | revoke one of your tokens |
| GET | `/api/v1/openapi.json` | — | OpenAPI 3.1 spec |
| GET | `/api/v1/docs` | — | this document |
