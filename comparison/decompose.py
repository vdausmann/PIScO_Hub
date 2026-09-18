"""Why do the two routes disagree on undeconvolved detections?

Under the counting directive, particle counts come from the background-corrected
image with no deconvolution, and objects above 50 px should match between the
routes. They do not (19%/34% at IoU 0.5). Everything else has been ruled out or
harmonised, so this holds each remaining difference constant in turn and
measures what it is worth:

  background estimator   python  = pixel-wise max over a centred window of 6,
                                   excluding self          (bg_correction.py)
                         cpp     = max of pairwise minima over a stack of 20
                                   (background_correction.cpp)
                         train   = max over 12 neighbouring frames, the
                                   convention the LUCYD bg_pool was built with
  morphology             cpp applies a 3x3 MORPH_CLOSE before contours

Detection is otherwise identical everywhere here - fixed threshold, area > 50,
external contours - so any disagreement reported is attributable to the one
thing that changed.
"""
import argparse, csv, glob, os, sys
import cv2 as cv
import numpy as np

SRC = "/mnt/pisco/testdata/m202_a"
A1_DATA = ("/mnt/pisco/hub-data/workflows/"
           "e9b29a80-9ef9-44cb-9e0f-dc2e436911b5/0_pisco_segment_py/results/Data")
THRESHOLD, MIN_AREA, IOU = 10, 50, 0.5


def sorted_frames():
    files = os.listdir(SRC)
    try:
        files.sort(key=lambda x: float(os.path.basename(x).split("_")[0].split("-")[1]))
    except Exception:
        files.sort()
    return [f for f in files if f.endswith(".png")]


def bg_python(imgs, i, n=6):
    half = n // 2
    s, e = i - half, i + half + 1
    if s < 0:
        e += -s; s = 0
    if e > len(imgs):
        s = max(0, s - (e - len(imgs))); e = len(imgs)
    return np.max([imgs[j] for j in range(s, e) if j != i], axis=0)


