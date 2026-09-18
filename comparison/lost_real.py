"""Lost area of the real tool's output against whole-frame deconvolution.

For every pass-1 ROI in a run's checkpoint (so the comparison is independent of
pass-2 boxes and thresholds): threshold both the stored deconvolved ROI and the
whole-frame deconvolution at 10, take the component under the ROI's strongest
object, and report how much of the whole-frame area the stored ROI recovers.

    lost_real.py <label>=<results_dir> [<label>=<results_dir> ...]
"""
import glob, os, sys
import cv2 as cv
import h5py
import numpy as np

SRC = "/mnt/pisco/testdata/m202_a"
MODEL = ("/opt/pisco/tools/pisco_segmenter_py/pisco_segmenter/models/"
         "best_ssim_v5a_noGrad_ablation.pth")
N_BLOCK, THR = 12, 10
FRAMES = list(range(24, 46))


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


def main_component(binary, ref=None):
    n, lab, stats, _ = cv.connectedComponentsWithStats(binary.astype(np.uint8), connectivity=8)
    if n <= 1:
        return np.zeros_like(binary, np.uint8)
    if ref is not None and ref.any():
        # the component overlapping the reference most
        best, bi = 0, 0
        for j in range(1, n):
            ov = int(((lab == j) & (ref > 0)).sum())
            if ov > best:
                best, bi = ov, j
        if best:
            return ((lab == bi) * 255).astype(np.uint8)
    j = 1 + int(np.argmax(stats[1:, cv.CC_STAT_AREA]))
    return ((lab == j) * 255).astype(np.uint8)


def rois(results_dir, frame):
    for p in sorted(glob.glob(f"{results_dir}/checkpoint_correction*.h5")):
        with h5py.File(p, "r") as f:
            if frame not in f:
                continue
            g = f[frame]
            bx, by = np.array(g["boundingBoxX"]), np.array(g["boundingBoxY"])
            bw, bh = np.array(g["boundingBoxW"]), np.array(g["boundingBoxH"])
            px = np.array(g["pixelData"]); out, o = [], 0
            for k in range(len(bx)):
                n = int(bw[k]) * int(bh[k])
                out.append((int(bx[k]), int(by[k]), int(bw[k]), int(bh[k]), px[o:o + n].reshape(int(bh[k]), int(bw[k]))))
                o += n
            return out
    return []


runs = dict(a.split("=", 1) for a in sys.argv[1:])
files = sorted_frames()
imgs = [cv.imread(os.path.join(SRC, f), cv.IMREAD_GRAYSCALE) for f in files]
import torch
sys.path.insert(0, "/opt/pisco/tools/pisco_segmenter_py")
from pisco_segmenter.lucyd import LUCYD
device = "cuda" if torch.cuda.is_available() else "cpu"
model = LUCYD(num_res=1).to(device); model.eval()
model.load_state_dict(torch.load(MODEL, map_location=device))

lost = {k: [] for k in runs}
for i in FRAMES:
    frame = files[i][:-4]
    s, e = block_bounds(i, N_BLOCK, len(files))
    off = cv.absdiff(imgs[i], maxmin(imgs[s:e]))
    with torch.no_grad():
        x = torch.from_numpy(cv.bitwise_not(off) / 255).unsqueeze(0).unsqueeze(0).to(device)
        whole = cv.bitwise_not((model(x.float())[0].detach().cpu().numpy()[0, 0] * 255).astype(np.uint8))
    for label, d in runs.items():
        for X, Y, W, H, roi in rois(d, frame):
            if W < 24 or H < 24 or roi.shape != (H, W):
                continue
            ref_bin = whole[Y:Y + H, X:X + W] > THR
            ref = main_component(ref_bin)
            ra = int((ref > 0).sum())
            if ra < 80:
                continue
            got = main_component(roi > THR, ref)
            lost[label].append(1 - int((got > 0).sum()) / ra)

print(f"{len(FRAMES)} settled frames, pass-1 ROIs, threshold {THR}, main component; "
      f"'lost' = 1 - stored / whole-frame area\n")
print(f"{'run':<12} {'ROIs':>6} {'median lost':>12} {'p90':>8} {'>10% lost':>10} {'>25% lost':>10}")
print("-" * 64)
for label in runs:
    a = np.array(lost[label])
    print(f"{label:<12} {len(a):>6} {np.median(a):>11.1%} {np.percentile(a, 90):>7.1%} "
          f"{(a > .10).mean():>9.1%} {(a > .25).mean():>9.1%}")
