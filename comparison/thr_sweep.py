"""Was 10 a good detection threshold for the cleaned (undeconvolved) image?

Under the counting directive this threshold *is* the count, so it deserves a
curve rather than an inherited number. On the cleaned frames, block-wise
background as both routes now do it:

  * the frame's own noise scale, so 10 can be read in sigma;
  * object count against threshold - a knee means a stable choice, a slope
    means the count is a function of the threshold;
  * median object area against threshold - if area keeps growing as the
    threshold drops, that growth is the EDOF haze halo, not the particle;
  * a noise floor: components that a threshold produces on a frame's
    object-free background alone (frame minus its own detections).
"""
import glob, os, sys
import cv2 as cv
import numpy as np

SRC = "/mnt/pisco/testdata/m202_a"
N_BLOCK, MIN_AREA = 12, 50
THRESHOLDS = [3, 4, 5, 6, 8, 10, 12, 15, 20, 25, 30]


def sorted_frames():
    files = os.listdir(SRC)
    try:
        files.sort(key=lambda x: float(os.path.basename(x).split("_")[0].split("-")[1]))
    except Exception:
        files.sort()
    return [f for f in files if f.endswith(".png")]


def block_bounds(i, n, total):
    s = (i // n) * n; e = s + n
    if e > total: s = max(0, s - n); e = total
    elif total - e < n: e = total
    return s, e


def maxmin(stack):
    bg = np.minimum(stack[0], stack[1])
    for k in range(1, len(stack) - 1):
        bg = np.maximum(bg, np.minimum(stack[k], stack[k + 1]))
    return bg


def comps(binary):
    n, lab, stats, _ = cv.connectedComponentsWithStats(binary.astype(np.uint8), connectivity=8)
    a = stats[1:, cv.CC_STAT_AREA]
    return a[a > MIN_AREA]


files = sorted_frames()
imgs = [cv.imread(os.path.join(SRC, f), cv.IMREAD_GRAYSCALE) for f in files]
picked = list(range(24, 46))         # the quiet, settled part of the profile

sigmas, counts, areas, noise = [], {t: 0 for t in THRESHOLDS}, {t: [] for t in THRESHOLDS}, {t: 0 for t in THRESHOLDS}
for i in picked:
    s, e = block_bounds(i, N_BLOCK, len(imgs))
    c = cv.absdiff(imgs[i], maxmin(imgs[s:e]))
    med = np.median(c); mad = np.median(np.abs(c.astype(np.float32) - med))
    sigmas.append((med, 1.4826 * mad))

    # object-free background: everything not inside a generous detection at 10,
    # dilated so halos are excluded too
    obj = (c > 10).astype(np.uint8)
    obj = cv.dilate(obj, cv.getStructuringElement(cv.MORPH_ELLIPSE, (31, 31)))
    bg_only = c.copy(); bg_only[obj > 0] = 0

    for t in THRESHOLDS:
        a = comps(c > t)
        counts[t] += len(a); areas[t] += list(a)
        noise[t] += len(comps(bg_only > t))

med = np.median([m for m, _ in sigmas]); sig = np.median([s for _, s in sigmas])
print(f"{len(picked)} settled frames, block-wise background of {N_BLOCK}\n")
print(f"cleaned-image background: median {med:.1f}, robust sigma {sig:.2f}  "
      f"(range across frames {min(s for _, s in sigmas):.2f}-{max(s for _, s in sigmas):.2f})")
print(f"threshold 10 = {(10 - med) / sig:.1f} sigma at the median frame\n")
print(f"{'thr':>4} {'sigma':>6} {'objects':>8} {'vs 10':>7} {'median area':>12} "
      f"{'noise comps':>12}  {'noise share':>11}")
print("-" * 70)
for t in THRESHOLDS:
    n = counts[t]; ma = np.median(areas[t]) if areas[t] else 0
    share = noise[t] / n if n else 0
    tag = "  <-" if t == 10 else ""
    print(f"{t:>4} {(t - med) / sig:>6.1f} {n:>8} {n / counts[10]:>6.2f}x {ma:>12.0f} "
          f"{noise[t]:>12}  {share:>10.1%}{tag}")
print("\nnoise comps = components a threshold finds on object-free background alone;")
print("noise share = how many of that threshold's objects are probably not particles.")
