"""Can the pipelines' readers actually decode BMP, JPEG 2000, TIFF, and a raw
sensor dump? Round-trips real frames through each format and reads them back
the way both pipelines do (cv.imread, IMREAD_GRAYSCALE), reporting whether the
pixels survive and how long a decode takes - JPEG 2000 on the CPU is the one
that could dominate a run.
"""
import glob, os, time
import cv2 as cv
import numpy as np

SRC = "/mnt/pisco/testdata/m202_a"
OUT = "/mnt/pisco/compare/formats"
os.makedirs(OUT, exist_ok=True)

frames = sorted(glob.glob(f"{SRC}/*.png"))[10:13]
print(f"{len(frames)} frames, {cv.imread(frames[0], cv.IMREAD_GRAYSCALE).shape}\n")
print(f"{'format':<22} {'encoded':>9} {'decode':>8} {'identical':>10}  note")
print("-" * 70)

for fmt, ext, params in [
    ("PNG (reference)", ".png", []),
    ("BMP", ".bmp", []),
    ("TIFF", ".tif", []),
    ("JPEG 2000 lossless", ".jp2", [cv.IMWRITE_JPEG2000_COMPRESSION_X1000, 1000]),
    ("JPEG 2000 ~20:1", ".jp2", [cv.IMWRITE_JPEG2000_COMPRESSION_X1000, 50]),
    ("JPEG q95", ".jpg", [cv.IMWRITE_JPEG_QUALITY, 95]),
]:
    sizes, times, ident, maxdiff = [], [], True, 0
    for f in frames:
        ref = cv.imread(f, cv.IMREAD_GRAYSCALE)
        p = f"{OUT}/{os.path.basename(f)[:-4]}_{fmt.split()[0]}{'_' + fmt.split()[-1] if len(fmt.split()) > 2 else ''}{ext}"
        ok = cv.imwrite(p, ref, params)
        if not ok:
            print(f"{fmt:<22} {'ENCODE FAILED':>9}")
            break
        sizes.append(os.path.getsize(p))
        t = time.perf_counter()
        back = cv.imread(p, cv.IMREAD_GRAYSCALE)      # exactly what both pipelines call
        times.append(time.perf_counter() - t)
        if back is None:
            ident = False; continue
        d = int(np.abs(back.astype(int) - ref.astype(int)).max())
        maxdiff = max(maxdiff, d)
        ident = ident and d == 0
    else:
        note = "" if ident else f"max pixel diff {maxdiff}"
        print(f"{fmt:<22} {np.mean(sizes) / 1e6:>7.2f}MB {np.mean(times) * 1000:>6.0f}ms "
              f"{'yes' if ident else 'no':>10}  {note}")

# a headerless sensor dump: imread cannot open it, but numpy can if the
# geometry and bit depth are known - which is what a camera profile is for
ref = cv.imread(frames[0], cv.IMREAD_GRAYSCALE)
p = f"{OUT}/frame.raw"
ref.tofile(p)
t = time.perf_counter()
via_imread = cv.imread(p, cv.IMREAD_GRAYSCALE)
via_numpy = np.fromfile(p, dtype=np.uint8).reshape(ref.shape)
dt = (time.perf_counter() - t) * 1000
print(f"{'raw 8-bit dump':<22} {os.path.getsize(p) / 1e6:>7.2f}MB {dt:>6.0f}ms "
      f"{'no (imread=None)' if via_imread is None else 'yes':>10}  "
      f"numpy.fromfile identical: {np.array_equal(via_numpy, ref)}")

# 16-bit input: both pipelines force IMREAD_GRAYSCALE, which is 8-bit
ref16 = (ref.astype(np.uint16) * 16)              # 12-bit data in a 16-bit container
p = f"{OUT}/frame16.png"
cv.imwrite(p, ref16)
back = cv.imread(p, cv.IMREAD_GRAYSCALE)
print(f"\n16-bit PNG through IMREAD_GRAYSCALE: dtype {back.dtype}, max {back.max()} "
      f"(source max {ref16.max()}) -> silently rescaled to 8-bit")
