# Getting started

Install the Hub, run it, register the example toolbox, and watch a
two-step chain go through. Twenty minutes on a laptop. Deploying on the
cluster with real workers is a different document:
[cluster-runbook.md](cluster-runbook.md).

## Requirements

- Python 3.11 or newer (the cluster runs 3.13, the nix shell 3.12).
- `ssh` on the PATH, if you ever want to add a remote worker.
- Nothing else: the database is SQLite, the front end is server-rendered.

## Install

Two ways. Pick one.

**Plain virtualenv** — what the deployments use:

```bash
git clone git@git.geomar.de:team-optics/plankton/PIScO_Hub.git
cd PIScO_Hub
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/pip install -e .            # the `hub` CLI, into the same venv
```

`requirements.txt` carries the server's dependencies; `pip install -e .`
adds the CLI as a console script (`.venv/bin/hub`). The CLI needs only
`requests`, so on a machine that will *only* talk to a Hub you can install
it alone: `pip install /path/to/PIScO_Hub`.

**Nix dev shell** — a Python with every dependency, plus two helper
commands:

```bash
nix develop
run_server      # = python3 -u main.py from the repo root
hub …           # = python3 -m hub_cli
```

## Configure

```bash
.venv/bin/python scripts/setup_env.py
```

writes `.env` next to `main.py` with a random `FLASK_SECRET_KEY` and
`DATABASE_URL=sqlite:///app.db` (the file lands in `instance/`). The
server refuses to start without the secret. Both files are git-ignored.

```bash
.venv/bin/python scripts/create_user.py
```

asks for a username and password — the web login. Passwords are stored
as scrypt hashes; there is no self-registration.

```bash
.venv/bin/python scripts/create_api_token.py <username> cli full
```

