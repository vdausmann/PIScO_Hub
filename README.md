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

## From the instrument to the first task

Two things happen before any processing tool runs: the camera's recording
and the controller's sensor readings have to be brought together, and the
Hub has to know what the instrument is doing while it is in the water.

```
PISCO instrument                                   Hub                                     workers
────────────────                                   ───                                     ───────
controller (NUC, Unified Controller)
  PiscoComputeSensors.txt  ── ssh, once a second ──▶ hardware poller
    one line: Pi-ExPres_23.45_Pi-TeCom_…              ├─▶ /hardware-control/live   (the page)
  PiscoComputeCommands.txt ◀── scp, only on change ── │   data/<device>.dat
                                                      └─▶ DATA_ROOT/sensor_logs/<device>/
                                                          sensorlog_<device>_<day>.csv ─┐
camera computer (Jetson, pisco_record.py)                                               │
  <prefix>_<date>_<time>.h5   ── copied to the share after the cast ────────────────────┼─▶ pisco_prepare_profile
    /frames /timestamp_ms /frame_counter                                                │     matches frames to rows by time
  or <prefix>_NNNNNN.raw + .json chunks                                                 │
                                                                                        ▼
                                                            profile_dataset.h5 ─▶ segment ─▶ deconvolve ─▶ … ─▶ EcoTaxa
```

### Live sensor values: the hardware poller

`app/backend/hardware_poller.py`, started with the other background threads.
For every device in `hardware_control/devices.json` that is enabled and has
a host, a thread fetches the controller's sensor file once a second over
one multiplexed SSH connection (`scp -p`, so the file's modification time
comes along — a moving mtime is the liveness signal, independent of the
controller's clock). The line is kept as the controller wrote it and goes
three ways:

- `GET /hardware-control/live` — `{PIScO: {"Pi-ExPres": "23.450", …}, …}`
  plus `_meta` per device (`received`, `measured`, `stale`, `error`,
  `clock_offset_s`); what the hardware-control page polls.
- `data/<device>.dat` — the latest line, which the page's file mode reads.
- `DATA_ROOT/sensor_logs/<device>/sensorlog_<device>_<YYYYMMDD>.csv` — one
  row per tick, changed or not, in the controller's own CSV layout
  (`TS_ISO`, then a column per `Pi-` shorthand) plus `TS_RECEIVED`. This
  is the sensor log the profile preparer takes. `TS_ISO` is the Hub's
  receive time until the controller stamps its line; then it is the
  measurement time and both are kept.

Commands go the other way: `POST /hardware-control/live/commands/<device>`
with `{"Pi-SetFPS": 10}` merges into the full command line read from the
device, and copies it over only if something changed — the controller
notices by the file's mtime. `GET` on the same URL shows the current line.
Every push is logged next to the sensor log.

`devices.json` has one entry per device of the hardware-control config
(host, user, `os`, the two file paths, interval, `stale_after_s`). Only
PIScO is enabled; the other cameras are stubs in the same shape, so
bringing one up is a config edit. The Hub's `pisco` key must be in the
controller's `authorized_keys` (on Windows, for an administrator, that is
`C:\ProgramData\ssh\administrators_authorized_keys`).

Two things the poller cannot fix: the controller, the camera computer and
the Hub must agree on the time to about a second (NTP), and the controller
logs once a second while the camera runs at ten frames a second, so the
pressure of a frame is an interpolation between two readings.

### The profile dataset: `pisco_prepare_profile`

