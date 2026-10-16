"""Compare blocking-provider timeout and request cancellation in Python and Java."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import re
import subprocess

from refactor.server.account.closure import AccountClosure


ROOT = Path(__file__).resolve().parents[3]
TASK_ROOT = Path(os.environ.get("JAVA144_TMP", r"D:\MentorFlow\.local\test-tmp\wire148"))
OPERATION = "0123456789abcdef0123456789abcdef"
TOKEN = "mfc1_" + "A" * 43


class Store:
    def __init__(self):
        self.failed = self.provider_done_calls = self.completed = 0

    def acquire(self, operation, digest, claim):
        return {"acquired": True, "state": "IN_PROGRESS", "operation_id": operation,
            "account_id": "mfa1_test", "subject_id": "subject148", "data_cleaned": False,
            "identity_deleted": False}

    def clean(self, operation, digest, claim):
        return {"acquired": True, "state": "IN_PROGRESS", "operation_id": operation,
            "account_id": "mfa1_test", "subject_id": "subject148", "data_cleaned": True,
            "identity_deleted": False}

    def provider_done(self, operation, digest, claim):
        self.provider_done_calls += 1
        return {"acquired": True, "state": "IN_PROGRESS", "operation_id": operation,
            "account_id": "mfa1_test", "subject_id": "subject148", "data_cleaned": True,
            "identity_deleted": True}

    def complete(self, operation, digest, claim):
        self.completed += 1
        return {"state": "COMPLETED", "operation_id": operation, "account_id": "mfa1_test"}

    def fail(self, operation, digest, claim, code):
        self.failed += 1
        return {"state": "RETRYABLE_FAILURE", "operation_id": operation,
            "account_id": "mfa1_test", "code": code}


class Runtime:
    def __init__(self):
        self.native_sessions = self
        self.client = self
        self.store = self

    def revoke_subject(self, subject):
        pass

    def revoke_by_subject_or_sid(self, subject, sid):
        pass


def _python_trace(monkeypatch):
    import refactor.server.account.closure as closure_module

    original_timeout = asyncio.timeout
    seen = []

    def short_timeout(seconds):
        seen.append(seconds)
        return original_timeout(0.05 if seconds == 75 else seconds)

    monkeypatch.setattr(closure_module.asyncio, "timeout", short_timeout)

    class Provider:
        def __init__(self):
            self.started = asyncio.Event()
            self.continued = 0

        async def ensure_deleted(self, subject):
            self.started.set()
            await asyncio.sleep(5)
            self.continued += 1

    async def run():
        timeout_store, timeout_provider = Store(), Provider()
        timeout_result = await AccountClosure(timeout_store, timeout_provider, Runtime()).commit(OPERATION, TOKEN)
        timeout = {"status": timeout_result.status, "state": timeout_result.payload["state"],
            "code": timeout_result.payload["code"], "fail_calls": timeout_store.failed,
            "provider_done": timeout_store.provider_done_calls, "complete_calls": timeout_store.completed}

        cancel_store, cancel_provider = Store(), Provider()
        closure = AccountClosure(cancel_store, cancel_provider, Runtime())
        task = asyncio.create_task(closure.commit(OPERATION, TOKEN))
        await cancel_provider.started.wait()
        task.cancel()
        try:
            await task
            cancelled = False
        except asyncio.CancelledError:
            cancelled = True
        cancel = {"cancelled": cancelled, "fail_calls": cancel_store.failed,
            "provider_done": cancel_store.provider_done_calls, "complete_calls": cancel_store.completed}
        return {"timeout": timeout, "cancel": cancel, "configured_seconds": seen}

    trace = asyncio.run(run())
    assert trace["configured_seconds"] == [75, 75]
    return {key: trace[key] for key in ("timeout", "cancel")}


def test_closure_timeout_interrupt_and_client_cancellation148(monkeypatch):
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
    assert java_trace == _python_trace(monkeypatch)
