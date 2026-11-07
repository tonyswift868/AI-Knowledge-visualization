"""One per-pytest-session Java build for the server_java contract tests."""
from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess
import threading
import time
from contextlib import contextmanager

ROOT = Path(__file__).resolve().parents[3]
_CACHE_ROOT = Path(os.environ.get("JAVA_CACHE_ROOT", r"D:\MentorFlow\.local\test-tmp\java-cache"))
_SHIP_ROOT = os.environ.get("SHIP156_TMP")
TASK_ROOT = Path(_SHIP_ROOT or os.environ.get("JAVA156_RUNTIME_TMP", str(_CACHE_ROOT / "runtime")))
BUILD = Path(os.environ.get("JAVA156_BUILD", str(_CACHE_ROOT / "build")))
JAVA_HOME = Path(os.environ.get("JAVA_HOME", r"D:\MentorFlow\.local\java\jdk-21.0.12.1+1"))
MAVEN = Path(os.environ.get("JAVA156_MAVEN", r"D:\MentorFlow\.local\java\apache-maven-3.9.16\bin\mvn.cmd"))
M2 = Path(os.environ.get("JAVA_MAVEN_REPO", str(_CACHE_ROOT / "m2")))
READONLY_M2 = Path(os.environ.get("JAVA156_READONLY_M2", r"D:\MentorFlow\.local\java\m2"))
CLASSPATH_FILE = Path(os.environ.get("JAVA156_CLASSPATH_FILE", str(TASK_ROOT / "test-classpath.txt")))
_lock = threading.Lock()
_state: tuple[bool, str] | None = None


@contextmanager
def _build_file_lock():
    """Serialize builders across pytest processes while reusing java-cache."""
    lock_path = _CACHE_ROOT / "build.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("a+")
    try:
        if os.name == "nt":
            import msvcrt
            handle.seek(0)
            handle.write("0")
            handle.flush()
            # LK_LOCK has a finite retry window on Windows.  Poll the
            # non-blocking operation so a Maven build longer than ten seconds
            # still queues the second pytest process instead of failing.
            while True:
                try:
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    time.sleep(0.25)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        try:
            if os.name == "nt":
                import msvcrt
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


def _classpath_problem(cp_file: Path) -> str | None:
    if not cp_file.is_file():
        return f"Java dependency classpath file is missing: {cp_file}"
    raw = cp_file.read_text(encoding="utf-8").strip()
    if not raw:
        return f"Java dependency classpath is empty: {cp_file}"
    missing = [entry for entry in raw.split(os.pathsep) if entry.strip()
               and not Path(entry.strip()).is_file()]
    if missing:
        return "Java dependency classpath references missing JARs: " + ", ".join(missing[:8])
    return None


def _harness_class_path(source: Path, test_classes: Path) -> Path:
    """Return javac -d's class path from the declared package and source filename."""
    header = source.read_text(encoding="utf-8")
    package = re.search(r"^\s*package\s+([A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*)\s*;",
                        header, re.MULTILINE)
    package_path = Path(*package.group(1).split(".")) if package else Path()
    # javac -d lays classes out by package, regardless of the source directory.
    return test_classes / package_path / f"{source.stem}.class"


