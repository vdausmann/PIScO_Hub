"""Is a fixed threshold of 10 the same sensitivity with and without deconvolution?

Reconstructs the python route's background correction exactly (bg_correction.py:
pixel-wise max over a centred window of `bg_size` frames excluding self, then
absdiff), then runs LUCYD over it the way deconvolution.py does, and reports what
threshold 10 means in each condition: as a noise multiple, as a percentile, and
as a detected-pixel count.

The point: if 10 sits at 6 sigma in one condition and 2 sigma in the other, the
object-count difference between the arms is a threshold artifact, not a
detection difference, and the deconv on/off comparison has to be re-based on a
matched operating point.
"""
import argparse, csv, glob, os, sys
import cv2 as cv
import numpy as np

SRC = "/mnt/pisco/testdata/m202_a"
A1_DATA = ("/mnt/pisco/hub-data/workflows/"
           "e9b29a80-9ef9-44cb-9e0f-dc2e436911b5/0_pisco_segment_py/results/Data")
MODEL = ("/opt/pisco/tools/pisco_segmenter_py/pisco_segmenter/models/"
         "best_ssim_v5a_noGrad_ablation.pth")
BG_SIZE = 6          # segmenter.py:273
THRESHOLD = 10       # detection.py:229


def sorted_frames():
    """Source frames in the order the pipeline reads them (by time key)."""
    files = os.listdir(SRC)
    try:
        files.sort(key=lambda x: float(os.path.basename(x).split("_")[0].split("-")[1]))
    except Exception:
        files.sort()
    return [f for f in files if f.endswith(".png")]


def counts_per_frame():
    out = {}
    for p in glob.glob(f"{A1_DATA}/*.csv"):
        n = 0
        for row in csv.reader(open(p)):
            if len(row) >= 12 and row[0] != "corrupt" and row[11] == "1":
                n += 1
        out[os.path.basename(p)[:-4]] = n
    return out


def background(imgs, i):
    """bg_correction.py: pixel-wise max over a centred window, excluding self."""
    half = BG_SIZE // 2
    start, end = i - half, i + half + 1
    if start < 0:
        end += -start; start = 0
    if end > len(imgs):
        start = max(0, start - (end - len(imgs))); end = len(imgs)
    stack = [imgs[j] for j in range(start, end) if j != i]
    return np.max(stack, axis=0)


def stats(corrected, label):
    """Robust background scale, and what THRESHOLD means against it."""
    med = float(np.median(corrected))
    mad = float(np.median(np.abs(corrected.astype(np.float32) - med)))
    sigma = 1.4826 * mad          # MAD -> gaussian sigma
    above = int((corrected > THRESHOLD).sum())
    frac = above / corrected.size
    sig = (THRESHOLD - med) / sigma if sigma > 0 else float("inf")
    pctile = float((corrected <= THRESHOLD).mean() * 100)
    return dict(label=label, median=med, sigma=sigma, sigmas=sig,
                pixels_above=above, frac_above=frac, percentile=pctile)


def matched_threshold(corrected, target_frac):
    """The threshold that flags the same fraction of pixels as the other arm."""
    return float(np.quantile(corrected, 1 - target_frac))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-n", type=int, default=5, help="how many sparse frames")
    args = ap.parse_args()

    files = sorted_frames()
    counts = counts_per_frame()
    # sparse frames, but never the profile start where the background is immature
    ranked = sorted(((counts.get(f[:-4], 0), i, f) for i, f in enumerate(files)
                     if i >= 6), key=lambda t: t[0])
    picked = ranked[:args.n]

    print(f"{len(files)} frames; picking the {args.n} sparsest (excluding the "
          f"first 6, where the background model is immature)\n")

    import torch
    sys.path.insert(0, "/opt/pisco/tools/pisco_segmenter_py")
    from pisco_segmenter.lucyd import LUCYD
    device = "cuda" if torch.cuda.is_available() else "cpu"
    model = LUCYD(num_res=1).to(device)
    model.load_state_dict(torch.load(MODEL, map_location=device))
    model.eval()
    print(f"LUCYD {os.path.basename(MODEL)} on {device}\n")

    imgs = [cv.imread(os.path.join(SRC, f), cv.IMREAD_GRAYSCALE) for f in files]
    imgs = [cv.resize(im, (2560, 2560)) for im in imgs]   # reader.py:97

    rows = []
    for n_obj, i, fn in picked:
        bg = background(imgs, i)
        off = cv.absdiff(imgs[i], bg)                       # thresholded when deconv is off

        cleaned = cv.bitwise_not(off)                       # what LUCYD is fed
        with torch.no_grad():
            x = torch.from_numpy(cleaned / 255).unsqueeze(0).unsqueeze(0).to(device)
            y = model(x.float())[0].detach().cpu().numpy()[0, 0]
        on = cv.bitwise_not((y * 255).astype(np.uint8))     # thresholded when deconv is on

        s_off, s_on = stats(off, "off"), stats(on, "on")
        eq = matched_threshold(on, s_off["frac_above"])
        rows.append((fn, n_obj, s_off, s_on, eq))

        print(f"{fn[:52]}  ({n_obj} objects in A1)")
        for s in (s_off, s_on):
            print(f"   deconv {s['label']:<3}  median {s['median']:5.1f}  "
                  f"sigma {s['sigma']:5.2f}  ->  threshold 10 = {s['sigmas']:5.1f} sigma, "
                  f"p{s['percentile']:.3f},  {s['frac_above'] * 100:6.3f}% of pixels")
        print(f"   to match the deconv-off pixel fraction, deconv-on needs "
              f"threshold {eq:.1f} (not 10)\n")

    print("=" * 78)
    m_off = np.mean([r[2]["sigmas"] for r in rows])
    m_on = np.mean([r[3]["sigmas"] for r in rows])
    m_eq = np.mean([r[4] for r in rows])
    r_frac = np.mean([r[3]["frac_above"] / r[2]["frac_above"] for r in rows])
    print(f"threshold 10 is {m_off:.1f} sigma with deconvolution off, "
          f"{m_on:.1f} sigma with it on")
    print(f"it flags {r_frac:.1f}x as many pixels with deconvolution on")
    print(f"matched operating point: deconv-on threshold {m_eq:.0f} "
          f"corresponds to deconv-off threshold 10")


if __name__ == "__main__":
    main()
