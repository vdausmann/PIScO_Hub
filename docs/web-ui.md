# The web interface

What each page does and where it leads. The UI needs a login
(`/login`; accounts are made with `scripts/create_user.py`). Everything
it can do, the API and CLI can do too — [cli.md](cli.md),
[AGENTS.md](../AGENTS.md) — and a few things (registering providers and
machines, minting tokens) are *only* there for now.

Pages are server-rendered; the parts that change while you watch (a
running workflow, a log) refresh themselves every few seconds with htmx
and stop once there is nothing left to watch.

## Dashboard — `/`

Five cards: **Processing Hub**, **File Explorer**, **Visualization Hub**,
**Hardware Control**, **Global Settings**. The first two are where the
work happens.

## Processing Hub — `/processing/`

A sub-navigation runs across every page here: **Workflows · Protocol ·
Templates**, a load pill (running weight over total capacity, refreshed
live), and a way back to the dashboard.

### Workflows — `/processing/`

Every workflow, newest first: name, template it came from, status, task
progress, when it was created. Filter by status (Pending, Running,
Finished, Failed) or search by name; the list refreshes every 2 s with
the filter kept. **+ New workflow** starts the guided path below.

### Workflow page — `/processing/workflow/<id>`

The run itself: its parameters, then one row per task — position, tool,
status, machine it ran on, duration, exit code — and under each task its
artifacts (name, path, whether it appeared) with a link that opens the
directory in the File Explorer. Polls while running.

Buttons:

| | |
|---|---|
| **Run again…** | opens the new-workflow form for the same template, prefilled with this run's parameters |
| **Export JSON** | downloads the run as the file `hub workflow create --file` accepts — stored settings, references intact |
| **Retry** | (failed workflows) requeues from the first failed task; artifacts from there on are discarded |
| **Delete** | removes the workflow's records; files on disk stay. Disabled while running |
| **Logs** (per task) | the task page, opening on stderr when the task failed |

### Task page — `/processing/task/<id>`

Header with status, machine, timing. Then:

- **Artifacts**, as on the workflow page.
- **Logs**: stdout / stderr switch, a *tail* length (0 = everything),
  and a *follow* box that re-fetches every 2 s and keeps the view pinned
  to the end. Follow is on by default while the task runs.
- **Settings (as stored)** — what was submitted, `$from`/`$param`
  references still visible — and **Settings file (as dispatched)** — the
  file the tool was actually given, references resolved. When a run
  behaves oddly, the second one tells you what the tool received rather
  than what you meant.

### Protocol — `/processing/protocol`

