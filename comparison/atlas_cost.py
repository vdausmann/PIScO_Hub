"""What does packing crops into an atlas cost, versus deconvolving the whole frame?

LUCYD was trained on whole 2560x2560 cleaned frames carrying 25-55 objects on a
real background texture with grain. The C++ route instead deconvolves a 2600x2600
atlas of crops packed 5 px apart on flat white (pack_crops.py:156,
ImagePacker(2600, 2600, 5)). The python route deconvolves the whole frame, which
is the in-distribution application.

This runs the same objects both ways and compares the resulting crops: how much
the pixels differ, and whether the detected mask area - Task 2 in the retraining
log - survives the round trip. Padding is swept, so the answer is not just "the
atlas is different" but "how much padding would it take".
"""
import csv, glob, os, sys
import cv2 as cv
import numpy as np

SRC = "/mnt/pisco/testdata/m202_a"
A1_DATA = ("/mnt/pisco/hub-data/workflows/"
           "e9b29a80-9ef9-44cb-9e0f-dc2e436911b5/0_pisco_segment_py/results/Data")
MODEL = ("/opt/pisco/tools/pisco_segmenter_py/pisco_segmenter/models/"
         "best_ssim_v5a_noGrad_ablation.pth")
BG_SIZE, THRESHOLD, MIN_AREA = 6, 10, 50
BBOX_PAD = 10            # the C++ boundingBoxPadding
ATLAS = 2600
PADDINGS = [5, 20, 60]   # 5 is what ships
FILLS = ["white", "texture"]


def sorted_frames():
    files = os.listdir(SRC)
    try:
        files.sort(key=lambda x: float(os.path.basename(x).split("_")[0].split("-")[1]))
    except Exception:
        files.sort()
    return [f for f in files if f.endswith(".png")]


def background(imgs, i):
    half = BG_SIZE // 2
    s, e = i - half, i + half + 1
    if s < 0:
        e += -s; s = 0
    if e > len(imgs):
        s = max(0, s - (e - len(imgs))); e = len(imgs)
    return np.max([imgs[j] for j in range(s, e) if j != i], axis=0)


def deconv(model, torch, device, img):
    with torch.no_grad():
        x = torch.from_numpy(img / 255).unsqueeze(0).unsqueeze(0).to(device)
        y = model(x.float())[0].detach().cpu().numpy()[0, 0]
    return np.clip(y * 255, 0, 255).astype(np.uint8)


def boxes(off):
    """Object bounding boxes from the corrected frame, padded as the C++ does."""
    binary = (off > THRESHOLD).astype(np.uint8)
    n, lab, stats, _ = cv.connectedComponentsWithStats(binary, connectivity=8)
    out = []
    for j in range(1, n):
        if stats[j, cv.CC_STAT_AREA] <= MIN_AREA:
            continue
        x, y = stats[j, cv.CC_STAT_LEFT], stats[j, cv.CC_STAT_TOP]
        w, h = stats[j, cv.CC_STAT_WIDTH], stats[j, cv.CC_STAT_HEIGHT]
        x0, y0 = max(0, x - BBOX_PAD), max(0, y - BBOX_PAD)
        x1, y1 = min(2560, x + w + BBOX_PAD), min(2560, y + h + BBOX_PAD)
        out.append((x0, y0, x1, y1))
    return out


def texture_atlas(off):
    """Tile object-free background from this frame, in the segmenter's domain.

    Training backgrounds are real cleaned textures plus grain, never flat white.
    This builds the nearest available equivalent out of the frame itself.
    """
    quiet = off.copy()
    quiet[off > THRESHOLD] = 0                        # drop the objects
    tile = quiet[:512, :512]
    reps = ATLAS // 512 + 1
    return np.tile(tile, (reps, reps))[:ATLAS, :ATLAS].astype(np.uint8)


def pack(crops, pad, base=None):
    """Shelf packing onto an atlas, the same geometry as ImagePacker."""
    atlas = (np.zeros((ATLAS, ATLAS), dtype=np.uint8) if base is None
             else base.copy())                        # 0 -> white after inversion
    placed, cx, cy, shelf = [], pad, pad, 0
    for k, c in enumerate(crops):
        h, w = c.shape
        if w + 2 * pad > ATLAS or h + 2 * pad > ATLAS:
            continue
        if cx + w + pad > ATLAS:
            cx, cy, shelf = pad, cy + shelf + pad, 0
        if cy + h + pad > ATLAS:
            break                                     # one atlas is enough for this test
        atlas[cy:cy + h, cx:cx + w] = c
        placed.append((k, cx, cy, w, h))
        cx += w + pad
        shelf = max(shelf, h)
    return atlas, placed


