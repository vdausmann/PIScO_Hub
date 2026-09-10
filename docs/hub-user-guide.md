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
an EcoTaxa archive with the predictions already in it.

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

### 4. Import into EcoTaxa — this part is manual

The chain ends at a `.zip`. **Importing it is not yet a Hub tool**, so:

1. Copy the zip somewhere EcoTaxa can read:
   `/srv/pisco/ecotaxa-imports/` on the Proxmox host, which EcoTaxa sees as
   `/file_srv/pisco/ecotaxa-imports/`.
2. In EcoTaxa (`http://10.11.11.114:8088`), create a project, then
   **Import** → "choose a folder or zip file on the server".

Objects arrive already carrying the ViT's predictions, ready to validate.

### 5. Enable "similar to" (optional, after import)

Similarity search needs the embeddings attached to the imported objects, and
that can only happen once EcoTaxa has assigned them ids. One more small
workflow, using the project id from step 4:

```json
{"name": "features", "tasks": [
  {"tool": "pisco_ecotaxa_features", "settings": {
    "embeddingsPath": "/mnt/pisco/hub-data/workflows/<id>/3_pisco_vit_classify/predictions/",
    "projectId": 8,
    "pcaPath": "/mnt/pisco/vit/pisco_pca50.npz"}}]}
```

It must cover **every** object in the project — EcoTaxa disables similarity
search entirely for a project where any object lacks a feature vector.

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

So `pisco_ecotaxa_features` (requires `ecotaxa`) always lands on the EcoTaxa
machine — that is how EcoTaxa's database stays on loopback instead of being
exposed to the whole subnet. Everything else goes to whichever GPU worker is
free.

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

## The manual steps, and why

Two things in the chain are not Hub tasks:

1. **Importing the zip into EcoTaxa.** There is no `pisco_ecotaxa_import` tool
   yet. This is the obvious next one to add.
2. **Attaching features**, which needs the project id that only exists after
   the import. Even with an import tool, this stays a separate step unless the
   Hub grows a way to pass a value (not a path) from one task to the next.

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
