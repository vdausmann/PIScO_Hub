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
| **Web GUI** at `http://10.11.11.110:5000` | browsing results, watching progress, reading logs, one-off tasks | chaining tasks — there is no "use the output of step 2" picker |
| **CLI** (`hub`, on the Hub machine) | everything, especially repeatable runs from a file | nothing much |
| **REST API** | scripting, other programs | interactive use |

**The practical answer: build chains as a JSON file and submit them with the
CLI.** Use the GUI to watch them and to look at the results.

The GUI cannot express `{"$from": ...}` references, so a chain built there needs
every path typed by hand. That works, but it is easy to get wrong and it breaks
if you re-run into a fresh workflow directory.

## Walkthrough: one profile, start to finish

### 1. Put the frames where every machine can see them

```
/mnt/pisco/incoming/<your profile>/     # raw .png frames
```

### 2. Write the workflow

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
      "targetPath": "", "modelPath": "<your model>",
      "emitEmbeddings": true, "topK": 3}},

    {"tool": "pisco_ecotaxa_export", "settings": {
      "sourcePath": {"$from": {"task": 0, "output": "results"}},
      "targetPath": "", "profileName": "my_profile",
      "predictionsPath": {"$from": {"task": 3, "output": "predictions"}},
      "taxonomyMapPath": "/opt/pisco/tools/process_pisco_profiles/ecotaxa_taxonomy_map.json",
      "linkBase": "http://10.11.11.110:5000"}}
  ]
}
```

Two things that look odd but are deliberate:

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
      "archivePath": {"$from": {"task": 4, "output": "export"}},
      "targetPath": "",
      "projectTitle": "my profile",
      "instrument": "Other camera"}},

    {"tool": "pisco_ecotaxa_features", "settings": {
      "embeddingsPath": {"$from": {"task": 3, "output": "predictions"}},
      "projectIdFrom": {"$from": {"task": 5, "output": "import"}},
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
3 pisco_vit_classify     gpuworker   classify + embeddings
4 pisco_ecotaxa_export   gpuworker   crops, TSV, zip
5 pisco_ecotaxa_import   ecotaxa     create project, import
6 pisco_ecotaxa_features ecotaxa     attach embeddings
```

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

In the GUI: the task manager page links each task to its log.

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
