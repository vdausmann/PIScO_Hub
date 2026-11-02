"""A ZIP archive sent while it is written, for downloads of many files."""

import os
import time
import zipfile


class _Sink:
    """Write-only stream for zipfile: it cannot seek, so the archive gets
    data descriptors and can be sent while it is written."""

    def __init__(self):
        self.parts = []

    def write(self, data):
        self.parts.append(bytes(data))
        return len(data)

    def flush(self):
        pass

    def take(self):
        data = b"".join(self.parts)
        self.parts = []
        return data


def zip_stream(paths, chunk=8 << 20):
    """A stored (uncompressed: images and HDF5 are compressed already, and
    deflating gigabytes would stall the download) ZIP64 archive of `paths`,
    yielded in pieces as it is read. An entry is a path, filed under its
    basename, or a (path, name in the archive) pair."""
    sink = _Sink()
    with zipfile.ZipFile(sink, "w", zipfile.ZIP_STORED, allowZip64=True) as zf:
        for entry in paths:
            path, name = entry if isinstance(entry, tuple) else (entry, os.path.basename(entry))
            info = zipfile.ZipInfo(name, time.gmtime(os.path.getmtime(path))[:6])
            info.compress_type = zipfile.ZIP_STORED
            with open(path, "rb") as src, zf.open(info, "w", force_zip64=True) as dst:
                while True:
                    data = src.read(chunk)
                    if not data:
                        break
                    dst.write(data)
                    yield sink.take()
            yield sink.take()
    yield sink.take()


def directory_entries(root):
    """(path, name) for every regular file under `root`, named relative to
    its parent so the archive unpacks into a folder of the same name.
    Symlinks are not followed: a link to / would otherwise pull in the disk."""
    base = os.path.dirname(os.path.abspath(root))
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for filename in sorted(filenames):
            path = os.path.join(dirpath, filename)
            if os.path.isfile(path) and not os.path.islink(path):
                yield path, os.path.relpath(path, base)
