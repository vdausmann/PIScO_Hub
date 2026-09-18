"""Deconvolution side by side: whole frame (python route) against the crop atlas
(C++ route), on a pixel-identical corrected input, same LUCYD weights.

The python route deconvolves the whole 2560x2560 cleaned frame. That is a
deterministic function of the corrected frame, so it is recomputed here and
*verified* against the pixels the python pipeline actually stored (its
result.h5 keeps only the masked object, so the surround has to come from the
recomputation). The C++ route's deconvolved crops are read straight from its
result.h5 - those went through the atlas.

Hypothesis under test: the flat white gaps between crops in the atlas make the
model hallucinate structure. If so, it shows up in the ring *around* the object
- the padding the C++ adds - as texture the whole-frame result does not have.
So the ring is measured separately from the object.

Writes deconv_examples.png: per matched object, corrected input | whole-frame
deconv | atlas deconv | amplified difference, with both routes' contours.
"""
import csv, os, sys
import cv2 as cv
import h5py
import numpy as np

SRC = "/mnt/pisco/testdata/m202_a"
PY = sys.argv[1]      # python results dir (A2y)
CPP = sys.argv[2]     # C++ result.h5 (B2z)
MATCHES = sys.argv[3]
FIXED = len(sys.argv) > 4 and sys.argv[4] == "--fixed"
OUT = "/mnt/pisco/compare/deconv"
MODEL = ("/opt/pisco/tools/pisco_segmenter_py/pisco_segmenter/models/"
         "best_ssim_v5a_noGrad_ablation.pth")
os.makedirs(OUT, exist_ok=True)
BLUE, ORANGE = (214, 120, 42), (52, 104, 235)
N_BLOCK, PAD = 12, 10


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


def py_objects(frame):
    out = {}
    for row in csv.reader(open(f"{PY}/Data/{frame}.csv")):
        if len(row) < 12 or row[0] == "corrupt" or row[11] != "1":
            continue
        idx = int(float(row[0]))
        x, y, w, h = (int(float(row[7])), int(float(row[8])),
                      int(float(row[9])), int(float(row[10])))
        m = cv.imread(f"{PY}/Masks/{frame}_{idx}.png", cv.IMREAD_GRAYSCALE)
        d = cv.imread(f"{PY}/Deconv_crops/{frame}_{idx}.png", cv.IMREAD_GRAYSCALE)
        if m is None or d is None:
            continue
        out[idx] = dict(x=x, y=y, w=w, h=h, area=float(row[6]), mask=m, deconv=d)
    return out


def cpp_objects(frame):
    out = {}
    with h5py.File(CPP, "r") as f:
        g = f[frame]
        bx, by = np.array(g["boundingBoxX"]), np.array(g["boundingBoxY"])
        bw, bh = np.array(g["boundingBoxW"]), np.array(g["boundingBoxH"])
        offs = np.array(g["offsets"])
        masks, crops = np.array(g["maskPixelData"]), np.array(g["cropPixelData"])
        for k in range(len(offs)):
            n = int(bw[k]) * int(bh[k])
            m = masks[offs[k]:offs[k] + n]; c = crops[offs[k]:offs[k] + n]
            if m.size != n:
                continue
            m = m.reshape(int(bh[k]), int(bw[k])); c = c.reshape(int(bh[k]), int(bw[k]))
            if FIXED:
                # What pass 2 would store with a fixed threshold: the crop
                # thresholded at 10. Which component is *this* object's own
                # contour is decided later, by overlap with the python object.
                m = ((c > 10) * 255).astype(np.uint8)
            out[k] = dict(x=int(bx[k]), y=int(by[k]), w=int(bw[k]), h=int(bh[k]),
                          mask=m, deconv=c)   # bright on dark
    return out


files = sorted_frames()
imgs = [cv.imread(os.path.join(SRC, f), cv.IMREAD_GRAYSCALE) for f in files]

import torch
sys.path.insert(0, "/opt/pisco/tools/pisco_segmenter_py")
from pisco_segmenter.lucyd import LUCYD
device = "cuda" if torch.cuda.is_available() else "cpu"
model = LUCYD(num_res=1).to(device); model.eval()
model.load_state_dict(torch.load(MODEL, map_location=device))

def select(matches_csv):
    rows = [r for r in csv.DictReader(open(matches_csv)) if r["stratum"] == "matched"]
    # mid-sized, well-matched objects from the settled part of the profile
    rows = [r for r in rows if 200 < float(r["old_area"]) < 3000 and float(r["iou"]) > 0.6
            and files.index(r["frame"] + ".png") >= 24]
    np.random.default_rng(1).shuffle(rows)
    return rows