prints a bearer token **once**; only its hash is stored. The last argument
is the scope: `read`, `operate` or `full` ([AGENTS.md](../AGENTS.md#authentication)
explains what each may do). Keep it for the CLI:

```bash
export PISCO_HUB_TOKEN=<the token>
export PISCO_HUB_URL=http://localhost:5000        # the default; set it for a remote Hub
```

or put both into `~/.config/pisco-hub/config.json` as `{"url": …, "token": …}`.

## Run

```bash
.venv/bin/python main.py
```

Listens on `0.0.0.0:5000`. `HUB_HOST` and `HUB_PORT` override that;
`HUB_DEBUG=1` turns on Flask's debugger *and* reloader, which restarts the
process — and the scheduler threads — underneath running tasks, so only
use it while editing the Hub itself.

On startup the Hub creates the database tables, applies any pending column
migrations, seeds the global settings, registers the `local` machine and
the `builtin` provider, scans every registered provider, loads the file
viewers, and starts three background threads (scheduler, process watcher,
machine monitor). You will see `[*] Background Task Handler Started.` and
`[*] Machine Monitor Started.` on the console.

For a real instance run it under gunicorn **with exactly one worker
process** (`--workers 1 --threads 8 … main:app`); the systemd unit is in
[deployment.md](deployment.md#6-run-it-as-a-service). Two worker processes
would be two schedulers dispatching the same queue.

Open `http://localhost:5000`, log in, and check `hub system load` from a
terminal: one machine, `local`, online, load 0/10.

## Run the demo chain

`examples/chain_demo/` is a complete toolbox: a manifest with three tools
and one workflow template. `demo_produce` writes `result.txt` into an
output directory it leaves for the Hub to choose; `demo_consume` reads it
from wherever the Hub says it is and writes a report; `demo_hang` sleeps
until the Hub's timeout stops it.

```bash
hub providers add examples/chain_demo
hub tools list
hub templates describe demo_chain
```

`providers add` validates the manifest, records the directory as a
provider and scans it. The tools appear as `demo_*`, and the manifest's
`workflows` entry becomes the template `demo_chain` with two parameters,
`label` (required) and `fail` (default false).

```bash
hub workflow create --template demo_chain --param label=hello --watch
```

The scheduler picks the first task up within `POLL_INTERVAL` seconds
(5 by default), runs it on `local`, the watcher notices it exit, the
scheduler picks up the second, and `--watch` prints the status line every
time it changes until the workflow is `Finished`. Then:

```bash
hub workflow status <id>            # tasks, where they ran, their artifacts
hub task artifacts <task-id>        # the paths, and whether they exist
hub task logs <task-id>             # what the tool printed
```

What just happened on disk — with the default `DATA_ROOT` this is
`data/` inside the checkout:

```
data/workflows/<workflow-id>/
    0_demo_produce/
        settings.json           the rendered settings the tool was given
        results/result.txt      the output; the Hub chose "results/" because
                                output_dir was left empty
    1_demo_consume/
        settings.json           input_dir holds the absolute path of 0_demo_produce/results/
        report/report.txt
logs/demo_produce/<task-id>.log and .err
logs/demo_consume/<task-id>.log and .err
```

The second task's `settings.json` is worth opening: the manifest said
`"input_dir": {"$from": {"task": 0, "output": "results"}}`, and the file
contains the real path. That substitution is the whole point of the
artifact mechanism — see [architecture.md](architecture.md#the-life-of-a-task).

Same thing from the web UI: **Processing Hub → + New workflow → demo_chain**,
fill in the label, review, start. The workflow page polls itself while it
runs; a task's page shows its logs live.

Now break it on purpose:

```bash
hub workflow create --template demo_chain --param label=oops --param fail=true --watch
```

`demo_produce` exits 1, the workflow is `Failed`, `demo_consume` never
starts. `hub task logs <task-id> --stderr` shows `failing on purpose`.
`hub workflow retry <id>` requeues from the failed task — it will fail
again, since the settings are the same, but that is the mechanism for a
task that failed for a transient reason.

And the timeout:

```bash
hub workflow create --name hang --task tool=demo_hang --watch
```

`demo_hang` declares `"timeout": 20` in the manifest. After 20 seconds the
watcher sends SIGTERM, the task fails, and its `.err` log ends with
`[hub] Exceeded its 20s limit …`.

## Where things are configured

Two layers: environment (read once at startup) and global settings
(stored in the database, editable at runtime under **Global Settings** in
the web UI or the `global_setting` table).

Environment, from `.env` or the process environment:

| Variable | Meaning |
|---|---|
| `FLASK_SECRET_KEY` | session signing key; required |
| `DATABASE_URL` | SQLAlchemy URL; `sqlite:///app.db` by default |
| `HUB_HOST`, `HUB_PORT` | bind address for `main.py` (gunicorn has its own `--bind`) |
| `HUB_DEBUG` | `1` enables the Flask debugger and reloader — development only |

Global settings (defaults seeded on first start; later changes are kept):

| Setting | Default | Meaning |
|---|---|---|
| `DATA_ROOT` | `<checkout>/data` | root for task workspaces: settings files and Hub-allocated outputs. **Must be on shared storage once a remote worker exists.** |
| `FILE_EXPLORER_ROOT` | `/home/tim/` | what the File Explorer page can browse. Set it. |
| `POLL_INTERVAL` | 5 | seconds between scheduler and watcher cycles |
| `MACHINE_PROBE_INTERVAL` | 30 | seconds between machine readiness probes |
| `TASK_TIMEOUT` | 0 | seconds a task may run when its tool declares no `timeout`; 0 = unlimited |
| `MAX_WEIGHT` | 10 | capacity given to the `local` machine when it is first created; per-machine capacity lives on the machine afterwards |
| `LOG_RETENTION_DAYS` | 30 | declared, not enforced: nothing deletes logs yet |
| `HARDWARE_CONTROL_CONFIG_FILE` | `hardware_control/hardware_control_config.json` | sensor/command definitions for the Hardware Control page |

Fixed locations, relative to the checkout: `logs/<tool>/<task-id>.log|.err`
for task output (the Hub serves them through the API; they stay local
even when the task ran elsewhere), `instance/app.db` for the database,
`tools/` for the builtin provider, `viewers/` for viewer plugins.

## Next

- Register a real toolbox: [toolbox-integration.md](toolbox-integration.md)
  for the procedure, [AGENTS.md](../AGENTS.md#authoring-a-toolbox-toolboxjson)
  for the manifest keys.
- Add a worker: `hub machines add worker1 --host 10.0.0.5 --user pisco --tag gpu`;
  [deployment.md](deployment.md#prepare-the-machine) covers what the worker needs.
- Process a profile: [hub-user-guide.md](hub-user-guide.md).

## Resetting

`scripts/reset_db.py` clears workflows, tasks, tools, templates, drafts and
viewers but keeps users and settings; the next start re-registers tools
and viewers. `scripts/hard_reset_db.py` drops every table and restores only
the users. Both need the server stopped. Neither touches `DATA_ROOT` or
`logs/`.