def bg_cpp(imgs, i, n=20):
    """minMaxBackgroundComputation: max over min(stack[k], stack[k+1])."""
    s = max(0, min(i - n // 2, len(imgs) - n))
    stack = imgs[s:s + n]
    out = np.minimum(stack[0], stack[1])
    for k in range(1, len(stack) - 1):
        out = np.maximum(out, np.minimum(stack[k], stack[k + 1]))
    return out


def detect(corrected, close=0):
    binary = (corrected > THRESHOLD).astype(np.uint8)
    if close:
        ker = cv.getStructuringElement(cv.MORPH_RECT, (close, close))
        binary = cv.morphologyEx(binary, cv.MORPH_CLOSE, ker)
    n, lab, stats, _ = cv.connectedComponentsWithStats(binary, connectivity=8)
    out = []
    for j in range(1, n):
        a = stats[j, cv.CC_STAT_AREA]
        if a <= MIN_AREA:
            continue
        out.append((stats[j, cv.CC_STAT_LEFT], stats[j, cv.CC_STAT_TOP],
                    stats[j, cv.CC_STAT_WIDTH], stats[j, cv.CC_STAT_HEIGHT], a))
    return out


def match(a, b):
    """Greedy one-to-one by IoU; sparse enough that greedy equals optimal here."""
    if not a or not b:
        return 0
    A = np.array([[x, y, x + w, y + h] for x, y, w, h, _ in a], float)
    B = np.array([[x, y, x + w, y + h] for x, y, w, h, _ in b], float)
    ix0 = np.maximum(A[:, None, 0], B[None, :, 0])
    iy0 = np.maximum(A[:, None, 1], B[None, :, 1])
    ix1 = np.minimum(A[:, None, 2], B[None, :, 2])
    iy1 = np.minimum(A[:, None, 3], B[None, :, 3])
    inter = np.clip(ix1 - ix0, 0, None) * np.clip(iy1 - iy0, 0, None)
    aa = ((A[:, 2] - A[:, 0]) * (A[:, 3] - A[:, 1]))[:, None]
    ab = ((B[:, 2] - B[:, 0]) * (B[:, 3] - B[:, 1]))[None, :]
    M = np.where(aa + ab - inter > 0, inter / (aa + ab - inter), 0.0)
    used_a, used_b, n = set(), set(), 0
    for i, j in np.dstack(np.unravel_index(np.argsort(-M, axis=None), M.shape))[0]:
        if M[i, j] < IOU:
            break
        if i in used_a or j in used_b:
            continue
        used_a.add(i); used_b.add(j); n += 1
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-n", type=int, default=8)
    args = ap.parse_args()

    files = sorted_frames()
    counts = {}
    for p in glob.glob(f"{A1_DATA}/*.csv"):
        counts[os.path.basename(p)[:-4]] = sum(
            1 for row in csv.reader(open(p))
            if len(row) >= 12 and row[0] != "corrupt" and row[11] == "1")
    ranked = sorted(((counts.get(f[:-4], 0), i, f) for i, f in enumerate(files)
                     if 10 <= i < len(files) - 10), key=lambda t: t[0])[:args.n]

    imgs = [cv.resize(cv.imread(os.path.join(SRC, f), cv.IMREAD_GRAYSCALE),
                      (2560, 2560)) for f in files]

    tot = dict(py=0, cp=0, tr=0, m_py_cp=0, m_py_tr=0, m_close=0, py_close=0)
    areas = []

    for _, i, fn in ranked:
        c_py = cv.absdiff(imgs[i], bg_python(imgs, i, 6))
        c_cp = cv.absdiff(imgs[i], bg_cpp(imgs, i, 20))
        c_tr = cv.absdiff(imgs[i], bg_python(imgs, i, 12))

        d_py, d_cp, d_tr = detect(c_py), detect(c_cp), detect(c_tr)
        d_py_close = detect(c_py, close=3)

        tot["py"] += len(d_py); tot["cp"] += len(d_cp); tot["tr"] += len(d_tr)
        tot["py_close"] += len(d_py_close)
        tot["m_py_cp"] += match(d_py, d_cp)
        tot["m_py_tr"] += match(d_py, d_tr)
        tot["m_close"] += match(d_py, d_py_close)

        # area agreement on the same objects, background estimator alone
        if d_py and d_cp:
            areas.append((np.median([a for *_, a in d_py]),
                          np.median([a for *_, a in d_cp])))

    print(f"{args.n} sparse frames, undeconvolved, threshold {THRESHOLD}, "
          f"area > {MIN_AREA} px, IoU >= {IOU}\n")
    print("objects found, identical detection, only the background estimator differs:")
    print(f"   python  max over centred 6      {tot['py']:>6}")
    print(f"   cpp     maxmin over 20          {tot['cp']:>6}"
          f"   ({tot['cp'] / max(tot['py'], 1):.2f}x python)")
    print(f"   train   max over 12             {tot['tr']:>6}"
          f"   ({tot['tr'] / max(tot['py'], 1):.2f}x python)")
    print()
    print("how many of those are the same object:")
    print(f"   python vs cpp background        {tot['m_py_cp']:>6}"
          f"   {tot['m_py_cp'] / max(tot['py'], 1):6.1%} of python,"
          f" {tot['m_py_cp'] / max(tot['cp'], 1):6.1%} of cpp")
    print(f"   python vs train background      {tot['m_py_tr']:>6}"
          f"   {tot['m_py_tr'] / max(tot['py'], 1):6.1%} of python,"
          f" {tot['m_py_tr'] / max(tot['tr'], 1):6.1%} of train")
    print()
    print("morphology alone, same corrected image:")
    print(f"   python + 3x3 MORPH_CLOSE        {tot['py_close']:>6}"
          f"   ({tot['py_close'] / max(tot['py'], 1):.2f}x), "
          f"matched {tot['m_close'] / max(tot['py'], 1):.1%}")
    if areas:
        r = np.array([b / a for a, b in areas])
        print(f"\nmedian object area, cpp background / python background: "
              f"{np.median(r):.3f}")


if __name__ == "__main__":
    main()
