# Comparing the legacy Python pipeline with the C++ segmenter route

Status: draft, 2026-09-10. Code facts below were verified by reading the
**deployed** sources on CT 112 (`/opt/pisco/tools/pisco_segmenter_py`,
`/opt/pisco/tools/pisco_segmenter_cpp`) on that date, not the clones on the Mac.

> **Read this first.** `~/pisco_segmenter` on the Mac (commit `50be23e`) is an
> older, materially different version: it upsamples frames to 5120x5120 and
> thresholds at `mean + n_sigma*std`. The deployed tool does neither. Any
> analysis must name the commit it ran against.

## What this is for

Decide whether the C++ route (`pisco_segment` -> `pisco_deconvolve` ->
extraction -> `pisco_ecotaxa_export` -> ViT) can replace the legacy all-Python
route (`pisco_segment_py`) for production, and quantify what changes if it does.

Two phases, in order:

1. **Agreement.** Particle by particle, how much do the two routes produce the
   same objects, the same crops, the same classifications, the same profile?
   No ground truth needed.
2. **Correctness, targeted.** Hand-validate a sample drawn mostly from where
   they *disagree*, to decide who is right when they differ.

Phase 1 defines the strata for phase 2, so it comes first.

## The two routes, as deployed

### Legacy Python (`pisco_segment_py`)

| stage | what it does | source |
|---|---|---|
| read | `cv.resize(img, (2560, 2560))` **unconditionally** | `reader.py:97` |
| background | pixel-wise `max` over a **centred window of 6 frames, excluding self** | `bg_correction.py:106-147` |
| correction | `absdiff(img, bg)`, then `bitwise_not` -> dark-on-light | `bg_correction.py:148-150` |
| frame gate | frames with `std(img) <= 2` are **skipped entirely** | `bg_correction.py:104` |
| deconvolution | LUCYD on the **whole frame**, **before** detection | `deconvolution.py` |
| threshold | `bitwise_not` back to bright-on-dark, then fixed **10** | `detection.py:211, 229` |
| FOV mask | circular mask, radius from `compute_radius()` (1500 on m202_a); pixels outside are zeroed | `detection.py:217-221` |
| objects | contours, no morphology, **no bbox padding**, **no merging** | `detection.py:236, 252-262` |
| area filter | record if `>= min_area_to_segment` (10), **save a crop only if `> min_area_to_save` (50)** | `detection.py:266, 278` |
| output | `Crops/`, `Deconv_crops/`, `Masks/`, `Data/*.csv`, `settings.csv` | |

**Bug: `Data/*.csv` holds two different coordinate conventions in the same two
columns.** `detection.py:280` reassigns `x, y` from the box centre to the
top-left corner, but that reassignment is *inside* `if settings.save_crops:`.
The `saved = 1` append at `:322` therefore writes **top-left**, while the
`saved = 0` append at `:328` writes **centre**. Verified against the raw frames:
for saved objects, correlating the stored crop against the frame region gives
r = +0.99 at top-left and -0.06 at centre. The EcoTaxa export is unaffected
because it only ever uses saved objects, but anything reading the unsaved rows
gets positions off by half a box. `scripts/compare_pipelines.py` compensates;
the pipeline itself should be fixed.

(Related latent trap in the same block: `raw_crop` and `crop_masked` are read by
`calculate_regionprops` outside the `if settings.save_crops:` guard, so
`save_crops = False` would raise `NameError`.)

Dead settings, worth knowing before anyone tunes them: `equalize_hist` is
declared and never read; `resize` is `False` in the deployed config
(`segmenter.py:223`) and if it were `True` it would scale areas by 4 and boxes
by 2 (`detection.py:252-262`) **without any resize actually happening**, since
that line is commented out. That is a live landmine.

### C++ route

Pass 1, `Mode::BACKGROUND_CORRECTION_AND_COMPRESSION`:

| stage | what it does | source |
|---|---|---|
| background | **max of pairwise minima** over a stack of `imageStackSize = 20` | `background_correction.cpp` |
| correction | `absdiff(img, bg)`, stays bright-on-dark | same |
| threshold | fixed `threshold = 10`, then **3x3 MORPH_CLOSE** | `detection.cpp:117-119` |
| objects | contours, filter `minContourArea = 10`, **pad +10 px per side** | `detection.cpp:9-31` |
| merge | overlapping boxes **merged** iteratively, up to `maxMergeIterations = 20`; on non-convergence **the whole frame becomes one ROI** | `detection.cpp:33-67` |
| output | ROIs as `pixelData` in `checkpoint_correction_*.h5` | |

