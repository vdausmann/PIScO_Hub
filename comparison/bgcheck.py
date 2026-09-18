"""Is the background now pixel-identical between the routes?

Reconstructs the corrected frame the way both routes should now compute it -
max of pairwise minima over the 12-frame block the frame falls in, frame
included - and compares it against the pixels the C++ actually stored in
result.h5 at each object's bounding box. If they match, whatever disagreement
remains is detection, not background.
"""
import glob, os
import cv2 as cv
import h5py
import numpy as np

SRC = "/mnt/pisco/testdata/m202_a"
H5 = ("/mnt/pisco/hub-data/workflows/634e7722-2be8-4af5-9f50-3218e3be14f2/"
      "0_pisco_segment/results/result.h5")
N = 12


def sorted_frames():
    files = os.listdir(SRC)
    try:
        files.sort(key=lambda x: float(os.path.basename(x).split("_")[0].split("-")[1]))
    except Exception:
        files.sort()
    return [f for f in files if f.endswith(".png")]


def block_bounds(i, n, total):
    s = (i // n) * n; e = s + n
    if e > total:
        s = max(0, s - n); e = total
    elif total - e < n:
        e = total
    return s, e


def maxmin(stack):
    bg = np.minimum(stack[0], stack[1])
    for k in range(1, len(stack) - 1):
        bg = np.maximum(bg, np.minimum(stack[k], stack[k + 1]))
    return bg


files = sorted_frames()
imgs = [cv.imread(os.path.join(SRC, f), cv.IMREAD_GRAYSCALE) for f in files]

# C++ sorts by name; python sorts by the time key - check they agree first
cpp_order = sorted(files)
print("frame order identical (name sort vs time-key sort):", cpp_order == files)

with h5py.File(H5, "r") as f:
    groups = list(f.keys())
    print(f"{len(groups)} groups in result.h5\n")
    checked = same = 0
    worst = 0
    per_frame = {}
    for gname in groups:
        i = files.index(gname + ".png")
        s, e = block_bounds(i, N, len(files))
        corrected = cv.absdiff(imgs[i], maxmin(imgs[s:e]))

        g = f[gname]
        if "offsets" not in g:
            continue
        bx, by = np.array(g["boundingBoxX"]), np.array(g["boundingBoxY"])
        bw, bh = np.array(g["boundingBoxW"]), np.array(g["boundingBoxH"])
        offs = np.array(g["offsets"]); crop = np.array(g["cropPixelData"])
        diffs = []
        for k in range(min(len(offs), 40)):
            n = int(bw[k]) * int(bh[k])
            c = crop[offs[k]:offs[k] + n]
            if c.size != n:
                continue
            c = c.reshape(int(bh[k]), int(bw[k]))
            mine = corrected[by[k]:by[k] + bh[k], bx[k]:bx[k] + bw[k]]
            if mine.shape != c.shape:
                continue
            d = int(np.abs(mine.astype(int) - c.astype(int)).max())
            diffs.append(d); checked += 1; same += (d == 0); worst = max(worst, d)
        per_frame[i] = (len(diffs), sum(1 for d in diffs if d == 0),
                        int(np.median(diffs)) if diffs else -1, s, e)

for i in sorted(per_frame):
    n, ok, med, s, e = per_frame[i]
    flag = "" if ok == n else "   <-- differs"
    print(f"  frame {i:>2}  block [{s:>2},{e:>2})  crops checked {n:>3}  "
          f"identical {ok:>3}  median max-diff {med:>3}{flag}")
print(f"\n{same}/{checked} crops pixel-identical to the reconstruction; "
      f"worst max-diff {worst}")
