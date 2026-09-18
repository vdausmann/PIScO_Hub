# Architecture

How the Hub is put together, for someone about to change it. The user's
view is in [hub-user-guide.md](hub-user-guide.md), the API contract in
[AGENTS.md](../AGENTS.md). Everything here was read off the code on the
`toolbox-framework` branch in September 2026; file references are the
place to check when this drifts.

## One process, four threads

The Hub is a single Flask process. `app/factory.py:create_app` wires it:

1. load `.env`; refuse to start without `FLASK_SECRET_KEY`;
2. `db.create_all()` then `migrations.migrate()` — create missing tables,
   add missing columns, switch SQLite to WAL;
3. `seed_settings` — insert any `GlobalSetting` rows that do not exist yet;
4. register the blueprints (web UI, `/api/v1`, `/v` deep links);
5. `ensure_local_machine` — the row for the Hub's own host;
6. `ToolRegistry.ensure_builtin_provider()` and `scan_all()` — sync every
   provider's manifest into the `tool` and `template_workflow` tables;
7. `ViewerManager.load_viewers()` — import and register the viewer
   blueprints under `viewers/`;
8. unless `create_worker=False` (the scripts), start three daemon threads.

| Thread | Module | Cycle | Does |
|---|---|---|---|
| scheduler | `backend/task_manager.py:TaskManager` | `POLL_INTERVAL` | picks the next runnable task and starts it |
| watcher | `backend/task_watcher.py:TaskWatcher` | `POLL_INTERVAL` | notices finished processes, enforces timeouts, updates task and workflow status |
| machine monitor | `backend/machines.py:MachineMonitor` | `MACHINE_PROBE_INTERVAL` | probes every enabled machine's readiness |

The request threads are the fourth kind. All of them share one SQLite
database through SQLAlchemy sessions scoped to the app context; WAL mode
keeps readers from blocking on the writers.

Consequence: **run one process**. Under gunicorn that is `--workers 1`;
scale with `--threads`. A second process is a second scheduler, and the
same task would be started twice.

## Data model

`app/backend/models.py`. Everything is a table in one SQLite file.

```
User ──< ApiToken (scope: read|operate|full)

ToolProvider ──< Tool ──< Task >── Machine
     │                     │
     └──< TemplateWorkflow ──< TemplateTask >── Tool
                               (manifest-shipped when provider_id is set)

Workflow ──< Task ──< Artifact
  params (JSON)         name, kind, path, exists, size_bytes
  template_id/name

TemporaryWorkflow ──< TemporaryTask      builder drafts; same shape as Workflow/Task
GlobalSetting                            key, value, type, description
Viewer                                   registered viewer plugins
```

Points that matter when touching it:

- **Tools are never deleted.** A tool missing from every manifest is set
  `available = False`; finished tasks keep pointing at it. Removing a
  provider does the same and clears `provider_id`.
- **Settings are text** in `Task.settings` — JSON text for `json`/`ini`
  tools, raw text for `opaque` ones. References (`$from`, `$param`,
  `$format`) stay in the stored text; resolution happens at dispatch.
- **Workflow.params** is the JSON object the workflow was created with.
  `template_id`/`template_name` are plain columns, so history stays
  readable after a template is deleted.
- **Machine.tags** and **Tool.requires** are JSON lists; scheduling tests
  `requires ⊆ tags`.
- **Migrations** are the `_MIGRATIONS` dict in `migrations.py`: table →
  `(column, DDL)`. Adding a column to a model means adding it there too,
  nullable or with a default, because `create_all` never alters an
  existing table. There is no down-migration and no versioning.

## Providers and tools

`app/backend/tools.py`. A provider is a directory. Two layouts:

- **builtin** — the Hub's own `tools/` directory, one `config.json` per
  subdirectory (the original Hub format). Only two test fixtures live
  there now, with absolute `program_path`s from a developer machine; the
  format is kept because it still works and costs nothing.
