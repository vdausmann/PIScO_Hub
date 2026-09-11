# Comparing the legacy Python pipeline with the C++ segmenter route

Status: draft, 2026-09-10. Code facts below were verified by reading the
**deployed** sources on CT 112 (`/opt/pisco/tools/pisco_segmenter_py`,
`/opt/pisco/tools/pisco_segmenter_cpp`) on that date, not the clones on the Mac.

> **Read this first.** `~/pisco_segmenter` on the Mac (commit `50be23e`) is an
> older, materially different version: it upsamples frames to 5120x5120 and
> thresholds at `mean + n_sigma*std`. The deployed tool does neither. Any
> analysis must name the commit it ran against.

## DIRECTIVE (Veit, 2026-09-11): counts come from raw, not deconvolved, data

**Particle counts are measured on the background-corrected image with no
deconvolution.** Deconvolution is not part of detection or counting. It has
exactly two jobs:

- **A.** make particles **larger than 50 px in area** recognisable to a human
  (contrast);
- **B.** make the segmentation masks sit better on the objects — *to be
  quantified*.

What follows from it:

1. The noise/particle threshold must be established on the **undeconvolved**
   background-corrected image, and particles counted between that threshold and
   50 px. The current fixed `threshold = 10` is not noise-referenced: measured
   on real frames it lands anywhere from **2.4 to 5.4 sigma** of the frame's own
   background, so sensitivity drifts frame to frame. That is tolerable when
   counting is a by-product; it is not when counting is the product.
   (The retraining log's "do not retune the detector threshold" was about not
   compensating for *model* differences, and does not bind this.)
2. The **blur/haze halo** around every particle, from the EDOF imaging, is part
   of the thresholded area. For a small particle the halo is a larger fraction
   of the measured area, so it biases the size spectrum exactly where the new
   counting regime lives. Needs measuring — an area-versus-threshold curve on
   isolated particles will show it.
3. **Particles above 50 px must match completely between the routes** when the
   mechanisms and models are the same. They currently do not. See the
   decomposition below.

### The background estimators diverged by accident, not by design

Traced 2026-09-11. The python route's background calculation was **never
changed** — the improvement was made in a different copy and never propagated to
the one that became the Hub tool. Two lineages:

| when | where | background |
|---|---|---|
| 2023-07-25 | `pisco_segmenter` (codebase.helmholtz.cloud), first commit `df38ffb` | `np.max` over raw frames |
| 2023-07-27 | same repo, `9a08862` — still the newest on that remote | unchanged |
| **2023-12-07** | `MaxSegmenterProcessPool/bg_correction.py` (the copy in `~/Downloads`, not under git) | **pairwise minima, then max** — the C++ algorithm, with the old plain-max line commented out directly above it |
| 2024-11-20 | `PIScO_Control_Center` first commit `56b9e0c` | carries the pairwise-min version |
| 2025-12-13 | `790859e`, and the working copy (2026-01-12) | unchanged |

The deployed `pisco_segmenter_py` descends from the **July-2023** lineage. It has
since gained a centred window, shape checks and error handling, but never the
pairwise minimum. So the C++ segmenter agrees with the *Control Center* python
version, and the Hub tool is the odd one out.

**Why a grep does not find this:** the line `bg = np.max(bg_imgs, axis=0)` is
*identical* in both versions. Only what fills `bg_imgs` differs — raw frames
versus pairwise minima. A diff of that line shows nothing.

Consequence: the decomposition below is measuring an accidental divergence, not
a design decision, and closing it is a small edit to the deployed
`bg_correction.py`.

### Why >50 px objects do not match yet

`comparison/decompose.py`, 8 sparse frames, undeconvolved, identical detection
in every row, only the named component changed:

| background estimator | objects | matched with python's |
|---|---|---|
| python — max over centred 6 | 314 | — |
| C++ — max of pairwise minima over 20 | 242 (0.77x) | **97.1% of C++**, 74.8% of python |
| training convention — max over 12 | 372 (1.18x) | 98.1% of python, 82.8% of train |

**The populations are nearly nested, not randomly different**: 97.1% of what the
C++ background finds, the python background also finds. The estimators order
maxmin-20 ⊂ max-6 ⊂ max-12 — a longer, gentler background estimate yields a
brighter background, a larger absdiff, and more detections.

Morphology pulls the other way, on the same corrected image:

