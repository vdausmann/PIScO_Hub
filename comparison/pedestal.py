"""What background level does LUCYD actually get, and does it matter?

The reference slide (LUCYD_background-corr_230705.png) shows the model being
applied to a background-corrected image whose background sits around 197 - a
light grey. Both pipelines feed it `bitwise_not(absdiff(img, bg))`, whose
background is ~253: near-white, and flat, because the subtraction removed the
sensor noise the model was trained alongside.

This measures the level the pipelines really deliver, then re-runs the model
with the background shifted onto a grey pedestal and compares what comes out.
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
PEDESTALS = [255, 230, 197, 170]


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


def count(corrected):
    binary = (corrected > THRESHOLD).astype(np.uint8)
    n, lab, stats, _ = cv.connectedComponentsWithStats(binary, connectivity=8)
    a = stats[1:, cv.CC_STAT_AREA]
    keep = a[a > MIN_AREA]
    return len(keep), int(binary.sum()), float(np.median(keep)) if len(keep) else 0.0


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

    raw_bg, fed_bg = [], []
    res = {p: dict(n=0, px=0, med=[]) for p in PEDESTALS}
    off_n = off_px = 0

    for _, i, fn in ranked:
        bg = background(imgs, i)
        off = cv.absdiff(imgs[i], bg)
        raw_bg.append(float(np.median(imgs[i])))
        fed = cv.bitwise_not(off)
        fed_bg.append(float(np.median(fed)))

        n, px, _ = count(off)
        off_n += n; off_px += px

        for ped in PEDESTALS:
            # same object contrast, different background level
            x_img = np.clip(ped.__float__() - off.astype(np.float32), 0, 255)
            with torch.no_grad():
                x = torch.from_numpy(x_img / 255).unsqueeze(0).unsqueeze(0).to(device)
                y = model(x.float())[0].detach().cpu().numpy()[0, 0]
            out = np.clip(y * 255, 0, 255)
            # The fixed threshold of 10 is only meaningful against a background
            # of ~255. Re-reference each output to its own background level so
            # the pedestals are compared at equal sensitivity, not at equal
            # absolute intensity.
            level = float(np.median(out))
            on = np.clip(level - out, 0, 255).astype(np.uint8)
            n, px, med = count(on)
            res[ped]["n"] += n; res[ped]["px"] += px; res[ped]["med"].append(med)

    print(f"{n_frames} sparse frames\n")
    print(f"raw frame background              median {np.median(raw_bg):5.1f}")
    print(f"what the pipelines feed LUCYD     median {np.median(fed_bg):5.1f}"
          f"   (reference slide: ~197)")
    print(f"no deconvolution                  {off_n:5d} objects, "
          f"{off_px:9d} px above threshold\n")
    print(f"{'pedestal':>9} {'objects':>9} {'vs no-deconv':>13} "
          f"{'px above thr':>13} {'median area':>12}")
    print("-" * 62)
    for ped in PEDESTALS:
        r = res[ped]
        tag = "  <- what the pipelines use" if ped == 255 else (
              "  <- reference slide" if ped == 197 else "")
        print(f"{ped:>9} {r['n']:>9} {r['n'] / max(off_n, 1):>12.2f}x "
              f"{r['px']:>13} {np.median(r['med']):>12.0f}{tag}")


if __name__ == "__main__":
    main()
