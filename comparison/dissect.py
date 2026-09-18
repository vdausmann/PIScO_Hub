"""Dissect one truncated object: the pass-1 ROI it came from, the deconvolved
ROI, the pass-2 mask and crop, and the whole-frame reference, all at 8x.
"""
import csv, glob, os, sys
import cv2 as cv
import h5py
import numpy as np

SRC = "/mnt/pisco/testdata/m202_a"
B2 = "/mnt/pisco/hub-data/workflows/06fde7c1-96f5-43f3-aff7-873cf33fffb7/0_pisco_segment/results"
PY = "/mnt/pisco/hub-data/workflows/e1bb15a2-280f-49a1-a2e4-4d23c32044fe/0_pisco_segment_py/results"
MATCHES = "/mnt/pisco/compare/A2y_B2z/matches.csv"
MODEL = ("/opt/pisco/tools/pisco_segmenter_py/pisco_segmenter/models/"
         "best_ssim_v5a_noGrad_ablation.pth")
N_BLOCK, S = 12, 8


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
# the worst matched object on a settled frame, by C++/python area ratio
rows = [r for r in csv.DictReader(open(MATCHES)) if r["stratum"] == "matched"
        and 300 < float(r["old_area"]) < 3000 and files.index(r["frame"] + ".png") >= 24]
rows.sort(key=lambda r: float(r["new_area"]) / float(r["old_area"]))
r = rows[0]
frame, k = r["frame"], int(float(r["new_idx"]))
i = files.index(frame + ".png")
print(f"object: {frame}  C++ idx {k}   python area {float(r['old_area']):.0f}  C++ area {float(r['new_area']):.0f}")

imgs = [cv.imread(os.path.join(SRC, f), cv.IMREAD_GRAYSCALE) for f in files]
s, e = block_bounds(i, N_BLOCK, len(files))
off = cv.absdiff(imgs[i], maxmin(imgs[s:e]))
import torch
sys.path.insert(0, "/opt/pisco/tools/pisco_segmenter_py")
from pisco_segmenter.lucyd import LUCYD
device = "cuda" if torch.cuda.is_available() else "cpu"
model = LUCYD(num_res=1).to(device); model.eval()
model.load_state_dict(torch.load(MODEL, map_location=device))
with torch.no_grad():
    x = torch.from_numpy(cv.bitwise_not(off) / 255).unsqueeze(0).unsqueeze(0).to(device)
    whole = cv.bitwise_not((model(x.float())[0].detach().cpu().numpy()[0, 0] * 255).astype(np.uint8))

# pass-2 object
with h5py.File(f"{B2}/result.h5", "r") as f:
    g = f[frame]
    X, Y, W, H = (int(g["boundingBoxX"][k]), int(g["boundingBoxY"][k]),
                  int(g["boundingBoxW"][k]), int(g["boundingBoxH"][k]))
    o = int(g["offsets"][k]); n = W * H
    crop = np.array(g["cropPixelData"][o:o + n]).reshape(H, W)
    mask = np.array(g["maskPixelData"][o:o + n]).reshape(H, W)
print(f"pass-2 box      x={X} y={Y} w={W} h={H}")
print(f"  crop: min {crop.min()} max {crop.max()}  |  top half max {crop[:H//2].max()}  bottom half max {crop[H//2:].max()}")
print(f"  mask: {int((mask>0).sum())} px  |  top half {int((mask[:H//2]>0).sum())}  bottom half {int((mask[H//2:]>0).sum())}")
ref = whole[Y:Y + H, X:X + W]
print(f"  whole-frame at box: top half max {ref[:H//2].max()}  bottom half max {ref[H//2:].max()}")
print(f"  crop == whole-frame at box?  MAE {np.abs(crop.astype(int) - ref.astype(int)).mean():.2f}")

# the parent pass-1 ROI
parent = None
for p in sorted(glob.glob(f"{B2}/checkpoint_correction*.h5")):
    with h5py.File(p, "r") as f:
        if frame not in f:
            continue
        g = f[frame]
        bx, by = np.array(g["boundingBoxX"]), np.array(g["boundingBoxY"])
        bw, bh = np.array(g["boundingBoxW"]), np.array(g["boundingBoxH"])
        px = np.array(g["pixelData"]); oo = 0
        for j in range(len(bx)):
            rx, ry, rw, rh = int(bx[j]), int(by[j]), int(bw[j]), int(bh[j])
            roi = px[oo:oo + rw * rh].reshape(rh, rw); oo += rw * rh
            if rx <= X and ry <= Y and rx + rw >= X + W and ry + rh >= Y + H:
                parent = (rx, ry, rw, rh, roi); break
if parent:
    rx, ry, rw, rh, roi = parent
    print(f"parent ROI      x={rx} y={ry} w={rw} h={rh}   (object box sits at +{X-rx},+{Y-ry} inside it)")
    rref = whole[ry:ry + rh, rx:rx + rw]
    print(f"  ROI == whole-frame at ROI?  MAE {np.abs(roi.astype(int) - rref.astype(int)).mean():.2f}")
    tri, _ = cv.threshold(roi, 0, 255, cv.THRESH_TRIANGLE)
    print(f"  TRIANGLE on the ROI: {tri:.0f}  -> pass-2 threshold {max(tri, 10):.0f}")
    sub = roi[Y - ry:Y - ry + H, X - rx:X - rx + W]
    print(f"  ROI cut at the object box == stored crop?  MAE {np.abs(sub.astype(int) - crop.astype(int)).mean():.2f}")

# picture: ROI (whole-frame ref | stored deconvolved ROI | pass-2 threshold mask) with the object box drawn
def up(im): return cv.resize(cv.cvtColor(im, cv.COLOR_GRAY2BGR), (im.shape[1] * S, im.shape[0] * S), interpolation=cv.INTER_NEAREST)
if parent:
    thr = max(tri, 10)
    binroi = ((roi > thr) * 255).astype(np.uint8)
    panels = [up(cv.bitwise_not(rref)), up(cv.bitwise_not(roi)), up(255 - binroi), up(cv.bitwise_not(crop))]
    labels = ["whole-frame at ROI", "stored deconvolved ROI", f"ROI > {thr:.0f} (pass-2)", "stored crop (pass-2 box)"]
    for pnl in panels[:3]:
        cv.rectangle(pnl, ((X - rx) * S, (Y - ry) * S), ((X - rx + W) * S, (Y - ry + H) * S), (52, 104, 235), 2)
    for pnl, l in zip(panels, labels):
        cv.putText(pnl, l, (4, 14), cv.FONT_HERSHEY_SIMPLEX, .45, (30, 30, 30), 1, cv.LINE_AA)
    h = max(p_.shape[0] for p_ in panels)
    panels = [cv.copyMakeBorder(p_, 0, h - p_.shape[0], 0, 6, cv.BORDER_CONSTANT, value=(255, 255, 255)) for p_ in panels]
    cv.imwrite("/mnt/pisco/compare/deconv/dissect.png", np.hstack(panels))
    print("wrote /mnt/pisco/compare/deconv/dissect.png")
