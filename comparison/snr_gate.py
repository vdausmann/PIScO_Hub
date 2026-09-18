"""Are the deconvolution-only objects real, by the project's own deploy gate?

Applies `local_snr` from underwater-lucyd/sim/real_vs_hallucinated.py verbatim:
contrast of the darkest object pixels against the surrounding background noise,
measured on the CLEANED input, which is identical with and without
deconvolution and therefore model-independent.

  real object    -> a genuine dark dip, SNR well above the control floor
  hallucination  -> flat noise, SNR indistinguishable from random control patches

Three populations, all measured on the same cleaned frames:
  kept     objects found with and without deconvolution - the real reference
  new      found only with deconvolution, below threshold without it
  control  random patches of the same size distribution - the noise floor
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
RNG = np.random.default_rng(0)


def local_snr(cleaned, x, y, w, h):
    """Verbatim from underwater-lucyd/sim/real_vs_hallucinated.py:52."""
    H, W = cleaned.shape
    m = 12
    Y0, Y1 = max(0, y - m), min(H, y + h + m)
    X0, X1 = max(0, x - m), min(W, x + w + m)
    patch = cleaned[Y0:Y1, X0:X1].astype(np.float32)
    inner = cleaned[y:y + h, x:x + w].astype(np.float32)
    ring = patch.copy()
    ry, rx = y - Y0, x - X0
    ring[ry:ry + h, rx:rx + w] = np.nan
    bg = np.nanmedian(ring); noise = np.nanstd(ring)
    core = np.percentile(inner, 5)
    contrast = bg - core
    return contrast, (contrast / noise if noise > 1e-3 else 0.0)


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


def comps(corrected):
    binary = (corrected > THRESHOLD).astype(np.uint8)
    n, lab, stats, cents = cv.connectedComponentsWithStats(binary, connectivity=8)
    return lab, stats, cents


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

    pops = {"kept": [], "split": [], "promoted": [], "new": [], "control": []}

    for _, i, fn in ranked:
        bg = background(imgs, i)
        off = cv.absdiff(imgs[i], bg)
        cleaned = cv.bitwise_not(off)          # the model-independent input
        with torch.no_grad():
            x = torch.from_numpy(cleaned / 255).unsqueeze(0).unsqueeze(0).to(device)
            y = model(x.float())[0].detach().cpu().numpy()[0, 0]
        on = cv.bitwise_not(np.clip(y * 255, 0, 255).astype(np.uint8))

        off_lab, off_stats, _ = comps(off)
        on_lab, on_stats, on_cents = comps(on)
        big_off = {j for j in range(1, len(off_stats))
                   if off_stats[j, cv.CC_STAT_AREA] > MIN_AREA}

        sizes = []
        for j in range(1, len(on_stats)):
            if on_stats[j, cv.CC_STAT_AREA] <= MIN_AREA:
                continue
            bx, by, bw, bh = (on_stats[j, cv.CC_STAT_LEFT], on_stats[j, cv.CC_STAT_TOP],
                              on_stats[j, cv.CC_STAT_WIDTH], on_stats[j, cv.CC_STAT_HEIGHT])
            cx, cy = int(round(on_cents[j][0])), int(round(on_cents[j][1]))
            cx = min(max(cx, 0), 2559); cy = min(max(cy, 0), 2559)
            parent = int(off_lab[cy, cx])
            cls = ("kept" if parent in big_off else
                   "promoted" if parent != 0 else "new")
            _, snr = local_snr(cleaned, bx, by, bw, bh)
            pops[cls].append(snr)
            sizes.append((bw, bh))

        # controls: random patches with the same size distribution
        for bw, bh in sizes:
            rx = int(RNG.integers(0, 2560 - bw)); ry = int(RNG.integers(0, 2560 - bh))
            _, snr = local_snr(cleaned, rx, ry, bw, bh)
            pops["control"].append(snr)

    print(f"{n_frames} sparse frames, SNR on the cleaned input "
          f"(underwater-lucyd/sim/real_vs_hallucinated.py)\n")
    print(f"{'population':>10} {'n':>6} {'median':>8} {'p25':>8} {'p75':>8} "
          f"{'> control p95':>14}")
    print("-" * 60)
    ctrl = np.array(pops["control"])
    ctrl_p95 = float(np.percentile(ctrl, 95)) if len(ctrl) else 0.0
    for k in ("kept", "promoted", "new", "control"):
        v = np.array(pops[k])
        if not len(v):
            continue
        frac = float((v > ctrl_p95).mean())
        print(f"{k:>10} {len(v):>6} {np.median(v):>8.2f} "
              f"{np.percentile(v, 25):>8.2f} {np.percentile(v, 75):>8.2f} "
              f"{frac:>13.1%}")
    print(f"\ncontrol p95 = {ctrl_p95:.2f} - the noise floor a detection must clear")


if __name__ == "__main__":
    main()
