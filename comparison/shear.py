"""Prove the write-back geometry bug in pisco_deconvolve.

pack_crops.py:145 maps each placed crop as (x+1, y+1, w-2, h-2) although it was
placed at full size; writer.py then writes (w-2)*(h-2) pixels with row stride
w-2 into a slot the reader interprets as h rows of w. If that is what happened,
then re-reading the first (w-2)*(h-2) pixels of each stored ROI as an (h-2, w-2)
array should match the whole-frame deconvolution of the ROI's interior almost
exactly, while reading it as (h, w) - what pass 2 does - should not.
"""
import glob, os, sys
import cv2 as cv
import h5py
import numpy as np

SRC = "/mnt/pisco/testdata/m202_a"
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


files = sorted_frames()
imgs = [cv.imread(os.path.join(SRC, f), cv.IMREAD_GRAYSCALE) for f in files]
import torch
sys.path.insert(0, "/opt/pisco/tools/pisco_segmenter_py")
from pisco_segmenter.lucyd import LUCYD
device = "cuda" if torch.cuda.is_available() else "cpu"
model = LUCYD(num_res=1).to(device); model.eval()
model.load_state_dict(torch.load(MODEL, map_location=device))

as_stored, as_unsheared, tail_is_old, n = [], [], [], 0
recs = []
for i in (30, 38, 42):
    frame = files[i][:-4]
    s, e = block_bounds(i, N_BLOCK, len(files))
    off = cv.absdiff(imgs[i], maxmin(imgs[s:e]))
    with torch.no_grad():
        x = torch.from_numpy(cv.bitwise_not(off) / 255).unsqueeze(0).unsqueeze(0).to(device)
        whole = cv.bitwise_not((model(x.float())[0].detach().cpu().numpy()[0, 0] * 255).astype(np.uint8))
    for p in sorted(glob.glob(f"{B2}/checkpoint_correction*.h5")):
        with h5py.File(p, "r") as f:
            if frame not in f:
                continue
            g = f[frame]
            bx, by = np.array(g["boundingBoxX"]), np.array(g["boundingBoxY"])
            bw, bh = np.array(g["boundingBoxW"]), np.array(g["boundingBoxH"])
            px = np.array(g["pixelData"]); o = 0
            for k in range(len(bx)):
                X, Y, W, H = int(bx[k]), int(by[k]), int(bw[k]), int(bh[k])
                slot = px[o:o + W * H]; o += W * H
                if W < 30 or H < 30 or (whole[Y:Y + H, X:X + W] > 40).sum() < 200:
                    continue                     # only ROIs with a real object in them
                stored = slot.reshape(H, W)
                unsheared = slot[:(W - 2) * (H - 2)].reshape(H - 2, W - 2)
                ref_full = whole[Y:Y + H, X:X + W]
                ref_in = whole[Y + 1:Y + H - 1, X + 1:X + W - 1]
                as_stored.append(float(np.abs(stored.astype(int) - ref_full.astype(int)).mean()))
                as_unsheared.append(float(np.abs(unsheared.astype(int) - ref_in.astype(int)).mean()))
                # the last 2W+2H-4 pixels of the slot were never written: still corrected, not deconvolved
                tail = slot[(W - 2) * (H - 2):]
                old_tail = off[Y:Y + H, X:X + W].flatten()[(W - 2) * (H - 2):]
                tail_is_old.append(bool(np.array_equal(tail, old_tail)))
                n += 1
                # where inside the ROI does it go wrong? compare top half vs bottom half
                d = np.abs(stored.astype(int) - ref_full.astype(int))
                recs.append((as_stored[-1], i, k, len(bx), W, H, float(d[:H // 2].mean()), float(d[H // 2:].mean()),
                             float(d[:, :W // 2].mean()), float(d[:, W // 2:].mean())))

print(f"{n} ROIs with a real object, three settled frames\n")
print(f"  stored ROI read as (h, w), as pass 2 reads it,  vs whole-frame:  MAE {np.median(as_stored):6.2f}")
print(f"  first (w-2)(h-2) pixels read as (h-2, w-2),      vs whole-frame:  MAE {np.median(as_unsheared):6.2f}")
print(f"  tail of the slot still holds pre-deconvolution pixels:            {np.mean(tail_is_old):.0%} of ROIs")
a = np.array(as_stored)
print(f"\n  per-ROI MAE vs whole-frame: p10 {np.percentile(a,10):.1f}  p50 {np.percentile(a,50):.1f}  "
      f"p90 {np.percentile(a,90):.1f}  max {a.max():.1f}   ROIs > 8: {(a > 8).sum()}/{len(a)}")
print(f"\n  worst ROIs (MAE, frame, roi idx / n, W x H, MAE top half, bottom half, left half, right half):")
for r in sorted(recs, key=lambda t: -t[0])[:8]:
    print(f"    {r[0]:5.1f}  f{r[1]}  {r[2]:>3}/{r[3]:<3}  {r[4]:>3}x{r[5]:<3}  top {r[6]:5.1f}  bot {r[7]:5.1f}  "
          f"left {r[8]:5.1f}  right {r[9]:5.1f}")
