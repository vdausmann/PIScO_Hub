# Using the Hub: a practical guide

For someone who wants to process a PIScO profile and end up with objects to
annotate in EcoTaxa, without reading the source.

## What the Hub is

A scheduler. You describe a **workflow** — an ordered list of **tasks**, each
one a **tool** with settings — and the Hub runs them on whichever **machine**
can. Tools are external programs (the C++ segmenter, a ViT, the exporter); the
Hub does not do any processing itself. It decides where and when things run,
keeps the logs, and records what each task produced.

Data is never copied. Every machine sees the same shared storage at the same
path (`/mnt/pisco`), so a task can hand its output to the next one by path.

## Three ways to drive it, and when to use which

| | good for | not good for |
|---|---|---|
| **Web GUI** at `http://10.11.11.110:5000` | starting a run from a template (guided: pick → parameters → review), watching progress, live logs, artifacts, retrying failed runs, exporting a run as JSON | building a new chain from scratch — references (`$from`, `$param`) are typed as JSON there, with no picker yet |
| **CLI** (`hub`, on the Hub machine) | everything, especially repeatable runs from a file | nothing much |
| **REST API** | scripting, other programs | interactive use |

**The practical answer: build chains as a JSON file and submit them with the
CLI, or instantiate a template.** Use the GUI to watch them and to look at
the results.

Values that several steps share — the frame directory, cruise and station,
a model path — go once into the workflow's `params` and are referenced from
settings as `{"$param": "frames"}` or `{"$format": "{cruise}_{station}"}`.
A *template* is such a chain with a schema for its parameters; the standard
ones are shipped with the toolboxes and `hub templates list` shows what is
registered:

```
hub templates describe process_profile        # which parameters it needs
hub workflow create --template process_profile \
    --param cruise=M203 --param station=012-01 --param frames=/mnt/pisco/incoming/M203_012-01/ --watch
```

Templates are made in the GUI under **Templates**: pick parameters from the
predefined list (cruise, station, instrument, position, frame directory,
sensor log, …) or define your own with a type, a default, choices or bounds,
and mark the ones that should be *remembered* between runs. A template can
start as just its parameters; **Steps** opens it in the builder to add the
tools, whose settings then reference the parameters. Templates shipped by a
toolbox are read-only there — **Copy** makes an editable one.

**Protocol** in the sub-navigation is the run log: one row per workflow,
the parameters as columns, with start and end times and where the outputs
went. Filter by template, click a column to sort, **Download CSV** for the
cruise report. The same table from the terminal:
`hub workflow list --template profile [--csv]`.

In the GUI, **+ New workflow** does the same thing guided: pick the template,
fill in its parameters (values marked *remembered* are prefilled from the
previous run of that template — cruise and instrument stay put across a
station), review the steps with everything filled in, adjust one if you must,
start. A finished run's page has **Run again…**, which opens the form
prefilled with that run's parameters, and **Export JSON**, which gives a file
that recreates the run from the CLI. See AGENTS.md for the reference syntax.

The GUI cannot express `{"$from": ...}` references, so a chain built there needs
every path typed by hand. That works, but it is easy to get wrong and it breaks
if you re-run into a fresh workflow directory.

## Walkthrough: one profile, start to finish

### 1. Put the frames where every machine can see them

```
/mnt/pisco/incoming/<your profile>/     # raw .png frames
```

### 1b. Or build the profile dataset first

The chain below reads a directory of named PNGs. During a cruise the frames
come from the camera without metadata; `pisco_prepare_profile` (toolbox
`pisco_profile_dataset`) turns the camera output plus the sensor log into
`profile_dataset.h5`, and both segmenters accept its directory as
`sourcePath` — `{"$from": {"task": 0, "output": "dataset"}}` in the next
step. Its parameters are the ones the `profile` template asks for.

### 2. Write the workflow