- **toolbox** — an external repository with `toolbox.json` at its root:
  `provider {name, description, version}`, `tools [...]`, optionally
  `workflows [...]`. The manifest keys are documented in
  [AGENTS.md](../AGENTS.md#authoring-a-toolbox-toolboxjson).

`load_toolbox_manifest` validates and normalises each tool entry into a
flat descriptor: relative `program_path`, `working_directory` and
`settings_template` are resolved against the provider root; `inputs` and
`outputs` are checked (unique names, `kind` in `directory|file`, and a
`settings_key` requires a structured settings format and, when a schema
exists, a matching property — `section.key` addresses one level of
nesting for INI sections).

`scan_all` runs on startup, on `POST /api/v1/tools/refresh` and after
`POST /api/v1/providers`. Per provider it upserts tools by name: insert if
new, update if the descriptor's SHA-256 changed (or the tool was
unavailable, or moved providers), skip if unchanged. A name already seen
from another provider is skipped with an error in the report. One broken
manifest is reported and does not stop the others from loading. After the
tools, `templates.sync_provider` does the same for the manifest's
`workflows` (see below). Finally every tool that no provider mentioned is
marked unavailable.

## Settings

`app/backend/settings_engine.py`. A tool's `settings_format` decides what
its settings are and what file it gets:

| format | stored as | rendered to | notes |
|---|---|---|---|
| `opaque` | free text | `settings.cfg`, verbatim | legacy; no validation, no references, no artifacts |
| `json` | JSON object | `settings.json` | validated against `settings_schema` (JSON Schema 2020-12) if present |
| `ini` | JSON object; top-level keys are sections | `settings.ini` via configparser | option names keep their case; booleans become `1`/`0` (C++ stream extraction reads those); lists and nested objects become JSON strings; scalar top-level keys go to `[DEFAULT]` |

Default settings for a new task come from schema defaults
(`apply_defaults`, recursive through nested objects), or from the tool's
`settings_template` text.

### References

A settings value that is a one-key object with one of three keys is a
reference:

| reference | resolved from | resolved when |
|---|---|---|
| `{"$param": "name"}` | the workflow's `params`, value as it is | validated at creation with the real value; substituted at dispatch |
| `{"$format": "…{name}…"}` | `str.format_map` over `params` | same |
| `{"$from": {"task": <pos> or "prev", "output": "<name>"}}` | the upstream task's recorded artifact path | checked at creation (backwards only, output declared with a `settings_key`); resolved at dispatch, when the upstream task is `Finished` |

`$param`/`$format` are walked through objects *and* lists;
`$from` is found only inside objects (`extract_refs` does not descend
into lists). That is why the user guide says two `$from` references cannot
be combined into one list-valued setting.

### Validation at creation

`create_new_workflow` validates a structured settings object against the
schema **after** building a *validation candidate*
(`artifacts.validation_candidate`): params resolved to their real values,
every `$from` replaced by a placeholder string, every declared output
whose key is empty set to a placeholder. So a schema may mark a path
property `required` and still accept a reference or a blank output — as
long as the property is a plain `"type": "string"` without a pattern.

## The life of a task

### 1. Creation — `task_manager.create_new_workflow`

Called by `POST /api/v1/workflows`, the web builder's *Start*, and the
deprecated `/task-manager/workflow/create`. For each task spec, in order:

- the tool must exist and be available;
- settings: absent → the tool's defaults; a string → stored verbatim; an
  object → `$from` references checked, schema validated, stored as JSON;
- an optional `machine` pin must name an enabled machine;
- `priority` and `weight` default from the tool.

Everything is committed at once; a bad task rejects the whole workflow
(`400`/`404` from the API). The workflow and all tasks start `Pending`.

### 2. Scheduling — `TaskManager._process_next_step`

Every cycle:

1. take every workflow with status `Pending` (a workflow whose current
   task is running is `Running`, so it is skipped);
2. from each, the lowest-position task that is still `Pending` — one
   candidate per workflow, which is what makes execution linear inside a
   workflow;
3. sort candidates by `(priority, workflow.created_at)` — lower priority
   number first, then oldest workflow;
4. for the first candidate that some machine can take, dispatch it and
   **return**. One dispatch per cycle; the rest wait `POLL_INTERVAL`.

