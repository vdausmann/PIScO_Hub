#!/usr/bin/env python3
"""Create the controlled comparison arms on the Hub.

Four runs over the same frames, crossing route with deconvolution:

    A1  python route, deconvolution off
    A2  python route, deconvolution on
    B1  C++ route,    deconvolution off   (correction -> extraction)
    B2  C++ route,    deconvolution on    (correction -> deconvolve -> extraction)

A1 vs A2 measures what deconvolution does to detection in the python route,
where it runs on the whole frame *before* detection. B1 vs B2 measures the same
for the C++ route: its pass-1 ROIs cannot depend on the model, but extraction
re-thresholds each ROI with TRIANGLE over pixels the deconvolution replaced, so
the final object set can still move. Both arms use the same LUCYD weights.

    export HUB_TOKEN=...
    python3 comparison/arms.py            # print the definitions, submit nothing
    python3 comparison/arms.py --create
    python3 comparison/arms.py --status
"""
import argparse
import json
import os
import sys
import urllib.request

HUB = os.environ.get("HUB_URL", "http://10.11.11.110:5000")

SOURCE = "/mnt/pisco/testdata/m202_a/"
MODEL = "best_ssim_v5a_noGrad_ablation.pth"   # shipped with both tools
CPP = dict(numProcesses=4, allowedImageFileExtensions=".png",
           imageStackSize=20, imageSizeX=2560, imageSizeY=2560,
           threshold=10, minContourArea=10, boundingBoxPadding=10,
           skipFirstNImages=0, skipLastNImages=0)

PREFIX = "cmp m202_a "


def api(path, payload=None, method=None):
    token = os.environ.get("HUB_TOKEN")
    if not token:
        sys.exit("set HUB_TOKEN")
    req = urllib.request.Request(
        HUB + path,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json"},
        method=method or ("POST" if payload is not None else "GET"))
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def python_arm(deconv, fov_mask=True):
    settings = {"sourcePath": SOURCE, "targetPath": "", "deconvolution": deconv,
                "nCores": 8, "maskImage": fov_mask}
    if deconv:
        # whole 2560^2 frames go through the model, so this is the peak
        # allocation; 2 is what fits an 8 GB Tesla P4
        settings.update(modelPath=MODEL, deconvBatchSize=2)
    return [{"tool": "pisco_segment_py", "settings": settings}]


def cpp_arm(deconv):
    tasks = [{"tool": "pisco_segment", "settings": dict(
        CPP, sourcePath=SOURCE, targetPath="",
        mode="Mode::BACKGROUND_CORRECTION_AND_COMPRESSION")}]
    if deconv:
        tasks.append({"tool": "pisco_deconvolve", "settings": {
            "targetPath": {"$from": {"task": 0, "output": "results"}},
            "modelPath": MODEL}})
    # extraction is a second sweep over the correction directory, never a fresh one
    tasks.append({"tool": "pisco_segment", "settings": dict(
        CPP, sourcePath=SOURCE,
        targetPath={"$from": {"task": 0, "output": "results"}},
        mode="Mode::OBJECT_EXTRACTION")})
    return tasks


# The python route masks a circular field of view and the C++ route has no
# equivalent, so the mask-off arms are the ones that compare the same pixels.
# The mask-on arms are kept: A1 against A1b measures what the mask costs.
ARMS = [("A1 python deconv=off", python_arm(False)),
        ("A2 python deconv=on",  python_arm(True)),
        ("B1 cpp deconv=off",    cpp_arm(False)),
        ("B2 cpp deconv=on",     cpp_arm(True)),
        ("A1b python deconv=off nomask", python_arm(False, fov_mask=False)),
        ("A2b python deconv=on nomask",  python_arm(True, fov_mask=False))]

# Arms already submitted are skipped by name, so this is safe to re-run.
SKIP_EXISTING = True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--create", action="store_true")
    ap.add_argument("--status", action="store_true")
    args = ap.parse_args()

    if args.create:
        existing = set()
        if SKIP_EXISTING:
            rows = api("/api/v1/workflows")
            rows = rows["workflows"] if isinstance(rows, dict) else rows
            existing = {w.get("name") for w in rows}
        for label, tasks in ARMS:
            name = PREFIX + label
            if name in existing:
                print(f"{'':36}  skipped, already submitted: {name}")
                continue
            wf = api("/api/v1/workflows", {"name": name, "tasks": tasks})
            print(f"{wf['id']}  {wf['name']}")
    elif args.status:
        wfs = api("/api/v1/workflows")
        rows = wfs["workflows"] if isinstance(wfs, dict) else wfs
        for w in rows:
            if w.get("name", "").startswith(PREFIX):
                print(f"{w['id']}  {w.get('status'):<12} {w['name']}")
    else:
        for label, tasks in ARMS:
            print(f"\n=== {PREFIX}{label}")
            print(json.dumps({"name": PREFIX + label, "tasks": tasks}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