The run log as a table: one row per workflow, one column per workflow
parameter, plus start, end, task progress and where the outputs went.
Filter by template (columns then follow that template's parameter order)
or by status; click a column header to sort (numeric when every value is
a number; missing values last). **Download CSV** writes the same table
with the same filters — the cruise report.

### Templates — `/workflow-builder/templates`

Two groups. **Standard chains** are shipped by toolboxes in their
`toolbox.json` and are read-only here (change the manifest and
`hub tools refresh`); **Copy** makes an editable copy with the same
parameters. **Saved on this Hub** are yours. Per template: the number of
parameters and steps, and **Use** (start a workflow from it),
**Parameters** (the editor), **Steps** (opens the steps in the builder),
**Delete**.

**+ New template** opens the editor: name, description, a *name format*
such as `{cruise}_{station}` that names each run, and the parameter
list. Add parameters from the catalogue (cruise, station, instrument,
position, frame directory, sensor log, date, operator, comment,
deconvolve — see `app/backend/param_catalog.py`) or **+ Custom
parameter** with a type (text, choice, number, whole number, yes/no,
directory, file, date), a default, choices or bounds, *required*, and
*remembered* — a remembered value is prefilled from the previous run of
the template, so cruise and instrument stay put across a station.
Reorder with the arrows. A template may be saved with parameters only;
**Steps** then opens it in the builder to add the tools.

## Starting a workflow

### The guided path — `/workflow-builder/new`

1. **Pick** a template (standard or saved) — or start from scratch, which
   goes to the builder.
2. **Parameters**: a form from the template's schema. Prefilled from the
   draft you are editing, from the run you clicked *Run again…* on, or
   from the *remembered* values of the last run of this template.
   A directory field has **Depth preview**: it lists the directory (no
   image is opened) and plots pressure over the profile from the frame
   names. If the template has `skip_first` and `skip_last` parameters,
   dragging across the plot keeps that stretch and fills both; *Downcast
   only* keeps everything up to the deepest frame. Beside the form, the
   first kept frame is shown as the Hub renders it (the camera's TIFFs,
   scaled and stretched to 8 bit); it follows the range, and ◀ ▶ step
   through the frames around it. The frames are counted
   in the order `pisco_prepare_profile` reads them, so the counts mean the
   same thing there (`app/backend/frame_preview.py`). **Fill metadata
   from names** copies cruise, station, instrument, position and date
   from the first frame's name into whichever of those fields are empty.
   Latitude and longitude take decimal degrees or the usual written
   forms — `37° 20.198' N`, `N 37 20.198`, `37°20'11.9"S`, `37:20:11.9`,
   `021 00.5 W` (or `O`), NMEA `3720.198 N`, with a point or a comma —
   and turn into decimal degrees when you leave the field
   (`app/backend/coords.py`; the API accepts the same text).
   Next to latitude and longitude, **Current ship position** fills both
   from the latest NMEA fix, in decimal degrees (south and west
   negative). **Fill from DSHIP**, beside the station and in the depth preview,
   finds the CTD operation in DSHIP's action log that the profile belongs
   to — by the first kept frame, else the directory's first frame, else
   the cast running now — and sets station (`002-09`), CTD cast, position
   and date from it, reporting the in-water, max-depth and on-deck times. **Fill from ship nav** sets latitude, longitude and date from the
   ship's track (see Hardware Control) at the time of the first kept
   frame.
   An optional parameter left empty drops the settings bound to it, so
   the tool's own default applies.
3. **Review**: the workflow name (from the name format), the parameters,
   and every step with its settings as they will be given to the tool —
   parameters substituted, `$from` shown as a pointer to the step it
   consumes. **Edit** a step's settings if you must, **Adjust** the
   parameters, **Discard**, or **Start workflow ▶**.

Starting goes through exactly the same validation as the API, so a
mistake comes back on the review page with the message rather than as a
failed task.

### The builder — `/workflow-builder/builder/<draft>`

For a chain that is not a template yet. Left: the toolbox — every
available tool with **ADD +**. Middle: the sequence, reorderable with the
arrows, each step with **CFG** (settings) and **DEL**. Right: **Load
Template Preset** replaces the steps with a template's, and **Workflow
parameters** is a JSON object edited by hand (**Save parameters**).

The settings editor (**CFG**) has two modes. **Form** is generated from
the tool's settings schema — selects for enumerations, checkboxes for
booleans, number fields with bounds, comma-separated arrays, nested
sections for INI tools — and prefilled with the schema's defaults.
**JSON** is the raw object, and the only place references
(`{"$from": …}`, `{"$param": …}`, `{"$format": …}`) can be written; a
step whose settings already contain references opens in JSON. Tools
without a schema get a plain text box.

Finish with **Start** (creates and queues the workflow), **Save as
template** (copies the steps into a saved template, keeping the draft;
a draft made from a template inherits its parameter schema), or — when
the draft was opened from a template's **Steps** — **Save steps into
template**. **Discard draft** throws the draft away.

The builder cannot pick `$from` targets or parameters for you; that is
the main reason the user guide recommends writing chains as JSON files
and submitting them with the CLI, and using the UI to run templates and
watch.

## File Explorer — `/file-explorer/`

Browses the server's filesystem below `FILE_EXPLORER_ROOT` (Global
Settings). Breadcrumbs, folders first, sizes and modification times.
Directories named by a workflow id show the workflow's name beside it,
so `DATA_ROOT/workflows/` is readable. Selecting a file offers
**download** and the viewers registered for its extension.

