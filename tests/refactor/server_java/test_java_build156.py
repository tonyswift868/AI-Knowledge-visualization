"""Regression coverage for the single-build/whole-group-skip contract."""
from pathlib import Path
import os
from types import SimpleNamespace

import java_build156 as bootstrap
from java_build156 import _artifacts_current, _run_build_once, group_skip_reason


def _fake_tools(root: Path):
    java_home = root / "jdk"
    for name in ("java.exe", "javac.exe"):
        path = java_home / "bin" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fake", encoding="ascii")
    maven = root / "mvn.cmd"
    maven.write_text("fake", encoding="ascii")
    return java_home, maven


def test_java_build156_runs_maven_once_and_compiles_harnesses_in_task_build(tmp_path):
    java_home, maven = _fake_tools(tmp_path)
    build = tmp_path / "build"
    cp_file = tmp_path / "test-classpath.txt"
    dependency = tmp_path / "fake-dependency.jar"
    calls = []

    def fake_run(command, **_kwargs):
        calls.append(command)
        if command[0] == str(maven):
            build.mkdir(parents=True, exist_ok=True)
            jar = build / "mentorflow-server-0.1.0.jar"
            jar.write_bytes(b"jar")
            main = build / "classes/win/clannad/mentorflow/ServerApplication.class"
            main.parent.mkdir(parents=True, exist_ok=True)
            main.write_bytes(b"class")
            (build / "test-classes").mkdir(exist_ok=True)
            dependency.write_bytes(b"fake jar")
            cp_file.write_text(str(dependency), encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    ready, reason = _run_build_once(build=build, java_home=java_home, maven=maven,
        m2=tmp_path / "m2", readonly_m2=tmp_path / "readonly-m2",
        classpath_file=cp_file, runner=fake_run)

    assert ready, reason
    assert len(calls) == 2  # one Maven build, one javac pass for all contract fixtures
    assert calls[0][0] == str(maven)
    assert "-Dmaven.repo.local=" + str(tmp_path / "m2") in calls[0]
    assert calls[1][0] == str(java_home / "bin" / "javac.exe")
    assert all(str(tmp_path) in argument for argument in calls[0] if argument.startswith("-Djava142.build.directory="))


def test_java_build156_build_failure_is_clear_group_skip_reason(tmp_path):
    java_home, maven = _fake_tools(tmp_path)

    def failed_run(_command, **_kwargs):
        return SimpleNamespace(returncode=17, stdout="dependency unavailable", stderr="")

    ready, reason = _run_build_once(build=tmp_path / "build", java_home=java_home,
        maven=maven, m2=tmp_path / "m2", readonly_m2=tmp_path / "readonly-m2",
        classpath_file=tmp_path / "test-classpath.txt", runner=failed_run)

    assert not ready
    skip = group_skip_reason(ready, reason)
    assert skip.startswith("server_java group skipped:")
    assert "exit 17" in skip and "dependency unavailable" in skip


def test_java_build156_ready_result_does_not_skip_group():
    assert group_skip_reason(True, "ready") is None


def test_java_build156_reuses_complete_current_artifacts(tmp_path):
    build = tmp_path / "build"
    for relative in (
        "mentorflow-server-0.1.0.jar",
        "classes/win/clannad/mentorflow/ServerApplication.class",
    ):
        path = build / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"ready")
    java_root = bootstrap.ROOT / "tests/refactor/server_java/java"
    for source in java_root.rglob("*.java"):
        target = bootstrap._harness_class_path(source, build / "test-classes")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"ready")
    cp = tmp_path / "test-classpath.txt"
    dependency = tmp_path / "fake-dependency.jar"
    dependency.write_bytes(b"fake jar")
    cp.write_text(str(dependency), encoding="utf-8")

    current, reason = _artifacts_current(build, cp, input_files=[])

    assert current, reason


def test_java_build156_test_harness_timestamp_does_not_stale_production_jar(tmp_path):
    build = tmp_path / "build"
    jar = build / "mentorflow-server-0.1.0.jar"
    main_class = build / "classes/win/clannad/mentorflow/ServerApplication.class"
    jar.parent.mkdir(parents=True, exist_ok=True)
    main_class.parent.mkdir(parents=True, exist_ok=True)
    jar.write_bytes(b"jar")
    main_class.write_bytes(b"class")
    java_root = bootstrap.ROOT / "tests/refactor/server_java/java"
    sources = sorted(java_root.rglob("*.java"))
    newest_test_source = max(source.stat().st_mtime_ns for source in sources)
    jar_time = newest_test_source - 2_000_000_000
    os.utime(jar, ns=(jar_time, jar_time))
    classes_time = newest_test_source + 2_000_000_000
    for source in sources:
        target = bootstrap._harness_class_path(source, build / "test-classes")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"class")
        os.utime(target, ns=(classes_time, classes_time))
    cp = tmp_path / "test-classpath.txt"
    dependency = tmp_path / "fake-dependency.jar"
    dependency.write_bytes(b"fake jar")
    cp.write_text(str(dependency), encoding="utf-8")
    cp_time = max(classes_time, (bootstrap.ROOT / "refactor/server-java/pom.xml").stat().st_mtime_ns + 1)
    os.utime(cp, ns=(cp_time, cp_time))
    production_input = tmp_path / "production-source.java"
    production_input.write_text("class Current {}", encoding="utf-8")
    os.utime(production_input, ns=(jar_time - 1, jar_time - 1))

    assert newest_test_source > jar.stat().st_mtime_ns
    current, reason = _artifacts_current(build, cp, input_files=[production_input])

    assert current, reason


