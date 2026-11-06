"""
healthcheck.py - used by `podman run --health-cmd "python /app/healthcheck.py"`.

Exits 0 when the app answers /health with status "ok", otherwise exits 1.
Kept as a file so the health command needs no nested quotes (which PowerShell mangles).
"""

import json
import os
import sys
import urllib.request

url = f"http://127.0.0.1:{os.environ.get('PORT', '5000')}/health"

try:
    with urllib.request.urlopen(url, timeout=3) as response:
        body = json.loads(response.read().decode("utf-8"))
    sys.exit(0 if body.get("status") == "ok" else 1)
except Exception as exc:  # any failure means unhealthy
    print(f"healthcheck failed: {exc}")
    sys.exit(1)
