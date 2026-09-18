"""Object registry across deconvolution on/off, on a handful of sparse frames.

Registers every deconv-on object against the deconv-off image by *containment*,
not centroid distance: where does this object's centre sit in the deconv-off
labelled component map? That is what "belongs to the same object" has to mean
when deconvolution fragments one diffuse blob into several sharp peaks.

Each deconv-on object gets one of four labels:

  kept        its centre lands in a deconv-off object, and it is the only one
              that does - a 1:1 correspondence, deconvolution just sharpened it
  split       several deconv-on objects land in the same deconv-off object
  promoted    its centre lands on above-threshold deconv-off pixels, but in a
              component too small to qualify as an object - the material was
              already there and deconvolution lifted it over the area filter
  new         its centre lands on deconv-off pixels that were below threshold -
              nothing was there at all

"new" is the only category that could be an artifact of deconvolution. "split"
and "promoted" are real material being re-partitioned, which is a size-spectrum
question, not a false-positive one.

Writes registry.csv (one row per deconv-on object) and per-frame overlay PNGs
for eyeballing.
"""
import argparse, csv, glob, json, os, sys
import cv2 as cv
import numpy as np

SRC = "/mnt/pisco/testdata/m202_a"
A1_DATA = ("/mnt/pisco/hub-data/workflows/"
           "e9b29a80-9ef9-44cb-9e0f-dc2e436911b5/0_pisco_segment_py/results/Data")
MODEL = ("/opt/pisco/tools/pisco_segmenter_py/pisco_segmenter/models/"
         "best_ssim_v5a_noGrad_ablation.pth")
BG_SIZE, THRESHOLD, MIN_AREA = 6, 10, 50

CLASS_COLOUR = {                      # BGR, for the overlays
    "kept":     (214, 120, 42),       # blue
    "split":    (52, 104, 235),       # orange
    "promoted": (122, 175, 27),       # aqua
    "new":      (72, 73, 227),        # red
}


def sorted_frames():
    files = os.listdir(SRC)
    try:
        files.sort(key=lambda x: float(os.path.basename(x).split("_")[0].split("-")[1]))
    except Exception:
        files.sort()
    return [f for f in files if f.endswith(".png")]


def counts_per_frame():
    out = {}
    for p in glob.glob(f"{A1_DATA}/*.csv"):
        n = sum(1 for row in csv.reader(open(p))
                if len(row) >= 12 and row[0] != "corrupt" and row[11] == "1")
        out[os.path.basename(p)[:-4]] = n
    return out


def background(imgs, i):
    half = BG_SIZE // 2
    start, end = i - half, i + half + 1
    if start < 0:
        end += -start; start = 0
    if end > len(imgs):
        start = max(0, start - (end - len(imgs))); end = len(imgs)
    return np.max([imgs[j] for j in range(start, end) if j != i], axis=0)


def components(corrected):
    """Labelled components of the thresholded image, plus their areas.

    Keeps sub-threshold-area components in the map: the point is to be able to
    tell "material was there but too small" apart from "nothing was there".
    """
    binary = (corrected > THRESHOLD).astype(np.uint8)
    n, labels, stats, cents = cv.connectedComponentsWithStats(binary, connectivity=8)
    areas = stats[:, cv.CC_STAT_AREA].astype(float)
    return labels, areas, cents, int(binary.sum())


