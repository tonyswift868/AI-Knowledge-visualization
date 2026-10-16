"""Exercise the same production-store entrypoint used by rag-verify.sh."""
import os
from pathlib import Path
import subprocess

import pytest
from support_rag154 import JAVA_HOME, BUILD, CLASSPATH_FILE, ROOT, rag154_backends


def test_rag154_disposable_pgvector_verification_entrypoint(rag154_backends):
    if os.environ.get("RAG154_VECTOR_BACKEND") != "pgvector":
        pytest.skip("Server verification entrypoint requires an actual pgvector database")
    environment = dict(rag154_backends["endpoint"]["env"])
    classpath = os.pathsep.join((str(BUILD / "classes"), str(BUILD / "test-classes"),
                                CLASSPATH_FILE.read_text(encoding="utf-8").strip()))
    log_path = rag154_backends["directory"] / "verification-entrypoint.log"
    with log_path.open("wb") as log:
        result = subprocess.run([str(JAVA_HOME / "bin/java.exe"), "-Xmx512m",
            "-Djava.io.tmpdir=" + environment["TEMP"], "-cp", classpath,
            "rag154.RagPgvectorVerify154"], cwd=ROOT, env=environment,
            stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, timeout=240)
    output = log_path.read_text(encoding="utf-8", errors="replace")
    assert result.returncode == 0, output[-5000:]
    assert "RAG_PGVECTOR154_OK" in output


def test_rag154_verification_shell_syntax():
    bash = Path("C:/Program Files/Git/bin/bash.exe")
    if not bash.is_file():
        pytest.skip("Git Bash not installed")
    subprocess.run([str(bash), "-n", str(ROOT / "refactor/server-java/deploy/rag-verify.sh")],
                   check=True, capture_output=True, timeout=30)
