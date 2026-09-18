"""Side-by-side patches around 'new' objects: raw frame, deconv off, deconv on.

This is the instrument the hand-validation needs - a 'new' object is judged on
what is visible in the raw frame at that position, never on either route's crop.
"""
import csv, os, sys
import cv2 as cv
import numpy as np

SRC = "/mnt/pisco/testdata/m202_a"
MODEL = ("/opt/pisco/tools/pisco_segmenter_py/pisco_segmenter/models/"
         "best_ssim_v5a_noGrad_ablation.pth")
REG = "/mnt/pisco/compare/registry/registry.csv"
OUT = "/mnt/pisco/compare/registry/patches.png"
BG_SIZE, THRESHOLD, HALF, SCALE = 6, 10, 60, 2


def sorted_frames():
    files = os.listdir(SRC)
    try:
        files.sort(key=lambda x: float(os.path.basename(x).split("_")[0].split("-")[1]))
    except Exception:
        files.sort()
    return [f for f in files if f.endswith(".png")]


def main():
    cls_want = sys.argv[1] if len(sys.argv) > 1 else "new"
    rows = [r for r in csv.DictReader(open(REG)) if r["cls"] == cls_want]
    rows.sort(key=lambda r: -float(r["area"]))
    frame = rows[0]["frame"]
    rows = [r for r in rows if r["frame"] == frame][:6]

    files = sorted_frames()
    idx = files.index(frame + ".png")
    imgs = [cv.resize(cv.imread(os.path.join(SRC, f), cv.IMREAD_GRAYSCALE),
                      (2560, 2560)) for f in files]

    half = BG_SIZE // 2
    s, e = max(0, idx - half), min(len(imgs), idx + half + 1)
    bg = np.max([imgs[j] for j in range(s, e) if j != idx], axis=0)
    off = cv.absdiff(imgs[idx], bg)

    import torch
    sys.path.insert(0, "/opt/pisco/tools/pisco_segmenter_py")
    from pisco_segmenter.lucyd import LUCYD
    device = "cuda" if torch.cuda.is_available() else "cpu"
    model = LUCYD(num_res=1).to(device); model.eval()
    model.load_state_dict(torch.load(MODEL, map_location=device))
    with torch.no_grad():
        x = torch.from_numpy(cv.bitwise_not(off) / 255).unsqueeze(0).unsqueeze(0).to(device)
        y = model(x.float())[0].detach().cpu().numpy()[0, 0]
    on = cv.bitwise_not((y * 255).astype(np.uint8))

    tiles = []
    for r in rows:
        cx, cy = int(float(r["cx"])), int(float(r["cy"]))
        x0, y0 = max(0, cx - HALF), max(0, cy - HALF)
        x1, y1 = min(2560, cx + HALF), min(2560, cy + HALF)
        col = []
        for src, label in ((imgs[idx], "raw"), (255 - off, "bg-corrected"),
                           (on, "deconvolved")):
            p = src[y0:y1, x0:x1]
            p = cv.resize(p, (p.shape[1] * SCALE, p.shape[0] * SCALE),
                          interpolation=cv.INTER_NEAREST)
            p = cv.cvtColor(p, cv.COLOR_GRAY2BGR)
            cv.putText(p, label, (6, 16), cv.FONT_HERSHEY_SIMPLEX, .45,
                       (60, 60, 200), 1, cv.LINE_AA)
            col.append(cv.copyMakeBorder(p, 1, 1, 1, 1, cv.BORDER_CONSTANT,
                                         value=(180, 180, 180)))
        # mark the reported centre on the deconvolved panel only
        cv.drawMarker(col[2], (HALF * SCALE, HALF * SCALE), (72, 73, 227),
                      cv.MARKER_CROSS, 18, 1)
        tiles.append(np.vstack(col))

    h = min(t.shape[0] for t in tiles)
    grid = np.hstack([t[:h] for t in tiles])
    cv.imwrite(OUT, grid)
    print(f"{cls_want}: {len(rows)} patches from {frame[:40]}")
    for r in rows:
        print(f"   ({r['cx']}, {r['cy']})  area {float(r['area']):.0f} px")
    print(f"wrote {OUT}  {grid.shape[1]}x{grid.shape[0]}")


if __name__ == "__main__":
    main()
