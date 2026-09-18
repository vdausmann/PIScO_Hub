"""Draw both routes' contours on the same frames, from their real outputs.

Python (A1y) contours come from its Masks/ PNGs placed at the CSV bounding
boxes; C++ (B1z) contours from maskPixelData in result.h5. Both routes were
run on a pixel-identical corrected image, so any difference in outline is
detection, and in particular the pass-2 TRIANGLE re-threshold.

Writes:
  frame_<i>_overview.png   whole frame, both contour sets, downscaled
  frame_<i>_zoom.png       a busy region at full resolution
  triangle_examples.png    matched pairs whose C++ mask is much smaller,
                           with the per-crop TRIANGLE threshold annotated
"""
import csv, os
import cv2 as cv
import h5py
import numpy as np

SRC = "/mnt/pisco/testdata/m202_a"
PY = "/mnt/pisco/hub-data/workflows/08198ba2-40f1-4f00-a3d3-f31bf756befe/0_pisco_segment_py/results"
CPP = "/mnt/pisco/hub-data/workflows/634e7722-2be8-4af5-9f50-3218e3be14f2/0_pisco_segment/results/result.h5"
MATCHES = "/mnt/pisco/compare/A1y_B1z/matches.csv"
OUT = "/mnt/pisco/compare/contours"
os.makedirs(OUT, exist_ok=True)

BLUE, ORANGE = (214, 120, 42), (52, 104, 235)     # BGR: legacy python, C++
N_BLOCK, THR = 12, 10


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


def py_objects(frame):
    """(idx, x, y, w, h, area, mask) for every saved object, from CSV + Masks/."""
    out = []
    for row in csv.reader(open(f"{PY}/Data/{frame}.csv")):
        if len(row) < 12 or row[0] == "corrupt" or row[11] != "1":
            continue
        idx = int(float(row[0]))
        x, y, w, h = (int(float(row[7])), int(float(row[8])),
                      int(float(row[9])), int(float(row[10])))
        m = cv.imread(f"{PY}/Masks/{frame}_{idx}.png", cv.IMREAD_GRAYSCALE)
        if m is None:
            continue
        out.append((idx, x, y, w, h, float(row[6]), m))
    return out


def cpp_objects(frame):
    out = []
    with h5py.File(CPP, "r") as f:
        g = f[frame]
        bx, by = np.array(g["boundingBoxX"]), np.array(g["boundingBoxY"])
        bw, bh = np.array(g["boundingBoxW"]), np.array(g["boundingBoxH"])
        offs = np.array(g["offsets"])
        masks, crops = np.array(g["maskPixelData"]), np.array(g["cropPixelData"])
        for k in range(len(offs)):
            n = int(bw[k]) * int(bh[k])
            m = masks[offs[k]:offs[k] + n]; c = crops[offs[k]:offs[k] + n]
            if m.size != n:
                continue
            m = m.reshape(int(bh[k]), int(bw[k])); c = c.reshape(int(bh[k]), int(bw[k]))
            cnts, _ = cv.findContours((m > 0).astype(np.uint8), cv.RETR_EXTERNAL,
                                      cv.CHAIN_APPROX_SIMPLE)
            area = max((cv.contourArea(x) for x in cnts), default=0.0)
            tri, _ = cv.threshold(c, 0, 255, cv.THRESH_TRIANGLE)
            out.append((k, int(bx[k]), int(by[k]), int(bw[k]), int(bh[k]), area, m, max(tri, THR)))
    return out


def draw(canvas, objs, colour, thickness):
    for o in objs:
        _, x, y, w, h, *_ = o
        m = o[6]
        cnts, _ = cv.findContours((m > 0).astype(np.uint8), cv.RETR_EXTERNAL,
                                  cv.CHAIN_APPROX_NONE)
        cv.drawContours(canvas, [c + [x, y] for c in cnts], -1, colour, thickness)


def legend(img, scale=1.0):
    s = int(22 * scale)
    cv.rectangle(img, (10, 10), (int(330 * scale), int(70 * scale)), (255, 255, 255), -1)
    cv.rectangle(img, (18, 18), (18 + s, 18 + s), BLUE, -1)
    cv.putText(img, "legacy python (A1y)", (18 + s + 10, 18 + s - 4),
               cv.FONT_HERSHEY_SIMPLEX, 0.6 * scale, (20, 20, 20), 1, cv.LINE_AA)
    cv.rectangle(img, (18, 22 + s), (18 + s, 22 + 2 * s), ORANGE, -1)
    cv.putText(img, "C++ (B1z)", (18 + s + 10, 22 + 2 * s - 4),
               cv.FONT_HERSHEY_SIMPLEX, 0.6 * scale, (20, 20, 20), 1, cv.LINE_AA)


files = sorted_frames()
imgs = [cv.imread(os.path.join(SRC, f), cv.IMREAD_GRAYSCALE) for f in files]