A machine can take a task when it is enabled, is the local machine or
probed `online`, its tags cover the tool's `requires` (or the task is
pinned to it), and `task.weight ≤ max_weight − load`, where load is the
summed weight of tasks currently `Running` there. Among candidates the
least loaded wins. A task nobody can host simply waits; nothing is logged.

### 3. Dispatch — `TaskManager._dispatch_task`

1. Workspace: `DATA_ROOT/workflows/<workflow-id>/<position>_<tool>/`,
   created if needed. On shared storage, because the tool may run
   elsewhere and is handed this path verbatim.
2. Settings resolution (`_resolve_settings`, structured formats only):
   substitute `$param`/`$format`; resolve each `$from` to the upstream
   artifact's path (the upstream task must be `Finished` and the artifact
   must not be recorded as missing; an artifact row that does not exist —
   tasks from before artifacts were tracked — is recomputed, since
   allocation is deterministic); for every declared output whose settings
   key is empty, allocate `<workspace>/<output name>` (**with a trailing
   separator for directories**, because some tools concatenate onto the
   value), create the directory, and record an `Artifact` row with
   `exists = None`.
3. Write `settings.<ext>` into the workspace.
4. Start the program (`_run_tool`): `python3 <program_path>` for
   `program_type: python`, `<program_path>` otherwise, plus the settings
   path as the single argument. Locally with `cwd = working_directory`;
   remotely as `ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new
   -o ConnectTimeout=10 user@host 'cd <wd> && exec <cmd> <settings>'`.
   stdout and stderr go to `logs/<tool>/<task-id>.log` and `.err` on the
   Hub — for a remote task, ssh forwards them. The child gets its own
   session (`start_new_session`) so a timeout can kill the whole group,
   and the `Popen` handle is kept in `process_registry`.
5. Task → `Running` with `pid` and `started_at`; workflow → `Running`.

If any of that raises, the task is `Failed` and the workflow `Failed`
(or back to `Pending` when the tool is `failed_ok`), same as a process
that exited non-zero: a task that never started must not let the next one
run against outputs that do not exist.

### 4. Watching — `TaskWatcher`

Every cycle, for every `Running` task with a pid:

- **Timeout** (`enforce_timeout`): the limit is the tool's
  `timeout_seconds`, else the `TASK_TIMEOUT` setting, else none. Past it,
  SIGTERM to the process group; 30 s later, if still there, SIGKILL. A
  note is appended to the task's `.err` (for remote tasks it says the
  remote program may survive, since only the local ssh is signalled).
- **Exit** (`check_task_status`): `Popen.poll()` on the kept handle. If
  there is no handle — the Hub was restarted while the task ran — fall
  back to `os.waitpid(WNOHANG)`, and if the process is not our child
  either, check `/proc/<pid>`; a vanished process is recorded as exit
  `-1`, because its real status is unknowable.

`finalize_task`: exit 0 → `Finished`, anything else → `Failed`;
`completed_at` set; the handle forgotten. On success every artifact row
is checked (`exists`, `size_bytes` for files); a missing declared output
is logged but does not fail the task — a later `$from` to it will fail
with a precise message. Then the workflow: if every task is terminal,
`Finished` when none failed, else `Failed`; otherwise `Failed` if this
task failed and the tool is not `failed_ok`, else back to `Pending` so the
scheduler takes the next task. Note that a `failed_ok` failure lets the
workflow continue but still counts at the end: a workflow with any failed
task ends `Failed`.

### 5. Afterwards — `backend/workflows.py`

- **Retry** (`POST /workflows/<id>/retry`, the *Retry* button): only for
  a terminal workflow with a failed task. Every task from the first
  failed one onwards is reset to `Pending` and its artifact rows deleted,
  so downstream references resolve against the new run. Stored settings
  are untouched — references are re-resolved.
- **Delete**: refused while `Running`, because the process would keep
  going with nothing tracking it. Tasks and artifact rows cascade; files
  on `DATA_ROOT` and logs stay.
- **Spec/export** (`GET /workflows/<id>/spec`): the workflow as the body
  `POST /workflows` accepts — name, params, template name, tasks with
  their *stored* settings (references intact) — so a run can be repeated
  or edited and resubmitted.