REF = os.environ.get("REF_MATCHES")          # an earlier run's matches: reuse its objects
if REF:
    # the python side is unchanged between runs, so key on (frame, python idx)
    keys = [(r["frame"], r["old_idx"]) for r in select(REF)]
    cur = {(r["frame"], r["old_idx"]): r for r in csv.DictReader(open(MATCHES)) if r["stratum"] == "matched"}
    rows = [cur[k] for k in keys if k in cur]
    missing = sum(1 for k in keys[:10] if k not in cur)
    if missing:
        print(f"note: {missing} of the first 10 reference objects have no match in this run")
else:
    rows = select(MATCHES)

# --- verify the whole-frame recomputation against what the python pipe stored
whole_cache = {}
def whole_deconv(i):
    if i not in whole_cache:
        s, e = block_bounds(i, N_BLOCK, len(files))
        cleaned = cv.bitwise_not(cv.absdiff(imgs[i], maxmin(imgs[s:e])))
        with torch.no_grad():
            x = torch.from_numpy(cleaned / 255).unsqueeze(0).unsqueeze(0).to(device)
            y = model(x.float())[0].detach().cpu().numpy()[0, 0]
        whole_cache[i] = (cleaned, (y * 255).astype(np.uint8))   # dark on light, like the pipe
    return whole_cache[i]