def objects(labels, areas, cents):
    """Components that pass the area filter, as the pipeline's objects."""
    return [dict(label=i, area=float(areas[i]),
                 cx=float(cents[i][0]), cy=float(cents[i][1]))
            for i in range(1, len(areas)) if areas[i] > MIN_AREA]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-n", type=int, default=6, help="how many sparse frames")
    ap.add_argument("--out", default="/mnt/pisco/compare/registry")
    ap.add_argument("--overlays", action="store_true", help="write annotated PNGs")
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)

    files = sorted_frames()
    counts = counts_per_frame()
    ranked = sorted(((counts.get(f[:-4], 0), i, f) for i, f in enumerate(files)
                     if i >= 6), key=lambda t: t[0])
    picked = ranked[:args.n]

    import torch
    sys.path.insert(0, "/opt/pisco/tools/pisco_segmenter_py")
    from pisco_segmenter.lucyd import LUCYD
    device = "cuda" if torch.cuda.is_available() else "cpu"
    model = LUCYD(num_res=1).to(device)
    model.load_state_dict(torch.load(MODEL, map_location=device))
    model.eval()

    imgs = [cv.resize(cv.imread(os.path.join(SRC, f), cv.IMREAD_GRAYSCALE),
                      (2560, 2560)) for f in files]

    rows = []
    print(f"{'frame':<16} {'off':>5} {'on':>5} {'x':>5} "
          f"{'kept':>6} {'split':>6} {'promoted':>9} {'new':>6}   "
          f"{'area on/off':>11}")
    print("-" * 82)

    for n_obj, i, fn in picked:
        bg = background(imgs, i)
        off_img = cv.absdiff(imgs[i], bg)
        with torch.no_grad():
            x = torch.from_numpy(cv.bitwise_not(off_img) / 255)
            x = x.unsqueeze(0).unsqueeze(0).to(device)
            y = model(x.float())[0].detach().cpu().numpy()[0, 0]
        on_img = cv.bitwise_not((y * 255).astype(np.uint8))

        off_labels, off_areas, off_cents, off_px = components(off_img)
        on_labels, on_areas, on_cents, on_px = components(on_img)
        off_objs = objects(off_labels, off_areas, off_cents)
        on_objs = objects(on_labels, on_areas, on_cents)

        # which deconv-off component does each deconv-on object's centre sit in?
        big_off = {o["label"] for o in off_objs}
        parents = []
        for o in on_objs:
            px, py = int(round(o["cx"])), int(round(o["cy"]))
            px = min(max(px, 0), off_labels.shape[1] - 1)
            py = min(max(py, 0), off_labels.shape[0] - 1)
            parents.append(int(off_labels[py, px]))
        claims = {}
        for p in parents:
            if p in big_off:
                claims[p] = claims.get(p, 0) + 1

        recs = []
        for o, p in zip(on_objs, parents):
            if p in big_off:
                cls = "split" if claims[p] > 1 else "kept"
                parent_area = float(off_areas[p])
            elif p != 0:
                cls, parent_area = "promoted", float(off_areas[p])
            else:
                cls, parent_area = "new", 0.0
            recs.append(dict(frame=fn[:-4], cx=round(o["cx"], 1), cy=round(o["cy"], 1),
                             area=o["area"], cls=cls, parent=p,
                             parent_area=parent_area))
        rows.extend(recs)

        c = lambda k: sum(1 for r in recs if r["cls"] == k)
        short = fn.split("_PISCO2_")[1][:12]
        print(f"{short:<16} {len(off_objs):>5} {len(on_objs):>5} "
              f"{len(on_objs) / max(len(off_objs), 1):>5.2f} "
              f"{c('kept'):>6} {c('split'):>6} {c('promoted'):>9} {c('new'):>6}   "
              f"{on_px / max(off_px, 1):>11.2f}")

        if args.overlays:
            vis = cv.cvtColor(cv.bitwise_not(on_img), cv.COLOR_GRAY2BGR)
            for o in off_objs:                       # deconv-off footprint, faint
                ys, xs = np.where(off_labels == o["label"])
                vis[ys, xs] = (0.78 * vis[ys, xs] + 0.22 *
                               np.array([210, 210, 210])).astype(np.uint8)
            for r, o in zip(recs, on_objs):
                cv.circle(vis, (int(o["cx"]), int(o["cy"])), 9,
                          CLASS_COLOUR[r["cls"]], 2)
            cv.imwrite(f"{args.out}/{fn[:-4]}_registry.png", vis)

    with open(f"{args.out}/registry.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader(); w.writerows(rows)

    print("-" * 82)
    tot = len(rows)
    for k in ("kept", "split", "promoted", "new"):
        sel = [r for r in rows if r["cls"] == k]
        med = np.median([r["area"] for r in sel]) if sel else 0
        print(f"{k:>9}: {len(sel):>5}  ({len(sel) / tot:5.1%})   "
              f"median area {med:>6.0f} px")
    print(f"\nwrote {args.out}/registry.csv"
          + (f" and {len(picked)} overlays" if args.overlays else ""))


if __name__ == "__main__":
    main()
