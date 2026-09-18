#!/usr/bin/env python3
"""Particle-by-particle agreement between the legacy Python route and the C++ route.

Matches detections per frame by bounding-box IoU and reports where the two
routes agree, where they disagree, and how the disagreement is distributed over
object size. Writes a per-object match table so the disagreement strata can be
sampled for hand validation.

Design notes and the verified differences between the routes are in
docs/pipeline-comparison-spec.md. The two that this script compensates for
explicitly:

  * the C++ pads every bounding box by ``boundingBoxPadding`` (twice, but the
    second pad is clipped to the enclosing ROI so it does not accumulate),
    the Python route not at all, so C++ boxes are de-padded before matching
    (``--depad``);
  * the Python route zeroes everything outside a circular field of view, the
    C++ does not, so the same mask is applied to both here (``--fov-radius``).

Usage:
    compare_pipelines.py --old <old_results_dir> --new <result.h5> --out <dir>
"""

import argparse
import csv
import json
import math
import os
import sys
from collections import defaultdict

import numpy as np

try:
    import h5py
except ImportError:
    sys.exit("h5py is required: run this with /opt/pisco/venvs/torch/bin/python")

try:
    from scipy.optimize import linear_sum_assignment
    _HAVE_SCIPY = True
except ImportError:
    _HAVE_SCIPY = False

try:
    import cv2
    _HAVE_CV2 = True
except ImportError:
    _HAVE_CV2 = False


# ---------------------------------------------------------------- loading

# Data/*.csv is written head+erless by save_crop_data(); the columns up to the
# morphometrics block are fixed. See pisco_segmenter/detection.py:322.
OLD_COLS = ("index", "filename", "raw_mean", "raw_std", "corr_mean", "corr_std",
            "area", "cx", "cy", "w", "h", "saved")


def load_old(old_dir):
    """-> {frame: [dict(x0, y0, x1, y1, area, saved, idx)]}"""
    data_dir = os.path.join(old_dir, "Data")
    if not os.path.isdir(data_dir):
        sys.exit(f"no Data/ directory under {old_dir}")

    frames = {}
    for name in sorted(os.listdir(data_dir)):
        if not name.endswith(".csv"):
            continue
        frame = name[:-4]
        objs = []
        with open(os.path.join(data_dir, name), newline="") as fh:
            for row in csv.reader(fh):
                # a corrupt frame is recorded as a single marker row
                if len(row) < len(OLD_COLS) or row[0] == "corrupt":
                    continue
                try:
                    area = float(row[6])
                    cx, cy = float(row[7]), float(row[8])
                    w, h = float(row[9]), float(row[10])
                    saved = int(float(row[11]))
                except ValueError:
                    continue
                # The two position columns do not hold the same thing in both
                # branches. detection.py:280 reassigns x,y to the top-left corner
                # inside `if settings.save_crops`, and the saved=1 append at :322
                # uses the reassigned value while the saved=0 append at :328 uses
                # the original centre. Verified against the raw frames: for saved
                # objects, top-left correlates at r=0.99 and centre at -0.06.
                if saved:
                    x0, y0 = cx, cy
                else:
                    x0, y0 = cx - w / 2, cy - h / 2
                objs.append(dict(idx=int(float(row[0])), area=area, saved=saved,
                                 x0=x0, y0=y0, x1=x0 + w, y1=y0 + h))
        frames[frame] = objs
    return frames


