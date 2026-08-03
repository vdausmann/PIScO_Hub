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
  capacity. Data flows between steps only through paths named in settings.
- **Scheduling** — each task has a `weight`; the sum of running weights
  stays below the `MAX_WEIGHT` global setting. Lower `priority` numbers are
  scheduled first. A failed task aborts its workflow unless the tool is
  marked `failed_ok`.
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
`python scripts/create_api_token.py <username> <token-name>`.
Tokens travel unencrypted over plain HTTP — the Hub is designed for
trusted-LAN deployments.

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
5. `GET /api/v1/tasks/<id>/logs?stream=stderr&tail=100` for diagnostics.

Equivalent CLI: `hub tools list`, `hub tools describe <name> --json`,
`hub workflow create --file wf.json --watch`, `hub task logs <id>`.
Every command supports `--json`; configuration comes from `--url/--token`,
`$PISCO_HUB_URL`/`$PISCO_HUB_TOKEN`, or `~/.config/pisco-hub/config.json`.

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
      "settings_schema": {
        "type": "object",
        "required": ["input_dir", "output_dir"],
        "properties": {
          "input_dir":  {"type": "string", "description": "Directory of input frames"},
          "output_dir": {"type": "string", "description": "Where results are written"},
          "n_frames":   {"type": "integer", "minimum": 1, "default": 4}
        }
      },
      "inputs":  [{"name": "input_dir", "kind": "directory"}],
      "outputs": [{"name": "output_dir", "kind": "directory"}]
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

## Endpoint summary

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/v1/health` | liveness (no auth) |
| GET | `/api/v1/tools` | list tools (`?available=`, `?tag=`) |
| GET | `/api/v1/tools/<name>` | full descriptor incl. schema |
| POST | `/api/v1/tools/refresh` | rescan providers (upsert) |
| GET/POST | `/api/v1/providers` | list / register a toolbox |
| DELETE | `/api/v1/providers/<id>` | remove (tools become unavailable) |
| GET/POST | `/api/v1/workflows` | list / create+queue |
| GET/DELETE | `/api/v1/workflows/<id>` | status detail / delete (409 while Running) |
| GET | `/api/v1/tasks/<id>` | task detail incl. stored settings |
| GET | `/api/v1/tasks/<id>/logs` | `?stream=stdout|stderr&tail=N` |
| GET | `/api/v1/system/load` | scheduler load vs capacity |
| GET/POST | `/api/v1/tokens` | list / mint API tokens (session only) |
| GET | `/api/v1/openapi.json` | OpenAPI 3.1 spec |
| GET | `/api/v1/docs` | this document |