The first task of the `profile` template, from the
[`pisco_profile_dataset`](https://git.geomar.de/team-optics/plankton/pisco_profile_dataset)
toolbox. It normalises the camera output **once** into one HDF5 file per
profile, `profile_dataset.h5`, and every later step reads that — the
segmenters, the exporter, the viewers. Nothing downstream knows the camera
format.

Inputs: the recording (`sourcePath` — the camera computer's `.h5`, or the
directory holding it, or its `.raw` chunk directory; also a PNG directory
from earlier cruises), the sensor log (`sensorLog` — the Hub's day file
above, or the controller's own `sensorlog_*.csv`), and the profile's
identity (`cruise`, `station`, `instrument`, `lat`, `lon`, `date`) from the
workflow parameters.

What it does: reads each frame's time from the recording, matches it to
the sensor rows by time (pressure and temperature interpolated between
the two rows around the frame; every other column — switches, lock
status, currents — takes the nearest row), names the frame by the
convention every later step and EcoTaxa's `object_id` rely on
(`M202_028-02_PISCO2_0009.94dbar-38.58N-027.32W-22.18C_20240725-05400026`),
decodes the pixels (12-bit packed frames, blosc2-compressed recordings)
and writes them in time order. The file holds `/frames`, `/names`, a
`/frame/<column>` per sensor column, the whole sensor log, and the
profile's metadata as attributes; `docs/format.md` in the toolbox is the
specification.

Verified on the test cluster: the 46 M202 frames packaged as a Video
Pipeline `.h5` plus a 1 Hz Hub-style sensor log ran the full template to
an EcoTaxa project with features (workflow `aba344e7`, 16 933 objects),
producing the same names as the PNG era.

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
  task_watcher.py         process reaper, timeouts, cancel, task/workflow status, log retention
  hooks.py                lifecycle hooks: run a command / POST a webhook when a task or workflow ends
  artifacts.py            output allocation, artifact records, workspace layout
  machines.py             machine registry, ssh, readiness probe thread
  hardware_poller.py      live sensor values from the instrument controllers, sensor log, commands
  templates.py            templates: user-saved and manifest-shipped
  workflows.py            retry / delete / export / protocol
  param_catalog.py        predefined parameters for the template editor
  migrations.py           idempotent SQLite column migrations
app/routes/               web UI blueprints (processing, workflow_builder, file_explorer, …)
app/routes/hardware_live.py  /hardware-control/live: the poller's values and command push
app/routes/api_v1/        REST API + openapi.py
app/templates, static/    Jinja + htmx front end
hub_cli/                  the `hub` CLI (pip-installable, see pyproject.toml)
viewers/                  file-viewer plugins (Flask blueprints): HDF viewer, profile viewer
tools/                    builtin provider: legacy config.json tools (test fixtures only)
examples/chain_demo/      a complete toolbox: two chained tools, a timeout demo, a template
scripts/                  setup_env, create_user, create_api_token, reset_db, hard_reset_db
hardware_control/         hardware control page config; devices.json for the poller
comparison/               analysis scripts behind docs/pipeline-comparison-spec.md
docs/                     documentation (see the table above)
```

## Status and limitations

Verified end to end on the test cluster: the full profile chain
(segment → deconvolve → extract → classify → export → import into EcoTaxa)
split across GPU workers, cross-machine handoff through shared storage,
timeouts, retry, artifact recording, templates and the protocol.

Also there: cancel (a workflow or a task, the process tree on the worker
included), timeouts, `failed_ok` tools that let a chain run on, `$from`
references anywhere in the settings (lists included), several dispatches
per scheduler cycle, log retention, and lifecycle hooks — a command to run
or a URL to POST when a task or workflow ends (`hooks.json`, see
`app/backend/hooks.py`).

Not there yet — see [docs/architecture.md](docs/architecture.md#known-limitations)
for the full list:

- the web builder has no picker for `$from`/`$param` references (typed as
  JSON); no Tools/Providers/Machines/Tokens pages yet;
- the Visualization Hub only frames external pages (`modules.json`); the
  Hardware Control page shows live values but its command widgets are not
  wired to the poller's command push yet;
- a task that outlives a Hub restart is tracked by pid only; if its
  process is gone by the time the watcher looks, the exit code is
  recorded as `-1`.