def load_new(h5_path, depad):
    """-> ({frame: [dict(...)]}, (height, width))"""
    frames = {}
    with h5py.File(h5_path, "r") as f:
        height = int(f.attrs.get("imageHeight", 0))
        width = int(f.attrs.get("imageWidth", 0))
        for frame in f.keys():
            g = f[frame]
            if "offsets" not in g:
                frames[frame] = []
                continue
            bx = np.asarray(g["boundingBoxX"]).astype(float)
            by = np.asarray(g["boundingBoxY"]).astype(float)
            bw = np.asarray(g["boundingBoxW"]).astype(float)
            bh = np.asarray(g["boundingBoxH"]).astype(float)
            offs = np.asarray(g["offsets"]).astype(np.int64)
            masks = np.asarray(g["maskPixelData"]) if "maskPixelData" in g else None

            objs = []
            for i in range(len(offs)):
                w, h = bw[i], bh[i]
                # undo the padding the C++ adds, but never past a 1px box
                d = min(depad, max(0.0, (w - 1) / 2), max(0.0, (h - 1) / 2))
                x0, y0 = bx[i] + d, by[i] + d
                x1, y1 = bx[i] + w - d, by[i] + h - d

                area = float("nan")
                if masks is not None:
                    n = int(bw[i] * bh[i])
                    m = masks[offs[i]:offs[i] + n]
                    if m.size == n:
                        m = m.reshape(int(bh[i]), int(bw[i]))
                        area = _mask_area(m)
                objs.append(dict(idx=i, area=area, saved=1,
                                 x0=x0, y0=y0, x1=x1, y1=y1))
            frames[frame] = objs
    return frames, (height, width)


def _mask_area(mask):
    """Area of the mask, matched to how the Python route measures it.

    The Python route records cv.contourArea (a polygon area), not a pixel
    count; the two differ by roughly the perimeter. Use contourArea when
    OpenCV is available so the definitions line up, else fall back to counting.
    """
    binary = (mask > 0).astype(np.uint8)
    if not _HAVE_CV2:
        return float(binary.sum())
    cnts, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not cnts:
        return float(binary.sum())
    return float(max(cv2.contourArea(c) for c in cnts))


# ---------------------------------------------------------------- filtering

def apply_fov(objs, radius, shape):
    """Drop objects whose centre falls outside the circular field of view."""
    if radius is None:
        return objs, 0
    h, w = shape
    cx, cy = w / 2.0, h / 2.0
    kept, dropped = [], 0
    for o in objs:
        ox, oy = (o["x0"] + o["x1"]) / 2, (o["y0"] + o["y1"]) / 2
        if (ox - cx) ** 2 + (oy - cy) ** 2 <= radius ** 2:
            kept.append(o)
        else:
            dropped += 1
    return kept, dropped


def apply_area(objs, min_area):
    if min_area is None:
        return objs, 0
    kept = [o for o in objs if not (o["area"] == o["area"]) or o["area"] > min_area]
    return kept, len(objs) - len(kept)


# ---------------------------------------------------------------- matching

def iou_matrix(a, b):
    if not a or not b:
        return np.zeros((len(a), len(b)))
    A = np.array([[o["x0"], o["y0"], o["x1"], o["y1"]] for o in a])
    B = np.array([[o["x0"], o["y0"], o["x1"], o["y1"]] for o in b])
    ix0 = np.maximum(A[:, None, 0], B[None, :, 0])
    iy0 = np.maximum(A[:, None, 1], B[None, :, 1])
    ix1 = np.minimum(A[:, None, 2], B[None, :, 2])
    iy1 = np.minimum(A[:, None, 3], B[None, :, 3])
    inter = np.clip(ix1 - ix0, 0, None) * np.clip(iy1 - iy0, 0, None)
    area_a = ((A[:, 2] - A[:, 0]) * (A[:, 3] - A[:, 1]))[:, None]
    area_b = ((B[:, 2] - B[:, 0]) * (B[:, 3] - B[:, 1]))[None, :]
    union = area_a + area_b - inter
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(union > 0, inter / union, 0.0)


