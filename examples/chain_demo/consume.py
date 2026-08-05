"""Demo consumer: reads the directory an earlier task produced."""

import json
import os
import sys

settings = json.load(open(sys.argv[1]))

source = os.path.join(settings["input_dir"], "result.txt")
if not os.path.exists(source):
    print(f"upstream file missing: {source}", file=sys.stderr)
    sys.exit(1)

with open(source) as f:
    content = f.read().strip()
print(f"consumed: {content}")

report_dir = settings["report_dir"]
os.makedirs(report_dir, exist_ok=True)
with open(os.path.join(report_dir, "report.txt"), "w") as f:
    f.write(f"read from {settings['input_dir']}\n{content}\n")
