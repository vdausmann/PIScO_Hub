"""Uploading data from the computer someone uses the Hub on to the NAS.

Two ways, both landing under one of the UPLOAD_ROOTS (the NAS mounts):

- rsync, run on that computer: the upload page only writes the command.
  It logs in as the `upload` account, which sshd restricts to rsync inside
  /mnt (rrsync), so paths in the command are relative to /mnt.
- the browser: files are sent in chunks to the routes in
  app/routes/upload.py and written here. A file is written to
  `<name>.part` and renamed when its last byte arrives, so an interrupted
  upload resumes from the .part's size and a half-written file never
  looks finished.

Every path a request names is resolved and must stay inside a root.
"""
import os

from .models import GlobalSetting

PART = ".part"


class UploadError(Exception):
    pass


def roots():
    """The allowed destination roots, as real paths."""
    value = GlobalSetting.get("UPLOAD_ROOTS", "/mnt/nas_a,/mnt/nas_b")
    return [os.path.realpath(r.strip()) for r in str(value).split(",") if r.strip()]


def _inside(path, root):
    return path == root or path.startswith(root.rstrip("/") + "/")


def resolve(path):
    """A real path under one of the roots, or UploadError."""
    if not path:
        raise UploadError("no destination given")
    real = os.path.realpath(path)
    if not any(_inside(real, r) for r in roots()):
        raise UploadError(f"{path} is not under an upload destination ({', '.join(roots())})")
    return real


def rsync_path(dest):
    """The destination as the rrsync-restricted account sees it (relative to /mnt)."""
    real = resolve(dest)
    rel = os.path.relpath(real, "/mnt")
    if rel.startswith(".."):
        raise UploadError(f"{dest} is outside /mnt, which the upload account is limited to")
    return rel


def list_dirs(path):
    """Subfolders of a destination folder, or the roots themselves for ''."""
    if not path:
        return [{"name": r, "path": r} for r in roots() if os.path.isdir(r)]
    real = resolve(path)
    if not os.path.isdir(real):
        raise UploadError(f"{path} is not a folder")
    out = []
    for name in sorted(os.listdir(real), key=str.lower):
        full = os.path.join(real, name)
        if os.path.isdir(full) and not name.startswith("."):
            out.append({"name": name, "path": full})
    return out


def writable(path):
    """Whether the Hub can write into a destination folder, and why not."""
    real = resolve(path)
    if not os.path.isdir(real):
        return False, "not a folder"
    if not os.access(real, os.W_OK):
        st = os.statvfs(real)
        if st.f_flag & os.ST_RDONLY:
            return False, "the share is mounted read-only on the Hub"
        return False, "no write permission for the Hub in this folder"
    return True, None


def make_dir(parent, name):
    name = name.strip().strip("/")
    if not name or name in (".", "..") or "/" in name or name.startswith("."):
        raise UploadError("a folder name without slashes, not starting with a dot")
    target = os.path.join(resolve(parent), name)
    resolve(target)
    os.makedirs(target, exist_ok=True)
    return target


def _file_target(dest, relpath):
    """Where a file of a browser upload goes: dest plus its path in the
    dropped folder, checked to stay inside dest."""
    base = resolve(dest)
    rel = os.path.normpath(relpath.replace("\\", "/")).lstrip("/")
    if not rel or rel == "." or rel.startswith(".."):
        raise UploadError(f"bad file path {relpath!r}")
    target = os.path.realpath(os.path.join(base, rel))
    if not _inside(target, base):
        raise UploadError(f"{relpath!r} leaves the destination")
    return target


def check(dest, files):
    """For each {path, size}: 'done' when the file is there complete,
    else the offset to continue from (the .part's size, or 0)."""
    out = []
    for f in files:
        target = _file_target(dest, f["path"])
        size = int(f["size"])
        if os.path.isfile(target) and os.path.getsize(target) == size:
            out.append({"path": f["path"], "state": "done", "offset": size})
        elif os.path.isfile(target):
            out.append({"path": f["path"], "state": "exists", "offset": 0})
        else:
            part = target + PART
            have = os.path.getsize(part) if os.path.isfile(part) else 0
            out.append({"path": f["path"], "state": "todo", "offset": min(have, size)})
    return out


def write_chunk(dest, relpath, offset, total, stream, overwrite=False):
    """Write one chunk at offset; rename to the final name when complete.
    Returns the bytes now on disk for this file."""
    target = _file_target(dest, relpath)
    if os.path.exists(target) and not overwrite:
        if os.path.getsize(target) == total:
            return total
        raise UploadError(f"{relpath} already exists with another size; not overwritten")
    os.makedirs(os.path.dirname(target), exist_ok=True)
    part = target + PART
    have = os.path.getsize(part) if os.path.exists(part) else 0
    if offset != have:
        raise UploadError(f"{relpath}: chunk at {offset}, but {have} bytes are there")
    with open(part, "ab") as fh:
        while True:
            block = stream.read(1 << 20)
            if not block:
                break
            fh.write(block)
    have = os.path.getsize(part)
    if have > total:
        raise UploadError(f"{relpath}: {have} bytes received, {total} expected")
    if have == total:
        os.replace(part, target)
    return have
