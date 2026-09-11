# Wiring the dual-ViT pipeline into EcoTaxa

A design for two capabilities on top of the export/import route in
[ecotaxa-testbed.md](ecotaxa-testbed.md):

1. **"Similar to" filtering** in EcoTaxa, driven by our own embeddings.
2. **Dual-ViT predictions** landing on objects as automatic classifications.

Status: specification. Nothing here is built. Every claim about EcoTaxa's
internals was checked against the running instance and the `/opt/dev`
checkouts (back `v3.3.1`, front `v3.2.6`); anything unverified is marked.

## The headline

Neither capability needs a fork of EcoTaxa, and neither needs a fork of
`ecotaxa_ML_back`. Similarity search is plain pgvector over a column we can
write ourselves, and predictions have a REST API. The work is a Hub tool that
runs after import, not a reimplementation of their ML backend.

We target the older `ViT_custom_size_sensitive_*` pair rather than
`ATAIIR2604_NorthSea`, because those were trained on non-isolated crops and so
match what the exporter already writes.

The fork question matters because `ecotaxa_ML_back` is pinned to **Python 3.8 +
CUDA 11.4 + TensorFlow**, while the dual ViT is PyTorch/transformers. Forking it means
porting a PyTorch model into an EOL TensorFlow environment and rebasing it
forever, in exchange for one thing only: EcoTaxa's own "Predict" button
driving the job. Not worth it.

## The appearance contract — read this first

`training/README.md` in `process_pisco_profiles` is explicit that a classifier
is only valid for crops that look like its training data, and names the cost of
getting it wrong: swapping the deconvolution model under the ATAIIR2604 models
**dropped the living rate by 83-93% at unchanged confidence**. Treat
deconvolution, isolation, scale bar and classifier as one versioned unit.

### Which models we target

**Not `ATAIIR2604_NorthSea`.** Those were trained on neighbour-**isolated**
crops, which our HDF5 export does not produce. We use the older pair instead —
the config default:

```json
"model_dirs": {
  "binary": ".../ViT_custom_size_sensitive_binary/best_model",
  "living": ".../ViT_custom_size_sensitive_v5/best_model"
}
```

These predate the isolation work — there is no isolation code anywhere in
`process_pisco_profiles` — so they were trained on non-isolated crops and match
what the exporter writes today. That removes the isolation mismatch entirely.

### Where the axes now stand

| axis | older pair trained as | HDF5 export today |
|---|---|---|
| neighbour isolation | not isolated | not isolated ✅ |
| scale bar | absent | present, but exactly removable ✅ |
| deconvolution model | **unconfirmed** | v5a, when `pisco_deconvolve` ran ⚠️ |

The scale bar must be stripped before inference via
`scale_bar.strip_scale_bar()` — that is why it was built reversible. Read the
PNGs with PIL so the `pisco_orig_size` text chunk survives; a converter that
drops text chunks silently leaves right padding in place.

### The one axis still open: deconvolution

Isolation is settled; deconvolution is not, and it is the *other* half of the
coupling the README warns about.

`pisco_deconvolve` defaults to `lucyd-edof-plankton_231204.pth`. Our M202
workflow explicitly used `best_loss_v5a_noGrad_ablation.pth`. Those are not
interchangeable: running the same 46 frames through both produced **zero
byte-identical groups and a mean absolute difference of 15** on the uint8
scale. If the older ViTs were trained on crops from the older default, feeding
them v5a crops is precisely the mismatch that cost 83-93% previously.

The `v5` in `ViT_custom_size_sensitive_v5` is ambiguous — it may name the ViT
revision or the LUCYD v5 models. **This needs confirming before any prediction
run is trusted**, and it is cheap to settle: run the same crops through the
classifier under both deconvolutions and compare living rates. Until then,
either match the deconvolution the models were trained with, or treat the first
run as a calibration rather than a result.

Whatever we build must record which deconvolution model, which isolation
setting and which classifier produced a result, or the next out-of-domain
regression will be as hard to attribute as the last one.

## Part 1 — "similar to"

### How EcoTaxa does it

