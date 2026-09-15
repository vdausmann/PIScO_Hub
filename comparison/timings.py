"""Per-task wall-clock for the comparison arms, from the Hub API."""
import datetime as dt, json, os, sys, urllib.request

HUB = "http://10.11.11.110:5000"
TOKEN = os.environ["HUB_TOKEN"]
FRAMES = 46

WF = [
    ("A1  python  deconv=off", "e9b29a80-9ef9-44cb-9e0f-dc2e436911b5"),
    ("A2  python  deconv=on ", "109c54d2-d3a7-4242-b14b-3bbadb3aa150"),
    ("A1b python  off nomask", "0fce75cc-35d2-46a8-9361-60db1844701a"),
    ("A2b python  on  nomask", "375ff63a-2d4b-4139-b858-dace4b027eb6"),
    ("B1  cpp     deconv=off", "2eda51b7-e837-4f1c-a6eb-147298e2e8b4"),
    ("B2  cpp     deconv=on ", "833c83b3-11c8-4f85-85fb-4d7607f46c79"),
    ("A1y python  blocks  8c", "08198ba2-40f1-4f00-a3d3-f31bf756befe"),
    ("B1y cpp     blocks  1p", "c62ed14e-e8e4-4b92-b8ad-274579cbfe34"),
    ("B1z cpp     blocks12 1p", "634e7722-2be8-4af5-9f50-3218e3be14f2"),
]


def get(path):
    req = urllib.request.Request(HUB + path,
                                 headers={"Authorization": f"Bearer {TOKEN}"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def parse(s):
    return dt.datetime.fromisoformat(s)


for label, wf in WF:
    d = get(f"/api/v1/workflows/{wf}")
    total = 0.0
    parts = []
    for t in sorted(d["tasks"], key=lambda t: t["workflow_position"]):
        if not t.get("started_at") or not t.get("completed_at"):
            parts.append(f"{t['tool']}=?"); continue
        secs = (parse(t["completed_at"]) - parse(t["started_at"])).total_seconds()
        total += secs
        name = t["tool"].replace("pisco_", "")
        parts.append(f"{name} {secs:6.1f}s")
    print(f"{label}  total {total:7.1f}s   {total / FRAMES:5.2f} s/frame   "
          f"[{' | '.join(parts)}]  on {d['tasks'][0].get('machine')}")