Then `pisco_deconvolve` runs LUCYD over those ROIs packed into atlases.

Pass 2, `Mode::OBJECT_EXTRACTION`, re-reads `targetPath`:

| stage | what it does | source |
|---|---|---|
| re-threshold | **per ROI**, `max(TRIANGLE(roi), 10)` — locally adaptive | `detection.cpp:141-149` |
| mask | that binarisation is the stored `maskPixelData` | `detection.cpp:151` |
| split | contours within the ROI, filter `minContourArea`, **pad +10 px again** | `detection.cpp:157-165` |
| output | one object per contour into `result.h5` | |

So a merged ROI *is* re-split into individual objects — the final object count is
per contour, not per ROI. But an isolated object's final crop carries **two
roundsackers of +10 px padding**.

## Verified differences that will move the numbers

Ordered by expected effect size.

1. **Circular FOV mask, Python only.** The C++ has none, so it detects in the
   frame corners the Python route zeroes out. Pure count offset, and trivially
   correctable in analysis by applying the same mask to C++ detections.
2. **Save threshold 50 vs 10.** Same units in both routes now (`resize=False`),
   so this is a genuine 5x area threshold difference. The Python CSV still
   records every detection `>= 10` with `saved = 0`, so a matched comparison is
   available from existing runs without re-running anything.
3. **Second, adaptive threshold in the C++ extraction.** Masks and therefore
   areas, ESD, biovolume and every mask-derived morphometric come from a
   per-ROI TRIANGLE threshold floored at 10, while the Python masks come from
   the global fixed 10. Expect systematically **tighter C++ masks**, most
   visible on faint objects.
4. **Bbox padding, 0 vs ~10 px.** For a 10 px object the C++ crop is several
   times the Python crop in area. This is a first-order appearance difference
   for a size-sensitive ViT, and it also decides whether neighbours appear in
   frame.
5. **Deconvolution on opposite sides of detection.** Python: whole frame, before
   detection, so **the deconvolution model changes what is detected**. C++: per
   ROI, after detection, so it **cannot**. Also different context: a whole
   2560^2 frame versus a padded ROI in an atlas.
6. **Background estimator.** Centred 6-frame max excluding self, versus
   max-of-pairwise-min over 20. Window length governs how much a large or slow
   object subtracts itself out of its own background; expect large objects more
   hollowed-out in the Python route.
7. **MORPH_CLOSE, C++ only.** Bridges near-touching pixels: slightly fewer,
   slightly larger contours.
8. **ROI merging, C++ only** — at pass 1. Mostly undone by the pass-2 re-split,
   but the non-convergence fallback (whole frame as one ROI) should be checked
   on profile-start frames, which carry the warm-up spike.
9. **Frame gate.** Python drops frames with `std <= 2` outright; the C++ does
   not. Denominators must match.

## The camera profile

The 5120 in the old clone is legacy from a 5120x5120 camera. The fix is one
file both tools read, and a hard guard against upsampling.

```json
{
  "camera": "PISCO2",
  "nativeWidth": 2560,
  "nativeHeight": 2560,
  "pixelSizeUm": 22.5,
  "fovMaskRadiusPx": 1500
}
```

- **Never upsample.** A requested size larger than native is an error, not a
  resize. Downsampling below native is allowed when explicitly requested.
- The C++ already has the mechanism (`resizeImageToSpecifiedSize`, `imageSizeX`,
  `imageSizeY`); it needs to read the profile instead of its own config keys.
- The Python route's `reader.py:97` resizes to a hardcoded 2560 regardless of
  the `resize` flag, and areas are not compensated. That is the bug this closes.
- `pixelSizeUm` currently lives in `scale_bar.PIXEL_SIZE_UM` (and was wrong at
  23 until recently); `fovMaskRadiusPx` is currently derived per profile by
  `compute_radius()`. Both belong here so the two routes cannot drift apart.

