"""Does the atlas lose faint features, how often, and does a texture-filled
atlas bring them back?

For each matched object (settled frames): the whole-frame deconvolution's
object at threshold 10 is what the python route renders. Inside the C++ box,
how much of that area does each variant recover at the same threshold?

  real atlas      pisco_deconvolve's stored crop (B2z)          - what ships
  white atlas     same crops re-packed here on flat white       - checks the re-pack is faithful
  texture atlas   same crops re-packed on real background texture from the frame
  texture, pad 30 same, with three times the padding

"lost" = 1 - recovered / whole-frame area, per object. Writes a panel of the
worst cases with all variants side by side.
"""
import csv, os, sys
import cv2 as cv
import h5py
import numpy as np

SRC = "/mnt/pisco/testdata/m202_a"
PY = "/mnt/pisco/hub-data/workflows/e1bb15a2-280f-49a1-a2e4-4d23c32044fe/0_pisco_segment_py/results"
CPP = "/mnt/pisco/hub-data/workflows/06fde7c1-96f5-43f3-aff7-873cf33fffb7/0_pisco_segment/results/result.h5"
MATCHES = "/mnt/pisco/compare/A2y_B2z/matches.csv"
OUT = "/mnt/pisco/compare/deconv"
MODEL = ("/opt/pisco/tools/pisco_segmenter_py/pisco_segmenter/models/"
         "best_ssim_v5a_noGrad_ablation.pth")
BLUE, ORANGE, GREEN = (214, 120, 42), (52, 104, 235), (122, 175, 27)
N_BLOCK, THR, ATLAS = 12, 10, 2600


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


def own_component(binary, ref_mask):
    """The component of `binary` containing the centre of `ref_mask`."""
    n, lab, stats, _ = cv.connectedComponentsWithStats(binary.astype(np.uint8), connectivity=8)
    if n <= 1:
        return np.zeros_like(binary, np.uint8)
    mo = cv.moments((ref_mask > 0).astype(np.uint8))
    if mo["m00"] > 0:
        cy, cx = int(mo["m01"] / mo["m00"]), int(mo["m10"] / mo["m00"])
        lbl = lab[min(max(cy, 0), lab.shape[0] - 1), min(max(cx, 0), lab.shape[1] - 1)]
        if lbl:
            return ((lab == lbl) * 255).astype(np.uint8)
    lbl = 1 + int(np.argmax(stats[1:, cv.CC_STAT_AREA]))
    return ((lab == lbl) * 255).astype(np.uint8)


def pack(crops, pad, base):
    atlas = base.copy(); placed, cx, cy, shelf = [], pad, pad, 0
    for k, c in enumerate(crops):
        h, w = c.shape
        if cx + w + pad > ATLAS:
            cx, cy, shelf = pad, cy + shelf + pad, 0
        if cy + h + pad > ATLAS:
            break
        atlas[cy:cy + h, cx:cx + w] = c
        placed.append((k, cx, cy, w, h)); cx += w + pad; shelf = max(shelf, h)
    return atlas, placed


files = sorted_frames()
imgs = [cv.imread(os.path.join(SRC, f), cv.IMREAD_GRAYSCALE) for f in files]

import torch
sys.path.insert(0, "/opt/pisco/tools/pisco_segmenter_py")
from pisco_segmenter.lucyd import LUCYD
device = "cuda" if torch.cuda.is_available() else "cpu"
model = LUCYD(num_res=1).to(device); model.eval()
model.load_state_dict(torch.load(MODEL, map_location=device))


def deconv(img):
    with torch.no_grad():
        x = torch.from_numpy(img / 255).unsqueeze(0).unsqueeze(0).to(device)
        return (model(x.float())[0].detach().cpu().numpy()[0, 0] * 255).astype(np.uint8)


rows = [r for r in csv.DictReader(open(MATCHES)) if r["stratum"] == "matched"
        and 150 < float(r["old_area"]) < 4000 and files.index(r["frame"] + ".png") >= 24]
by_frame = {}
for r in rows:
    by_frame.setdefault(r["frame"], []).append(r)

VARIANTS = ["real atlas", "white atlas", "texture atlas", "texture, pad 30"]
lost = {v: [] for v in VARIANTS}
examples = []

