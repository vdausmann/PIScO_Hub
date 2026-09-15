"""Does pisco_deconvolve write the deconvolved ROIs back where they came from?

Pass-1 ROI boxes are identical in B1z (no deconvolution) and B2z (with), since
the correction settings are the same. So for every ROI the checkpoint files
give: the corrected pixels (B1z) and the deconvolved pixels (B2z), at one box.
The whole-frame deconvolution at that box is what the deconvolved ROI should
look like, up to the atlas-context effect measured earlier (a few grey levels).

If instead the stored ROI matches the whole-frame result only after a spatial
shift, or matches a *different* box, the write-back is misplacing data.
"""
import glob, os, sys
import cv2 as cv
import h5py
import numpy as np

SRC = "/mnt/pisco/testdata/m202_a"
B1 = "/mnt/pisco/hub-data/workflows/634e7722-2be8-4af5-9f50-3218e3be14f2/0_pisco_segment/results"
B2 = "/mnt/pisco/hub-data/workflows/06fde7c1-96f5-43f3-aff7-873cf33fffb7/0_pisco_segment/results"
MODEL = ("/opt/pisco/tools/pisco_segmenter_py/pisco_segmenter/models/"
         "best_ssim_v5a_noGrad_ablation.pth")
N_BLOCK = 12


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


def rois(results_dir, frame):
    """{k: (x, y, w, h, pixels)} from the correction checkpoint holding `frame`."""
    for p in sorted(glob.glob(f"{results_dir}/checkpoint_correction*.h5")):
        with h5py.File(p, "r") as f:
            if frame not in f:
                continue
            g = f[frame]
            bx, by = np.array(g["boundingBoxX"]), np.array(g["boundingBoxY"])
            bw, bh = np.array(g["boundingBoxW"]), np.array(g["boundingBoxH"])
            px = np.array(g["pixelData"])
            out, o = {}, 0
            for k in range(len(bx)):
                n = int(bw[k]) * int(bh[k])
                out[k] = (int(bx[k]), int(by[k]), int(bw[k]), int(bh[k]),
                          px[o:o + n].reshape(int(bh[k]), int(bw[k])))
                o += n
            return out
    return {}


files = sorted_frames()
imgs = [cv.imread(os.path.join(SRC, f), cv.IMREAD_GRAYSCALE) for f in files]

import torch
sys.path.insert(0, "/opt/pisco/tools/pisco_segmenter_py")
from pisco_segmenter.lucyd import LUCYD
device = "cuda" if torch.cuda.is_available() else "cpu"
model = LUCYD(num_res=1).to(device); model.eval()
model.load_state_dict(torch.load(MODEL, map_location=device))

print(f"{'frame':>5} {'ROIs':>5} {'boxes same':>10} {'pre==corr':>9} "
      f"{'MAE deconv vs whole':>20} {'best shift MAE':>15} {'shift':>9}")
print("-" * 82)
tot, shifted = 0, 0
for i in (26, 30, 34, 38, 42):
    frame = files[i][:-4]
    r1, r2 = rois(B1, frame), rois(B2, frame)
    same_boxes = all(r1[k][:4] == r2[k][:4] for k in r1) and len(r1) == len(r2)
    s, e = block_bounds(i, N_BLOCK, len(files))
    off = cv.absdiff(imgs[i], maxmin(imgs[s:e]))
    cleaned = cv.bitwise_not(off)
    with torch.no_grad():
        x = torch.from_numpy(cleaned / 255).unsqueeze(0).unsqueeze(0).to(device)
        whole = cv.bitwise_not((model(x.float())[0].detach().cpu().numpy()[0, 0] * 255).astype(np.uint8))

    pre_ok = np.mean([np.array_equal(r1[k][4], off[r1[k][1]:r1[k][1] + r1[k][3], r1[k][0]:r1[k][0] + r1[k][2]])
                      for k in r1]) if r1 else 0
    maes, best, shifts = [], [], []
    for k, (X, Y, W, H, dec) in r2.items():
        if W < 24 or H < 24:
            continue
        ref = whole[Y:Y + H, X:X + W]
        maes.append(float(np.abs(ref.astype(int) - dec.astype(int)).mean()))
        # search a shift of up to +-12 px for the best alignment
        Y0, X0 = max(0, Y - 12), max(0, X - 12)
        big = whole[Y0:Y + H + 12, X0:X + W + 12]
        if big.shape[0] < H or big.shape[1] < W:
            continue
        res = cv.matchTemplate(big.astype(np.float32), dec.astype(np.float32), cv.TM_SQDIFF)
        _, _, loc, _ = cv.minMaxLoc(res)
        dy, dx = loc[1] + Y0 - Y, loc[0] + X0 - X
        alt = whole[Y + dy:Y + dy + H, X + dx:X + dx + W]
        best.append(float(np.abs(alt.astype(int) - dec.astype(int)).mean()))
        shifts.append((dx, dy))
        tot += 1; shifted += (dx, dy) != (0, 0)
    sh = max(set(shifts), key=shifts.count) if shifts else None
    print(f"{i:>5} {len(r2):>5} {str(same_boxes):>10} {pre_ok:>8.0%} "
          f"{np.median(maes):>20.2f} {np.median(best):>15.2f} {str(sh):>9}")
print(f"\nROIs whose stored deconvolved pixels align better after a shift: {shifted}/{tot}")
