"""The ship's station book, from DSHIP.

DSHIP (Werum) keeps the action log of an expedition: device operations
named after station and operation (MSM147_2-9 is station 2, operation 9)
and their events — in the water, max depth/on ground, on deck, station
start/end — each stamped with the time, the ship's position, the water
depth and the winch's rope length. Its web interface reads all of that
from a REST service on the same server (`/dship-actionlog/rest/…`, POST
with form fields), and an anonymous login may read it. This module does
the same, read-only: a thread fetches the current expedition's action
log every DSHIP_POLL_INTERVAL seconds, keeps it in memory and writes a
snapshot to <DATA_ROOT>/dship/, so lookups still work while DSHIP is
unreachable.

From the log it derives what a profile needs: the operation a time
falls into (PIScO rides on the CTD, and starts recording a few minutes
before the CTD goes in), its station written as 002-09, and the CTD cast
number — the operation's place among the expedition's CTD operations in
time order, every one that went into the water counted, aborted ones
included.

Settings: DSHIP_URL (e.g. http://192.168.100.10:8080; empty = off),
DSHIP_USER / DSHIP_PASSWORD (anonymous / anonymous), DSHIP_CAST_DEVICE
(the device short name PIScO is mounted on, CTD), DSHIP_POLL_INTERVAL.
"""

import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from .models import GlobalSetting

COLUMNS = {
    "time": "ac_a_timestamp", "action": "ac_ad_name", "operation": "ac_do_name",
    "device": "ac_d_shortname", "device_name": "ac_d_longname", "comment": "ac_a_comment",
    "lat": "cv_SYS.STR.PosLat", "lon": "cv_SYS.STR.PosLon", "water_depth_m": "cv_SYS.STR.DPT",
    "winch": "cv_Winch.SysStr.WinchName", "rope_m": "cv_Winch.SysStr.Rope_length",
}
PAGE = 100
# A recording that starts this long before the device goes in still
# belongs to it (MSM147: PIScO started 8 min before the CTD).
LEAD_S = 45 * 60
_NAME = re.compile(r"^(?P<cruise>.+)_(?P<station>\d+)-(?P<operation>\d+)$")


class DshipError(Exception):
    pass


def _iso(ms):
    return None if ms is None else datetime.fromtimestamp(ms / 1000, timezone.utc) \
        .isoformat(timespec="seconds").replace("+00:00", "Z")


def _number(text):
    try:
        return float(text) if text not in (None, "") else None
    except ValueError:
        return None


