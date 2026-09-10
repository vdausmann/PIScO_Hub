# Handover: train future ViTs against EcoTaxa's taxonomy

**For a later session. Nothing here is started.**

Today's models predict their own label vocabulary, which is then translated
into EcoTaxa taxon ids by `ecotaxa_taxonomy_map.py` in `process_pisco_profiles`.
That translation works and is verified, but it is a **bridge, not the
destination**. The next models trained should predict EcoTaxa taxa directly.

This note exists so whoever picks that up does not have to re-derive why.

## Why translation is a bridge

The mapping is a hand-resolved file. It works, but it carries costs that grow:

* **Ambiguity needs a human.** Of 13 labels in the current model, two could not
  be resolved automatically and had to be decided by hand:
  * `Ctenophora` matches `Ctenophora<Animalia` (comb jellies) **and**
    `Ctenophora<Fragilariaceae` — a diatom. Picking the lowest id gives the
    diatom.
  * `Copepoda` matches a phylogenetic Class and a morphological category.
* **It rots.** EcoTaxa taxa get renamed and deactivated. `ecotaxa_taxonomy_map.py
  check` exists precisely to catch that, which means someone has to run it.
* **It is lossy in one direction.** The model cannot express a distinction its
  own vocabulary does not have, however fine EcoTaxa's tree is.
* **It hides label drift.** Two models trained months apart can use the same
  word for different things, and the mapping file will happily translate both.

A model trained on EcoTaxa taxon ids has none of these. Its `id2label` *is* the
mapping, and it is fixed at training time.

## What to do

Change where labels come from in `training/build_trainset.py`. It already pulls
validated objects from EcoTaxa via `ecotaxa_py_client` — it just discards the
taxon identity and keeps a cleaned display name.

The concrete change: keep the numeric `classif_id` alongside the name, and use
`str(classif_id)` (or the id itself) as the class label, with `id2label` mapping
back to the display name for humans. Then a prediction is a taxon id already,
`ecotaxa_write_predictions.py` needs no mapping file, and
`classify_auto_multiple` takes the model output as-is.

### Decisions that need making, not coding

1. **What granularity?** EcoTaxa's tree is far finer than any classifier should
   attempt. Training on raw leaf ids will produce hundreds of classes with a
   handful of examples each. You almost certainly want to roll up to a chosen
   set of nodes — which set is a science decision, and it should be **recorded
   in the model** (a list of permitted taxon ids), not implied by whatever the
   training set happened to contain.
2. **How to handle the rollup at prediction time.** If an annotator validates
   something finer than the model can predict, the training set builder has to
   climb the tree to the nearest permitted node. `taxonomy.parent_id` makes that
   easy; deciding when *not* to roll up is the harder half.
3. **The binary stage stays a gate.** `living` / `not-living` is not a taxon
   pair — `living` matches only `living<t011` and `living<Chydoridae` in the
   taxonomy, neither of them meaningful. Keep that stage outside the taxonomy
   entirely, exactly as `GATE_LABELS` does now.
4. **Instance portability.** Taxon ids come from EcoTaxoServer and are shared
   across instances, so a model trained against the testbed's taxonomy should be
   valid against production. **Verify this before relying on it** — check a
   handful of ids on both instances. If it does not hold, the model is bound to
   one instance, which is a much bigger constraint than the mapping file.

## What already exists to build on

| piece | where | note |
|---|---|---|
| trainset builder | `training/build_trainset.py` | pulls validated labels from EcoTaxa; currently drops the id |
| training script | `training/train_vit.py` | takes `id2label`/`label2id` already, so ids drop straight in |
| mapping + validator | `ecotaxa_taxonomy_map.py` | keep for old models; `check` is still useful |
| prediction writer | `ecotaxa_write_predictions.py` | drop the `--taxonomy-map` requirement once labels are ids |
| taxonomy table | EcoTaxa Postgres | `id`, `parent_id`, `name`, `display_name`, `taxotype` (P/M), `taxostatus`, `rename_to` |

## The constraint that outranks all of this

Whatever is retrained must still match the crops it will see. `training/README.md`
records that changing the deconvolution model under a classifier cost **83–93% of
the living rate**. The axes are the deconvolution model, neighbour isolation, and
the scale bar (strippable, see `scale_bar.py`). Retraining is the moment to
*record* which regime a model expects, in the model directory, rather than
leaving it to be remembered — see [ecotaxa-ml-integration.md](ecotaxa-ml-integration.md).

A retrain against EcoTaxa taxonomy is a good opportunity to fix that too: ship a
small manifest beside the weights naming the deconvolution model, the isolation
setting, and the permitted taxon ids. Then a prediction run can refuse a
mismatch instead of quietly producing worse numbers.
