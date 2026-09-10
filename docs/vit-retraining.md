# Retraining the dual ViT from EcoTaxa

The chain in [hub-user-guide.md](hub-user-guide.md) ends with predictions
sitting on objects in EcoTaxa. This is the way back: what a human validates
there becomes the training set for the next pair of models, without anyone
copying crops around or running a notebook on a laptop.

```
segment -> classify -> export -> import ──> a human validates in EcoTaxa
                                                      |
   pisco_ecotaxa_labels  <───────────────────────────-─┘
            |
   pisco_vit_trainset ──> pisco_vit_train (binary)     ──┐
                     └──> pisco_vit_train (multiclass) ──┴─> pisco_vit_classify
```

Nothing here is new science: it is the recipe in `training/` in
`process_pisco_profiles`, which is where the shipped models came from, wired up
as tools so it runs on the cluster's GPUs against data already on the share.

## The three tools

| tool | runs on | in | out |
|---|---|---|---|
| `pisco_ecotaxa_labels` | `ecotaxa` | project ids | `labels.csv` |
| `pisco_vit_trainset` | `pisco-tools` | labels + the run's crops | `binary/`, `multiclass/` |
| `pisco_vit_train` | `pisco-tools`, `gpu` | one imagefolder | `best_model/`, `eval_report.json` |

The label fetch is a separate tool because **EcoTaxa credentials live on the
EcoTaxa machine**, not in task settings — the Hub renders those onto shared
storage in plain text. Everything downstream works from `labels.csv` and needs
no credentials at all.

## Before you start

**Validate objects in EcoTaxa.** Only `statusFilter = V` objects are fetched,
and that default is the point: predicted objects carry this pipeline's own
output, and training on those teaches the model its own mistakes. The tool
fails loudly when a project has no validated objects rather than quietly
training on nothing.

**To retrain the binary stage, the not-living objects have to be in the
project and validated too.** They arrive labelled — `not-living` is a real
EcoTaxa category (84960), while `living` is a gate that is never written — but
a project holding only the living taxa can only ever rebuild the taxon stage.
`nonLiving` is the setting that decides which validated categories form the
not-living side; `bubble` and `t001` belong there when present, because they
are imaging artifacts and calling them living reintroduces exactly the false
positives the binary stage exists to remove.

**Know which run the objects came from.** `cropsPath` must point at the
segmenter results (`result.h5`) or old-pipeline output the EcoTaxa objects were
exported from. Crops are matched by object id, so a wrong run simply matches
nothing and the task fails with a count of what it could not find.

## The workflow

```json
{"name": "retrain the dual ViT", "tasks": [
  {"tool": "pisco_ecotaxa_labels", "settings": {
    "projectIds": "23022,23052,23196", "targetPath": ""}},

  {"tool": "pisco_vit_trainset", "settings": {
    "labelsPath": {"$from": {"task": 0, "output": "labels"}},
    "cropsPath": "/mnt/pisco/hub-data/workflows/<id>/0_pisco_segment/results/",
    "targetPath": "", "isolateCrops": false,
    "nonLiving": "Unknowns,not-living,bubble,t001",
    "excludeMulticlass": "multiple species", "minPerClass": 20}},

  {"tool": "pisco_vit_train", "settings": {
    "dataPath": {"$from": {"task": 1, "output": "trainset"}},
    "stage": "binary", "targetPath": "", "epochs": 30,
    "initModel": "/opt/pisco/models/ViT_custom_size_sensitive_binary/best_model"}},

  {"tool": "pisco_vit_train", "settings": {
    "dataPath": {"$from": {"task": 1, "output": "trainset"}},
    "stage": "multiclass", "targetPath": "", "epochs": 30,
    "initModel": "/opt/pisco/models/ViT_custom_size_sensitive_v5/best_model"}}
]}
```

`projectIds` is in **priority order and the last one wins** on a duplicate crop:
EcoTaxa subsets are copies, so the same crop appears in a project and in every
subset drawn from it, and letting the later project win is what lets a
re-validation supersede the original call.

The two training tasks read the same trainset and are independent, so with two
GPU workers free they run at once. Each has `default_weight` 10 against a
per-machine capacity of 10, i.e. a training task owns its GPU.

`initModel` is the checkpoint being fine-tuned — normally the previous model
for that stage. Its head is reinitialised automatically when the class set has
changed (7 classes against 13 is fine). Training from
`google/vit-base-patch16-224-in21k` instead works, but wants
`learningRate` around 2e-4 rather than the 5e-5 default.

## Using the result

`pisco_vit_classify` takes a training task's **output directory** as its
`modelPath` and finds `best_model/` inside it, so a retrained model chains
straight into a classification run:

```json
  {"tool": "pisco_vit_classify", "settings": {
    "sourcePath": "/mnt/pisco/.../results/", "targetPath": "",
    "modelPath": {"$from": {"task": 2, "output": "model"}},
    "isolateCrops": false, "emitEmbeddings": true}}
```

`eval_report.json` beside it holds per-class precision, recall and F1, the
confusion matrix, the per-epoch history and the settings the run used. Read the
tail classes with care: single-digit support means the macro average is not a
statement about model quality.

## The traps

**`isolateCrops` must match.** Whether neighbouring objects were masked out to
white is one of the axes a classifier is bound to. Set it the same on the
trainset task and on every `pisco_vit_classify` that uses the resulting model.
Nothing checks this for you, and getting it wrong is an out-of-domain shift,
not a cosmetic difference — it once cost 83–93% of the living rate at unchanged
confidence.

**The deconvolution model is the other half of that coupling.** Crops from a
different deconvolution are different crops. Treat deconvolution, isolation and
classifier as one versioned unit; `trainset.json` in the trainset output records
the isolation setting and every source file the crops came from, so a model can
at least be traced back to the run that trained it.

**Preprocessing cannot drift.** `pisco_vit_train` runs its own preprocessing and
the classifier's over the same crops at startup and aborts if they disagree, so
the one axis that is checkable is checked on every run.

**A new class set means a new taxonomy map.** `ecotaxa_taxonomy_map.json`
translates ViT labels into numeric EcoTaxa taxon ids, and the export refuses to
run with unresolved labels. Any class the retrained model emits that the map
does not know has to be resolved there before predictions can go back into
EcoTaxa — see [ecotaxa-ml-integration.md](ecotaxa-ml-integration.md).

**Classes below `minPerClass` are dropped**, because a class that cannot be
split into train/validation/test cannot be evaluated. The task prints exactly
what it dropped; that list is the honest statement of what the new model will
never predict.

## Verified

2026-09-10 on the pvetemp testbed, as one four-task Hub workflow: label fetch
on the `ecotaxa` machine, trainset build and training on `gpuworker1`, then a
classification run using the model that had just been trained. 1139 EcoTaxa
objects matched 1139 crops in the source `result.h5`; ViT-base trained at ~28 s
per epoch in fp32 on a Tesla P4.

The run used `statusFilter = P` because the testbed had no validated objects —
that exercises every mechanism but is not a real retraining. `auto` precision
correctly chose fp32: the P4 is Pascal, where fp16 is slower, not faster.
