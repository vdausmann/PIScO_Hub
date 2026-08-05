"""Demo producer: writes a file into the output directory it is given."""

import json
import os
import sys

settings = json.load(open(sys.argv[1]))

output_dir = settings["output_dir"]
os.makedirs(output_dir, exist_ok=True)

target = os.path.join(output_dir, "result.txt")
with open(target, "w") as f:
    f.write(f"produced by {settings.get('label', 'unlabelled')}\n")

print(f"wrote {target}")

# Lets you watch a failure and a retry without editing anything.
if settings.get("fail"):
    print("failing on purpose", file=sys.stderr)
    sys.exit(1)
