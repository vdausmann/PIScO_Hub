#!/usr/bin/env python3
"""Walk a zip whose central directory is missing (truncated copy) entry by
entry from the start, inflate each, verify its CRC, and report (or extract)
every complete entry.

    zip_salvage.py ZIP [--extract DIR] [--list FILE]
"""
import argparse, os, struct, sys, time, zlib

LOCAL = 0x04034B50
DESC = 0x08074B50
CHUNK = 1 << 20


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("zip")
    ap.add_argument("--extract", help="write complete entries here (flat: basename only)")
    ap.add_argument("--list", help="write 'name size crc' of complete entries here")
    ap.add_argument("--only", help="file of image basenames without extension: extract only these images (non-image entries always)")
    a = ap.parse_args()
    only = set(l.strip() for l in open(a.only)) if a.only else None
    f = open(a.zip, "rb", buffering=8 * CHUNK)
    total = os.path.getsize(a.zip)
    lst = open(a.list, "w") if a.list else None
    n_ok = n_bad = 0
    by_ext = {}
    last_good_end = 0
    t0 = time.time()
    reason = "?"
    while True:
        pos = f.tell()
        h = f.read(30)
        if len(h) < 30:
            reason = "end of file at a header"; break
        sig, ver, flag, meth, mt, md, crc, cs, us, nl, el = struct.unpack("<IHHHHHIIIHH", h)
        if sig != LOCAL:
            reason = f"signature {sig:#x} at {pos} (central directory or garbage)"; break
        name = f.read(nl).decode("utf-8", "replace")
        extra = f.read(el)
        zip64 = False
        j = 0
        while j + 4 <= len(extra):
            hid, sz = struct.unpack("<HH", extra[j:j + 4])
            if hid == 1:
                zip64 = True
                vals = extra[j + 4:j + 4 + sz]
                if us == 0xFFFFFFFF and len(vals) >= 8:
                    us = struct.unpack("<Q", vals[:8])[0]; vals = vals[8:]
                if cs == 0xFFFFFFFF and len(vals) >= 8:
                    cs = struct.unpack("<Q", vals[:8])[0]
            j += 4 + sz
        if name.endswith("/"):
            continue
        out = None
        tmp = None
        stem, ext0 = os.path.splitext(os.path.basename(name))
        wanted = only is None or ext0.lower() not in (".png", ".jpg") or stem in only
        if a.extract and wanted:
            tmp = os.path.join(a.extract, os.path.basename(name) + ".part")
            out = open(tmp, "wb")
        c = 0
        size = 0
        ok = True
        if meth == 8:
            d = zlib.decompressobj(-15)
            while not d.eof:
                buf = f.read(CHUNK if (flag & 8) else min(CHUNK, max(cs - (f.tell() - pos), 1)))
                if not buf:
                    ok = False; break
                data = d.decompress(buf)
                c = zlib.crc32(data, c); size += len(data)
                if out: out.write(data)
            if ok:
                f.seek(-len(d.unused_data), 1)
        elif meth == 0 and not (flag & 8):
            left = cs
            while left:
                buf = f.read(min(CHUNK, left))
                if not buf:
                    ok = False; break
                left -= len(buf)
                c = zlib.crc32(buf, c); size += len(buf)
                if out: out.write(buf)
        else:
            reason = f"unsupported method {meth} flag {flag} for {name}"; break
        if ok and (flag & 8):
            dd = f.read(4)
            if len(dd) == 4 and struct.unpack("<I", dd)[0] == DESC:
                dd = f.read(4)
            if len(dd) < 4:
                ok = False
            else:
                crc = struct.unpack("<I", dd)[0]
                f.read(16 if zip64 else 8)
        if out:
            out.close()
        if not ok:
            if tmp: os.remove(tmp)
            reason = f"truncated inside {name}"; break
        if c != crc:
            n_bad += 1
            print(f"CRC MISMATCH {name}", flush=True)
            if tmp: os.rename(tmp, tmp[:-5] + ".BADCRC")
            continue
        if tmp:
            os.rename(tmp, tmp[:-5])
        n_ok += 1
        ext = os.path.splitext(name)[1].lower()
        by_ext[ext] = by_ext.get(ext, 0) + 1
        last_good_end = f.tell()
        if lst and wanted: lst.write(f"{os.path.basename(name)} {size} {c:08x}\n")
        if n_ok % 2000 == 0:
            el_ = time.time() - t0
            print(f"  {n_ok} ok, {f.tell()/1e9:.1f}/{total/1e9:.1f} GB, {f.tell()/1e6/max(el_,1e-6):.0f} MB/s", flush=True)
    print(f"stop: {reason}")
    print(f"complete entries: {n_ok} {by_ext}  crc errors: {n_bad}")
    print(f"last complete entry ends at {last_good_end/1e9:.2f} GB of {total/1e9:.2f} GB")


if __name__ == "__main__":
    main()