| detection | objects | matched |
|---|---|---|
| python, plain contours | 314 | — |
| python + 3x3 MORPH_CLOSE (what the C++ adds) | 492 (1.57x) | 84.1% |

So the C++ route's two differences partly cancel — its background estimator
removes objects, its morphology invents them — which is why the end-to-end
numbers were hard to read.

**Action:** standardise the background estimator across both routes. The
training convention (**max over 12 neighbouring frames**) is the natural choice,
because the LUCYD `bg_pool` textures were built that way, so it also puts the
deconvolution input back in distribution. Then re-check the >50 px match.

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

## What the LUCYD training sources establish (2026-09-11)

Repo `git@git.geomar.de:team-optics/plankton/underwater-lucyd.git` (SSH works;
clone it, don't guess) and Zenodo record 22705689. `docs/RETRAINING_LOG.md` is
the development log and it already answers several questions asked here.

**Use `snr_real_vs_hallucinated`, not object counts.** The log's own words:
"raw detection count is not a metric. The deploy gate is
`snr_real_vs_hallucinated` — small-detection SNR distribution vs a
model-independent control noise floor". Model v3 produced **+436%** detections
and was rejected as speckle; "raw count would have called it a success; only the
SNR-vs-control gate caught it". Implementations:
`sim/real_vs_hallucinated.py`, `sim/validate_v3.py`, `eval/metrics.py`.

**Do not retune the detection threshold.** "Every model under-segments at
threshold 10; v4/v5d IoU already peaks there — do not retune the detector
threshold." That closes the adaptive-threshold question for deconv-off.

**The priorities are not abundance.** Task 1 is human recognizability
(object/background contrast), Task 2 is correct segmentation-mask area, and
"detection completeness is subordinate."

**`v5d` is the deploy candidate, not `v5a`.** The log lists `v5a` as
"ablation: v5 data, no gradient term" and `v5d` (+13%) as "**deploy candidate**
— boost 1.30 + mask weighting, no gradient term". The comparison arms here used
`best_ssim_v5a_noGrad_ablation.pth`; they should be re-run on
`*_v5d_maskweight.pth`.

**Training composition**, which bounds what the model can be trusted to
reconstruct: 29,477 in-focus objects, area **120–40,000 px**, contrast **>= 60**,
focus <= 2.0 px, mostly marine snow; 25–55 per synthetic frame at scale 0.5–1.6
and opacity 0.85–1.0, composited onto real cleaned background textures plus
Gaussian grain sigma 2.8–3.8. Training images are **whole 2560x2560 frames**.

**Two mismatches worth checking:**

- The background pool was built with `absdiff(img, max over **12** neighbouring
  frames)`; the deployed python route uses `bg_size = 6` and the C++ route a
  max-of-pairwise-minima over 20. Neither matches the training condition.
- Training images are whole frames, so the python route's whole-frame
  deconvolution is the in-distribution application and the **C++ route's atlas
  of packed crops is not** — unrelated crops adjacent, white gaps between them.

### The atlas costs ~20% of mask area, and the gap fill is why

`comparison/atlas_cost.py`. Same objects deconvolved both ways on three sparse
frames: whole frame (in-distribution, what the python route does) versus packed
into a 2600x2600 atlas (`ImagePacker(2600, 2600, 5)`, what `pisco_deconvolve`
does). Atlas crops compared against the whole-frame crops they should match:

| padding | gap fill | MAE (0-255) | mask area ratio | p10 | p90 |
|---|---|---|---|---|---|
| **5** | **white** | **2.10** | **1.201** | 1.057 | 1.490 |
| 5 | texture | 1.74 | 1.142 | 1.013 | 1.383 |
| 20 | white | 2.61 | 1.304 | 1.079 | 1.655 |
| 60 | white | 2.53 | 1.302 | 1.103 | 1.703 |

Pixel-wise the atlas is close (MAE ~2/255). **Mask area is not**: the atlas
inflates it by 20%, and mask area is Task 2 in the retraining log. Two things
follow:

- **More padding makes it worse**, so this is not neighbour bleed — it is the
  flat white surround. Keep padding at 5.
- **Filling the gaps with real background texture instead of white recovers
  about a third of the error** (1.201 -> 1.142) and lowers MAE. The
  commented-out `atlas += 20` and `GaussianBlur` in `pack_crops.py` were
  reaching for this. The test used a crude single tile from the frame's own
  quiet region; sampling `bg_pool`-style textures with grain (sigma 2.8-3.8, as
  the generator adds) should do better.

### Gate result: the deconvolution-only objects are real

`comparison/snr_gate.py` applies `local_snr` verbatim to the six sparse frames.
SNR measured on the cleaned input, which is identical with and without
deconvolution:

| population | n | median SNR | clears control p95 |
|---|---|---|---|
| kept (found both ways) | 254 | 5.63 | 82.3% |
| promoted | 199 | 3.67 | 88.9% |
| **new (deconvolution only)** | **820** | **3.04** | **82.0%** |
| control (noise floor) | 1273 | 1.90 | 5.0% |

The "new" objects clear the noise floor at the same rate as the reference
population. They are fainter, as expected, but **they are not hallucinated**.

## Settled: the deconvolution input is correct

**Do not re-open this.** Both routes feed LUCYD `bitwise_not(absdiff(img, bg))` —
dark objects on a near-white (~253) flat background, with the sensor noise
removed by the subtraction. The C++ route does the same via an atlas of packed
crops (`pack_crops.py:104` inverts in, `writer.py:14` inverts back).

**Veit confirmed (2026-09-10) the model was trained on images matching that
near-white cleaned background**, so this is in-distribution and correct. The
`LUCYD_background-corr_230705.png` slide showing a ~197 grey background is not
the training condition and should be disregarded.

This matters because the model is *not* invariant to the level — feeding a 197
pedestal puts 6x as much area above threshold, and 230 explodes the count to
156x baseline (`comparison/pedestal.py`). The current setting is right; anything
that changes the background level of the deconvolution input would not be.

Related: the fixed detection threshold of 10 is only meaningful against a ~253
background. Any change to the correction step has to re-reference it.

## Controlled-arm results (2026-09-10)

Six arms on m202_a, 46 frames, same LUCYD weights
(`best_ssim_v5a_noGrad_ablation.pth`) in both deconvolution arms. Submitted via
`comparison/arms.py`.

| arm | route | deconv | FOV mask | recorded | objects |
|---|---|---|---|---|---|
| A1 | python | off | on | 10944 | 6107 |
| A2 | python | on | on | 41813 | **27891** |
| A1b | python | off | off | 11085 | 6187 |
| A2b | python | on | off | 42400 | 28255 |
| B1 | C++ | off | n/a | — | 5548 |
| B2 | C++ | on | n/a | — | **14055** |

**Deconvolution multiplies the object count in both routes** — python 4.6x,
C++ 2.5x. Without it the routes are within 10% (6107 vs 5548); with it they
differ by a factor of two. Deconvolution is meant to sharpen crops, not create
particles, so this is the finding the validation must address first: it is
lifting texture above the detection threshold.

**This retracts the claim that C++ abundance cannot depend on the deconvolution
model.** Pass-1 ROIs are model-independent, but extraction re-thresholds ROI
pixels that deconvolution has replaced, so the final object set moves with the
model.

**The FOV mask does not matter.** Turning it off (`maskImage = false`, a setting
added to the tool for this) adds 80 objects to A1 and 364 to A2 — about 1.3%.

Agreement, after harmonisation (FOV mask both sides, C++ de-padded 10 px,
area > 50 px both, python saved-only):

| comparison | python | C++ | matched | of python | of C++ | area C++/python |
|---|---|---|---|---|---|---|
| A1 vs B1, no deconv | 6107 | 3494 | 1180 | 19.3% | 33.8% | 0.622 |
| A2 vs B2, deconv | 27891 | 10359 | 6686 | 24.0% | 64.5% | 0.849 |

Deconvolution *improves* C++-side agreement (34% -> 65%, and 93% for objects
above 1000 um) while the python route accumulates a large unmatched
small-object population. Crowding bands for A2 vs B2 locate it: on the 15
sparsest frames python reports 4153 objects against the C++ chain's 557, and
only 5.1% of them match. A quiet frame is where a false-positive mechanism is
most visible.

**The TRIANGLE re-threshold explains nothing.** Over 10347 objects the TRIANGLE
estimate lands *below* the floor of 10 for 86% of them, so the effective
threshold is just 10 — the same fixed value the python route uses. Recomputing
every mask at a fixed 10 reproduces the stored areas exactly (ratio 1.000 at
p10, median and p90). The area gap remains open; the background estimator is the
remaining candidate.

Report with charts: https://claude.ai/code/artifact/e2f65008-a379-4ca4-bbf8-81739c82a023

## Earlier shakedown results — superseded

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