def test_java_build156_missing_classpath_dependency_forces_rebuild(tmp_path):
    build = tmp_path / "build"
    jar = build / "mentorflow-server-0.1.0.jar"
    main_class = build / "classes/win/clannad/mentorflow/ServerApplication.class"
    jar.parent.mkdir(parents=True, exist_ok=True)
    main_class.parent.mkdir(parents=True, exist_ok=True)
    jar.write_bytes(b"jar")
    main_class.write_bytes(b"class")
    java_root = bootstrap.ROOT / "tests/refactor/server_java/java"
    for source in java_root.rglob("*.java"):
        target = bootstrap._harness_class_path(source, build / "test-classes")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"class")
    cp = tmp_path / "test-classpath.txt"
    cp.write_text(str(tmp_path / "m2" / "removed.jar"), encoding="utf-8")

    current, reason = _artifacts_current(build, cp, input_files=[])

    assert not current
    assert "missing JARs" in reason


def test_java_build156_uses_declared_package_and_rebuilds_stale_class(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    monkeypatch.setattr(bootstrap, "ROOT", repo)
    source_root = repo / "tests/refactor/server_java/java"
    packaged = source_root / "contract148/ClosureCancellation148.java"
    packaged.parent.mkdir(parents=True)
    packaged.write_text(
        "package win.clannad.mentorflow.closure;\npublic final class ClosureCancellation148 {}\n",
        encoding="utf-8",
    )
    default_package = source_root / "fakecases/DefaultCase.java"
    default_package.parent.mkdir(parents=True)
    default_package.write_text("public final class DefaultCase {}\n", encoding="utf-8")

    build = tmp_path / "build"
    jar = build / "mentorflow-server-0.1.0.jar"
    main_class = build / "classes/win/clannad/mentorflow/ServerApplication.class"
    packaged_class = bootstrap._harness_class_path(packaged, build / "test-classes")
    default_class = bootstrap._harness_class_path(default_package, build / "test-classes")
    for path in (jar, main_class, packaged_class, default_class):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"ready")
    # A fresh class under the source tree must not hide a stale javac output.
    wrong_tree_class = build / "test-classes/contract148/ClosureCancellation148.class"
    wrong_tree_class.parent.mkdir(parents=True)
    wrong_tree_class.write_bytes(b"stale-layout")

    dependency = tmp_path / "fake-dependency.jar"
    dependency.write_bytes(b"fake jar")
    cp = tmp_path / "test-classpath.txt"
    cp.write_text(str(dependency), encoding="utf-8")
    stale_ns = packaged.stat().st_mtime_ns - 1_000_000_000
    os.utime(packaged_class, ns=(stale_ns, stale_ns))
    fresh_ns = packaged.stat().st_mtime_ns + 1_000_000_000
    os.utime(wrong_tree_class, ns=(fresh_ns, fresh_ns))
    pom = repo / "refactor/server-java/pom.xml"
    pom.parent.mkdir(parents=True, exist_ok=True)
    pom.write_text("<project/>", encoding="utf-8")
    os.utime(cp, ns=(pom.stat().st_mtime_ns + 1_000_000_000,) * 2)

    assert packaged_class == build / "test-classes/win/clannad/mentorflow/closure/ClosureCancellation148.class"
    assert default_class == build / "test-classes/DefaultCase.class"
    current, reason = _artifacts_current(build, cp, input_files=[])

    assert not current
    assert "ClosureCancellation148.java" in reason and "stale" in reason


def test_java_build156_build_attempt_is_shared_once(monkeypatch):
    calls = []
    bootstrap.reset_for_test()
    monkeypatch.setattr(bootstrap, "_artifacts_current", lambda *_args: (False, "missing"))
    monkeypatch.setattr(bootstrap, "_run_build_once",
                        lambda **_kwargs: calls.append("build") or (True, "ready"))
    try:
        assert bootstrap.ensure_java_build() == (True, "ready")
        assert bootstrap.ensure_java_build() == (True, "ready")
        assert calls == ["build"]
    finally:
        bootstrap.reset_for_test()
