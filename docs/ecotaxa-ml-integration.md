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

That matters because `ecotaxa_ML_back` is pinned to **Python 3.8 + CUDA 11.4 +
TensorFlow**, while the dual ViT is PyTorch/transformers. Forking it means
porting a PyTorch model into an EOL TensorFlow environment and rebasing it
forever, in exchange for one thing only: EcoTaxa's own "Predict" button
driving the job. Not worth it.

## The appearance contract — read this first

`training/README.md` in `process_pisco_profiles` is explicit that a classifier
is only valid for crops that look like its training data, and names the cost of
getting it wrong: swapping the deconvolution model under the ATAIIR2604 models
**dropped the living rate by 83–93% at unchanged confidence**.

There are now **three** axes on which our exported crops must match the regime a
model was trained under. Treat deconvolution, isolation, scale bar and
classifier as one versioned unit.

| axis | trained as (ATAIIR2604_NorthSea) | HDF5 export today |
|---|---|---|
| deconvolution model | LUCYD **v5a** | v5a when `pisco_deconvolve` ran first ✅ |
| neighbour isolation | **isolated** | **not isolated** ❌ |
| scale bar | absent | present, but exactly removable ✅ |

Two consequences, both of which are work:

- **The exporter needs an `isolateCrops` option.** `result.h5` carries
  `maskPixelData`, and the viewer already does the operation
  (`np.where(mask > 0, crop, 255)` — non-object pixels to white). The exporter
  ignores it today, so its crops are out of domain for a model trained on
  isolated ones. This is the same axis the README warns about, so it should be
  expected to matter, not assumed not to.
- **The scale bar must be stripped before inference**, via
  `scale_bar.strip_scale_bar()`. That is precisely why it was built reversible.
  Read the PNGs with PIL so the `pisco_orig_size` text chunk survives; a
  converter that drops text chunks silently leaves right-padding in place.

Any inference tool we build must record which deconvolution model, which
isolation setting and which classifier produced a result, or the next
out-of-domain regression will be as hard to attribute as the last one.

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

1. **`isolateCrops` in the exporter.** Small, and everything downstream depends
   on matching the training regime.
2. **Features + similarity search.** Self-contained, no taxonomy mapping needed,
   and immediately useful for annotation even with no predictions.
3. **Taxonomy mapping.** Decision work, not code work.
4. **Predictions.**

## Risks

- **Out-of-domain crops.** The isolation mismatch is real today. Quantify it
  before trusting any prediction run: same crops, isolated and not, through the
  same model.
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