Viewers are plugins under `viewers/`; two ship:

- **HDF Viewer** (`/viewer/hdf-viewer?path=…`): the group/dataset tree
  of any `.h5` file, with dataset shapes, dtypes and attributes, and the
  data itself.
- **HDF Profile Viewer** (`/viewer/hdf-profile-viewer?path=…`): for the
  segmenter's `result.h5`. Per-frame views with the extracted crops
  (click a crop to select it, toggle isolating it), the raw frame, and a
  depth/count profile of the whole file. The panel split is draggable.

Links of the form `/v/<workflow-id>/<frame>/<object index>` open the
profile viewer on one object of one frame; the EcoTaxa export writes one
such link per object so an annotator can jump back to the source frame.

## Global Settings — `/settings`

Every `GlobalSetting` row as a form field with its description. Changes
take effect on the next scheduler/watcher cycle; nothing needs a restart.
The settings and their meaning are listed in
[getting-started.md](getting-started.md#where-things-are-configured).
The two worth checking on a fresh install are `DATA_ROOT` and
`FILE_EXPLORER_ROOT`.

## Visualization Hub — `/visualization/`

Cards for **modules**: web pages that live elsewhere — a colleague's
GitLab Pages site, a dashboard on another host — which the Hub shows
inside its own layout. The Hub does not run or proxy them; it only knows
the address. Each card has the title and description, a **Source** link
(the repository behind it), **Open in new tab**, and **Open**, which
frames the page at `/visualization/m/<name>` with a way back to the list.

Modules are declared in the JSON file named by the `MODULES_FILE` setting
(default `modules.json` in the Hub root; a relative path is taken from
there). The file is read on every request, so an edit shows up on reload.
One entry per module:

```json
[
  {
    "name": "msm147-satellite",
    "title": "MSM147 satellite snapshots",
    "description": "Near-real-time Copernicus data for the cruise …",
    "url": "https://marco-schulz.pages.geomar.de/msm147_nrt_satellite_data/",
    "source": "https://git.geomar.de/marco-schulz/msm147_nrt_satellite_data",
    "contact": "Marco Schulz <mschulz@geomar.de>",
    "mode": "embed"
  }
]
```

`name` is the slug in the Hub's URL (letters, digits, `-`, `_`); `title`,
`description`, `source` and `contact` are for the card. `mode` is one of:

- `embed` (default) — frames `url` inside the Hub;
- `link` — sends the browser straight to `url`; for a site that refuses
  to be framed (the embed page then stays blank — the site sends
  `X-Frame-Options` or a `frame-ancestors` policy) or wants its own tab;
- `static` — serves a directory on the Hub's disk, given as `path`
  instead of `url`, and frames its `index.html`. This is how a site that a
  tool *writes* is shown — the satellite gallery's `<work_dir>/public`,
  say. Files are served at `/visualization/m/<name>/files/<file>`, login
  required; before the tool has run once the page says so instead of
  framing nothing.

A malformed file is an error on the page, with the offending entry named,
rather than a silently empty list.

## Hardware Control — `/hardware-control/`

The **Ship navigation** card shows the ship's latest position, heading,
course and speed from its NMEA feed, refreshed every 2 s, and turns red
when the last fix is more than 10 s old. Configured by `NAV_SOURCES`
(Global Settings, then restart the Hub); the track is logged per day under
`<DATA_ROOT>/nav/`.

The rest is in progress. Renders the sensors and commands declared in the file named
by `HARDWARE_CONTROL_CONFIG_FILE` (`hardware_control/hardware_control_config.json`:
the PIScO compute unit's sensors with their ranges, and its command
shorthands). The sensor-data endpoints exist but the parser returns
nothing yet.

## Not in the UI yet

Tools, providers, machines and API tokens have no pages. Use the CLI
(`hub tools`, `hub providers`, `hub machines`) or the API; tokens are
minted with `POST /api/v1/tokens` from a logged-in session or with
`scripts/create_api_token.py`.
