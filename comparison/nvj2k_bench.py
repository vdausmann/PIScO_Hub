"""GPU JPEG 2000 decode (nvJPEG2000 via nvImageCodec) against the CPU path
both pipelines use today (OpenCV / OpenJPEG). Same files, same frames, checked
for pixel identity so the comparison is honest.
"""
import glob, time
import numpy as np
from nvidia import nvimgcodec

FILES = sorted(glob.glob("/mnt/pisco/compare/formats/*_JPEG_lossless.jp2"))
PNGS = sorted(glob.glob("/mnt/pisco/compare/formats/*_PNG.png"))
assert FILES, "run formats.py first"

dec = nvimgcodec.Decoder()
params = nvimgcodec.DecodeParams(color_spec=nvimgcodec.ColorSpec.GRAY)

# warm up: first decode pays for context creation and kernel load
_ = dec.read(FILES[0], params=params).cpu()

# ---- one at a time, wall clock including the transfer back to host
t = time.perf_counter()
outs = [np.asarray(dec.read(f, params=params).cpu()) for f in FILES]
per = (time.perf_counter() - t) / len(FILES) * 1000

# ---- batched, the way a pipeline would actually feed it
datas = [open(f, "rb").read() for f in FILES]
t = time.perf_counter()
imgs = dec.decode(datas, params=params)
host = [np.asarray(i.cpu()) for i in imgs]
per_batch = (time.perf_counter() - t) / len(FILES) * 1000

# ---- pixel identity against the PNG originals
import cv2 as cv
ok = all(np.array_equal(np.squeeze(o), cv.imread(p, cv.IMREAD_GRAYSCALE))
         for o, p in zip(outs, PNGS))

print(f"{len(FILES)} lossless JPEG 2000 frames, {np.squeeze(outs[0]).shape}, "
      f"{np.squeeze(outs[0]).dtype}")
print(f"  nvJPEG2000, one at a time : {per:7.1f} ms/frame")
print(f"  nvJPEG2000, batched       : {per_batch:7.1f} ms/frame")
print(f"  OpenCV/OpenJPEG on CPU    :   418   ms/frame   (measured earlier)")
print(f"  pixel-identical to PNG    : {ok}")