def match_frame(a, b, thr):
    """One-to-one assignment maximising total IoU, kept only above `thr`."""
    M = iou_matrix(a, b)
    if M.size == 0:
        return [], list(range(len(a))), list(range(len(b))), M
    if _HAVE_SCIPY:
        ri, ci = linear_sum_assignment(-M)
        pairs = [(int(i), int(j)) for i, j in zip(ri, ci) if M[i, j] >= thr]
    else:  # greedy fallback, good enough when overlaps are sparse
        pairs, used_a, used_b = [], set(), set()
        order = np.dstack(np.unravel_index(np.argsort(-M, axis=None), M.shape))[0]
        for i, j in order:
            if M[i, j] < thr:
                break
            if i in used_a or j in used_b:
                continue
            used_a.add(i); used_b.add(j); pairs.append((int(i), int(j)))
    ma = {i for i, _ in pairs}
    mb = {j for _, j in pairs}
    return (pairs,
            [i for i in range(len(a)) if i not in ma],
            [j for j in range(len(b)) if j not in mb],
            M)


def split_merge(M, pairs, n_a, n_b, overlap_thr=0.1):
    """Count 1-to-N and N-to-1 relationships among the objects left unmatched.

    A split is one route's object overlapping several of the other's; it is the
    signature of the C++ merge/re-split path and of morphology differences, and
    it matters because merging changes biovolume nonlinearly.
    """
    if M.size == 0:
        return 0, 0
    matched_a = {i for i, _ in pairs}
    matched_b = {j for _, j in pairs}
    over = M >= overlap_thr
    splits = sum(1 for i in range(n_a)
                 if i not in matched_a and over[i].sum() > 1)
    merges = sum(1 for j in range(n_b)
                 if j not in matched_b and over[:, j].sum() > 1)
    return splits, merges


# ---------------------------------------------------------------- reporting

def esd_um(area_px, pixel_size_um):
    if area_px != area_px or area_px <= 0:
        return float("nan")
    return 2.0 * math.sqrt(area_px / math.pi) * pixel_size_um


