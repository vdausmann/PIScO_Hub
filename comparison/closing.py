"""Does the deconvolved object count collapse under morphological closing?

The patches show deconvolution rendering one filamentous aggregate as a chain of
separate beads. If that is what inflates the count, closing the gaps before
counting should collapse it - and the size that collapses it tells you how wide
the gaps are. The C++ route already applies a 3x3 MORPH_CLOSE plus ROI merging,
which is the same idea.
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
KERNELS = [1, 3, 5, 7, 9, 13, 17, 21]


def sorted_frames():
    files = os.listdir(SRC)
    try:
        files.sort(key=lambda x: float(os.path.basename(x).split("_")[0].split("-")[1]))
    except Exception:
        files.sort()
    return [f for f in files if f.endswith(".png")]


def count(corrected, k):
    binary = (corrected > THRESHOLD).astype(np.uint8)
    if k > 1:
        ker = cv.getStructuringElement(cv.MORPH_ELLIPSE, (k, k))
        binary = cv.morphologyEx(binary, cv.MORPH_CLOSE, ker)
    n, lab, stats, _ = cv.connectedComponentsWithStats(binary, connectivity=8)
    areas = stats[1:, cv.CC_STAT_AREA]
    keep = areas[areas > MIN_AREA]
    return len(keep), float(np.median(keep)) if len(keep) else 0.0


def main():
    n_frames = int(sys.argv[1]) if len(sys.argv) > 1 else 6
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

    tot_off = {k: 0 for k in KERNELS}
    tot_on = {k: 0 for k in KERNELS}
    med_on = {k: [] for k in KERNELS}

    for _, i, fn in ranked:
        half = BG_SIZE // 2
        s, e = max(0, i - half), min(len(imgs), i + half + 1)
        bg = np.max([imgs[j] for j in range(s, e) if j != i], axis=0)
        off = cv.absdiff(imgs[i], bg)
        with torch.no_grad():
            x = torch.from_numpy(cv.bitwise_not(off) / 255)
            x = x.unsqueeze(0).unsqueeze(0).to(device)
            y = model(x.float())[0].detach().cpu().numpy()[0, 0]
        on = cv.bitwise_not((y * 255).astype(np.uint8))
        for k in KERNELS:
            n_off, _ = count(off, k)
            n_on, m_on = count(on, k)
            tot_off[k] += n_off
            tot_on[k] += n_on
            med_on[k].append(m_on)

    print(f"{n_frames} sparse frames, area > {MIN_AREA} px\n")
    print(f"{'close':>6} {'objects off':>12} {'objects on':>11} {'on/off':>8} "
          f"{'median area on':>15}")
    print("-" * 56)
    for k in KERNELS:
        label = "none" if k == 1 else f"{k}x{k}"
        print(f"{label:>6} {tot_off[k]:>12} {tot_on[k]:>11} "
              f"{tot_on[k] / max(tot_off[k], 1):>8.2f} "
              f"{np.median(med_on[k]):>15.0f}")


if __name__ == "__main__":
    main()
