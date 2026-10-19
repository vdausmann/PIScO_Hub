#!/usr/bin/env python3
"""Install or update a workflow template on the Hub from a JSON file.

Templates live in the Hub's database, which gets reset now and then; the
definition that matters is the file in the repo. This puts it back.

    HUB_TOKEN=... scripts/install_template.py examples/profile_template.json
    HUB_TOKEN=... scripts/install_template.py examples/profile_template.json --hub http://10.11.11.110:5000

A template with the same name is updated in place (steps replaced), so the
id stays and anything pointing at it keeps working.

A file may also list `variants`: the same steps installed under another
name with other parameter defaults, e.g. the profile chain for a camera
with a different frame size and pixel size. One definition, so the steps
cannot drift apart:

    "variants": [{"name": "profile_pisco3", "description": "…",
                  "defaults": {"image_size": 4504, "pixel_size_um": 17.13}}]
"""
import copy
import argparse
import json
import os
import sys
import urllib.error
import urllib.request


def api(hub, token, method, path, payload=None):
    req = urllib.request.Request(
        hub + path, method=method,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("file")
    ap.add_argument("--hub", default=os.environ.get("HUB_URL", "http://10.11.11.110:5000"))
    args = ap.parse_args()
    token = os.environ.get("HUB_TOKEN")
    if not token:
        sys.exit("set HUB_TOKEN")

    spec = json.load(open(args.file))
    variants = spec.pop("variants", [])
    install(args.hub, token, spec)
    for variant in variants:
        install(args.hub, token, make_variant(spec, variant))
    return 0


def make_variant(spec, variant):
    """The base template under the variant's name, with its defaults."""
    out = copy.deepcopy(spec)
    out["name"] = variant["name"]
    for key in ("description", "name_format"):
        if key in variant:
            out[key] = variant[key]
    props = out.get("params_schema", {}).get("properties", {})
    for key, value in variant.get("defaults", {}).items():
        if key not in props:
            sys.exit(f"variant {variant['name']}: no parameter '{key}' in {spec['name']}")
        props[key]["default"] = value
    return out


def install(hub, token, spec):
    name = spec["name"]
    status, existing = api(hub, token, "GET", f"/api/v1/templates/{name}")
    if status == 200 and existing.get("id"):
        body = {k: spec[k] for k in ("description", "name_format", "params_schema", "tasks") if k in spec}
        status, out = api(hub, token, "PATCH", f"/api/v1/templates/{existing['id']}", body)
        verb = "updated"
    else:
        status, out = api(hub, token, "POST", "/api/v1/templates", spec)
        verb = "created"
    if status not in (200, 201):
        sys.exit(f"{verb} failed ({status}): {out.get('error', out)}")
    print(f"{verb} template '{out['name']}' ({out['id']}): {out.get('task_count')} steps, "
          f"tools {out.get('tools')}")


if __name__ == "__main__":
    sys.exit(main())