def bland_altman(x, y):
    x, y = np.asarray(x, float), np.asarray(y, float)
    d = x - y
    return dict(bias=float(d.mean()), sd=float(d.std(ddof=1)) if len(d) > 1 else 0.0,
                loa_low=float(d.mean() - 1.96 * d.std(ddof=1)) if len(d) > 1 else 0.0,
                loa_high=float(d.mean() + 1.96 * d.std(ddof=1)) if len(d) > 1 else 0.0)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--old", required=True, help="legacy route results directory")
    ap.add_argument("--new", required=True, help="C++ route result.h5")
    ap.add_argument("--out", required=True, help="output directory")
    ap.add_argument("--iou", type=float, default=0.5,
                    help="IoU for a one-to-one match (default 0.5)")
    ap.add_argument("--depad", type=float, default=10.0,
                    help="pixels to shrink each C++ box by per side. The C++ pads "
                         "twice, but the second pad is clipped to the ROI it sits "
                         "in, so it does not accumulate: one boundingBoxPadding "
                         "(default 10) is the right correction.")
    ap.add_argument("--fov-radius", default="auto",
                    help="'auto' to read mask_radius from the old settings.csv, "
                         "a number, or 'none'")
    ap.add_argument("--min-area", type=float, default=None,
                    help="area threshold applied to BOTH routes, to undo the "
                         "min_area_to_save=50 vs minContourArea=10 difference")
    ap.add_argument("--old-saved-only", action="store_true",
                    help="use only detections the old route actually saved a crop for")
    ap.add_argument("--skip-first", type=int, default=0,
                    help="drop the first N frames in sort order. The background "
                         "model is immature at the start of a profile and inflates "
                         "counts by an order of magnitude over roughly six frames, "
                         "which otherwise dominates every aggregate here.")
    ap.add_argument("--pixel-size-um", type=float, default=22.5)
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)

    old = load_old(args.old)
    new, shape = load_new(args.new, args.depad)

    radius = None
    if args.fov_radius == "auto":
        settings = os.path.join(args.old, "settings.csv")
        if os.path.exists(settings):
            with open(settings, newline="") as fh:
                for row in csv.reader(fh):
                    if len(row) >= 2 and row[0] == "mask_radius":
                        radius = float(row[1])
    elif args.fov_radius != "none":
        radius = float(args.fov_radius)

    frames = sorted(set(old) & set(new))
    skipped_frames = frames[:args.skip_first]
    frames = frames[args.skip_first:]
    only_old = sorted(set(old) - set(new))
    only_new = sorted(set(new) - set(old))

    rows = []
    per_frame = []
    dropped = defaultdict(int)
    tot = defaultdict(int)

    for frame in frames:
        a, d = apply_fov(old[frame], radius, shape); dropped["fov_old"] += d
        b, d = apply_fov(new[frame], radius, shape); dropped["fov_new"] += d
        if args.old_saved_only:
            n0 = len(a); a = [o for o in a if o["saved"]]
            dropped["unsaved_old"] += n0 - len(a)
        a, d = apply_area(a, args.min_area); dropped["area_old"] += d
        b, d = apply_area(b, args.min_area); dropped["area_new"] += d

        pairs, un_a, un_b, M = match_frame(a, b, args.iou)
        s, m = split_merge(M, pairs, len(a), len(b))
        tot["splits"] += s; tot["merges"] += m

        per_frame.append(dict(frame=frame, n_old=len(a), n_new=len(b),
                              matched=len(pairs), only_old=len(un_a),
                              only_new=len(un_b), splits=s, merges=m))

        for i, j in pairs:
            rows.append(dict(frame=frame, stratum="matched", old_idx=a[i]["idx"],
                             new_idx=b[j]["idx"], iou=round(float(M[i, j]), 4),
                             old_area=a[i]["area"], new_area=b[j]["area"],
                             old_esd_um=esd_um(a[i]["area"], args.pixel_size_um),
                             new_esd_um=esd_um(b[j]["area"], args.pixel_size_um),
                             old_saved=a[i]["saved"]))
        for i in un_a:
            best = float(M[i].max()) if M.size else 0.0
            rows.append(dict(frame=frame, stratum="only_old", old_idx=a[i]["idx"],
                             new_idx="", iou=round(best, 4), old_area=a[i]["area"],
                             new_area="", old_esd_um=esd_um(a[i]["area"], args.pixel_size_um),
                             new_esd_um="", old_saved=a[i]["saved"]))
        for j in un_b:
            best = float(M[:, j].max()) if M.size else 0.0
            rows.append(dict(frame=frame, stratum="only_new", old_idx="",
                             new_idx=b[j]["idx"], iou=round(best, 4), old_area="",
                             new_area=b[j]["area"], old_esd_um="",
                             new_esd_um=esd_um(b[j]["area"], args.pixel_size_um),
                             old_saved=""))

    # ---- write the per-object table; it defines the validation strata
    match_csv = os.path.join(args.out, "matches.csv")
    fields = ["frame", "stratum", "old_idx", "new_idx", "iou", "old_area",
              "new_area", "old_esd_um", "new_esd_um", "old_saved"]
    with open(match_csv, "w", newline="") as fh:
        wr = csv.DictWriter(fh, fieldnames=fields)
        wr.writeheader()
        wr.writerows(rows)

    n_old = sum(f["n_old"] for f in per_frame)
    n_new = sum(f["n_new"] for f in per_frame)
    n_matched = sum(f["matched"] for f in per_frame)

    matched_rows = [r for r in rows if r["stratum"] == "matched"]
    area_ratio = [r["new_area"] / r["old_area"] for r in matched_rows
                  if r["old_area"] and r["new_area"] == r["new_area"] and r["old_area"] > 0]

    # matched fraction by size, because most disagreement will be small objects
    by_size = {}
    for lo, hi in [(0, 125), (125, 250), (250, 500), (500, 1000), (1000, 1e9)]:
        label = f"{lo}-{hi if hi < 1e9 else 'inf'}um"
        o = [r for r in rows if r["stratum"] in ("matched", "only_old")
             and r["old_esd_um"] != "" and lo <= r["old_esd_um"] < hi]
        n = [r for r in rows if r["stratum"] in ("matched", "only_new")
             and r["new_esd_um"] != "" and lo <= r["new_esd_um"] < hi]
        by_size[label] = dict(
            old=len(o), new=len(n),
            matched_of_old=sum(1 for r in o if r["stratum"] == "matched"),
            matched_of_new=sum(1 for r in n if r["stratum"] == "matched"))

    summary = dict(
        old=os.path.abspath(args.old), new=os.path.abspath(args.new),
        params=dict(iou=args.iou, depad=args.depad, fov_radius=radius,
                    min_area=args.min_area, old_saved_only=args.old_saved_only,
                    skip_first=args.skip_first,
                    pixel_size_um=args.pixel_size_um,
                    hungarian=_HAVE_SCIPY, contour_area=_HAVE_CV2),
        frames=dict(common=len(frames), skipped=len(skipped_frames),
                    only_old=len(only_old), only_new=len(only_new),
                    only_old_names=only_old[:5], only_new_names=only_new[:5]),
        dropped_by_harmonisation=dict(dropped),
        counts=dict(old=n_old, new=n_new, matched=n_matched,
                    only_old=n_old - n_matched, only_new=n_new - n_matched,
                    matched_frac_of_old=round(n_matched / n_old, 4) if n_old else None,
                    matched_frac_of_new=round(n_matched / n_new, 4) if n_new else None),
        split_merge=dict(tot),
        per_frame_counts=bland_altman([f["n_old"] for f in per_frame],
                                      [f["n_new"] for f in per_frame]),
        matched_area_ratio=dict(
            n=len(area_ratio),
            median=float(np.median(area_ratio)) if area_ratio else None,
            p10=float(np.percentile(area_ratio, 10)) if area_ratio else None,
            p90=float(np.percentile(area_ratio, 90)) if area_ratio else None),
        by_size_class=by_size,
    )
    with open(os.path.join(args.out, "summary.json"), "w") as fh:
        json.dump(summary, fh, indent=2)
    with open(os.path.join(args.out, "per_frame.csv"), "w", newline="") as fh:
        wr = csv.DictWriter(fh, fieldnames=list(per_frame[0].keys()) if per_frame
                            else ["frame"])
        wr.writeheader(); wr.writerows(per_frame)

    # ---- report
    print(f"frames        : {len(frames)} compared, {len(skipped_frames)} skipped"
          f"  ({len(only_old)} old-only, {len(only_new)} new-only)")
    print(f"fov radius    : {radius}   depad: {args.depad}px   min_area: {args.min_area}")
    print(f"dropped       : {dict(dropped)}")
    print(f"objects       : old {n_old}   new {n_new}")
    print(f"matched       : {n_matched}"
          f"  ({n_matched / n_old:.1%} of old, {n_matched / n_new:.1%} of new)"
          if n_old and n_new else "matched       : 0")
    print(f"unmatched     : {n_old - n_matched} old-only, {n_new - n_matched} new-only")
    print(f"split/merge   : {tot['splits']} splits, {tot['merges']} merges")
    ba = summary["per_frame_counts"]
    print(f"per-frame diff: bias {ba['bias']:+.1f}"
          f"  LoA [{ba['loa_low']:+.1f}, {ba['loa_high']:+.1f}]")
    ar = summary["matched_area_ratio"]
    if ar["median"] is not None:
        print(f"area new/old  : median {ar['median']:.3f}"
              f"  p10 {ar['p10']:.3f}  p90 {ar['p90']:.3f}")
    print("\nby ESD class (old-side / new-side matched fraction):")
    for k, v in by_size.items():
        fo = v["matched_of_old"] / v["old"] if v["old"] else float("nan")
        fn = v["matched_of_new"] / v["new"] if v["new"] else float("nan")
        print(f"  {k:>12}  old {v['old']:>6} ({fo:6.1%})   new {v['new']:>6} ({fn:6.1%})")
    print(f"\nwrote {match_csv}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