Fixing the resize path changes areas and crop scale, so **old-route output
produced before the fix is not comparable to output after it**. Re-run the
reference set once the camera profile lands.

## Stage-isolation ladder

Each rung changes one thing. Run on at least three profiles spanning conditions
(surface-dense, deep-sparse, a mid-profile start), because single-profile numbers
are not stable — see the warm-up artefact.

- **E0 — harmonised detection.** Both routes, deconvolution **off**, same frames.
  Apply the FOV mask to both in analysis; compare at both area thresholds
  (>=10 and >=50). Isolates background estimation + morphology + padding.
- **E1 — deconvolution's effect on detection.** Python route only, deconv on vs
  off, same frames. The count delta is the effect, with no cross-pipeline
  confound. The C++ delta is zero by construction; assert it.
- **E2 — deconvolution context.** One frame, no pipeline: deconvolve-then-crop
  versus crop-then-deconvolve on identical boxes, same weights. Isolates
  whole-frame versus ROI-atlas context.
- **E3 — crop appearance.** Matched objects only. Compare crops after
  normalising inversion and padding. Isolates what the classifier sees.
- **E4 — classification.** Same ViT weights, same preprocessing, over each
  route's crops. Isolates the crop, not the model.
- **E5 — end to end.** Both routes to EcoTaxa, full profile. The headline
  numbers, interpretable only in the light of E0-E4.

Pin, for every rung: segmenter commits, the LUCYD checkpoint, ViT weights,
`imageStackSize`/`bg_size`, thresholds, `skipFirstNImages`, and the camera
profile.

## Metrics

### Detection geometry

Match per frame by bbox IoU with Hungarian assignment; `IoU >= 0.5` is one-to-one.
Compare C++ boxes **de-padded** by the known padding, and record raw as well.

- matched fraction each way (neither route is truth — report both, not an F1)
- split / merge rate: 1-to-N and N-to-1 components of the overlap graph
- per-frame count agreement as Bland-Altman: bias plus limits of agreement
- disagreement stratified by ESD bin
- area and ESD agreement on matched pairs: regression slope and bias
- repeat detection of the same particle across consecutive frames

### Crop appearance (matched objects)

- **embedding cosine distance under the same ViT encoder**, scaled against the
  within-route nearest-neighbour distance distribution. More decision-relevant
  than SSIM: it answers whether the classifier sees the same object
- SSIM / MAE after normalising inversion, padding and scale
- sharpness: Laplacian variance and radial power-spectrum slope, per route and
  as a delta against that route's own non-deconvolved crop
- ringing overshoot in a ring just outside the object mask
- background pedestal: mean and sigma of non-object pixels
- object extent (object px / crop px), and whether neighbours intrude

### Classification

- top-1 agreement **and** Cohen's kappa
- mean Jensen-Shannon divergence between softmax vectors; top-5 Jaccard
- entropy / OOD-score distribution shift — the early warning that one route's
  crops are out of the training distribution
- binary living/non-living gate agreement, isolated, since it gates everything
- route-A x route-B confusion matrix, to expose systematic drift
- **living fraction** — the number the deconvolution-swap risk is denominated in
  (`training/README.md`: 83-93% of the living rate)

### Profile / ecology

- depth-binned abundance per ESD class: correlation and RMSE
- NBSS slope
- total biovolume, not only counts
- Bray-Curtis dissimilarity of community composition, per depth bin

### Operational

- wall-clock per stage and end to end, CPU and GPU separated; frames/s, objects/s
- peak GPU memory and target-card fit (whole-frame deconvolution does not fit the
  P4; the 3060/3080 question is still untested)
- scaling with parallel workers
- storage: bytes/object **and inode count**
- determinism: run each route twice; ungrounded nondeterminism caps the smallest
  difference the comparison can resolve
- time-to-EcoTaxa including export, upload and import job

## Validation of disagreements

Validating only disagreements is the right instinct — highest information per
label, and it directly answers "when they differ, who is right". Two adjustments
make it hold up.

**Include a small agreement stratum.** Labelling only disagreements assumes the
agreement set is correct. Both routes share a background-subtraction family and
the same ViT weights, so correlated errors are likely: they can agree and both be
wrong. Spend roughly 50-100 of 400 labels on a random sample of agreements. That
is what turns "who wins the disputes" into an actual precision and recall per
route.

