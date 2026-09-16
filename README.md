# PIScO Hub

A small server that schedules and runs the PIScO image-processing pipeline.
The Hub is the **framework**: it stores workflows, decides where and when
each step runs, keeps the logs, and records what was produced. The
processing itself lives in **toolbox repositories** (the C++ segmenter, the
deconvolution, the ViT classifier, the EcoTaxa exporter, …) that plug into
the Hub through a `toolbox.json` manifest. The Hub contains no processing
code of its own.

```
toolbox repos                          PIScO Hub                           interfaces
┌──────────────────────┐  register  ┌──────────────────────────┐   ┌──────────────────────┐
│ toolbox.json         │ ─────────► │ Tool registry            │ ◄─│ Web UI (htmx)        │
│  - tool definitions  │  scan      │ Templates + parameters   │   │ hub CLI              │
│  - settings schemas  │            │ Workflow = ordered tasks │   │ REST /api/v1         │
│  - workflow templates│            │                          │   │ AI agents (via API)  │
│ run.sh + program     │            │ scheduler thread         │   └──────────────────────┘
└──────────┬───────────┘            │ watcher thread           │
           │  program <settings>    │ machine monitor thread   │
           └────────────◄───────────│                          │
             local or over ssh      └────────────┬─────────────┘
             exit 0 = success                    │ shared storage: settings, outputs
                                                 ▼
                                    DATA_ROOT/workflows/<id>/<n>_<tool>/
```

## What it does

- **Tools** are external programs, declared in a manifest with a JSON
  Schema for their settings. The Hub runs each as `program <settings_file>`
  and trusts the exit code.
- **Workflows** are ordered lists of tasks (tool + settings). Steps run
  strictly in order within a workflow; different workflows run in parallel.
- **Machines**: the Hub's own host plus workers reached over SSH. Tasks are
  placed by capability tags (`gpu`, `ecotaxa`, …) and weight against each
  machine's capacity. Nothing is copied — every machine sees the same
  shared storage at the same path.
- **Data flow**: a tool declares its outputs; the Hub allocates a location
  for each, records it as an *artifact*, and a later task references it
  with `{"$from": {"task": 0, "output": "results"}}`.
- **Parameters and templates**: values several steps share (cruise,
  station, frame directory) live once in the workflow's `params`. A
  template is a task list plus a schema for those parameters; toolboxes
  ship the standard chains, users save their own.
- **Protocol**: every run as a row, its parameters as columns, exportable
  as CSV.
- **Three equal front ends**: everything the web UI does, the REST API and
  the `hub` CLI do too.

## Documentation

| Read this | when you want to |
|---|---|
| [docs/getting-started.md](docs/getting-started.md) | install the Hub on a laptop or server, run it, and see a two-step demo chain go through |
| [docs/hub-user-guide.md](docs/hub-user-guide.md) | process a real PIScO profile end to end, from raw frames to an EcoTaxa project |
| [docs/web-ui.md](docs/web-ui.md) | know what each page of the web interface does |
| [docs/cli.md](docs/cli.md) | drive the Hub from a terminal; every `hub` command and flag |
| [AGENTS.md](AGENTS.md) | drive it programmatically: the REST API, settings references, token scopes; also the manifest contract for toolbox authors (served at `/api/v1/docs`; OpenAPI at `/api/v1/openapi.json`) |
| [docs/toolbox-integration.md](docs/toolbox-integration.md) | wrap an existing repository as a toolbox — the procedure and the traps |
| [docs/architecture.md](docs/architecture.md) | change the Hub itself: components, data model, the life of a task, storage layout, limitations |
| [docs/cluster-runbook.md](docs/cluster-runbook.md) | deploy on the production Proxmox hosts (condensed); [docs/deployment.md](docs/deployment.md) has the same ground with the reasoning |
| [MANIFEST.md](MANIFEST.md) | understand why the Hub exists and where its boundaries are |

Research and integration notes that live beside the code:
[pipeline comparison](docs/pipeline-comparison-spec.md) (legacy Python
route vs. the C++ segmenter), [ViT retraining from EcoTaxa](docs/vit-retraining.md),
[EcoTaxa testbed](docs/ecotaxa-testbed.md) and
[EcoTaxa ML integration](docs/ecotaxa-ml-integration.md),
[ViT/EcoTaxa taxonomy handover](docs/handover-vit-ecotaxa-taxonomy.md),
[GitLab cruise mirror](docs/gitlab-cruise-mirror.md).

