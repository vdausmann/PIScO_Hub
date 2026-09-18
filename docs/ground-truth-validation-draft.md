# Ground-truth validation of the segmentation routes — draft for its own session

Status: draft, 2026-09-15. Everything measured so far in
`docs/pipeline-comparison-spec.md` is **agreement** between the legacy python
route and the C++ route. Agreement says how often they find the same thing; it
says nothing about which one is right when they differ, and nothing at all
when they agree and are both wrong. This session is about the second question.

## Where we stand

After aligning backgrounds, block partition, crop padding and pass-2 masks,
the routes agree on most objects — but not all, and the residual is
size-dependent:

| deconvolved, area > 50 px | python objects | matched | C++ objects | matched |
|---|---|---|---|---|
| 125–250 µm | 6015 | 63.2% | 4240 | 89.3% |
| 250–500 µm | 7944 | 79.6% | 7148 | 88.5% |
| 500–1000 µm | 1388 | 94.5% | 1354 | 97.3% |
| > 1000 µm | 280 | 97.5% | 278 | 98.2% |

| undeconvolved, area > 50 px | python objects | matched | C++ objects | matched |
|---|---|---|---|---|
| 125–250 µm | 1292 | 50.2% | 1020 | 77.6% |
| 250–500 µm | 1901 | 74.6% | 1645 | 83.0% |
| 500–1000 µm | 741 | 81.4% | 685 | 81.0% |
| > 1000 µm | 415 | 76.1% | 337 | 81.6% |

Above 500 µm with deconvolution the routes agree at 95–98%. Below 250 µm the
python route carries thousands of objects the C++ route does not, and that is
where a decision needs ground truth. (Data: `/mnt/pisco/compare/A2y_B2s/` and
`A1y_B1s/`, `matches.csv` with a `stratum` column per object.)

## The questions ground truth has to settle

Each of these is currently a measured *difference* with no verdict attached.

1. **Detection: who is right when they disagree?** Python-only objects (3921
   deconvolved, 1362 undeconvolved) and C++-only objects (1314 / 700). Per
   route: precision and recall, by size class.

2. **Are the deconvolution-revealed objects real?** With deconvolution both
   routes find 3–4× as many objects as without. The LUCYD deploy gate
   (`snr_real_vs_hallucinated`, run in `comparison/snr_gate.py`) says they
   clear the noise floor at the same rate as objects found both ways — but
   that is a signal-statistics argument, not a person looking at them. A
   human verdict on a sample decides whether they enter abundance.

3. **Which mask is right?** The python route thresholds at a fixed 10 and its
   contour visibly includes halo; the C++ pass-2 TRIANGLE re-threshold cuts
   inside it. On undeconvolved input the C++ mask is a median 0.67× the
   python one (`comparison/triangle_undeconv.py`); Veit's read of the
   overlays was that the tighter contour looks *more* correct. Neither can be
   judged by eye at this contrast. This needs human-drawn outlines on a
   sample, not just labels, and it decides ESD, biovolume and the
   size-spectrum slope.

4. **Where does noise end and particle begin, below 50 px?** The counting
   directive wants particles counted between the noise floor and 50 px on
   the undeconvolved image. The threshold sweep (`comparison/thr_sweep.py`)
   shows the count halves every ~4 grey levels from 6 to 15 with no knee, so
   the threshold *is* the count. Only labelled small particles can say where
   to put it.

## Sampling design (the notes from the first discussion, kept)

**Sample from the disagreements, but not only from them.** Validating only
disagreements gives the highest information per label and answers "who wins
the disputes". It cannot give absolute precision or recall, because it
assumes the agreement set is correct — and both routes share a
background-subtraction family and the same LUCYD weights, so correlated
errors are likely. Spend **50–100 of ~400 labels on a random sample of
agreements**. That is what turns dispute-arbitration into a real accuracy
per route.

**Stratify, and record the sampling fraction per stratum**, so anything
estimated can be re-weighted to the population:

- python-only, C++-only, split/merge, agreement — all available from
  `matches.csv` `stratum`;
- by size class (125–250, 250–500, 500–1000, > 1000 µm) within each — or the
  sample is 400 specks and the large objects that carry the biovolume go
  unchecked;
- for question 2: deconvolution-only objects, i.e. found with LUCYD on and not
  off, which `comparison/registry.py` labels per object (`new`, `promoted`,
  `split`, `kept`).

**Label detection disagreements on raw-frame patches, never on a route's
crop.** Only one route produced a crop for a disputed object, so showing it
reveals which route found it, and the sharper rendering wins by default. A
fixed-size patch cut from the raw frame at the disputed position removes that
bias. `comparison/patch.py` already cuts raw / bg-corrected / deconvolved
patches around registry objects.

**Classification disagreements exist in both routes**, so those can be shown
blind and randomised, both renderings.

**Re-show ~10% as duplicates** to measure intra-rater agreement. Without that
number, a 15% disagreement rate cannot be told from label noise.

**For question 3, a smaller subset needs outlines, not labels.** Perhaps 50
objects across sizes, drawn by hand on the undeconvolved cleaned crop and on
the deconvolved one, so both masks can be scored by IoU against a human
outline.

## Where to label

EcoTaxa is the natural tool: an **arbitration project** holding exactly the
sampled objects, with stratum, route provenance and sampling weight in free
columns hidden from the sorting view. `pisco_ecotaxa_export` writes the TSV;
`object_link` deep-links each object back to its frame in the Hub viewer,
which has a raw-frame toggle — useful for exactly the "look at the raw pixels"
judgement this needs. Outlines (question 3) need a different tool; EcoTaxa
does not do polygons.

## What to compute afterwards

- precision and recall per route, per size class, re-weighted by stratum
  sampling fractions;
- the same restricted to deconvolution-revealed objects (question 2);
- mask IoU against human outlines, python vs C++, per size class (question 3);
- for question 4: fraction of labelled real particles by threshold, to place
  the noise cut empirically;
- intra-rater agreement, reported alongside every other number.

## Decisions to make before starting

- Sample size and who labels. 300–500 objects was the earlier estimate;
  10% duplicates on top.
- Whether to validate both chains (deconvolved and undeconvolved) or only the
  one the counting directive uses. Question 3 needs both.
- Which profile(s). m202_a has all the comparison runs; a second profile from
  a different condition would show whether the conclusions travel.
- Acceptance thresholds, written down *before* the labels come back.

## Prerequisites already in place

- Both routes on identical backgrounds and the rebuilt C++ pass 2
  (`pisco_segmenter_cpp` `9c2e3ae`); all comparison runs and `matches.csv`
  files on `/mnt/pisco/compare/`.
- `comparison/registry.py`, `patch.py`, `snr_gate.py`, `thr_sweep.py`,
  `contours.py`, `deconv_side.py` in the Hub repo.
- EcoTaxa testbed on CT 114 with the export / import / features tools
  registered on the Hub (see `docs/ecotaxa-testbed.md`).