**Stratify and record the sampling fraction.** Strata: detected-only-by-Python,
detected-only-by-C++, split/merge, class-disagreement, agreement. Sample within
each, re-weight when estimating any population quantity. Stratify by size class
too, or 400 labels get spent on specks while the large objects that carry the
biovolume go unchecked.

**Label detection disagreements on raw-frame patches, not on either route's
crop.** Only one route produced a crop, so showing it reveals which route found
the object, and the sharper rendering wins by default. A fixed-size patch cut
from the raw frame removes that bias. Classification disagreements exist in both
routes, so show them blind and randomised.

**Re-show ~10% as duplicates** to measure intra-rater agreement. Without it a 15%
disagreement rate cannot be distinguished from label noise.

EcoTaxa is the natural annotation tool: an arbitration project holding exactly
the sampled objects, with stratum and route provenance in free columns, hidden
from the sorting view.

## Acceptance criteria

To be agreed **before** results are looked at. Placeholders:

- total abundance within +/- X% after harmonising FOV mask and area threshold
- living fraction within +/- Y%
- top-1 classification agreement above Z on matched objects
- NBSS slope difference below S
- no regression in wall-clock per profile at equal worker count

## Harness

`scripts/compare_pipelines.py` — matching and metrics. See its `--help`.

It needs `h5py` (and uses `scipy` for Hungarian assignment and `cv2` for
contour areas when present), so run it with `/opt/pisco/venvs/torch/bin/python`
on a worker.

    compare_pipelines.py --old /mnt/pisco/testdata/oldpipe_a \
      --new <workflow>/0_pisco_segment/results/result.h5 \
      --out /mnt/pisco/compare/harmonised \
      --min-area 50 --old-saved-only --skip-first 6

Outputs `summary.json`, `per_frame.csv`, and `matches.csv` — the per-object
table whose `stratum` column (`matched`, `only_old`, `only_new`) is what the
validation sample is drawn from.

## First results — preliminary

m202_a, 46 frames, `oldpipe_a` against workflow
`2c7e9867-b3dd-45b3-9ac9-696af916d4c7`. **Provenance is incomplete**: the old
run's `settings.csv` does not record whether deconvolution was on, and the
C++ workflow's settings were not captured. Treat these as a harness shakedown,
not as the comparison. The controlled E0 arms still need to be run.

Harmonised (FOV mask on both, C++ de-padded 10 px, area > 50 on both, old
saved-only), warm-up frames dropped:

|  | value |
|---|---|
| frames compared | 40 (first 6 skipped) |
| objects | old 3269, C++ 3878 (+19%) |
| matched at IoU >= 0.5 | 1832 — 56.0% of old, 47.2% of C++ |
| per-frame count difference | bias -15.2, LoA [-119.0, +88.5] |
| area ratio C++/old, matched | median 0.874 (p10 0.64, p90 1.36) |
| split / merge | 37 splits, 10 merges |

Matched fraction by ESD, old-side / C++-side:

| ESD | old n | matched | C++ n | matched |
|---|---|---|---|---|
| 125-250 um | 919 | 44.3% | 1295 | 24.9% |
| 250-500 um | 1421 | 68.5% | 1879 | 62.7% |
| 500-1000 um | 622 | 63.7% | 573 | 51.8% |
| >1000 um | 307 | 17.9% | 131 | 25.2% |

Three things to read from this:

- **The area ratio of 0.87 is the TRIANGLE threshold showing up as predicted.**
  C++ masks are systematically tighter, so every mask-derived morphometric,
  ESD and biovolume is biased low relative to the Python route.
- **Disagreement is worst at the extremes, not in the middle.** The
  125-250 um class matches poorly because that is where the tighter mask pushes
  objects across the area threshold; the >1000 um class matches poorly *and*
  the old route reports 2.3x as many of them, which is the C++ re-split
  breaking large blobs into pieces.
- **Without `--skip-first 6` the aggregates are meaningless.** The same
  comparison over all 46 frames gives a per-frame bias of -96.9 with limits of
  agreement of [-616, +422]; the warm-up frames dominate everything.

Not yet measured: crop appearance, classification, profile-level and
operational metrics. Those need the controlled E0-E5 runs.