ring_whole, ring_atlas, obj_mae, verified = [], [], [], []
truncated, edge_mae, core_mae = [], [], []
tiles = []
for r in rows:
    if len(tiles) >= 10 and len(ring_whole) >= 120:
        break
    frame = r["frame"]; i = files.index(frame + ".png")
    py = py_objects(frame); cp = cpp_objects(frame)
    po = py.get(int(float(r["old_idx"]))); co = cp.get(int(float(r["new_idx"])))
    if po is None or co is None:
        continue
    cleaned, whole = whole_deconv(i)

    # verification: inside the python mask, the pipe's stored Deconv_crops
    # pixels must equal the recomputation (Deconv_crops is masked with 255)
    wcrop = whole[po["y"]:po["y"] + po["h"], po["x"]:po["x"] + po["w"]]
    inside = po["mask"] == 255
    if inside.any() and wcrop.shape == po["deconv"].shape:
        verified.append(float(np.abs(wcrop[inside].astype(int) - po["deconv"][inside].astype(int)).mean()))

    # C++ box (padded); the same box from the whole-frame result
    X, Y, W, H = co["x"], co["y"], co["w"], co["h"]
    w_box = whole[Y:Y + H, X:X + W]
    a_box = cv.bitwise_not(co["deconv"])          # to dark-on-light for comparison
    c_box = cleaned[Y:Y + H, X:X + W]
    if w_box.shape != a_box.shape:
        continue

    # ring = inside the C++ box, outside the (dilated) python object
    pm = np.zeros((H, W), np.uint8)
    py_in_box = po["mask"][max(0, Y - po["y"]):po["h"], max(0, X - po["x"]):po["w"]]
    oy, ox = max(0, po["y"] - Y), max(0, po["x"] - X)
    hh, ww = min(py_in_box.shape[0], H - oy), min(py_in_box.shape[1], W - ox)
    pm[oy:oy + hh, ox:ox + ww] = py_in_box[:hh, :ww]
    ring = cv.dilate(pm, cv.getStructuringElement(cv.MORPH_ELLIPSE, (7, 7))) == 0
    if ring.sum() > 30:
        # structure in the ring: how far its pixels stray from their own background level
        ring_whole.append(float(np.std(w_box[ring].astype(np.float32))))
        ring_atlas.append(float(np.std(a_box[ring].astype(np.float32))))
    obj = pm > 0
    if obj.any():
        obj_mae.append(float(np.abs(w_box[obj].astype(int) - a_box[obj].astype(int)).mean()))

    # does the deconvolved object (python contour, threshold 10 on the
    # whole-frame result) extend past the C++ box, which was sized before
    # deconvolution? and is the disagreement concentrated at the box border?
    full_mask = np.zeros(whole.shape, np.uint8)
    full_mask[po["y"]:po["y"] + po["h"], po["x"]:po["x"] + po["w"]] = po["mask"]
    outside = full_mask.copy(); outside[Y:Y + H, X:X + W] = 0
    truncated.append(bool((outside > 0).any()))
    d = np.abs(w_box.astype(int) - a_box.astype(int))
    border = np.ones((H, W), bool); border[3:-3, 3:-3] = False
    edge_mae.append(float(d[border].mean())); core_mae.append(float(d[~border].mean()))

    if len(tiles) < 10:
        S = max(2, min(6, 200 // max(H, W)))
        def up(im):
            im = cv.cvtColor(im, cv.COLOR_GRAY2BGR)
            return cv.resize(im, (W * S, H * S), interpolation=cv.INTER_NEAREST)
        diff = np.abs(w_box.astype(int) - a_box.astype(int))
        diff = np.clip(diff * 4, 0, 255).astype(np.uint8)          # amplified 4x
        panels = [up(c_box), up(w_box), up(a_box), up(255 - diff)]
        # contours: python mask (blue) on the whole-frame panel, C++ mask (orange) on the atlas panel
        pc, _ = cv.findContours(pm, cv.RETR_EXTERNAL, cv.CHAIN_APPROX_NONE)
        cmask = (co["mask"] > 0).astype(np.uint8)
        if FIXED:
            # own contour = the component overlapping the python object most
            n_, lab, stats, _ = cv.connectedComponentsWithStats(cmask, connectivity=8)
            if n_ > 1:
                ov = [int(((lab == j) & (pm > 0)).sum()) for j in range(1, n_)]
                j = 1 + int(np.argmax(ov)) if max(ov) > 0 else 1 + int(np.argmax(stats[1:, cv.CC_STAT_AREA]))
                cmask = (lab == j).astype(np.uint8)
        cc, _ = cv.findContours(cmask, cv.RETR_EXTERNAL, cv.CHAIN_APPROX_NONE)
        cv.drawContours(panels[1], [c * S for c in pc], -1, BLUE, 1)
        cv.drawContours(panels[2], [c * S for c in cc], -1, ORANGE, 1)
        for p_, label in zip(panels, ("corrected input", "whole-frame (python)", "atlas (C++)" + (" fixed 10, own contour" if FIXED else ""), "|diff| x4")):
            cv.putText(p_, label, (4, 12), cv.FONT_HERSHEY_SIMPLEX, .38, (30, 30, 30), 1, cv.LINE_AA)
        row_img = np.hstack([cv.copyMakeBorder(p_, 1, 1, 1, 1, cv.BORDER_CONSTANT, value=(160, 160, 160)) for p_ in panels])
        info = (f"{frame[:36]}  ring std whole {ring_whole[-1]:.1f} / atlas {ring_atlas[-1]:.1f}"
                if ring_whole else frame[:36])
        row_img = cv.copyMakeBorder(row_img, 18, 0, 0, 0, cv.BORDER_CONSTANT, value=(255, 255, 255))
        cv.putText(row_img, info, (4, 13), cv.FONT_HERSHEY_SIMPLEX, .4, (60, 60, 60), 1, cv.LINE_AA)
        tiles.append(row_img)

w = max(t.shape[1] for t in tiles)
tiles = [cv.copyMakeBorder(t, 0, 0, 0, w - t.shape[1], cv.BORDER_CONSTANT, value=(255, 255, 255)) for t in tiles]
tag = os.environ.get("OUT_TAG", "")
cv.imwrite(f"{OUT}/deconv_examples{tag}{'_fixed10' if FIXED else ''}.png", np.vstack(tiles))

rw, ra = np.array(ring_whole), np.array(ring_atlas)
print(f"recomputed whole-frame deconvolution vs python pipe's stored pixels: "
      f"mean abs diff {np.mean(verified):.3f} over {len(verified)} objects "
      f"({'identical' if np.mean(verified) < 0.01 else 'NOT identical'})")
print(f"\n{len(rw)} matched objects, settled frames, same LUCYD weights\n")
print(f"  inside the object, |whole - atlas| mean:        {np.mean(obj_mae):5.2f}  (0-255)")
print(f"  ring around the object, texture (std):")
print(f"     whole-frame (python route)                   {np.median(rw):5.2f}  median")
print(f"     atlas       (C++ route)                      {np.median(ra):5.2f}  median")
print(f"     atlas / whole, per object                    {np.median(ra / np.maximum(rw, 1e-3)):5.2f}x  "
      f"(p10 {np.percentile(ra / np.maximum(rw, 1e-3), 10):.2f}, p90 {np.percentile(ra / np.maximum(rw, 1e-3), 90):.2f})")
print(f"     objects where the atlas ring is >1.5x busier  {(ra > 1.5 * rw).mean():5.1%}")
print(f"\n  python's deconvolved object extends past the C++ box:  {np.mean(truncated):5.1%} of objects")
print(f"  |whole - atlas| in the outer 3 px of the box:          {np.mean(edge_mae):5.2f}")
print(f"  |whole - atlas| in the interior:                       {np.mean(core_mae):5.2f}"
      f"   -> border is {np.mean(edge_mae) / max(np.mean(core_mae), 1e-3):.1f}x")
print(f"\nwrote {OUT}/deconv_examples.png")