class Client:
    """One read: a session, the requests, the session closed again."""

    def __init__(self, url, user, password, timeout=30):
        self.url = url.rstrip("/")
        self.user, self.password, self.timeout = user, password, timeout
        self.session = None

    def post(self, path, **fields):
        data = urllib.parse.urlencode({k: v for k, v in fields.items() if v is not None}).encode()
        request = urllib.request.Request(self.url + path, data=data, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = response.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            raise DshipError(f"{path}: HTTP {e.code}") from e
        except (urllib.error.URLError, OSError) as e:
            raise DshipError(f"cannot reach DSHIP at {self.url}: {getattr(e, 'reason', e)}") from e
        try:
            return json.loads(body) if body else None
        except ValueError:
            return body

    def __enter__(self):
        reply = self.post("/dship-usermanagement/rest/createSession",
                          user=self.user, password=self.password)
        if not isinstance(reply, dict) or "id" not in reply:
            raise DshipError("DSHIP refused the login")
        self.session = reply["id"]
        return self

    def __exit__(self, *exc):
        try:
            self.post("/dship-usermanagement/rest/destroySession", sessionId=self.session)
        except DshipError:
            pass

    def expedition(self):
        return self.post("/dship-actionlog/rest/getCurrentExpedition", sessionId=self.session)

    def action_log(self, expedition_id):
        """Every event of the expedition, oldest first."""
        keys = list(COLUMNS)
        events, offset = [], 0
        while True:
            page = self.post("/dship-actionlog/rest/getActionLogPage", sessionId=self.session,
                             offset=offset, size=PAGE, state="", allUserGroups="true",
                             expeditionIds=str(expedition_id), sortOrder="asc",
                             columnIds=",".join(COLUMNS[k] for k in keys))
            if not isinstance(page, dict):
                raise DshipError("unexpected answer to getActionLogPage")
            entries = page.get("actionLogEntries") or []
            for entry in entries:
                values = dict(zip(keys, entry.get("values") or []))
                events.append({
                    "time": entry.get("timestamp") or values.get("time"),
                    "action": entry.get("actionName") or values.get("action"),
                    "operation": entry.get("deviceOperationName") or values.get("operation"),
                    "operation_id": entry.get("deviceOperationId"),
                    "device": values.get("device") or entry.get("deviceOperationLabel"),
                    "device_name": values.get("device_name"),
                    "comment": values.get("comment") or "",
                    "lat": _number(values.get("lat")), "lon": _number(values.get("lon")),
                    "water_depth_m": _number(values.get("water_depth_m")),
                    "winch": values.get("winch") or None, "rope_m": _number(values.get("rope_m")),
                })
            offset += len(entries)
            if not entries or offset >= int(page.get("count") or 0):
                break
        events.sort(key=lambda e: e["time"] or 0)
        return events


def operations(events, cast_device="CTD"):
    """Device operations from the events, oldest first, with station,
    in-water / max-depth / on-deck events and, for the cast device, the
    cast number."""
    ops = {}
    for e in events:
        if not e["operation"]:
            continue
        op = ops.setdefault(e["operation"], {
            "name": e["operation"], "device": e["device"], "device_name": e["device_name"],
            "events": []})
        op["events"].append(e)
    result = []
    for op in ops.values():
        m = _NAME.match(op["name"])
        op["station"] = f"{int(m['station']):03d}-{int(m['operation']):02d}" if m else None
        op["cruise"] = m["cruise"] if m else None
        first = {}
        for e in op["events"]:
            first.setdefault(e["action"], e)
        op["in_water"] = first.get("in the water")
        op["max_depth"] = first.get("max depth/on ground")
        op["on_deck"] = first.get("on deck")
        op["start"] = (op["in_water"] or op["events"][0])["time"]
        op["end"] = (op["on_deck"] or op["events"][-1])["time"] if op["on_deck"] else None
        op["aborted"] = any("problem" in (e["comment"] or "").lower()
                            or "problem" in (e["action"] or "").lower() for e in op["events"]) \
            or (op["on_deck"] is not None and op["max_depth"] is None)
        op["cast"] = None
        result.append(op)
    result.sort(key=lambda o: o["start"] or 0)
    n = 0
    for op in result:
        if op["device"] == cast_device and op["in_water"] is not None:
            n += 1
            op["cast"] = n
    return result


def summary(op):
    """An operation as the API and the form see it."""
    def event(e):
        return None if e is None else {
            "time": _iso(e["time"]), "lat": e["lat"], "lon": e["lon"],
            "water_depth_m": e["water_depth_m"], "rope_m": e["rope_m"], "comment": e["comment"]}
    position = op["in_water"] or (op["events"][0] if op["events"] else None)
    return {
        "operation": op["name"], "station": op["station"], "cruise": op["cruise"],
        "device": op["device"], "device_name": op["device_name"], "cast": op["cast"],
        "aborted": op["aborted"],
        "in_water": event(op["in_water"]), "max_depth": event(op["max_depth"]),
        "on_deck": event(op["on_deck"]),
        "lat": position["lat"] if position else None, "lon": position["lon"] if position else None,
        "date": _iso(op["start"])[:10] if op["start"] else None,
        "events": [{"time": _iso(e["time"]), "action": e["action"], "comment": e["comment"]}
                   for e in op["events"]],
    }


def operation_at(ops, when_ms, device):
    """The operation of `device` a moment belongs to: the one running
    then, or the next one starting within LEAD_S (recording starts on
    deck). None if neither."""
    best, best_gap = None, None
    for op in ops:
        if op["device"] != device or op["start"] is None:
            continue
        start = op["start"] - LEAD_S * 1000
        end = op["end"] if op["end"] is not None else time.time() * 1000
        if start <= when_ms <= end:
            gap = 0 if op["start"] <= when_ms else op["start"] - when_ms
            if best is None or gap < best_gap:
                best, best_gap = op, gap
    return best


class DshipService:
    def __init__(self, app):
        self.app = app
        with app.app_context():
            self.url = GlobalSetting.get("DSHIP_URL", "") or ""
            self.user = GlobalSetting.get("DSHIP_USER", "anonymous") or "anonymous"
            self.password = GlobalSetting.get("DSHIP_PASSWORD", "anonymous") or "anonymous"
            self.device = GlobalSetting.get("DSHIP_CAST_DEVICE", "CTD") or "CTD"
            self.interval = float(GlobalSetting.get("DSHIP_POLL_INTERVAL", 120) or 120)
            self.directory = os.path.join(
                GlobalSetting.get("DATA_ROOT", os.path.join(app.config["HUB_ROOT"], "data")), "dship")
        self.lock = threading.Lock()
        self.expedition = None
        self.events = []
        self.ops = []
        self.fetched_at = None
        self.error = None
        self._load_snapshot()
        if self.url:
            threading.Thread(target=self._run, daemon=True, name="dship").start()
        app.extensions["dship"] = self

    # ---- reading ----
    def _run(self):
        while True:
            try:
                self.refresh()
            except DshipError as e:
                self.error = str(e)
            time.sleep(self.interval)

    def refresh(self):
        with Client(self.url, self.user, self.password) as client:
            expedition = client.expedition()
            if not isinstance(expedition, dict) or "id" not in expedition:
                raise DshipError("DSHIP has no current expedition")
            events = client.action_log(expedition["id"])
        ops = operations(events, self.device)
        with self.lock:
            self.expedition, self.events, self.ops = expedition, events, ops
            self.fetched_at, self.error = time.time(), None
        self._save_snapshot()

    def _snapshot_path(self):
        number = (self.expedition or {}).get("number") or "expedition"
        return os.path.join(self.directory, f"{number}_actionlog.json")

    def _save_snapshot(self):
        try:
            os.makedirs(self.directory, exist_ok=True)
            path = self._snapshot_path()
            with open(path + ".tmp", "w") as f:
                json.dump({"expedition": self.expedition, "fetched_at": self.fetched_at,
                           "events": self.events}, f)
            os.replace(path + ".tmp", path)
        except OSError as e:
            self.error = f"cannot write the snapshot: {e}"

    def _load_snapshot(self):
        """The newest snapshot, so the Hub knows the log before DSHIP answers."""
        try:
            files = [os.path.join(self.directory, f) for f in os.listdir(self.directory)
                     if f.endswith("_actionlog.json")]
        except OSError:
            return
        if not files:
            return
        try:
            with open(max(files, key=os.path.getmtime)) as f:
                data = json.load(f)
        except (OSError, ValueError):
            return
        self.expedition, self.events = data.get("expedition"), data.get("events") or []
        self.fetched_at = data.get("fetched_at")
        self.ops = operations(self.events, self.device)

    # ---- looking up ----
    def status(self):
        with self.lock:
            return {
                "url": self.url or None,
                "expedition": {k: (self.expedition or {}).get(k) for k in ("number", "name", "chiefScientist")}
                if self.expedition else None,
                "fetched_at": None if self.fetched_at is None else
                datetime.fromtimestamp(self.fetched_at, timezone.utc).isoformat(timespec="seconds"),
                "error": self.error, "events": len(self.events), "operations": len(self.ops),
                "cast_device": self.device,
            }

    def list(self, device=None):
        with self.lock:
            ops = list(self.ops)
        return [summary(op) for op in ops if device is None or op["device"] == device]

    def at(self, when_s, device=None):
        with self.lock:
            ops = list(self.ops)
        op = operation_at(ops, when_s * 1000, device or self.device)
        return summary(op) if op else None


def service(app):
    return app.extensions.get("dship")
