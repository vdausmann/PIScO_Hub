"""Demo tool that never finishes, for exercising the task timeout."""

import json
import sys
import time

settings = json.load(open(sys.argv[1]))
print(f"sleeping for {settings.get('seconds', 3600)}s", flush=True)
time.sleep(settings.get("seconds", 3600))
