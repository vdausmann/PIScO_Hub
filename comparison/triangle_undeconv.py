"""Isolate the TRIANGLE re-threshold: recompute each C++ mask at a fixed 10
and compare the area with the mask the tool actually stored."""
import cv2, h5py, numpy as np

p = "/mnt/pisco/hub-data/workflows/634e7722-2be8-4af5-9f50-3218e3be14f2/0_pisco_segment/results/result.h5"
def area(binary):
    c, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    return max((cv2.contourArea(x) for x in c), default=float(binary.sum()))

ratios, tri_thr = [], []
with h5py.File(p, "r") as f:
    for gi, name in enumerate(sorted(f.keys())):
        g = f[name]
        if "offsets" not in g: continue
        bw = np.array(g["boundingBoxW"]); bh = np.array(g["boundingBoxH"])
        offs = np.array(g["offsets"]); crop = np.array(g["cropPixelData"]); mask = np.array(g["maskPixelData"])
        for i in range(len(offs)):
            n = int(bw[i]) * int(bh[i])
            c = crop[offs[i]:offs[i]+n]
            m = mask[offs[i]:offs[i]+n]
            if c.size != n or m.size != n: continue
            c = c.reshape(int(bh[i]), int(bw[i])); m = m.reshape(int(bh[i]), int(bw[i]))
            a_stored = area((m > 0).astype(np.uint8))
            a_fixed = area((c > 10).astype(np.uint8))
            t, _ = cv2.threshold(c, 0, 255, cv2.THRESH_TRIANGLE)
            tri_thr.append(t)
            if a_fixed > 0:
                ratios.append(a_stored / a_fixed)
        pass

r = np.array(ratios); t = np.array(tri_thr)
print(f"n objects            : {len(r)}")
print(f"TRIANGLE threshold   : median {np.median(t):.0f}   p10 {np.percentile(t,10):.0f}   p90 {np.percentile(t,90):.0f}   (floor is 10)")
print(f"above the floor      : {(t > 10).mean():.1%} of objects")
print(f"stored / fixed-10 area: median {np.median(r):.3f}  p10 {np.percentile(r,10):.3f}  p90 {np.percentile(r,90):.3f}")