`API_operations/SimilaritySearch.py` builds one query:

```sql
cnn.features::halfvec(50) <-> (SELECT features::halfvec(50)
                               FROM obj_cnn_features_vector WHERE objcnnid=<target>)
ORDER BY l2_dist LIMIT n
```

Plain pgvector L2 distance (`vector` 0.8.6 is installed). **It does not care
what produced the vector.** There is no model identity stored beside it, no
checksum, no provenance — a row is a row.

### What we must satisfy

- **Exactly 50 dimensions.** `obj_cnn_features_vector.features` is
  `vector(50)`, keyed by `objcnnid` → `obj_head.objid`. A ViT-Base embedding is
  768 (confirm against the actual checkpoint — `train_vit.py` takes `--init`
  with no default), so it needs reducing. EcoTaxa's own pipeline is CNN → PCA →
  50, so PCA to 50 is the sanctioned shape rather than a workaround.
- **Every object in the project needs a row.** `DeepFeatures.find_missing()`
  outer-joins the table and, if *any* object lacks features, returns
  *"Some or all features are missing"* and does no search at all. Coverage is
  all-or-nothing, not degraded.
- **Write directly to Postgres.** There is no REST endpoint for features; the
  `DeepFeatures.save()` method is commented out in the source. This is how
  `ecotaxa_ML_back` works too — it shares the database rather than calling the
  API.

Useful consequence: when features are complete, the `project.cnn_network_id`
check is skipped entirely, so we never have to register as one of their CNN
models.

### Expectation setting

50 dimensions is a tight budget for a ViT embedding. It is what EcoTaxa squeezes
its own CNN features into and it evidently serves them, but neighbourhood
quality will be PCA-limited rather than ViT-limited. Worth measuring on a
validated project before promising much.

The PCA basis must be **fitted once and stored**, not refitted per run — two
projects reduced under different bases are not comparable, and the table has no
field to tell them apart.

## Part 2 — predictions

Three routes exist. They are not equivalent.

### (a) In the import TSV — simplest, already proven

`object_annotation_category` + `object_annotation_status = predicted` are
ordinary import columns, and `rename_for_ecotaxa()` in the old pipeline already
populates them from `top1`. The exporter writes both columns today (empty).

- No API calls, no credentials, no second pass.
- Predictions arrive atomically with the objects.
- **But**: only at import time. Re-predicting an existing project means a
  re-import with `update_mode`, and it writes a display name, so the taxonomy
  mapping has to be right at export time.

### (b) `POST /object_set/classify_auto_multiple` — recommended

```
{ target_ids:      [objid, ...],
  classifications: [[taxon_id, ...], ...],   # top-k per object
  scores:          [[0.0-1.0, ...], ...],    # parallel to classifications
  keep_log:        true }
```

Verified signature (`API_models/objects.py:434`). Scores are asserted to be
floats in `[0, 1]`. The nested lists take top-k per object, which fits the dual
ViT's `top1..top5` / `prob1..prob5` output directly rather than flattening it.

- Works on an existing project, repeatably, without re-import.
- Uses taxon **ids**, so the mapping is explicit rather than string matching.
- Writes proper automatic classifications, so EcoTaxa's validation workflow and
  the `prediction` table behave normally.

### (c) Fork `ecotaxa_ML_back`

Buys the "Predict" button inside EcoTaxa. Costs a Python 3.8/TensorFlow
environment for a PyTorch model, plus permanent rebasing. **Not recommended.**

### Recommendation

**(b)**, with (a) as a convenience for the first import of a fresh project.
Build (b) first; it subsumes (a).

## Shared plumbing

### Object identity

Two join keys already line up, and `build_trainset.py` uses the second, so
prefer it for consistency:

- `obj_head.orig_id` == our `object_id` (`{frame}_{n}`)
- `images.orig_file_name` == our `img_file_name`

Note EcoTaxa **subsets are copies**: the same physical crop can exist under
several `objid`s in different projects. `build_trainset.py` already deduplicates
for this reason. Feature writing must decide whether it targets one project or
every copy.

### Taxonomy mapping

