"""Behavior checks for identity closure dispatch, JDBC pool lifecycle, and bounded body reads."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess

import contract_support144 as support
from java_build156 import default_build_path, default_classpath_path

ROOT = Path(__file__).resolve().parents[3]
JAVA_HOME = Path(os.environ.get("JAVA_HOME", r"D:\MentorFlow\.local\java\jdk-21.0.12.1+1"))
_CACHE_ROOT = Path(os.environ.get("JAVA_CACHE_ROOT", r"D:\MentorFlow\.local\test-tmp\java-cache"))
BUILD = Path(os.environ.get("JAVA156_BUILD", str(default_build_path(_CACHE_ROOT))))
CLASSPATH = Path(os.environ.get("JAVA156_CLASSPATH_FILE", str(default_classpath_path(_CACHE_ROOT))))


def test_identity_and_workspace_219_behaviors():
    artifact_root = Path(os.environ.get("TEMP", r"D:\MentorFlow\.local\test-tmp\fix219\MF01\identity"))
    pg_dir = artifact_root / "identity-pool-postgres"
    pg_dir.mkdir(parents=True, exist_ok=True)
    postgres = support.start_postgres144(pg_dir)
    try:
        jdbc_url = f"jdbc:postgresql://127.0.0.1:{postgres['port']}/postgres?user=postgres"
        dependencies = CLASSPATH.read_text(encoding="utf-8").strip()
        classpath = os.pathsep.join((str(BUILD / "classes"), str(BUILD / "test-classes"), dependencies))
        result = subprocess.run(
            [str(JAVA_HOME / "bin/java.exe"), "-Djava.io.tmpdir=" + str(artifact_root),
             "-cp", classpath, "identity219.IdentityWorkspace219Main", "context,body,pool", jdbc_url],
            cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
            env={**os.environ, "TEMP": str(artifact_root), "TMP": str(artifact_root),
                 "TMPDIR": str(artifact_root)}
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert "IDENTITY_WORKSPACE_219_OK" in result.stdout
    finally:
        support.stop_postgres144(postgres)
