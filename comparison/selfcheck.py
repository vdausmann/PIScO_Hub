"""Two loose ends after aligning the background algorithm.

1. Raw object counts in the two C++ runs (stack 20 vs 12) - the harness reported
   the same filtered count for both, which wants checking.

2. The C++ stack *includes the frame being corrected*
   (`imageStack[s : s+n]`, no exclusion), while the python route explicitly skips
   it. A frame contributing to its own background partially subtracts its own
   objects, which would make the C++ find fewer and smaller objects - exactly
   what is left in the comparison. Measured here by running the same estimator
   both ways.
"""
import csv, glob, os, sys
import cv2 as cv
import h5py
import numpy as np

SRC = "/mnt/pisco/testdata/m202_a"
WF = "/mnt/pisco/hub-data/workflows"
B1 = f"{WF}/2eda51b7-e837-4f1c-a6eb-147298e2e8b4/0_pisco_segment/results/result.h5"
B1X = f"{WF}/777fa271-b687-4d93-ba66-1abe7e368d7b/0_pisco_segment/results/result.h5"
THRESHOLD, MIN_AREA = 10, 50


def h5_count(p):
    with h5py.File(p, "r") as f:
        return sum(len(f[k]["offsets"]) for k in f.keys() if "offsets" in f[k])


def sorted_frames():
    files = os.listdir(SRC)
    try:
        files.sort(key=lambda x: float(os.path.basename(x).split("_")[0].split("-")[1]))
    except Exception:
        files.sort()
    return [f for f in files if f.endswith(".png")]


def maxmin(stack):
    bg = np.minimum(stack[0], stack[1])
    for i in range(1, len(stack) - 1):
        bg = np.maximum(bg, np.minimum(stack[i], stack[i + 1]))
    return bg


def detect(corrected):
    b = (corrected > THRESHOLD).astype(np.uint8)
    n, lab, stats, _ = cv.connectedComponentsWithStats(b, connectivity=8)
    a = stats[1:, cv.CC_STAT_AREA]
    keep = a[a > MIN_AREA]
    return len(keep), float(np.median(keep)) if len(keep) else 0.0


print("raw objects in result.h5 (no filtering):")
print(f"   B1  imageStackSize=20   {h5_count(B1):>6}")
print(f"   B1x imageStackSize=12   {h5_count(B1X):>6}")

files = sorted_frames()
imgs = [cv.resize(cv.imread(os.path.join(SRC, f), cv.IMREAD_GRAYSCALE), (2560, 2560))
        for f in files]
picked = list(range(12, 12 + 8))

n_in = n_ex = 0
a_in, a_ex = [], []
for i in picked:
    n = 12
    s = max(0, min(i - n // 2, len(imgs) - n))
    stack_in = imgs[s:s + n]                                   # the C++ way
    stack_ex = [imgs[j] for j in range(s, s + n) if j != i]     # the python way

    for stack, cnt, areas in ((stack_in, "in", a_in), (stack_ex, "ex", a_ex)):
        c, med = detect(cv.absdiff(imgs[i], maxmin(stack)))
        areas.append(med)
        if cnt == "in":
            n_in += c
        else:
            n_ex += c

print(f"\nsame estimator, 8 frames, stack 12 - does including the frame in its "
      f"own background matter?")
print(f"   includes the frame (C++)    {n_in:>5} objects,  median area "
      f"{np.median(a_in):>6.0f} px")
print(f"   excludes it (python)        {n_ex:>5} objects,  median area "
      f"{np.median(a_ex):>6.0f} px")
print(f"   ratio                       {n_in / max(n_ex, 1):>5.2f}x           "
      f"{np.median(a_in) / max(np.median(a_ex), 1):>10.2f}x")