for frame, frs in by_frame.items():
    i = files.index(frame + ".png")
    s, e = block_bounds(i, N_BLOCK, len(files))
    off = cv.absdiff(imgs[i], maxmin(imgs[s:e]))
    cleaned = cv.bitwise_not(off)
    whole = deconv(cleaned)

    # python objects
    py = {}
    for row in csv.reader(open(f"{PY}/Data/{frame}.csv")):
        if len(row) >= 12 and row[0] != "corrupt" and row[11] == "1":
            idx = int(float(row[0]))
            m = cv.imread(f"{PY}/Masks/{frame}_{idx}.png", cv.IMREAD_GRAYSCALE)
            if m is not None:
                py[idx] = (int(float(row[7])), int(float(row[8])), int(float(row[9])), int(float(row[10])), m)
    # C++ objects: box, stored deconvolved crop, stored mask
    cp = {}
    with h5py.File(CPP, "r") as f:
        g = f[frame]
        bx, by = np.array(g["boundingBoxX"]), np.array(g["boundingBoxY"])
        bw, bh = np.array(g["boundingBoxW"]), np.array(g["boundingBoxH"])
        offs = np.array(g["offsets"]); masks = np.array(g["maskPixelData"]); crops = np.array(g["cropPixelData"])
        for k in range(len(offs)):
            n = int(bw[k]) * int(bh[k])
            if masks[offs[k]:offs[k] + n].size == n:
                cp[k] = (int(bx[k]), int(by[k]), int(bw[k]), int(bh[k]),
                         masks[offs[k]:offs[k] + n].reshape(int(bh[k]), int(bw[k])),
                         crops[offs[k]:offs[k] + n].reshape(int(bh[k]), int(bw[k])))

    # the atlases: every C++ object on this frame, cut from the identical corrected frame
    keys = [int(float(r["new_idx"])) for r in frs if int(float(r["new_idx"])) in cp]
    src = [off[cp[k][1]:cp[k][1] + cp[k][3], cp[k][0]:cp[k][0] + cp[k][2]] for k in keys]
    quiet = off.copy(); quiet[off > THR] = 0
    tex = np.tile(quiet[256:768, 256:768], (ATLAS // 512 + 1, ATLAS // 512 + 1))[:ATLAS, :ATLAS]
    white = np.zeros((ATLAS, ATLAS), np.uint8)
    dec = {}
    for name, base, pad in (("white atlas", white, 5), ("texture atlas", tex, 5), ("texture, pad 30", tex, 30)):
        atlas, placed = pack(src, pad, base)
        d = cv.bitwise_not(deconv(cv.bitwise_not(atlas)))          # back to bright-on-dark
        dec[name] = {keys[k]: d[ay:ay + h, ax:ax + w] for k, ax, ay, w, h in placed}

    for r in frs:
        po = py.get(int(float(r["old_idx"]))); k = int(float(r["new_idx"]))
        if po is None or k not in cp:
            continue
        X, Y, W, H, cmask, ccrop = cp[k]
        # reference: whole-frame result inside the box, own component
        wbox = cv.bitwise_not(whole[Y:Y + H, X:X + W])                 # bright on dark
        ref = own_component(wbox > THR, cmask)
        ref_area = int((ref > 0).sum())
        if ref_area < 50:
            continue
        got = {"real atlas": own_component(ccrop > THR, cmask)}
        for name in ("white atlas", "texture atlas", "texture, pad 30"):
            if k in dec[name]:
                got[name] = own_component(dec[name][k] > THR, cmask)
        for name, m in got.items():
            lost[name].append(1 - int((m > 0).sum()) / ref_area)
        if "texture atlas" in got:
            examples.append((lost["real atlas"][-1], frame, X, Y, W, H, cleaned[Y:Y + H, X:X + W],
                             cv.bitwise_not(wbox), cv.bitwise_not(ccrop), cv.bitwise_not(dec["texture atlas"][k]),
                             cv.bitwise_not(dec["texture, pad 30"][k]) if k in dec["texture, pad 30"] else None,
                             ref, got["real atlas"], got["texture atlas"]))

print(f"{len(lost['real atlas'])} matched objects, settled frames, threshold {THR}, own contour\n")
print(f"{'variant':<18} {'median lost':>12} {'p90 lost':>9} {'>10% lost':>10} {'>25% lost':>10}")
print("-" * 64)
for v in VARIANTS:
    a = np.array(lost[v])
    if not len(a):
        continue
    print(f"{v:<18} {np.median(a):>11.1%} {np.percentile(a, 90):>8.1%} {(a > .10).mean():>9.1%} {(a > .25).mean():>9.1%}")

# panel: worst real-atlas losses
examples.sort(key=lambda t: -t[0])
tiles = []
for lostf, frame, X, Y, W, H, c_in, w_img, r_img, t_img, t30_img, ref, got_r, got_t in examples[:8]:
    S = max(2, min(6, 200 // max(H, W)))
    def up(im): return cv.resize(cv.cvtColor(im, cv.COLOR_GRAY2BGR), (W * S, H * S), interpolation=cv.INTER_NEAREST)
    def outline(panel, mask, col):
        cs, _ = cv.findContours((mask > 0).astype(np.uint8), cv.RETR_EXTERNAL, cv.CHAIN_APPROX_NONE)
        cv.drawContours(panel, [c * S for c in cs], -1, col, 1)
    p = [up(c_in), up(w_img), up(r_img), up(t_img)]
    outline(p[1], ref, BLUE); outline(p[2], got_r, ORANGE); outline(p[3], got_t, GREEN)
    labels = ["corrected input", "whole-frame", f"real atlas  lost {lostf:.0%}", "texture atlas"]
    if t30_img is not None:
        p.append(up(t30_img)); labels.append("texture, pad 30")
    for q, l in zip(p, labels):
        cv.putText(q, l, (4, 12), cv.FONT_HERSHEY_SIMPLEX, .38, (30, 30, 30), 1, cv.LINE_AA)
    row = np.hstack([cv.copyMakeBorder(q, 1, 1, 1, 1, cv.BORDER_CONSTANT, value=(160, 160, 160)) for q in p])
    row = cv.copyMakeBorder(row, 16, 0, 0, 0, cv.BORDER_CONSTANT, value=(255, 255, 255))
    cv.putText(row, frame[:40], (4, 12), cv.FONT_HERSHEY_SIMPLEX, .38, (80, 80, 80), 1, cv.LINE_AA)
    tiles.append(row)
w = max(t.shape[1] for t in tiles)
tiles = [cv.copyMakeBorder(t, 0, 0, 0, w - t.shape[1], cv.BORDER_CONSTANT, value=(255, 255, 255)) for t in tiles]
cv.imwrite(f"{OUT}/lost_features.png", np.vstack(tiles))
print(f"\nwrote {OUT}/lost_features.png")
