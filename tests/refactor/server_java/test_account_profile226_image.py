"""Compile and exercise the server-side canonical avatar re-encoder on the installed JDK."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess

from java_build156 import BUILD, CLASSPATH_FILE, JAVA_HOME


def test_account_profile226_jdk_image_normalization_and_route_boundary():
    java = JAVA_HOME / "bin" / "java.exe"
    classpath = os.pathsep.join((str(BUILD / "classes"), str(BUILD / "test-classes"),
                                 CLASSPATH_FILE.read_text(encoding="utf-8").strip()))
    result = subprocess.run(
        [str(java), "-Djava.awt.headless=true", "-cp", classpath,
         "win.clannad.mentorflow.identity.AccountProfile226Main"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PROFILE226_JDK_IMAGE_AND_ROUTE_OK" in result.stdout