This is the only genuinely new design work, and it needs a decision per class
rather than a guess.

`classify_auto_multiple` wants `classif_id` referencing `taxonomy.id`. Our
classes are model labels (`copepoda`, `detritus`, …). `ECOTAXA_TAXON_MAP` in
`utils.py` handles the display-name remapping (`Copepoda` →
`Copepoda<Multicrustacea`); what is missing is name → id resolution against the
synced `taxonomy` table (12,044 rows on the testbed).

Requirements:

- A **versioned mapping file** in `process_pisco_profiles`, not a lookup at
  runtime — an unresolvable or ambiguous class must fail loudly at load, not
  silently drop objects mid-run.
- Resolution must be exact and unambiguous; several taxa share a leaf name.
- The binary stage's `not-living` classes need a decision: map to `Unknowns`,
  to a detritus taxon, or leave unclassified.

## Proposed shape

One Hub tool, `pisco_ecotaxa_predict`, running after import:

```
settings: projectId, ecotaxaUrl, credentials,
          modelPath, pcaPath, taxonMapPath,
          writeFeatures (bool), writePredictions (bool)
```

1. Resolve the project's objects → `objid`, `orig_file_name`
2. Load crops, **`strip_scale_bar()`**, apply `custom_image_processor`
   (longest edge → 224, centre-pad white, normalise to [-1, 1])
3. Dual ViT: binary living/not-living, then taxon over living
4. `classify_auto_multiple` with top-k + scores
5. PCA → 50, write `obj_cnn_features_vector` directly

Runs on the GPU workers where the PyTorch stack already lives.

Open: where crops come from at step 2 — re-read from `result.h5` (clean, no
strip needed, but needs the workflow) or from the imported PNGs (matches
exactly what EcoTaxa holds). Re-reading the HDF5 is probably better: it skips
the strip/pad round trip and keeps isolation a rendering choice rather than a
property baked into files.

## Sequencing

1. **Confirm the deconvolution the older ViTs were trained under.** Cheap, and
   it gates whether any prediction number means anything.
2. **Features + similarity search.** Self-contained, needs no taxonomy mapping,
   and immediately useful for annotation even with no predictions.
3. **Taxonomy mapping.** Decision work, not code work.
4. **Predictions.**
5. **`isolateCrops` in the exporter** (see below) — wanted regardless, but no
   longer a prerequisite now that we target the non-isolated models.

## `isolateCrops` — worth having on its own merits

Not needed for the older models, but specced because it is wanted anyway.

A bounding box usually catches parts of its neighbours. `result.h5` stores
`maskPixelData` saying which pixels belong to *this* object, and the viewer
already does the operation at display time:

```python
np.where(mask > 0, crop, 255)   # non-object pixels to white
```

Adding it to the exporter is a handful of lines, since the mask is already read
there for morphometrics. What it buys:

- Crops that match the ATAIIR2604 models, should we want them later.
- A cleaner training set: `build_trainset.py` derives its images from exported
  crops, so isolation becomes a property of the export rather than something
  reproduced by hand.
- Less neighbour contamination in the 50-dim embeddings, which should sharpen
  similarity search.

It must be a **setting, not a default**: it is one of the axes a classifier is
tied to, so flipping it silently is the failure mode this whole section exists
to prevent. Record it alongside the deconvolution model in whatever provenance
we keep.

## Risks

- **Out-of-domain crops.** Isolation is settled by targeting the older models,
  but the deconvolution axis is not. Quantify before trusting any prediction
  run: same crops through both deconvolutions, same classifier, compare living
  rates.
- **All-or-nothing features.** One object without a vector disables similarity
  search for the whole project. Feature writing needs to be complete or
  transactional.
- **No provenance on features.** The table records nothing about which model or
  PCA basis produced a vector. Mixing runs corrupts the neighbourhood silently.
  Track it on our side, per project.
- **Credentials.** The tool needs an EcoTaxa account with annotate rights on the
  target project, plus direct Postgres access for the feature write. On the
  testbed both are local; against a production instance, direct DB access may
  simply not be available — in which case similarity search is off the table and
  only predictions are possible.