# ---------------- full frames + zoom, two settled frames
for i in ():
    frame = files[i][:-4]
    s, e = block_bounds(i, N_BLOCK, len(files))
    cleaned = cv.bitwise_not(cv.absdiff(imgs[i], maxmin(imgs[s:e])))
    base = cv.cvtColor(cleaned, cv.COLOR_GRAY2BGR)

    py, cp = py_objects(frame), cpp_objects(frame)
    full = base.copy()
    draw(full, py, BLUE, 3)
    draw(full, cp, ORANGE, 2)
    small = cv.resize(full, (1400, 1400), interpolation=cv.INTER_AREA)
    legend(small)
    cv.putText(small, f"frame {i}  python {len(py)} objects   C++ {len(cp)} objects",
               (10, 1385), cv.FONT_HERSHEY_SIMPLEX, 0.6, (20, 20, 20), 1, cv.LINE_AA)
    cv.imwrite(f"{OUT}/frame_{i}_overview.png", small)

    # zoom on the region with the most python objects
    best, bx0, by0 = -1, 0, 0
    for yy in range(0, 2560 - 640, 160):
        for xx in range(0, 2560 - 640, 160):
            n = sum(1 for _, x, y, *_ in py if xx <= x < xx + 640 and yy <= y < yy + 640)
            if n > best:
                best, bx0, by0 = n, xx, yy
    zoom = base.copy()
    draw(zoom, py, BLUE, 2)
    draw(zoom, cp, ORANGE, 1)
    zoom = zoom[by0:by0 + 640, bx0:bx0 + 640]
    zoom = cv.resize(zoom, (1280, 1280), interpolation=cv.INTER_NEAREST)
    legend(zoom)
    cv.putText(zoom, f"frame {i}, region x={bx0} y={by0}, 640px at 2x",
               (10, 1265), cv.FONT_HERSHEY_SIMPLEX, 0.6, (20, 20, 20), 1, cv.LINE_AA)
    cv.imwrite(f"{OUT}/frame_{i}_zoom.png", zoom)
    print(f"frame {i}: python {len(py)}, C++ {len(cp)} objects")

# ---------------- crops where TRIANGLE shrank the mask
rows = [r for r in csv.DictReader(open(MATCHES)) if r["stratum"] == "matched"]
rows = [r for r in rows if 250 < float(r["old_area"]) < 2500 and
        float(r["new_area"]) / float(r["old_area"]) < 0.6]
rows.sort(key=lambda r: float(r["new_area"]) / float(r["old_area"]))   # worst first
picked = []
seen = set()
for r in rows:
    if r["frame"] in seen:
        continue
    seen.add(r["frame"]); picked.append(r)
    if len(picked) == 8:
        break

tiles = []
S, HALF = 4, 45
for r in picked:
    frame = r["frame"]; i = files.index(frame + ".png")
    s, e = block_bounds(i, N_BLOCK, len(files))
    cleaned = cv.bitwise_not(cv.absdiff(imgs[i], maxmin(imgs[s:e])))
    py = {o[0]: o for o in py_objects(frame)}
    cp = {o[0]: o for o in cpp_objects(frame)}
    po, co = py[int(float(r["old_idx"]))], cp[int(float(r["new_idx"]))]
    cx = po[1] + po[3] // 2; cy = po[2] + po[4] // 2
    half = max(po[3], po[4]) // 2 + 14                       # fit the object
    x0, y0 = max(0, cx - half), max(0, cy - half)
    x1, y1 = min(2560, cx + half), min(2560, cy + half)

    tile = cv.cvtColor(cleaned[y0:y1, x0:x1], cv.COLOR_GRAY2BGR)
    S = max(2, min(6, 260 // max(tile.shape[:2])))          # ~260px tiles
    tile = cv.resize(tile, (tile.shape[1] * S, tile.shape[0] * S), interpolation=cv.INTER_NEAREST)
    for o, col, t in ((po, BLUE, 2), (co, ORANGE, 2)):
        _, x, y, w, h, area, m, *rest = o
        cnts, _ = cv.findContours((m > 0).astype(np.uint8), cv.RETR_EXTERNAL, cv.CHAIN_APPROX_NONE)
        cnts = [(c + [x - x0, y - y0]) * S for c in cnts]
        cv.drawContours(tile, cnts, -1, col, t)
    tile = cv.copyMakeBorder(tile, 44, 0, 0, 0, cv.BORDER_CONSTANT, value=(255, 255, 255))
    cv.putText(tile, f"py  {po[5]:.0f} px  thr 10", (6, 16), cv.FONT_HERSHEY_SIMPLEX, .45, BLUE, 1, cv.LINE_AA)
    cv.putText(tile, f"C++ {co[5]:.0f} px  thr {co[7]:.0f}  ({co[5] / po[5]:.2f}x)", (6, 36), cv.FONT_HERSHEY_SIMPLEX, .45, ORANGE, 1, cv.LINE_AA)
    tiles.append(tile)

h = max(t.shape[0] for t in tiles); w = max(t.shape[1] for t in tiles)
tiles = [cv.copyMakeBorder(t, 0, h - t.shape[0], 0, w - t.shape[1], cv.BORDER_CONSTANT, value=(255, 255, 255)) for t in tiles]
grid = np.vstack([np.hstack(tiles[:4]), np.hstack(tiles[4:8])]) if len(tiles) >= 8 else np.hstack(tiles)
cv.imwrite(f"{OUT}/triangle_examples.png", grid)
print(f"wrote {len(tiles)} TRIANGLE examples, {grid.shape[1]}x{grid.shape[0]}")
