"""Run the offline health truthiness regression through the shared Java build gate."""
from __future__ import annotations

import os
import subprocess

import java_build156 as java_build


def test_health_truthy323_matches_python_semantics():
    classpath = os.pathsep.join((
        str(java_build.BUILD / "classes"),
        str(java_build.BUILD / "test-classes"),
        java_build.CLASSPATH_FILE.read_text(encoding="utf-8").strip(),
    ))
    result = subprocess.run(
        [str(java_build.JAVA_HOME / "bin/java.exe"), "-cp", classpath,
         "health323.HealthTruthy323Main"],
        cwd=java_build.ROOT, capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PASS: 22 Python-compatible health truthiness cases" in result.stdout
