# PIScO Hub

A local server that runs and schedules the PIScO image-processing pipeline.
The Hub is the **framework**: it stores workflows, schedules tasks, and runs
tools as subprocesses. The tools themselves come from **toolbox
repositories** (first one: [DIPLO](../DIPLO)) that plug into the Hub via a
manifest — the Hub contains no processing code of its own.

Detailed API + toolbox-authoring contract: [AGENTS.md](AGENTS.md)
(also served at `/api/v1/docs`). OpenAPI spec: `/api/v1/openapi.json`.
Procedure and pitfalls for wiring up a new toolbox:
[docs/toolbox-integration.md](docs/toolbox-integration.md).

## How a run works

```
toolbox repo (e.g. DIPLO)                    PIScO Hub                        interfaces
┌──────────────────────┐    register    ┌──────────────────────┐    ┌─────────────────────┐
│ toolbox.json         │ ─────────────► │ Provider registry    │ ◄─ │ Web UI (htmx)       │
│  - N tool defs       │  scan/upsert   │  (Tool table)        │    │ hub CLI             │
│  - settings schemas  │                │                      │    │ REST /api/v1        │
│ run.sh + runner      │                │ Workflow = ordered   │    │ AI agents (schema-  │
└──────────┬───────────┘                │ list of tasks        │    │  driven via API)    │
           │                            │                      │    └─────────────────────┘
           │   subprocess:              │ TaskManager thread:  │
           │   run.sh <settings_file>   │  weight/priority     │
           └─────────────◄──────────────│  scheduling          │
               exit 0 = success         │ TaskWatcher thread:  │
               logs → logs/<tool>/      │  reaps PIDs, status  │
                                        └──────────────────────┘
```

1. A toolbox directory is registered once: `hub providers add ~/PIScO_dev/DIPLO`.
   The Hub reads its `toolbox.json` and upserts the declared tools
   (re-sync anytime with `hub tools refresh` — edits update, removed tools
   become unavailable, history is never lost).
2. A **workflow** (named, ordered list of tasks) is created in the web
   builder, via `POST /api/v1/workflows`, or `hub workflow create`. Each
   task = one tool + settings. Tools with a JSON Schema get validated
   settings and an auto-generated web form; legacy tools keep a free-text
   settings blob.
3. The scheduler dispatches tasks: one at a time per workflow, in parallel
   across workflows, capped by task `weight` vs the `MAX_WEIGHT` setting
   (lower `priority` number = scheduled first).
4. Each task runs as `program <settings_file>` with logs captured to
   `logs/<tool>/<task_id>.log|.err`; exit 0 → Finished. A failed task
   aborts its workflow unless the tool is `failed_ok`.

## Interfaces

| Interface | What | Auth |
|---|---|---|
| Web UI (`:5000`) | dashboard, workflow builder (auto-forms), file explorer, HDF viewers | login session |
| REST `/api/v1` | tools, providers, workflows, tasks/logs, tokens, system load | session or `Authorization: Bearer <token>` |
| `hub` CLI | same operations from the terminal, `--json` everywhere | `$PISCO_HUB_TOKEN` |
| AI agents | discover tools + settings schemas via API, then drive it like the CLI | token |

CLI quick reference (full help: `hub -h`):

```
hub tools list | describe <name> | refresh
hub providers list | add <path> | remove <id>
hub workflow create --file wf.json [--watch] | list | status <id> | delete <id>
hub task status <id> | logs <id> [--stderr] [--follow]
hub system load
```

## Current tools

**builtin** (legacy `tools/<name>/config.json`, opaque settings):

| Tool | Purpose |
|---|---|
| `Deconvolution` | PIScO deconvolution (external checkout, run.sh) |
| `Segmenter` | PIScO C++ segmenter |
| `test_load` / `test_segmenter` | test fixtures |

**diplo** provider — *prototype, not yet committed to DIPLO*. A manifest
exposing three tools (`diplo_run_workflow`, `diplo_max_bg_calc`,
`diplo_pisco_full`) was validated end-to-end against this Hub, but the
DIPLO-side entry point still has to be switched from the scratch runner
to DIPLO's own headless loader. See
[docs/toolbox-integration.md](docs/toolbox-integration.md) for the status
and the plan.

## Setup / running

```bash
nix develop                          # dev shell with all Hub deps
python scripts/setup_env.py          # one-time: .env with secret + DB url
python scripts/create_user.py        # one-time: web login
python scripts/create_api_token.py <user> <name>   # token for CLI/agents
run_server                           # start on 0.0.0.0:5000
```

DIPLO tools need a Python env with DIPLO's deps (PySide6, torch, opencv, …).
`run.sh` picks, in order: `$DIPLO_PYTHON` → conda env `diplo` → `python3`.

## Repo layout

```
app/factory.py            app wiring: DB, migrations, blueprints, registry, workers
app/backend/
  tools.py                provider registry (toolbox.json / legacy config.json, upsert)
  settings_engine.py      settings validation (JSON Schema) + rendering (json/ini/opaque)
  task_manager.py         scheduler thread + dispatch
  task_watcher.py         PID reaper, task/workflow status
  migrations.py           idempotent SQLite column migrations
  models.py               Workflow/Task, Tool, ToolProvider, ApiToken, …
app/routes/api_v1/        REST API (+ openapi.py spec)
hub_cli/                  the `hub` CLI (argparse + requests)
tools/                    builtin (legacy) tool definitions
viewers/                  file-viewer plugins (Flask blueprints)
AGENTS.md                 API + toolbox authoring contract
```

## Not yet implemented / next-step candidates

- **Hooks** (planned Phase 7): `toolbox.json` declares handlers for
  `task.finished/failed`, `workflow.finished/failed` → run a command or
  POST a webhook. Seam: `TaskWatcher.finalize_task`.
- **Data flow between tasks**: steps are chained only by paths inside
  settings; no output→input passing. The `inputs`/`outputs` manifest
  fields are declared but not yet wired to anything.
- **Expose DIPLO's benchmarking layer as tools**: DIPLO already implements
  `diplo_lib/eval/` (metric registry, dataset runner, EcoTaxa export,
  candidate comparison). None of it is surfaced through the Hub yet —
  adding manifest entries for those entry points is the obvious next step.
- **Reconcile the DIPLO runner**: `diplo_run.py` here predates DIPLO's own
  `diplo_lib/eval/workflow.py` + `runner.py`, which do the same job Qt-free.
  The manifest should call those instead of the standalone script.
- Namespaced tool names, websocket status updates (flask-sock is wired but
  unused).