**The complete chain as it runs today is in
[`examples/pisco_full_chain.json`](../examples/pisco_full_chain.json)** —
segment, deconvolve, extract, both ViT stages, EcoTaxa export, import and
features — with every setting written out even where it is the tool's default,
so the file documents what a run means. Copy it, change `sourcePath`,
`profileName` and the two model paths, and submit it. The settings it carries
are the ones settled in `docs/pipeline-comparison-spec.md`: a 12-frame
background block on both routes, 30 px of padding around each object so the
deconvolution model sees enough of the frame, a texture-filled atlas, and
crops handed to EcoTaxa and the classifier as the object alone in a tight box
(`isolateCrops`). The walkthrough below is the same chain, explained.


Save this as `wf.json`. It is the chain that was verified end to end: clean and
compress the frames, deblur the crops, extract them, classify them, and write
an EcoTaxa archive with the predictions already in it. Two more tasks in
step 4 carry it the rest of the way into EcoTaxa.

```json
{
  "name": "my profile",
  "tasks": [
    {"tool": "pisco_segment", "settings": {
      "sourcePath": "/mnt/pisco/incoming/my_profile/", "targetPath": "",
      "mode": "Mode::BACKGROUND_CORRECTION_AND_COMPRESSION",
      "numProcesses": 4, "allowedImageFileExtensions": ".png",
      "imageSizeX": 2560, "imageSizeY": 2560}},

    {"tool": "pisco_deconvolve", "settings": {
      "targetPath": {"$from": {"task": 0, "output": "results"}},
      "modelPath": "best_loss_v5a_noGrad_ablation.pth"}},

    {"tool": "pisco_segment", "settings": {
      "sourcePath": "/mnt/pisco/incoming/my_profile/",
      "targetPath": {"$from": {"task": 0, "output": "results"}},
      "mode": "Mode::OBJECT_EXTRACTION",
      "numProcesses": 4, "allowedImageFileExtensions": ".png",
      "imageSizeX": 2560, "imageSizeY": 2560}},

    {"tool": "pisco_vit_classify", "settings": {
      "sourcePath": {"$from": {"task": 0, "output": "results"}},
      "targetPath": "", "modelPath": "<binary model>",
      "emitEmbeddings": true, "topK": 1}},

    {"tool": "pisco_vit_classify", "settings": {
      "sourcePath": {"$from": {"task": 0, "output": "results"}},
      "targetPath": "", "modelPath": "<taxon model>",
      "restrictToPath": {"$from": {"task": 3, "output": "predictions"}},
      "restrictLabels": "living", "topK": 3}},

    {"tool": "pisco_ecotaxa_export", "settings": {
      "sourcePath": {"$from": {"task": 0, "output": "results"}},
      "targetPath": "", "profileName": "my_profile",
      "predictionsPath": {"$from": {"task": 4, "output": "predictions"}},
      "taxonomyMapPath": "/opt/pisco/tools/process_pisco_profiles/ecotaxa_taxonomy_map.json",
      "linkBase": "http://10.11.11.110:5000"}}
  ]
}
```

That is the **dual-ViT setup**: task 3 is the binary living/not-living pass
over every crop, task 4 the taxon pass over the living ones only. Three details
matter and are easy to get wrong:

* **`emitEmbeddings` goes on the binary stage**, not the taxon one. Only the
  binary stage sees every object, and EcoTaxa disables similarity search
  entirely for a project where any object lacks a feature vector.
* **`predictionsPath` points at the *last* stage only.** The taxon stage carries
  forward the rows it skipped (`carryForward`, on by default), so its
  `predictions.csv` already covers every object: taxon labels where it
  classified, the binary verdict elsewhere. Turn `carryForward` off and the
  not-living objects arrive unannotated.

  This exists because the Hub resolves `{"$from": ...}` inside dicts but **not
  inside lists**, so two references cannot be combined into one setting. Making
  each stage's output complete sidesteps that. (For a manual run, the exporter
  also accepts several paths comma-separated, applied in order.)
