"""The last hour or so of one instrument's ensembles, reduced to what the
live page draws: attitude, temperature, transmit voltage, the echo
intensity profile per beam, mean correlation per cell, the instrument's
vertical speed and the distance to a hard echo (the seafloor for the
down-looker, the surface for the up-looker), plus a depth estimate
integrated from the vertical speed since the start.

The page asks for what is new since the last sequence number it saw, so
a poll every second or two carries a handful of ensembles, not the hour.

For the long windows (up to six hours) a thinned history is kept beside
that: one ensemble every HISTORY_STEP seconds, reduced to what the time
plots draw (echo intensity averaged over the beams, depth, bottom
distance). A page that starts from nothing gets it once with its first
answer; drawing six hours at full rate would be some 20,000 ensembles per
instrument for a plot a thousand pixels wide.
"""

import collections
import threading
import time

import numpy as np

from . import pd0

HISTORY_STEP = 10           # seconds between history entries
HISTORY_SPAN = 6 * 3600     # the longest window the page offers


class YoyoDetector:
    """Spots the turn of a yoyo cast: the package heaved a long way back
    towards the surface, then lowered again. The depth is the integral
    of the vertical speed, so it drifts; the test is relative — the heave
    must take back most of the depth gained since the last turn — and it
    only ever suggests. The operator splits (or dismisses) on the page.

    min_heave_m     the heave must be at least this long
    return_fraction ... and take back at least this share of the depth
    min_lower_m     lowering by this much after the shallowest point is
                    the turn (holding at 20 m, then going down again)
    """

    def __init__(self, min_heave_m=150, return_fraction=0.6, min_lower_m=30):
        self.min_heave_m = min_heave_m
        self.return_fraction = return_fraction
        self.min_lower_m = min_lower_m
        self.suggestion = None
        self.rebase(0.0)

    def rebase(self, depth, time=None):
        """Start looking afresh from here (the start, a split, a dismissal)."""
        self.base = self.peak = self.trough = depth
        self.trough_time = time
        self.suggestion = None

    def feed(self, time, depth):
        if depth is None or self.suggestion is not None:
            return self.suggestion
        if depth > self.peak:
            self.peak = self.trough = depth
            self.trough_time = time
            return None
        if depth < self.trough:
            self.trough, self.trough_time = depth, time
        heave = self.peak - self.trough
        if (heave >= self.min_heave_m
                and heave >= self.return_fraction * (self.peak - self.base)
                and depth - self.trough >= self.min_lower_m):
            self.suggestion = {"turn_time": self.trough_time, "turn_depth": round(self.trough, 1),
                               "peak_depth": round(self.peak, 1), "depth_now": round(depth, 1),
                               "detected": time}
        return self.suggestion


class LiveBuffer:
    def __init__(self, keep=3000, threshold=25):
        self.records = collections.deque(maxlen=keep)
        self.history = collections.deque(maxlen=HISTORY_SPAN // HISTORY_STEP + 60)
        self.lock = threading.Lock()
        self.seq = 0
        self.count = 0
        self.skipped = 0
        self.errors = collections.deque(maxlen=20)
        self.geometry = None
        self.threshold = threshold
        self.depth = 0.0
        self.prev_time = None
        self.first_received = None
        self.last_received = None
        self.yoyo = YoyoDetector()

    def note_error(self, text):
        with self.lock:
            self.errors.append({"t": time.time(), "text": text})

    def add(self, ens):
        now = time.time()
        w = pd0.vertical_velocity(ens)
        t = ens.get("time")
        if w is not None and t is not None and self.prev_time is not None and 0 < t - self.prev_time < 60:
            self.depth += w * (t - self.prev_time)
        self.prev_time = t
        ei = ens.get("ei")
        corr = ens.get("corr")
        vel = ens.get("vel")
        record = {
            "number": ens.get("number"),
            "time": t,
            "received": now,
            "heading": ens.get("heading"),
            "pitch": ens.get("pitch"),
            "roll": ens.get("roll"),
            "temperature": ens.get("temperature"),
            "voltage_raw": ens.get("xmit_voltage_raw"),
            "current_raw": ens.get("xmit_current_raw"),
            "w": None if w is None else round(w, 3),
            "depth": round(self.depth, 1),
            "target": pd0.bottom_distance(ens, threshold=self.threshold),
            "ei": ei.tolist() if ei is not None else None,
            "corr": np.round(corr.mean(axis=1)).astype(int).tolist() if corr is not None else None,
            "good": (np.isfinite(vel).sum(axis=1)).tolist() if vel is not None else None,
        }
        with self.lock:
            self.seq += 1
            self.count += 1
            record["seq"] = self.seq
            self.records.append(record)
            if not self.history or now - self.history[-1]["received"] >= HISTORY_STEP:
                self.history.append(slim(record))
            self.geometry = {"n_cells": ens.get("n_cells"), "cell_m": ens.get("cell_m"),
                             "bin1_m": ens.get("bin1_m"), "up": ens.get("up"),
                             "serial": ens.get("serial"), "beam_angle": ens.get("beam_angle"),
                             "frequency_khz": ens.get("frequency_khz")}
            if self.first_received is None:
                self.first_received = now
            self.last_received = now
            self.yoyo.feed(t if t is not None else now, self.depth)

    def snapshot(self, since=0, limit=1500):
        with self.lock:
            new = [r for r in self.records if r["seq"] > since]
            # Only a page starting from nothing needs the history; one that
            # keeps polling adds to its own copy as records arrive.
            history = list(self.history) if since == 0 else []
            return {
                "seq": self.seq,
                "count": self.count,
                "skipped_bytes": self.skipped,
                "errors": list(self.errors),
                "geometry": self.geometry,
                "first_received": self.first_received,
                "last_received": self.last_received,
                "age": None if self.last_received is None else round(time.time() - self.last_received, 1),
                "records": new[-limit:],
                "history": history,
            }


def slim(record):
    """A record cut down to what the time plots need."""
    ei = record.get("ei")
    return {
        "seq": record["seq"],
        "time": record["time"],
        "received": record["received"],
        "depth": record["depth"],
        "target": record["target"],
        "eim": None if ei is None else [int(round(sum(beams) / len(beams))) for beams in ei],
    }
