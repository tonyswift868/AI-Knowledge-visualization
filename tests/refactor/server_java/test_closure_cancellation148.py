"""Java closure timeout and request cancellation preserve checkpoints."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[3]
TASK_ROOT = Path(os.environ.get("JAVA144_TMP", r"D:\MentorFlow\.local\test-tmp\wire148"))
def test_closure_timeout_interrupt_and_client_cancellation148():
    build = Path(os.environ.get("JAVA142_ARTIFACTS", str(TASK_ROOT / "build")))
    classes = build / "classes"
    test_classes = build / "test-classes"
    dep_file = Path(os.environ.get("JAVA156_CLASSPATH_FILE", str(TASK_ROOT / "test-classpath.txt")))
    assert classes.is_dir() and test_classes.is_dir() and dep_file.is_file(), \
        "Run the unified Java build first; this test must not start Maven."
    deps = dep_file.read_text(encoding="utf-8").strip()
    cp = os.pathsep.join((str(classes), str(test_classes), deps))
    helper = ROOT / "tests/refactor/server_java/java/contract148/ClosureCancellation148.java"
    task_tmp = TASK_ROOT / "tmp"
    task_tmp.mkdir(parents=True, exist_ok=True)
    # TEMP/TMP are scoped by the server_java test hook.  This test's child
    # process receives its own directory through the explicit environment
    # assembled below; never leak it to the pytest process.
    harness_classes = build / "closure-harness148"
    harness_classes.mkdir(parents=True, exist_ok=True)
    javac = Path(r"D:\MentorFlow\.local\java\jdk-21.0.12.1+1\bin\javac.exe")
    java = Path(r"D:\MentorFlow\.local\java\jdk-21.0.12.1+1\bin\java.exe")
    compile_log = TASK_ROOT / "closure-harness148-compile.log"
    run_log = TASK_ROOT / "closure-harness148-run.log"
    with compile_log.open("wb") as stream:
        subprocess.run([str(javac), "-encoding", "UTF-8", "-cp", cp, "-d",
            str(harness_classes), str(helper)], cwd=ROOT, check=True, timeout=180,
            stdout=stream, stderr=subprocess.STDOUT)
    with run_log.open("wb") as stream:
        subprocess.run([str(java), "-cp", os.pathsep.join((str(harness_classes), cp)),
            "win.clannad.mentorflow.closure.ClosureCancellation148"], cwd=ROOT,
            check=True, timeout=10, stdout=stream, stderr=subprocess.STDOUT)
    output = run_log.read_text(encoding="utf-8")
    match = re.search(r"CLOSURE_TRACE_148=(\{.*\})", output)
    assert match, output
    java_trace = json.loads(match.group(1))
    assert java_trace == {
        "timeout": {"status": 503, "state": "RETRYABLE_FAILURE",
                    "code": "CLOSURE_RETRY_REQUIRED", "fail_calls": 1,
                    "provider_done": 0, "complete_calls": 0},
        "cancel": {"cancelled": True, "fail_calls": 0,
                   "provider_done": 0, "complete_calls": 0},
    }
