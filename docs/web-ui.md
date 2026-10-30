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
   same thing there (`app/backend/frame_preview.py`). A folder of the
   PISCO3 recorder's `.h5` files works too: the preview takes the files
   that overlap the cast's start and end time, the frames' own times, and
   the pressure from the *Sensor log* field when that holds the Keller
   logs (a `kellerlog_*.csv` or their folder). If the template has
   `fov_diameter_px`, the preview also finds the **field of view** — the
   lit disc on the dark frame — by fitting a circle to its edge on the
   median of seven frames spread over the kept part (`app/backend/fov.py`).
   The found circle is drawn dashed over the frame; *Use this field of
   view* writes diameter and centre into the form, and the solid circle
   shows what the form holds as you correct it. PISCO2's disc is larger
   than its frame, so only the corners are dark and the fit runs on
   those arcs; the sampled area is the part of the disc inside the
   frame. The run keeps these values, with `depth_of_field_mm`, as
   parameters. **Fill metadata
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

Viewers are plugins under `viewers/`; three ship:

- **HDF Viewer** (`/viewer/hdf-viewer?path=…`): the group/dataset tree
  of any `.h5` file, with dataset shapes, dtypes and attributes, and the
  data itself.
- **HDF Profile Viewer** (`/viewer/hdf-profile-viewer?path=…`): for the
  segmenter's `result.h5`. Per-frame views with the extracted crops
  (click a crop to select it, toggle isolating it), the raw frame, and a
  depth/count profile of the whole file. The panel split is draggable.
  The file structure is listed a page (200) at a time and a group opens
  on click, so casts with tens of thousands of frames load at once; the
  filter box narrows the frame list by name (a depth such as `0150.`).
- **PIScO Data Explorer** (`/viewer/pisco-data-explorer/?path=…`): for an
  EcoTaxa TSV. Binned depth profiles (count, abundance and biovolume per
  litre, by taxon) and ESD size spectra, with a table of the binned
  values. It also has its own entry in the Visualization Hub (below).
  For a profile the Hub exported, the sampled volume comes from the
  workflow: the frames per pressure from its `profile_dataset.h5`, times
  what one frame samples according to the run's parameters — the part of
  the field-of-view disc (`fov_diameter_px`, `fov_center_x_px`,
  `fov_center_y_px`) inside the frame, at `pixel_size_um` (else the
  instrument's: 22.5 µm PISCO2, 17.13 µm PISCO3), times
  `depth_of_field_mm` (default 500). The **Optics** panel above the plot
  shows them over one of the run's frames. A run made before the depth
  preview found the field of view has none: the panel fits it on the
  run's own frames, and *Save to this run* adds the values to that
  workflow's parameters (nothing else of the run changes). *Save to this
  run and N more* does the same for the cruise's other runs of that
  instrument that have none yet — the disc stays put within a cruise but
  moved between M202 and MSM147. Until then it shows counts only. TSVs
  from the old pipeline's `<profile>_Results/EcoTaxa/` tree take the
  radius from its `settings.csv`, as before.

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

Viewers that stand on their own come first, with a card each: the
**PIScO Data Explorer**'s card has a dropdown of the profiles the Hub has
exported, and **Open** goes straight to the chosen one. A viewer gets a card
by declaring a `visualization` block in its `config.json` (`title`,
`description`, and optionally `choices`: a route of the viewer answering
`{"choices": [{"label", "url"}]}`, plus `choices_label` for the dropdown).

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

**Status Display** (`/hardware-control/sensor-display/`) shows every sensor
declared in `HARDWARE_CONTROL_CONFIG_FILE`
(`hardware_control/hardware_control_config.json`) with the latest value from
the hardware poller, once a second. A value outside its `min`/`max` turns
red, and raises an alarm when its `alarm condition` holds (or has none).
Each device's heading says which controllers feed it and whether they are
live; the last value of a controller that went quiet is shown faded and
raises no alarm. `?file=<name>` shows a local `hardware_control/*.txt`
instead, for working on the page without an instrument.

**All alarms** (bottom of the Status Display) lists every alarm that is
set, with a filter and an *only alarms that are on now* switch.

*Configured limits*: each sensor of `hardware_control_config.json` with a
min or max, its *only while* condition, the value now and the state. Each
can be switched off, edited in its row (min, max, condition such as
`Pa-PiPwr==1`; empty means none) and reset. A change is kept on the Hub
(`hardware_control/state/limit_overrides.json`) on top of the config file,
which stays as it is in the controllers' repository, and the row is marked
*changed* (hover for who and the file's limit). A switched-off limit still
colours its field, but raises no alarm. API: `PUT
/hardware-control/sensor-display/limits/<shorthand>` with `{"min", "max",
"condition", "enabled"}`, `DELETE …/limits/<shorthand>` to reset; the
status endpoint returns them under `limits`.

*Your own alarms*: alarms anyone can add on
any field, configured or not, on top of the configured limits. Conditions:
compare with a value (`>` `>=` `<` `<=` `==` `!=`), outside a range, is on (1)
/ is off (0), unchanged for N seconds (e.g. a frame counter that stopped),
has no value; optionally *only while* another field meets a condition
(`Pi-Record == 1`). *Edit* loads one into the form and saves it in place.
They are kept on the Hub
(`hardware_control/state/user_alarms.json`) and evaluated there, so all
viewers share them; an active one goes to the alarm board and popup like a
configured alarm and outlines its field. A stale controller raises none.
"Unchanged" counts from the Hub's last start. 

**Alarms on every page.** The Hub checks the configured limits and these
alarms once a second whether or not anyone has a page open
(`app/backend/alarm_monitor.py`). While any is on, every Hub page shows a
red banner linking to the Status Display, and a popup for alarms this
browser has not acknowledged; an alarm that ends and starts again pops up
again. Acknowledgements are kept per browser. Starts, ends and
acknowledgements (with the user) are logged to
`<DATA_ROOT>/sensor_logs/alarms/alarms_<YYYYMMDD>.csv`. Pages poll `GET
/hardware-control/sensor-display/alarms/active`; `POST …/alarms/ack`
with `{"ids": [...]}` logs an acknowledgement. API: `POST
/hardware-control/sensor-display/alarms`, `PATCH …/alarms/<id>`
(`{"enabled": false}`, or a body like POST's to change it), `DELETE
…/alarms/<id>`; the status endpoint returns
them under `alarms`.

**Control Devices** reads each controller's command file and sends
changes back to it.

**Shutdown All** (top of Control Devices) turns off the whole instrument
before it comes back on deck. It runs sea-through-controll's
`set_shutdown_and_upload.py` from `SEA_THROUGH_DIR`: Keller log from the
Propel-Akku first, then each controller's `*-Shutdown` fields set to 1,
in the order and with the pauses of its `upload_order.txt`. A dialog
lists the steps and asks to proceed; **Dry run** only checks the
configuration. One run
at a time; its output is shown on the page and kept under
`<DATA_ROOT>/sensor_logs/shutdown/`. API: `POST
/hardware-control/device-control/shutdown-all` with `{"confirm":
"SHUTDOWN"}` or `{"dry_run": true}`; `GET` the same URL for the status.
A Hub restart during a run stops it part-way.

## Not in the UI yet

Tools, providers, machines and API tokens have no pages. Use the CLI
(`hub tools`, `hub providers`, `hub machines`) or the API; tokens are
minted with `POST /api/v1/tokens` from a logged-in session or with
`scripts/create_api_token.py`.