* `living` is a **gate**, not a taxon — it is marked `skip` in the taxonomy map
  and never becomes an annotation. `not-living` *is* a real EcoTaxa category
  (84960), so those objects do get labelled.

A single-model chain is the same thing with task 4 removed and
`predictionsPath` naming one directory.

Two more things that look odd but are deliberate:

* **`targetPath: ""`** means "Hub, pick a directory". Leave it empty unless you
  have a reason not to.
* **Extraction's `targetPath` points at task 0's output, not its own.** The
  extraction pass is a second sweep over the *correction* directory and writes
  `result.h5` beside the checkpoints. Give it a fresh directory and it silently
  produces empty files.

### 3. Run it

```bash
ssh root@10.11.11.58
pct exec 110 -- /usr/local/bin/hub workflow create --file /tmp/wf.json
pct exec 110 -- /usr/local/bin/hub workflow status <id>
```

`--watch` follows it. `hub workflow list` shows everything.

### 4. Import into EcoTaxa

Add two more tasks and the chain finishes on its own — no manual import, no
carrying a project number around:

```json
    {"tool": "pisco_ecotaxa_import", "settings": {
      "archivePath": {"$from": {"task": 5, "output": "export"}},
      "targetPath": "",
      "projectTitle": "my profile",
      "instrument": "Other camera"}},

    {"tool": "pisco_ecotaxa_features", "settings": {
      "embeddingsPath": {"$from": {"task": 3, "output": "predictions"}},
      "projectIdFrom": {"$from": {"task": 6, "output": "import"}},
      "pcaPath": "/mnt/pisco/vit/pisco_pca50.npz"}}
```

By default the import **creates a new project**. Set `projectId` instead to add
to one that already exists — useful for building a project up profile by
profile. `updateMode: "Yes"` with `skipExistingObjects: true` re-imports without
duplicating.

Nothing is copied. The share is mounted inside EcoTaxa's server load area, so
the archive is imported where the export task already wrote it.

`instrument` must be one EcoTaxa knows. There is no PIScO entry, so
`Other camera` is the generic that works.

### 5. What the last task buys you

`pisco_ecotaxa_features` attaches the ViT embeddings, which is what enables
EcoTaxa's **"similar to"** filter. It reads the project id from the import
task's `import.json`, because the Hub chains tasks by *path*, not by value —
a project that did not exist when you wrote the workflow could not be named in
it otherwise.

It must cover **every** object in the project: EcoTaxa disables similarity
search entirely for a project where any object lacks a feature vector, rather
than degrading. The task refuses a partial write for that reason.

That is the whole chain — seven tasks, raw frames to an annotated, searchable
project:

```
0 pisco_segment          gpuworker   clean + compress
1 pisco_deconvolve       gpuworker   deblur the crops
2 pisco_segment          gpuworker   extract objects
3 pisco_vit_classify     gpuworker   binary pass, all crops + embeddings
4 pisco_vit_classify     gpuworker   taxon pass, living crops only
5 pisco_ecotaxa_export   gpuworker   crops, TSV, zip
6 pisco_ecotaxa_import   ecotaxa     create project, import
7 pisco_ecotaxa_features ecotaxa     attach embeddings
```

The seven-task single-model version of this has been run end to end. The dual
variant's pieces are each verified — stage chaining and restriction, and the
prediction merge — but no run has used a real binary model yet, since only one
stand-in was available.

### 6. Closing the loop: retraining on what was validated

Once a human has validated objects in EcoTaxa, three more tools turn that work
back into models — `pisco_ecotaxa_labels`, `pisco_vit_trainset` and
`pisco_vit_train` — and `pisco_vit_classify` takes a training task's output
directory straight as its `modelPath`. So the next run classifies with a model
trained on the corrections made to the last one, without anyone moving crops
between machines.

See [vit-retraining.md](vit-retraining.md) for the workflow and, more
importantly, for the couplings that make a retrained model valid or worthless:
crop isolation, the deconvolution model, and validated-versus-predicted labels.

