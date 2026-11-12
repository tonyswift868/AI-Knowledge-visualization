"""Pure path calculations for the per-repository Java test cache."""

from __future__ import annotations

import importlib
import os
from pathlib import Path

import java_build156 as build156


def test_default_cache_paths_are_stable_and_repository_specific(tmp_path):
    cache_root = tmp_path / "java-cache"
    first = Path("D:/MentorFlow-office/tools/repo")
    equivalent = first / "tests" / ".."
    second = Path("D:/another-worktree/repo")

    assert build156.repository_cache_key(first) == build156.repository_cache_key(equivalent)
    assert build156.repository_cache_key(first) != build156.repository_cache_key(second)
    assert build156.default_build_path(cache_root, first) != build156.default_build_path(cache_root, second)
    assert build156.default_classpath_path(cache_root, first) != build156.default_classpath_path(cache_root, second)
    assert build156.default_build_lock_path(cache_root, first) != build156.default_build_lock_path(cache_root, second)

    assert build156.default_build_path(cache_root, first).name.startswith("build-")
    assert build156.default_classpath_path(cache_root, first).name.startswith("test-classpath-")
    assert build156.default_build_lock_path(cache_root, first).name.startswith("build-")


def test_explicit_build_and_classpath_environment_values_are_kept():
    keys = ("JAVA156_BUILD", "JAVA156_CLASSPATH_FILE", "JAVA_CACHE_ROOT",
            "SHIP156_TMP", "JAVA156_RUNTIME_TMP")
    previous = {key: os.environ.get(key) for key in keys}
    build = Path("D:/MentorFlow/.local/test-tmp/374-MF06/explicit-build")
    classpath = Path("D:/MentorFlow/.local/test-tmp/374-MF06/explicit-classpath.txt")
    try:
        os.environ.pop("SHIP156_TMP", None)
        os.environ.pop("JAVA156_RUNTIME_TMP", None)
        os.environ["JAVA156_BUILD"] = str(build)
        os.environ["JAVA156_CLASSPATH_FILE"] = str(classpath)
        loaded = importlib.reload(build156)
        assert loaded.BUILD == build
        assert loaded.CLASSPATH_FILE == classpath
        os.environ.pop("JAVA156_CLASSPATH_FILE")
        loaded = importlib.reload(build156)
        assert loaded.BUILD == build
        assert loaded.CLASSPATH_FILE == loaded.TASK_ROOT / "test-classpath.txt"
        os.environ.pop("JAVA156_BUILD")
        custom_cache = Path("D:/MentorFlow/.local/test-tmp/374-MF06/custom-cache")
        os.environ["JAVA_CACHE_ROOT"] = str(custom_cache)
        loaded = importlib.reload(build156)
        assert loaded.BUILD == loaded.default_build_path(custom_cache)
        assert loaded.CLASSPATH_FILE == loaded.default_classpath_path(custom_cache)
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        importlib.reload(build156)