- **Protocol** (`protocol`, `sort_protocol`): rows for the run table —
  columns from the templates' schema order, then any extra keys from
  hand-made runs; start/end from the tasks' timestamps.

## Templates and parameters

`app/backend/templates.py`. A `TemplateWorkflow` is a list of
`TemplateTask`s (tool + settings text, same as a draft) plus
`params_schema` (JSON Schema for the run's `params`), `name_format`
(`"{cruise}_{station}"`) and `description`.

Two origins, one table:

- **provider-shipped**: the manifest's `workflows` list. `sync_provider`
  runs after every scan, upserting by `(provider, name)` on a content
  hash and deleting templates the manifest no longer lists. Rows with a
  `provider_id` are read-only through the Hub (`_editable` raises).
- **user-saved**: `POST /api/v1/templates` or the web editor. May start
  with no steps at all — a set of parameters whose steps are added later
  in the builder.

`find(id_or_name)` tries the id, then the name, preferring shipped over
saved on a clash. `instantiate` coerces string params to the schema's
declared types (CLI flags and HTML forms send text), fills defaults,
validates, refuses if any step's tool is unavailable, and names the
workflow from `name_format` (falling back to `<template> <timestamp>`).

Schema extensions the Hub understands beyond JSON Schema: `sticky: true`
on a property (the wizard prefills it from the last run of the same
template — cruise, instrument, operator), and `format: directory|file|date`
(the editor's type menu). `param_catalog.py` is the list of predefined
parameters the template editor offers with one click; keys there are a
convention that tools may rely on, so keep them stable.

## Machines

`app/backend/machines.py`. The `local` row is seeded at startup and cannot
be removed; it runs tasks directly. Any other machine is
`ssh_user@host`, reached with key-based, non-interactive SSH from the
account the Hub runs as. **Shared storage at identical paths** is the
standing assumption: settings files and outputs are written by the Hub
under `DATA_ROOT` and the same absolute path is passed to the remote
program. Tool code likewise has to exist at `program_path` on the
machine that runs it.

Readiness, not reachability: `probe` runs
`test -d DATA_ROOT && touch DATA_ROOT/.hub-probe-<name> && rm …` over ssh
(locally: create and check write access), with a 5 s timeout. The result
is `status = online|offline` and `last_probe_error`, so an offline
machine says what is wrong — `ssh failed: …` versus `cannot use
/mnt/pisco/hub-data: …`. The monitor probes every enabled machine every
`MACHINE_PROBE_INTERVAL` seconds in its own thread, because a hung ssh
must never stall the scheduler; registering a machine probes it once
immediately so the API answer already says.

Capacity is per machine (`max_weight`); the `MAX_WEIGHT` setting only
seeds the local one. `GET /system/load` sums enabled machines.

## Authentication and authorisation

`app/routes/api_v1/auth.py`. Two credentials:

- a **browser session** (flask-login; `User.password` is a werkzeug scrypt
  hash) — always full access, including token management;
- a **bearer token** (`Authorization: Bearer …`; SHA-256 of the plaintext
  stored in `ApiToken.token_hash`) with a scope `read < operate < full`.
  `api_auth(required)` compares ranks and answers `403` naming the scope
  the request needed. `last_used_at` is updated on every use.

Token management (`/api/v1/tokens`) is session-only, plus the
`scripts/create_api_token.py` bootstrap. Tokens travel in clear over HTTP;
the Hub is built for a trusted LAN.

The web UI routes use `@login_required` only.

## Web front end

Server-rendered Jinja with htmx for the parts that update. Pages render
once; where something is in flight they poll a `partials/…` route, and
the partial itself decides whether to keep polling (a finished workflow
stops hitting the server). Static assets are `app/static/`; `htmx.min.js`
is vendored. No build step.

The builder (`routes/workflow_builder.py`) edits a `TemporaryWorkflow`
draft — both the guided wizard and the free-form builder — and starts it
through `create_new_workflow`, so a browser-built workflow is validated
exactly like an API one. Schema'd tools get a form generated from their
JSON Schema (`partials/schema_form.html`: enums → select, booleans →
checkbox, numbers with min/max, arrays as comma-separated text, one level
of nested objects for INI sections); values a form cannot express —
references — are edited as JSON. [web-ui.md](web-ui.md) describes the
pages.

## Viewer plugins

`viewers/<name>/config.json` names a `module` inside that package and the
`blueprint` variable in it; `ViewerManager` imports and registers it and
records a `Viewer` row (`name`, `extensions`, `url`). The blueprint mounts
itself under `/viewer/<slug>` (slug = lower-cased name, spaces and
underscores to dashes). The File Explorer asks `/file-explorer/find_viewers?path=`
which viewers match a file's extension (`"*"` matches everything) and
offers them. Two ship: a generic HDF5 tree viewer and the PIScO profile
viewer (crops, per-frame views, depth/count profile).

`/v/<workflow-id>/<frame>/<index>` (`routes/deep_link.py`) is a short
redirect into the profile viewer, short because EcoTaxa stores one such
link per object in a 255-character column. It finds the workflow's
`result.h5` through the artifacts, falling back to the workspace layout.

## Storage layout

```
<checkout>/
  instance/app.db                       SQLite (DATABASE_URL); -wal/-shm beside it
  logs/<tool>/<task-id>.log|.err        task stdout/stderr, always on the Hub
  tools/<name>/config.json              builtin provider
  viewers/<name>/                       viewer plugins

DATA_ROOT/                              shared storage in a multi-machine setup
  .hub-probe-<machine>                  transient, from readiness probes
  workflows/<workflow-id>/
    <position>_<tool>/                  the task workspace
      settings.json|.ini|.cfg           what the tool was given, references resolved
      <output name>/                    Hub-allocated directory outputs
      <output name>                     Hub-allocated file outputs
```

Nothing under `DATA_ROOT` is ever deleted by the Hub. Deleting a workflow
removes rows, not files.

## Interfaces summary

| Surface | Where | Notes |
|---|---|---|
| REST API | `app/routes/api_v1/` | JSON in and out; errors `{"error": …}`; `openapi.py` builds the spec by hand — extend it when adding an endpoint |
| CLI | `hub_cli/` | a thin `requests` client over the API; no server imports |
| Web UI | `app/routes/*.py`, `app/templates/` | shares `backend/` with the API; rules about what may be retried or deleted live in `backend/workflows.py`, never in a route |
| Agent docs | `GET /api/v1/docs` | serves `AGENTS.md` from the checkout |
| Deprecated | `POST /task-manager/workflow/create` | pre-API creation route, kept for old scripts; use `/api/v1/workflows` |

## Known limitations

Things the code does not do, so nobody has to rediscover them:

- **No cancel.** There is no operation to stop a running task or
  workflow. A timeout is the only way the Hub stops a process, and for a
  remote task it can only kill its local ssh; the program on the worker
  keeps running until killed there. A worker-side daemon would fix both.
- **One dispatch per scheduler cycle.** With `POLL_INTERVAL` 5 and ten
  runnable workflows, the tenth starts about 45 s after the first.
- **`$from` is not resolved inside lists**, only inside objects.
- **A workflow with any failed task ends `Failed`**, even when the tool was
  `failed_ok` and the remaining tasks ran.
- **Tasks that outlive a Hub restart** are tracked by pid only; if the
  process is gone by the time the watcher looks, the exit code is recorded
  as `-1`.
- **`LOG_RETENTION_DAYS` is not enforced**; log files accumulate.
- **Lifecycle hooks** (`task.failed` → run a command / POST a webhook) are
  not implemented. The seam is `TaskWatcher.finalize_task`.
- **`inputs` declarations** are validated and served but not otherwise
  used; only `outputs` drive allocation and artifacts.
- **`flask-sock`** is a dependency but unused; status updates are htmx
  polling.
- **Web UI gaps**: no pages for tools, providers, machines or tokens (API
  and CLI only); the builder has no picker for `$from`/`$param`; the
  Visualization Hub only frames external pages declared in `modules.json`
  (`app/backend/modules.py`); Hardware Control renders its config
  but `parse_sensor_data` returns `{}`.
- **Stale scripts**: `scripts/add_test_task.py` imports a module that no
  longer exists; `scripts/create_workflow.py` drives the deprecated
  creation route with hard-coded credentials. Use the CLI instead.