## Quick start

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt -e .
.venv/bin/python scripts/setup_env.py                     # .env: secret + sqlite url
.venv/bin/python scripts/create_user.py                   # web login
.venv/bin/python scripts/create_api_token.py <user> cli   # token for the CLI
.venv/bin/python main.py                                  # http://localhost:5000
```

Then, with `PISCO_HUB_TOKEN` set to the token:

```bash
hub providers add examples/chain_demo
hub workflow create --template demo_chain --param label=hello --watch
```

[docs/getting-started.md](docs/getting-started.md) walks through this in
detail, including the nix dev shell and what to look at afterwards.

## CLI at a glance

```
hub tools     list | describe <name> | refresh
hub providers list | add <path> | remove <id>
hub templates list | describe <name> | delete <id>
hub workflow  create --file wf.json | --template <name> --param k=v … [--watch]
hub workflow  list [--template <name>] [--params] [--csv] | status <id> | export <id> | cancel <id> | retry <id> | delete <id>
hub task      status <id> | artifacts <id> | logs <id> [--stderr] [--tail N] [--follow]
hub machines  list | add <name> --host <h> [--user u] [--tag t] [--max-weight n] | enable | disable | remove
hub system    load
```

`--json` is a global flag (`hub --json workflow status <id>`). Connection:
`--url/--token`, `$PISCO_HUB_URL`/`$PISCO_HUB_TOKEN`, or
`~/.config/pisco-hub/config.json`.

## Repository layout

```
main.py                   entry point (Flask dev server); gunicorn target is main:app
app/factory.py            app wiring: .env, DB + migrations, blueprints, registry, threads
app/backend/
  models.py               User, ApiToken, ToolProvider, Tool, Machine, Workflow, Task,
                          Artifact, TemplateWorkflow, TemporaryWorkflow (drafts), …
  tools.py                provider registry: toolbox.json / legacy config.json → Tool rows
  settings_engine.py      settings validation (JSON Schema), $from/$param/$format, rendering
  task_manager.py         workflow creation + the scheduler thread
  task_watcher.py         process reaper, timeouts, task/workflow status
  artifacts.py            output allocation, artifact records, workspace layout
  machines.py             machine registry, ssh, readiness probe thread
  templates.py            templates: user-saved and manifest-shipped
  workflows.py            retry / delete / export / protocol
  param_catalog.py        predefined parameters for the template editor
  migrations.py           idempotent SQLite column migrations
app/routes/               web UI blueprints (processing, workflow_builder, file_explorer, …)
app/routes/api_v1/        REST API + openapi.py
app/templates, static/    Jinja + htmx front end
hub_cli/                  the `hub` CLI (pip-installable, see pyproject.toml)
viewers/                  file-viewer plugins (Flask blueprints): HDF viewer, profile viewer
tools/                    builtin provider: legacy config.json tools (test fixtures only)
examples/chain_demo/      a complete toolbox: two chained tools, a timeout demo, a template
scripts/                  setup_env, create_user, create_api_token, reset_db, hard_reset_db
hardware_control/         config for the (in-progress) hardware control page
comparison/               analysis scripts behind docs/pipeline-comparison-spec.md
docs/                     documentation (see the table above)
```

## Status and limitations

Verified end to end on the test cluster: the full profile chain
(segment → deconvolve → extract → classify → export → import into EcoTaxa)
split across GPU workers, cross-machine handoff through shared storage,
timeouts, retry, artifact recording, templates and the protocol.

Not there yet — see [docs/architecture.md](docs/architecture.md#known-limitations)
for the full list:

- no cancel operation; a remote task that is stopped by a timeout keeps
  running on the worker until killed there;
- `$from` references are resolved inside objects, not inside lists;
- the web builder has no picker for `$from`/`$param` references (typed as
  JSON); no Tools/Providers/Machines/Tokens pages yet;
- the Visualization Hub and Hardware Control pages are placeholders;
- lifecycle hooks (run a command on `task.failed` etc.) are not implemented.
