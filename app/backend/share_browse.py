"""Browsing the ship's SMB share from the Hub, read-only.

The share of SHARE_MIRROR_URL (MSM147: public_wiss on storage1), with the
same login; the page opens at the mirror's folder. The Hub cannot mount
CIFS (see share_mirror.py), so every listing and download is an SMB
request of its own.
"""

import mimetypes
import stat as statmod
from datetime import datetime, timezone

from .models import GlobalSetting
from .share_mirror import Share, parse_url

# Shown in the browser; anything else is downloaded. Never HTML, SVG or
# script: the share is writable by everyone on board, and an inline page
# would run with the Hub's origin.
INLINE = {"image/png", "image/jpeg", "image/gif", "image/webp", "application/pdf", "text/plain",
          "text/csv", "text/markdown"}
CHUNK = 1 << 20


class BrowseError(ValueError):
    pass


def settings():
    url = GlobalSetting.get("SHARE_MIRROR_URL", "") or ""
    if not url:
        raise BrowseError("No share configured (SHARE_MIRROR_URL)")
    server, unc = parse_url(url)
    parts = [p for p in url.replace("\\", "/").split("/") if p]
    return {"server": server, "share": parts[1], "start": "/".join(parts[2:]),
            "user": GlobalSetting.get("SHARE_MIRROR_USER", "") or "",
            "password": GlobalSetting.get("SHARE_MIRROR_PASSWORD", "") or ""}


def clean(path):
    parts = [p for p in (path or "").replace("\\", "/").split("/") if p]
    if any(p in (".", "..") for p in parts):
        raise BrowseError("Invalid path")
    return "/".join(parts)


def _share(conf):
    share = Share(f"//{conf['server']}/{conf['share']}", conf["user"], conf["password"])
    share.connect()
    return share


def _iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%d %H:%M")


def list_dir(path):
    conf = settings()
    path = clean(path)
    share = _share(conf)
    try:
        entries = []
        for e in share.smb.scandir(share.path(path), **share.kw):
            if e.name.startswith((".", "~$")):     # .DS_Store, ._ files, Office lock files
                continue
            st = e.stat()
            is_dir = e.is_dir()
            entries.append({"name": e.name, "dir": is_dir, "size": None if is_dir else st.st_size,
                            "modified": _iso(st.st_mtime)})
    finally:
        share.close()
    entries.sort(key=lambda e: (not e["dir"], e["name"].lower()))
    return {"path": path, "share": conf["share"], "server": conf["server"], "entries": entries}


def open_file(path):
    """(name, size, mimetype, inline, chunks) for a file; the chunks
    generator closes the connection when it is done."""
    conf = settings()
    path = clean(path)
    if not path:
        raise BrowseError("Not a file")
    share = _share(conf)
    try:
        st = share.smb.stat(share.path(path), **share.kw)
        if statmod.S_ISDIR(st.st_mode):
            raise BrowseError("Not a file")
        f = share.smb.open_file(share.path(path), mode="rb", **share.kw)
    except Exception:
        share.close()
        raise
    name = path.rsplit("/", 1)[-1]
    mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
    if name.lower().endswith((".csv", ".txt", ".log", ".md", ".cnv", ".hdr", ".dat")):
        mime = "text/plain"

    def chunks():
        try:
            while True:
                data = f.read(CHUNK)
                if not data:
                    break
                yield data
        finally:
            f.close()
            share.close()
    return name, st.st_size, mime, mime in INLINE, chunks()
