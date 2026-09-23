"""Submit two profiles to the Hub at the same time and show which machine ran
each step - the on-board case of two casts processed together, one per GPU
worker. Input: the two hdf5-sink halves of the m202_a test frames made by
pisco_profile_dataset/scripts/make_hdf5_testset.py.

    HUB_TOKEN=... python3 scripts/concurrent_profiles_test.py [--wait]

Stations 028-02 and 028-03 are both M202 station 028-02 (the template's
station pattern allows no suffix); the second is a label, not a real cast.
"""
import argparse
import json
import os
import sys
import time
import urllib.request
from datetime import datetime

HUB = os.environ.get("HUB_URL", "http://10.11.11.110:5000")
TOKEN = os.environ.get("HUB_TOKEN")
DATA = "/mnt/pisco/testdata/m202_h5"
MODELS = "/mnt/pisco/models/vit"


def api(path, data=None):
    req = urllib.request.Request(
        HUB + path, data=json.dumps(data).encode() if data else None,
        method="POST" if data else "GET",
        headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req))


def submit():
    common = dict(cruise="M202", instrument="PISCO2", lat=38.58, lon=-27.32, date="2024-07-25",
                  operator="concurrency test",
                  binary_model=f"{MODELS}/ATAIIR2604_NorthSea_binary",
                  taxon_model=f"{MODELS}/ATAIIR2604_NorthSea_multiclass")
    ids = []
    for station, half in (("028-02", "half1"), ("028-03", "half2")):
        params = dict(common, station=station, frames=f"{DATA}/{half}",
                      sensor_log=f"{DATA}/{half}/sensors.txt",
                      comment=f"m202_a {half} as hdf5 sink, submitted together with the other half")
        w = api("/api/v1/workflows", {"template": "profile", "params": params})
        ids.append((station, w["id"]))
        print(f"{station} -> {w['id']} {w['status']}")
    return ids


def timeline(ids):
    parse = datetime.fromisoformat
    rows, t0, done = [], None, True
    for station, wid in ids:
        w = api(f"/api/v1/workflows/{wid}")
        done &= w["status"] in ("Finished", "Failed", "Cancelled")
        print(f"{station}: {w['status']}  {w['name']}")
        for t in w["tasks"]:
            if not t.get("started_at"):
                continue
            s = parse(t["started_at"])
            e = parse(t["completed_at"]) if t.get("completed_at") else None
            t0 = s if t0 is None or s < t0 else t0
            rows.append((station, t["workflow_position"], t["tool"], t["machine"] or "-",
                         t["status"], s, e))
    print(f"\n{'station':8s} {'#':>2s} {'tool':23s} {'machine':11s} {'status':9s} "
          f"{'start':>6s} {'end':>6s} {'dur':>5s}")
    for st, i, tool, m, status, s, e in sorted(rows, key=lambda r: r[5]):
        end = (e - t0).total_seconds() if e else float("nan")
        dur = (e - s).total_seconds() if e else float("nan")
        print(f"{st:8s} {i:2d} {tool:23s} {m:11s} {status:9s} "
              f"{(s - t0).total_seconds():6.0f} {end:6.0f} {dur:5.0f}")
    spans = {}
    for st, i, tool, m, status, s, e in rows:
        if m.startswith("gpuworker") and e:
            spans.setdefault(st, []).append((s, e))
    a, b = (spans.get(st, []) for st, _ in ids)
    overlap = sum(max(0.0, (min(e1, e2) - max(s1, s2)).total_seconds())
                  for s1, e1 in a for s2, e2 in b)
    gpus = {st: sorted({m for st2, i, tool, m, *_ in rows
                        if st2 == st and m.startswith("gpuworker")}) for st, _ in ids}
    print(f"\nGPU-worker time overlapping between the two profiles: {overlap:.0f} s")
    print("GPU workers per profile:", gpus)
    return done


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--wait", action="store_true", help="poll until both finished, then print the timeline")
    ap.add_argument("--ids", help="JSON file of an earlier submission, to print its timeline")
    args = ap.parse_args()
    if not TOKEN:
        sys.exit("HUB_TOKEN not set")
    if args.ids:
        ids = json.load(open(args.ids))
    else:
        ids = submit()
        json.dump(ids, open("concurrent_profiles_ids.json", "w"))
    while True:
        done = timeline(ids)
        if done or not args.wait:
            return
        time.sleep(30)


if __name__ == "__main__":
    main()
