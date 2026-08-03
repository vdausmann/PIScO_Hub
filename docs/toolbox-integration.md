# Integrating a toolbox into the Hub

The working plan for connecting any external repository (DIPLO, the
segmenter, a future toolbox) to the Hub. [AGENTS.md](../AGENTS.md) is the
formal contract — manifest keys, API endpoints; this document is the
*procedure*: what to look at, in what order, and the traps that have
already cost time.

## The recipe

### 1. Find the toolbox's real entry points before writing anything

Ask: **what can this repo already do without its GUI?** Look for a
headless runner, a CLI, a `scripts/` directory, a test suite. Never infer
capability from the GUI's structure alone, and never from a stale
checkout — `git fetch` every remote and compare, because the working copy
on disk may be many commits behind the line where development happens
(exactly what happened with DIPLO, see below).

A tool should wrap an entry point the toolbox *maintains*. If you find
yourself reimplementing the toolbox's internals in a runner script, stop:
that code will rot the moment upstream changes.

### 2. Decide what each tool is

One manifest entry per *user-meaningful operation*, not per internal
function. Good candidates: "run this pipeline over a dataset", "score
candidate vs. ground truth", "export to EcoTaxa". Each becomes a `name`
in `toolbox.json`, prefixed with the toolbox name (`diplo_*`) since names
are globally unique across providers.

### 3. Provide a `run.sh` wrapper

Unless the toolbox runs in the Hub's own Python environment, use
`program_type: "binary"` with a wrapper that activates the toolbox's
environment. The wrapper receives exactly one argument: the path to the
rendered settings file.

```bash
#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# The Hub server runs inside its own (nix) Python environment. Leaking it
# into the toolbox produces confusing numpy/cv2 import failures.
unset PYTHONPATH PYTHONHOME

exec "${TOOLBOX_PYTHON:-python3}" "$SCRIPT_DIR/<entry point>" "$@"
```

### 4. Write the schema, and make it the documentation

`settings_schema` is what the web form, the CLI and any AI agent read to
learn how to call the tool. Every property gets a `description`; every
optional one gets a `default`; numeric ranges get `minimum`/`maximum`.
Required keys go in `required`. This is the highest-leverage part of the
whole integration — a good schema means nobody has to read the source to
use the tool.

Keep settings to what varies per run (paths, thresholds, frame ranges).
Anything structural belongs in the toolbox, not in the manifest.

### 5. Register and iterate

```bash
hub providers add /path/to/toolbox      # validates the manifest, then scans
hub tools describe <tool> --json        # confirm the schema the Hub sees
hub workflow create --name t1 --task tool=<tool> settings=@s.json --watch
hub task logs <task-id> --stderr
```

Manifest edits are picked up by `hub tools refresh` — no restart, no DB
reset. Tools removed from a manifest go `available=false` and keep their
history.

### 6. Verify honestly

The Hub trusts the exit code completely. Before declaring a tool working:

- run it standalone first (`./run.sh settings.json`) — faster feedback
  than going through the scheduler;
- then through the Hub, and confirm the *output files* exist, not just
  that the status says Finished;
- feed it deliberately invalid settings and confirm the API returns 400
  rather than queueing a doomed task.

## Traps already hit

| Trap | What happens | Fix |
|---|---|---|
| Hub's Python leaks into the tool | `ModuleNotFoundError: numpy._core._multiarray_umath` | `unset PYTHONPATH PYTHONHOME` in `run.sh` |
| Toolbox reports errors as return values, not exceptions | task exits 0 while having failed | runner must inspect results and exit non-zero |
| Env read at import/construction time (e.g. `WORKFLOW_DIR`) | output lands in the wrong directory | set it before importing/constructing anything |
| Results only rendered in the GUI | headless run produces nothing on disk | the entry point must write outputs explicitly |
| Stale local checkout | you rebuild something upstream already has | fetch all remotes and diff before designing |
| Model downloads on first use | first task hangs or fails on a server | pre-warm caches; give the service user a writable `$HOME` |

## Case: DIPLO (status as of 2026-08-03)

**DIPLO is intentionally unchanged in git** — nothing from this work is
committed there. The Hub-side files that exist in the DIPLO working tree
(`diplo_run.py`, `run.sh`, `toolbox.json`) are **untracked scratch** and
should be treated as superseded, not as the integration.

What the current DIPLO line (`3e01d93`, on both GitLab and GitHub)
already provides — the local checkout at `abd79ef` predates all of it:

- `diplo_lib/eval/workflow.py` — headless workflow loader
  (`load_workflow` / `build_workflow`: workflow JSON → runnable `Graph`).
- `diplo_lib/eval/runner.py` — dataset runner with a **Qt-free**
  `DirectorySource`, closing the build/run/measure loop.
- `diplo_lib/eval/` also has `metrics.py` (extensible metric registry),
  `compare.py` (candidates vs. ground truth), `ecotaxa.py`,
  `classify.py`, `dataset.py`.
- `scripts/benchmark_demo.py`, `scripts/analyse_dataset.py` — existing
  CLI entry points.
- The node engine has been decoupled from Qt; a test suite exists.

So the benchmarking layer that earlier notes called "not implemented"
**exists**. The scratch `diplo_run.py` duplicates the loader and runner,
and does it worse (it still needs Qt).

### How the DIPLO integration should actually be done

1. Update the local checkout to the current line first.
2. Drop `diplo_run.py`. Point the manifest at DIPLO's own entry points —
   `diplo_lib/eval/runner.py` and the `scripts/*.py` CLIs — adding a thin
   settings-file adapter *in DIPLO* if those take argv flags rather than
   a settings file. That adapter is a legitimate upstream contribution;
   a reimplementation of the loader is not.
3. Keep `run.sh` (the wrapper and its `PYTHONPATH` fix are still
   correct) and keep the manifest shape below.
4. Surface the benchmarking layer as its own tools — this is where the
   Hub gets the most out of DIPLO: `diplo_benchmark_compare`,
   `diplo_analyse_dataset`, `diplo_classify_export`.

Manifest shape that worked (schema abbreviated):

```json
{
  "manifest_version": 1,
  "provider": {"name": "diplo", "description": "...", "version": "0.1.0"},
  "tools": [{
    "name": "diplo_run_workflow",
    "description": "Run a DIPLO workflow over an image directory.",
    "tags": ["diplo", "workflow"],
    "program_path": "run.sh",
    "program_type": "binary",
    "working_directory": ".",
    "default_priority": 3, "default_weight": 4, "failed_ok": false,
    "settings_format": "json",
    "settings_schema": {
      "type": "object",
      "required": ["workflow", "input_dir", "output_dir"],
      "properties": {
        "workflow":   {"type": "string", "description": "workflow JSON path"},
        "input_dir":  {"type": "string", "description": "frame directory"},
        "output_dir": {"type": "string", "description": "results directory"}
      }
    },
    "inputs":  [{"name": "input_dir",  "kind": "directory"}],
    "outputs": [{"name": "output_dir", "kind": "directory"}]
  }]
}
```

Verified working end-to-end against the Hub (scheduler → subprocess →
Finished, with output on disk) using the `max_bg_calc` workflow — so the
Hub-side contract is proven; only the DIPLO-side entry point needs to
change from the scratch runner to the upstream one.

### Environment note

There is no `diplo` conda env on this machine. Miniconda **base**
(`/home/veit/miniconda3/bin/python`) has the required dependencies, so
`DIPLO_PYTHON=/home/veit/miniconda3/bin/python` is how tools were run
during verification.
