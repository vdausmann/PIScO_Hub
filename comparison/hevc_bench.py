"""Lossless HEVC round trip on the Tesla P4: NVENC encode, NVDEC decode.

Grayscale frames go in as NV12 with a flat 128 chroma plane, so the luma is
carried losslessly and the chroma costs almost nothing to code - and it stays
decodable by every NVDEC generation, which 4:4:4 / monochrome profiles do not.
Intra-only (gop=1): every frame is independently decodable, so the stream is
seekable and one corrupt frame cannot cascade into its neighbours.

Checks pixel identity against the PNG originals, and measures compression and
decode speed - the two numbers that decide whether this is worth the change to
the data model.
"""
import ctypes, glob, os, sys, time
import numpy as np
import cv2 as cv
import PyNvVideoCodec as nvc

SRC = "/mnt/pisco/testdata/m202_a"
OUT = "/mnt/pisco/compare/formats/m202_a_lossless.hevc"
N = int(sys.argv[1]) if len(sys.argv) > 1 else 20

files = sorted(glob.glob(f"{SRC}/*.png"))[:N]
frames = [cv.imread(f, cv.IMREAD_GRAYSCALE) for f in files]
H, W = frames[0].shape
png_bytes = sum(os.path.getsize(f) for f in files)


def to_nv12(gray):
    chroma = np.full((gray.shape[0] // 2, gray.shape[1]), 128, dtype=np.uint8)
    return np.vstack([gray, chroma])


def frame_to_numpy(fr):
    """Host-memory NV12 frame -> luma plane, via the plane pointer."""
    h, w = fr.shape[0], fr.shape[1]           # shape is (H*3/2, W) for NV12
    n = int(np.prod(fr.shape))
    ptr = fr.GetPtrToPlane(0)
    buf = ctypes.string_at(ptr, n)
    return np.frombuffer(buf, dtype=np.uint8).reshape(h, w)[:H, :W].copy()


# ---- encode
enc = nvc.CreateEncoder(W, H, "NV12", True, codec="hevc", preset="P7",
                        tuning_info="lossless", gop=1, bf=0)
packets = []
t = time.perf_counter()
for g in frames:
    packets += [p["data"] for p in enc.Encode(to_nv12(g))]
packets += [p["data"] for p in enc.EndEncode()]
enc_ms = (time.perf_counter() - t) / len(frames) * 1000
with open(OUT, "wb") as fh:
    for d in packets:
        fh.write(d)
hevc_bytes = os.path.getsize(OUT)

# ---- decode: packets straight into NVDEC, then flush with an empty packet
dec = nvc.CreateDecoder(gpuid=0, codec=nvc.cudaVideoCodec.HEVC,
                        cudacontext=0, cudastream=0, usedevicememory=False)
out = []
t = time.perf_counter()
keep = []
for d in packets + [b""]:
    buf = np.frombuffer(d, dtype=np.uint8) if d else None
    keep.append(buf)
    pd = nvc.PacketData()
    pd.bsl_data = buf.ctypes.data if d else 0
    pd.bsl = buf.size if d else 0
    for fr in dec.Decode(pd):
        out.append(frame_to_numpy(fr))
dec_ms = (time.perf_counter() - t) / max(len(out), 1) * 1000

# ---- identity
ok, maxdiff = len(out) == len(frames), 0
for a, g in zip(out, frames):
    d = int(np.abs(a.astype(int) - g.astype(int)).max())
    maxdiff = max(maxdiff, d); ok = ok and d == 0

print(f"{len(frames)} frames {W}x{H}, lossless HEVC, intra-only, NV12 flat chroma, "
      f"{len(packets)} packets")
print(f"  PNG total          {png_bytes / 1e6:8.1f} MB   ({png_bytes / len(frames) / 1e6:.2f} MB/frame)")
print(f"  HEVC total         {hevc_bytes / 1e6:8.1f} MB   ({hevc_bytes / len(frames) / 1e6:.2f} MB/frame)"
      f"   -> {png_bytes / hevc_bytes:.2f}x vs PNG")
print(f"  NVENC encode       {enc_ms:8.1f} ms/frame")
print(f"  NVDEC decode       {dec_ms:8.1f} ms/frame   ({len(out)} frames decoded, "
      f"incl. copy to numpy)")
print(f"  pixel-identical    {ok}   (max luma diff {maxdiff})")
