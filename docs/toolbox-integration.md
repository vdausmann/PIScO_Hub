# Integrating a toolbox into the Hub

The working plan for connecting any external repository to the Hub.
[AGENTS.md](../AGENTS.md) is the formal contract — manifest keys, API
endpoints; this document is the *procedure*: what to look at, in what
order, and the traps that have already cost time. `examples/chain_demo/`
is a complete, minimal toolbox to copy from.

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
| `run.sh` greps an optional INI key under `set -euo pipefail` | the Hub omits unset optional settings; `grep` returns 1 and the wrapper dies before any output — a failed task with empty logs | guard the read with `\|\| true` |
| Tool treats the output path as a filename prefix | files land *beside* the directory | the Hub appends a trailing separator to allocated directories; do the same in defaults |
| Tool reads its input from the setting you did not expect | e.g. the segmenter's extraction mode iterates `targetPath`, not `sourcePath`; a fresh Hub-allocated directory yields empty output | read the reader code; bind `$from` to the key the tool really consumes |
| Manifest registered by a path that only exists on the workers | `providers add` fails: the Hub reads `toolbox.json` on *its own* filesystem | put the checkout (or at least its manifest) at the same absolute path on the Hub; the `requires` tag keeps tasks off it |
| GPU tool OOMs inside a worker thread | the process hangs instead of exiting | give the tool a `timeout` in the manifest |

## The PIScO toolboxes

What is registered on the test cluster (`/opt/pisco/tools/<repo>` on the
Hub and every worker), each on its `hub-integration` branch unless noted:

| repository | tools | environment | notes |
|---|---|---|---|
| `pisco_segmenter_cpp` | `pisco_segment` | nix dev shell, built with CMake | INI settings. Two modes: `BACKGROUND_CORRECTION_AND_COMPRESSION` reads `sourcePath`, writes `checkpoint_correction_*.h5` into `targetPath`; `OBJECT_EXTRACTION` is a second pass over `targetPath` writing `result.h5`. Weight 8. |
| `pisco_deconvolution_py` | `pisco_deconvolve` | official PyTorch wheel in `/opt/pisco/venvs/torch` (`$PISCO_PYTHON`), on the GPU workers | reads the correction checkpoints directly, so segment → deconvolve → extract is the chain; `modelPath` selects the LUCYD model. |
| `pisco_segmenter_py` | `pisco_segment_py` | same torch venv | the legacy all-Python pipeline, kept for comparison; also writes `result.h5`; needs a timeout because a CUDA OOM hangs it. |
| `process_pisco_profiles` (`main`, `vit-retraining`) | `pisco_vit_classify`, `pisco_ecotaxa_export`, `pisco_ecotaxa_import`, `pisco_ecotaxa_features`, `pisco_ecotaxa_labels`, `pisco_vit_trainset`, `pisco_vit_train` | torch venv; the EcoTaxa import/features/labels tools `require: ecotaxa` so they run on the EcoTaxa machine, where the credentials are | the chain from crops to an annotated EcoTaxa project and back to a retrained model; [hub-user-guide.md](hub-user-guide.md) and [vit-retraining.md](vit-retraining.md) walk through it. |

The `requires` tags in use: `pisco-tools` (the nix/venv toolchain is
installed), `gpu`, `ecotaxa`. The Hub's own `local` machine carries none,
so nothing heavy is ever scheduled onto the Hub container.

The pattern every one of these follows: `program_type: "binary"`, a
`run.sh` that picks the interpreter (`$PISCO_PYTHON`, else the venv, else
the nix shell), unsets `PYTHONPATH`, and execs the real entry point with
the settings file; a `settings_schema` with a description on every key;
`outputs` bound to the settings key the tool writes to, so the Hub can
allocate the directory and the next task can `$from` it.

## DIPLO

DIPLO — the node-based workflow builder — was the first integration
attempted (August 2026) and is still the intended *builder* in
[MANIFEST.md](../MANIFEST.md). A three-tool manifest was validated
end-to-end against the Hub, but nothing was committed to DIPLO and the
scratch runner (`diplo_run.py`) has since been superseded by DIPLO's own
headless layer (`diplo_lib/eval/workflow.py`, `runner.py`, and the
`scripts/*.py` CLIs). When that integration is picked up again: point
the manifest at those entry points, add a thin settings-file adapter
*in DIPLO* if they take argv flags, and surface the benchmarking layer
(`diplo_lib/eval/compare.py`, `metrics.py`) as tools of its own — that
is where the Build → Run → Compare loop closes.