## How work gets distributed

Each tool declares what it needs (`requires`) and how heavy it is (`weight`).
Each machine has tags and a capacity. The Hub gives a task to any enabled,
online machine whose tags cover the requirement and that has capacity left:

```
task.weight <= machine.max_weight - current load
```

| machine | tags | capacity |
|---|---|---|
| `gpuworker1` (10.11.11.112) | `pisco-tools, gpu` | 10 |
| `gpuworker2` (10.11.11.113) | `pisco-tools, gpu` | 10 |
| `ecotaxa` (10.11.11.114) | `ecotaxa` | 4 |
| `local` (the Hub itself) | — | 10 |

So `pisco_ecotaxa_import` and `pisco_ecotaxa_features` (both require
`ecotaxa`) always land on the EcoTaxa machine — that is how EcoTaxa's database
stays on loopback instead of being exposed to the whole subnet, and how its
credentials stay off the shared storage. Everything else goes to whichever GPU
worker is free.

Weights are what keep a machine from being over-committed: the segmenter is 8
and a ViT pass is 6, so those two cannot share a worker with capacity 10. Two
profiles running at once naturally spread across both workers.

**Within one workflow tasks run strictly in order**, one at a time. Parallelism
comes from running several workflows.

## Where everything lands

```
/mnt/pisco/hub-data/workflows/<workflow-id>/
    0_pisco_segment/
        settings.ini            <- exactly what the tool was given
        results/                <- result.h5, checkpoints
    3_pisco_vit_classify/
        predictions/            <- predictions.csv, embeddings.npz
    4_pisco_ecotaxa_export/
        export/                 <- crops/, the TSV, the .zip
```

`settings.ini` is worth knowing about: it is the rendered settings, so when a
run behaves oddly, it tells you what the tool actually received rather than
what you meant.

## When something breaks

```bash
hub workflow status <workflow-id>     # which task failed, on which machine
hub task logs <task-id>               # what it printed
```

Or over the API, which is what to use if you want the raw text:

```
GET /api/v1/tasks/<task-id>/logs?stream=stdout&tail=100
GET /api/v1/tasks/<task-id>/logs?stream=stderr&tail=100
```

On disk, on the Hub machine: `/opt/pisco-hub/app/logs/<tool>/<task-id>.log`
and `.err`. The Hub's own service log is `journalctl -u pisco-hub`.

In the GUI: open the workflow from the Processing Hub, then a task's **Logs**
button. The task page shows stdout and stderr with a tail length and a
*follow* toggle that refreshes every two seconds while the task runs, the
settings as stored and as dispatched (with `$from` references resolved), and
the artifacts with links into the File Explorer. A failed workflow has a
**Retry** button, which requeues it from the first failed task exactly like
`hub workflow retry`, and **Export JSON** downloads it in the form
`hub workflow create --file` accepts. A running or pending workflow has
**Cancel** (`hub workflow cancel <id> [--watch]`): pending tasks never start,
the running one is asked to stop and killed if it does not within 30 s — on
the worker as well, not just the Hub's ssh — and the workflow reads
*Cancelled*; Retry picks it up again from there.

**An empty log with a non-zero exit** usually means the tool's wrapper died
before it ran anything — a missing required setting, or an interpreter that
cannot import what the script needs.

## Quick reference

```bash
hub tools list                      # what can be run
hub tools describe <tool> --json    # its settings, with descriptions
hub machines list                   # where things can run
hub workflow create --file wf.json  # submit
hub workflow status <id>            # progress and artifacts
hub workflow retry <id>             # requeue from the first failed task
hub task logs <task-id>             # output
```

Two traps that cost time repeatedly:

* Under `pct exec`, call it as **`/usr/local/bin/hub`** — that PATH has no
  `/usr/local/bin`.
* **`--json` is a global flag**: `hub --json workflow status <id>`, not
  `hub workflow status --json <id>`.
