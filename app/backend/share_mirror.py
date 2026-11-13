"""Instrument data and their cast overviews, mirrored to the ship's share.

The ship's scientific share (MSM147: public_wiss on storage1, Samba) is
where the other groups look. Every SHARE_MIRROR_INTERVAL seconds a
thread writes each instrument's cast overview (cast_overview.py) to
<DATA_ROOT>/overview/<instrument>/ and copies the instrument's folder and
that overview to <SHARE_MIRROR_URL>/<subfolder>/. Only files that are new
there, differ in size or changed since are sent; nothing on the share is
ever deleted or renamed, since others work there too. The Hub is an
unprivileged container and cannot mount CIFS, so this talks SMB itself
(smbprotocol).

Instruments: LADCP (the LADCP page's data folder, unfinished downloads
in _incoming/ left out) and LISST (LISST_DIR). Overviews are written
even when SHARE_MIRROR_URL is empty.

The other way round, the files named in SHARE_PULL (paths under
SHARE_MIRROR_URL, e.g. the underway export another group keeps there)
are copied to <DATA_ROOT>/share/<same path> on each pass when they
changed, so tools on the workers can read them from the shared storage.

Settings: SHARE_MIRROR_URL (//192.168.100.34/public_wiss/MSM147; empty =
no copying), SHARE_MIRROR_USER / SHARE_MIRROR_PASSWORD,
SHARE_MIRROR_INTERVAL, SHARE_MIRROR_LADCP_DIR / SHARE_MIRROR_LISST_DIR
(subfolders on the share), SHARE_PULL, LISST_DIR, LISST_CLOCK_OFFSET_H.
"""

import errno
import os
import threading
import time
import traceback
from datetime import datetime, timezone

from . import cast_overview, dship
from .models import GlobalSetting

SKIP_DIRS = {"_incoming"}


def _iso(t):
    return None if t is None else datetime.fromtimestamp(t, timezone.utc).isoformat(timespec="seconds")


def parse_url(url):
    """//server/share/path → (server, \\\\server\\share\\path)."""
    parts = [p for p in url.replace("\\", "/").split("/") if p]
    if len(parts) < 2:
        raise ValueError(f"SHARE_MIRROR_URL must be //server/share[/path], not {url!r}")
    return parts[0], "\\\\" + "\\".join(parts)


def local_files(root):
    """{relative path with /: (size, mtime)} under root, leaving out
    unfinished downloads and hidden files."""
    found = {}
    for here, dirs, names in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith("."))
        for name in names:
            if name.startswith(".") or name.endswith(".part"):
                continue
            path = os.path.join(here, name)
            try:
                st = os.stat(path)
            except OSError:
                continue
            found[os.path.relpath(path, root).replace(os.sep, "/")] = (st.st_size, st.st_mtime)
    return found


class Share:
    """The few SMB calls the mirror needs."""

    def __init__(self, url, user, password):
        import smbclient
        self.smb = smbclient
        self.server, self.root = parse_url(url)
        self.user, self.password = user, password
        # Its own connections: the mirror thread and page requests must not
        # close each other's when they are done.
        self.kw = {"connection_cache": {}}

    def connect(self):
        self.smb.register_session(self.server, username=self.user or None,
                                  password=self.password or None, connection_timeout=20, **self.kw)

    def path(self, *rel):
        return "\\".join([self.root, *(r.replace("/", "\\") for r in rel if r)])

    def listing(self, sub):
        """{relative path: (size, mtime)} of what is already there."""
        found = {}

        def walk(rel):
            try:
                entries = list(self.smb.scandir(self.path(sub, rel), **self.kw))
            except OSError as e:          # smbprotocol's own class, not FileNotFoundError
                if e.errno == errno.ENOENT:
                    return
                raise
            for e in entries:
                child = f"{rel}/{e.name}" if rel else e.name
                if e.is_dir():
                    walk(child)
                else:
                    st = e.stat()
                    found[child] = (st.st_size, st.st_mtime)
        walk("")
        return found

    def put(self, local, sub, rel, made):
        folder = os.path.dirname(rel)
        if (sub, folder) not in made:
            self.smb.makedirs(self.path(sub, folder), exist_ok=True, **self.kw)
            made.add((sub, folder))
        target = self.path(sub, rel)
        partial = target + ".hubpart"
        with open(local, "rb") as src, self.smb.open_file(partial, mode="wb", **self.kw) as dst:
            while True:
                chunk = src.read(1 << 20)
                if not chunk:
                    break
                dst.write(chunk)
        self.smb.replace(partial, target, **self.kw)

    def get(self, rel, local):
        """Copy a file from the share unless `local` already has its size
        and time; True if it was copied."""
        st = self.smb.stat(self.path(rel), **self.kw)
        try:
            have = os.stat(local)
            if have.st_size == st.st_size and abs(have.st_mtime - st.st_mtime) < 2:
                return False
        except OSError:
            pass
        os.makedirs(os.path.dirname(local), exist_ok=True)
        partial = local + ".hubpart"
        with self.smb.open_file(self.path(rel), mode="rb", **self.kw) as src, open(partial, "wb") as dst:
            while True:
                chunk = src.read(1 << 20)
                if not chunk:
                    break
                dst.write(chunk)
        os.utime(partial, (st.st_mtime, st.st_mtime))
        os.replace(partial, local)
        return True

    def close(self):
        self.smb.reset_connection_cache(**self.kw)


