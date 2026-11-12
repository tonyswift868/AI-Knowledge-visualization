#!/usr/bin/env python3
"""Validate the versioned Kuma monitor manifest without contacting Kuma."""
import json
import pathlib
import sys

if len(sys.argv) > 2:
    raise SystemExit("usage: validate-kuma-manifest.py [manifest.json]")
path = pathlib.Path(sys.argv[1]) if len(sys.argv) == 2 else pathlib.Path(__file__).with_name("kuma-monitors.json")
data = json.loads(path.read_text(encoding="utf-8"))
if data.get("schema_version") != "mentorflow.kuma-monitors.v1":
    raise ValueError("unsupported schema_version")
monitors = data["monitors"]
names = [m["name"] for m in monitors]
if len(names) != len(set(names)):
    raise ValueError("duplicate monitor name")
if len(monitors) != 4:
    raise ValueError(f"expected 4 monitors, got {len(monitors)}")
if not all(m.get("interval_seconds", data["defaults"]["interval_seconds"]) > 0 for m in monitors):
    raise ValueError("monitor interval must be positive")
if not all(m.get("retry_count", data["defaults"]["retry_count"]) >= 1 for m in monitors):
    raise ValueError("monitor retry_count must be positive")
for monitor in monitors:
    if monitor["type"] == "docker":
        if not monitor.get("container"):
            raise ValueError("docker monitor needs container")
    elif monitor["type"] == "http":
        if not monitor.get("url", "").startswith("https://") or monitor.get("expected_status") != 200:
            raise ValueError("http monitor must use https and expected_status 200")
    elif monitor["type"] == "https-certificate":
        if not monitor.get("url", "").startswith("https://"):
            raise ValueError("certificate monitor must use https")
        if not monitor.get("accepted_status"):
            raise ValueError("certificate monitor needs accepted_status")
    else:
        raise ValueError(f"unsupported monitor type: {monitor['type']}")
print(f"OK: {len(monitors)} monitors; names and intervals are valid")