def _run_build_once(*, build: Path, java_home: Path, maven: Path, m2: Path,
                    readonly_m2: Path, classpath_file: Path | None = None,
                    runner=subprocess.run) -> tuple[bool, str]:
    """Build artifacts and custom harnesses, returning an actionable failure reason."""
    java = java_home / "bin" / "java.exe"
    javac = java_home / "bin" / "javac.exe"
    jar = build / "mentorflow-server-0.1.0.jar"
    classes = build / "classes"
    test_classes = build / "test-classes"
    cp_file = classpath_file or CLASSPATH_FILE
    required = (jar, classes / "win" / "clannad" / "mentorflow" / "ServerApplication.class",
                test_classes, cp_file)
    if not java.is_file():
        return False, f"Java 21 executable is missing: {java}"
    if not javac.is_file():
        return False, f"Java 21 compiler is missing: {javac}"
    if not maven.is_file():
        return False, f"Maven launcher is missing: {maven}"
    build.mkdir(parents=True, exist_ok=True)
    m2.mkdir(parents=True, exist_ok=True)
    temp = TASK_ROOT / "tmp"
    temp.mkdir(parents=True, exist_ok=True)
    settings = Path(os.environ.get("JAVA_MAVEN_SETTINGS", str(TASK_ROOT / "maven-settings156.xml")))
    # Existing artifacts remain read-only; Maven writes any newly fetched files to task-local m2.
    if not settings.exists():
      settings.parent.mkdir(parents=True, exist_ok=True)
      settings.write_text(
        "<settings><profiles><profile><id>mentorflow-readonly-cache</id><activation><activeByDefault>true</activeByDefault></activation>"
        "<repositories><repository><id>mentorflow-readonly-cache</id><url>file:///" +
        str(readonly_m2).replace("\\", "/") +
        "</url><releases><checksumPolicy>ignore</checksumPolicy></releases><snapshots><enabled>false</enabled></snapshots></repository></repositories>"
        "<pluginRepositories><pluginRepository><id>mentorflow-readonly-plugins</id><url>file:///" +
        str(readonly_m2).replace("\\", "/") +
        "</url></pluginRepository></pluginRepositories></profile></profiles></settings>",
        encoding="utf-8")
    env = os.environ.copy()
    env.update({"JAVA_HOME": str(java_home), "TEMP": str(temp), "TMP": str(temp),
                "TMPDIR": str(temp), "MAVEN_OPTS": "-Djava.io.tmpdir=" + str(temp)})
    env["PATH"] = str(java_home / "bin") + os.pathsep + env.get("PATH", "")
    pom = ROOT / "refactor/server-java/pom.xml"
    command = [str(maven), "-B", "-s", str(settings), "-Dmaven.repo.local=" + str(m2),
               "-Djava142.build.directory=" + str(build), "-DskipTests",
               "-Dmdep.includeScope=test", "-Dmdep.outputFile=" + str(cp_file),
               "-f", str(pom), "package", "dependency:build-classpath"]
    try:
        result = runner(command, cwd=ROOT, env=env, capture_output=True, text=True,
                        encoding="utf-8", errors="replace", timeout=1200, check=False)
    except Exception as exc:
        return False, f"Maven build could not start: {type(exc).__name__}: {exc}"
    if result.returncode:
        detail = (getattr(result, "stdout", "") or "") + (getattr(result, "stderr", "") or "")
        return False, "Maven package/classpath build failed (exit " + str(result.returncode) + "): " + detail[-3500:]
    test_classes.mkdir(parents=True, exist_ok=True)
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        return False, "Maven succeeded but required artifacts are missing: " + ", ".join(missing)
    cp_problem = _classpath_problem(cp_file)
    if cp_problem:
        return False, "Maven succeeded but " + cp_problem
    dependencies = cp_file.read_text(encoding="utf-8").strip()
    source_root = ROOT / "tests/refactor/server_java/java"
    sources = sorted(str(path) for path in source_root.rglob("*.java"))
    if sources:
        test_classes.mkdir(parents=True, exist_ok=True)
        compile_cp = os.pathsep.join((str(classes), str(test_classes), dependencies))
        compile_result = runner([str(javac), "-encoding", "UTF-8", "-cp", compile_cp,
                                 "-d", str(test_classes), *sources], cwd=ROOT, env=env,
                                capture_output=True, text=True, encoding="utf-8",
                                errors="replace", timeout=300, check=False)
        if compile_result.returncode:
            detail = (getattr(compile_result, "stdout", "") or "") + (getattr(compile_result, "stderr", "") or "")
            return False, "Java contract harness compilation failed (exit " + str(compile_result.returncode) + "): " + detail[-3500:]
    return True, "Java 21/Maven artifacts and server_java harnesses are ready"


def ensure_java_build() -> tuple[bool, str]:
    """Share one build attempt among all Java contract fixtures in this pytest process."""
    global _state
    with _lock:
        if _state is None:
            with _build_file_lock():
                if not (JAVA_HOME / "bin" / "java.exe").is_file():
                    _state = (False, f"Java 21 executable is missing: {JAVA_HOME / 'bin' / 'java.exe'}")
                else:
                    current, reason = _artifacts_current(BUILD, CLASSPATH_FILE)
                    _state = ((True, "Existing Java 21/Maven artifacts are current") if current else
                              _run_build_once(build=BUILD, java_home=JAVA_HOME, maven=MAVEN,
                                              m2=M2, readonly_m2=READONLY_M2))
        return _state


def _artifacts_current(build: Path, cp_file: Path,
                       input_files: list[Path] | None = None) -> tuple[bool, str]:
    jar = build / "mentorflow-server-0.1.0.jar"
    classes = build / "classes"
    test_classes = build / "test-classes"
    java_root = ROOT / "tests/refactor/server_java/java"
    sources = sorted(java_root.rglob("*.java"))
    harness_classes = [_harness_class_path(source, test_classes) for source in sources]
    required = (jar, classes / "win" / "clannad" / "mentorflow" / "ServerApplication.class",
                cp_file, *harness_classes)
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        return False, "Java artifacts are missing: " + ", ".join(missing)
    pom = ROOT / "refactor/server-java/pom.xml"
    source_roots = (ROOT / "refactor/server-java/src/main",)
    inputs = input_files if input_files is not None else [
        pom, *(path for source_root in source_roots for path in source_root.rglob("*.java"))]
    if max((path.stat().st_mtime_ns for path in inputs if path.exists()), default=0) > jar.stat().st_mtime_ns:
        return False, "Java source or POM is newer than the prepared application JAR"
    for source, output in zip(sources, harness_classes):
        if output.stat().st_mtime_ns < source.stat().st_mtime_ns:
            return False, f"Java test harness is stale: {source.relative_to(ROOT)}"
    if cp_file.stat().st_mtime_ns < pom.stat().st_mtime_ns:
        return False, "Java dependency classpath is older than the Maven POM"
    cp_problem = _classpath_problem(cp_file)
    if cp_problem:
        return False, cp_problem
    return True, ""


def reset_for_test() -> None:
    """Reset process memoization; only used by isolated unit tests."""
    global _state
    with _lock:
        _state = None


def group_skip_reason(ready: bool, reason: str) -> str | None:
    return None if ready else "server_java group skipped: " + reason