def mask_area(crop_cleaned_domain):
    """Detected area inside a crop, in the pipeline's own terms."""
    corrected = cv.bitwise_not(crop_cleaned_domain)
    return int((corrected > THRESHOLD).sum())


def main():
    n_frames = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    files = sorted_frames()
    counts = {}
    for p in glob.glob(f"{A1_DATA}/*.csv"):
        counts[os.path.basename(p)[:-4]] = sum(
            1 for row in csv.reader(open(p))
            if len(row) >= 12 and row[0] != "corrupt" and row[11] == "1")
    ranked = sorted(((counts.get(f[:-4], 0), i, f) for i, f in enumerate(files)
                     if i >= 6), key=lambda t: t[0])[:n_frames]

    import torch
    sys.path.insert(0, "/opt/pisco/tools/pisco_segmenter_py")
    from pisco_segmenter.lucyd import LUCYD
    device = "cuda" if torch.cuda.is_available() else "cpu"
    model = LUCYD(num_res=1).to(device); model.eval()
    model.load_state_dict(torch.load(MODEL, map_location=device))

    imgs = [cv.resize(cv.imread(os.path.join(SRC, f), cv.IMREAD_GRAYSCALE),
                      (2560, 2560)) for f in files]

    res = {(p, f): dict(mae=[], area=[], n=0) for p in PADDINGS for f in FILLS}
    n_obj = 0

    for _, i, fn in ranked:
        bg = background(imgs, i)
        off = cv.absdiff(imgs[i], bg)
        cleaned = cv.bitwise_not(off)

        # --- in-distribution: deconvolve the whole frame, then cut
        whole = deconv(model, torch, device, cleaned)
        bxs = boxes(off)
        n_obj += len(bxs)
        ref = [whole[y0:y1, x0:x1] for x0, y0, x1, y1 in bxs]
        src = [cleaned[y0:y1, x0:x1] for x0, y0, x1, y1 in bxs]

        tex = texture_atlas(off)
        for pad in PADDINGS:
          for fill in FILLS:
            # the atlas is built in the segmenter's own domain (objects bright on
            # 0) and inverted as a whole, exactly as pack_crops does
            atlas, placed = pack([cv.bitwise_not(s) for s in src], pad,
                                 None if fill == "white" else tex)
            dec = deconv(model, torch, device, cv.bitwise_not(atlas))
            for k, ax, ay, w, h in placed:
                got = dec[ay:ay + h, ax:ax + w]
                want = ref[k]
                if got.shape != want.shape:
                    continue
                res[(pad, fill)]["mae"].append(
                    float(np.abs(got.astype(np.float32) -
                                 want.astype(np.float32)).mean()))
                a_ref = mask_area(want)
                a_got = mask_area(got)
                if a_ref > 0:
                    res[(pad, fill)]["area"].append(a_got / a_ref)
                res[(pad, fill)]["n"] += 1

    print(f"{n_frames} sparse frames, {n_obj} objects, atlas {ATLAS}x{ATLAS}\n")
    print(f"{'padding':>8} {'gap fill':>9} {'crops':>7} {'MAE':>7} "
          f"{'mask area ratio':>17} {'p10':>7} {'p90':>7}")
    print("-" * 72)
    for pad in PADDINGS:
        for fill in FILLS:
            r = res[(pad, fill)]
            if not r["n"]:
                continue
            a = np.array(r["area"])
            tag = "   <- ships" if (pad == 5 and fill == "white") else ""
            print(f"{pad:>8} {fill:>9} {r['n']:>7} {np.median(r['mae']):>7.2f} "
                  f"{np.median(a):>17.3f} {np.percentile(a, 10):>7.3f} "
                  f"{np.percentile(a, 90):>7.3f}{tag}")
    print("\nMAE is on the 0-255 scale. A ratio of 1.0 means the atlas reproduces "
          "the\nwhole-frame mask area; below 1.0 the atlas shrinks objects.")


if __name__ == "__main__":
    main()