class ShareMirror:
    def __init__(self, app):
        self.app = app
        with app.app_context():
            get = GlobalSetting.get
            self.url = get("SHARE_MIRROR_URL", "") or ""
            self.user = get("SHARE_MIRROR_USER", "") or ""
            self.password = get("SHARE_MIRROR_PASSWORD", "") or ""
            self.interval = float(get("SHARE_MIRROR_INTERVAL", 600) or 600)
            self.lisst_dir = get("LISST_DIR", "") or ""
            self.lisst_offset_h = get("LISST_CLOCK_OFFSET_H", "0") or "0"
            self.subdirs = {"LADCP": get("SHARE_MIRROR_LADCP_DIR", "07_LADCP") or "07_LADCP",
                            "LISST": get("SHARE_MIRROR_LISST_DIR", "08_LISST") or "08_LISST"}
            data_root = get("DATA_ROOT", os.path.join(app.config["HUB_ROOT"], "data"))
            self.overview_root = os.path.join(data_root, "overview")
            self.pull_root = os.path.join(data_root, "share")
            self.pull = [p.strip().strip("/") for p in (get("SHARE_PULL", "") or "").split(",")
                         if p.strip() and ".." not in p.split("/")]
        self.lock = threading.Lock()          # one run at a time
        self.wake = threading.Event()
        self.state = {"url": self.url or None, "last_run": None, "error": None, "instruments": {},
                      "pulled": {}}
        app.extensions["share_mirror"] = self
        threading.Thread(target=self._run, daemon=True, name="share-mirror").start()

    def _run(self):
        time.sleep(30)                         # let DSHIP read its log first
        while True:
            self.run_once()
            self.wake.wait(self.interval)
            self.wake.clear()

    def trigger(self):
        self.wake.set()

    def status(self):
        return dict(self.state, interval_s=self.interval)

    # ---- one pass ----
    def _instruments(self):
        """(name, data folder, overview builder) for each configured one."""
        found = []
        ladcp = self.app.extensions.get("ladcp")
        if ladcp is not None:
            root = ladcp.data_dir()
            found.append(("LADCP", root, lambda ops, root=root: cast_overview.ladcp_overview(root, ops)))
        if self.lisst_dir:
            root = self.lisst_dir
            found.append(("LISST", root, lambda ops, root=root:
                          cast_overview.lisst_overview(root, ops, self.lisst_offset_h)))
        return found

    def _cruise_and_ops(self):
        service = dship.service(self.app)
        if service is None:
            return "", []
        with service.lock:
            return (service.expedition or {}).get("number") or "", list(service.ops)

    def _write_overview(self, name, builder, cruise, ops):
        text, rows = builder(ops)
        folder = os.path.join(self.overview_root, name)
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, f"{cruise + '_' if cruise else ''}{name}_casts.csv")
        try:
            with open(path) as f:
                unchanged = f.read() == text
        except OSError:
            unchanged = False
        if not unchanged:
            with open(path + ".tmp", "w") as f:
                f.write(text)
            os.replace(path + ".tmp", path)
        return folder, rows

    def run_once(self):
        with self.lock:
            started = time.time()
            cruise, ops = self._cruise_and_ops()
            plan = []
            for name, root, builder in self._instruments():
                info = self.state["instruments"].setdefault(name, {})
                info.update(folder=root, subfolder=self.subdirs[name])
                try:
                    overview, rows = self._write_overview(name, builder, cruise, ops)
                    info.update(overview_rows=rows, overview_error=None)
                    plan.append((name, root, overview))
                except Exception as e:  # one instrument's data must not stop the others
                    info["overview_error"] = f"{type(e).__name__}: {e}"
                    traceback.print_exc()
            error = None
            if self.url:
                try:
                    self._sync(plan)
                except Exception as e:
                    error = f"{type(e).__name__}: {e}"
                    traceback.print_exc()
                if self.pull:
                    try:
                        self._pull()
                    except Exception as e:
                        error = error or f"{type(e).__name__}: {e}"
                        traceback.print_exc()
            self.state.update(last_run=_iso(started), error=error,
                              duration_s=round(time.time() - started, 1))

    def _sync(self, plan):
        share = Share(self.url, self.user, self.password)
        share.connect()
        made = set()
        try:
            for name, root, overview in plan:
                sub = self.subdirs[name]
                info = self.state["instruments"][name]
                there = share.listing(sub)
                sent = 0
                for base in (root, overview):
                    if not os.path.isdir(base):
                        continue
                    for rel, (size, mtime) in sorted(local_files(base).items()):
                        have = there.get(rel)
                        if have and have[0] == size and have[1] + 2 >= mtime:
                            continue
                        share.put(os.path.join(base, rel), sub, rel, made)
                        sent += 1
                info.update(last_sync=_iso(time.time()), files_sent=sent)
        finally:
            share.close()

    def _pull(self):
        share = Share(self.url, self.user, self.password)
        share.connect()
        try:
            for rel in self.pull:
                info = self.state["pulled"].setdefault(rel, {})
                local = os.path.join(self.pull_root, *rel.split("/"))
                try:
                    if share.get(rel, local):
                        info["last_copy"] = _iso(time.time())
                    info.update(local=local, size=os.path.getsize(local),
                                modified=_iso(os.path.getmtime(local)), error=None)
                except OSError as e:
                    info["error"] = f"{type(e).__name__}: {e}"
        finally:
            share.close()


def service(app):
    return app.extensions.get("share_mirror")
