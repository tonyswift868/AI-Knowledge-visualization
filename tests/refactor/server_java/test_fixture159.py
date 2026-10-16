"""Regression checks for BUG-108 fixture isolation and cross-process locking."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import time


def test_server_java_temp_is_this_pytest_basetemp(tmp_path, request):
    temp = Path(os.environ["TEMP"]).resolve()
    base = Path(str(request.config._tmp_path_factory.getbasetemp())).resolve()
    assert base in temp.parents or temp == base
    assert "ship156" not in str(temp).lower()


def test_java_cache_file_lock_serializes_processes(tmp_path):
    cache = tmp_path / "java-cache"
    events = tmp_path / "events.jsonl"
    script = tmp_path / "lock_probe.py"
    script.write_text(
        "import json, os, time\n"
        "from pathlib import Path\n"
        "from java_build156 import _build_file_lock\n"
        "events=Path(os.environ['LOCK_EVENTS'] + '.' + str(os.getpid()))\n"
        "with _build_file_lock():\n"
        "  start=time.monotonic(); events.open('a', encoding='utf-8').write(json.dumps({'start':start})+'\\n')\n"
        "  time.sleep(0.20)\n"
        "  end=time.monotonic(); events.open('a', encoding='utf-8').write(json.dumps({'end':end})+'\\n')\n",
        encoding="utf-8",
    )
    env = os.environ.copy()
    env.update({"JAVA_CACHE_ROOT": str(cache), "LOCK_EVENTS": str(events),
                "PYTHONPATH": str(Path(__file__).resolve().parent) + os.pathsep + env.get("PYTHONPATH", "")})
    first = subprocess.Popen([sys.executable, str(script)], env=env)
    second = subprocess.Popen([sys.executable, str(script)], env=env)
    assert first.wait(timeout=30) == 0
    assert second.wait(timeout=30) == 0
    rows = [json.loads(line) for path in tmp_path.glob("events.jsonl.*")
            for line in path.read_text(encoding="utf-8").splitlines()]
    starts = [row["start"] for row in rows if "start" in row]
    ends = [row["end"] for row in rows if "end" in row]
    assert len(starts) == len(ends) == 2
    assert max(starts) >= min(ends)
