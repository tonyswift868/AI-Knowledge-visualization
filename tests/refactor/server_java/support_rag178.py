"""Isolated fixed-embedding Java classpath helper for RAG178."""
from __future__ import annotations

from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[3]
SHADOW_SOURCE = ROOT / "tests/refactor/server_java/java178/win/clannad/mentorflow/rag/RagEmbedding.java"
VERIFY_SOURCE = ROOT / "tests/refactor/server_java/java178/verify178/EmbeddingShadowVerify178.java"


def compile_embedding_shadow178(build_classes: Path, dependency_classpath: str,
                                java_home: Path, directory: Path) -> Path:
    """Compile and verify the no-model shadow class into task-local output."""
    build_classes = Path(build_classes)
    java_home = Path(java_home)
    directory = Path(directory)
    output = directory / "shadow-classes"
    output.mkdir(parents=True, exist_ok=True)
    javac = java_home / "bin" / "javac.exe"
    java = java_home / "bin" / "java.exe"
    if not javac.is_file() or not java.is_file():
        raise RuntimeError(f"RAG178 Java 21 toolchain is unavailable under {java_home}")
    classpath = str(build_classes) + ";" + dependency_classpath
    compiled = subprocess.run(
        [str(javac), "-proc:none", "-encoding", "UTF-8", "-cp", classpath,
         "-d", str(output), str(SHADOW_SOURCE), str(VERIFY_SOURCE)],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
    if compiled.returncode:
        raise RuntimeError("RAG178 embedding shadow compilation failed: " + compiled.stdout[-2000:] + compiled.stderr[-2000:])
    runtime_cp = str(output) + ";" + classpath
    verified = subprocess.run(
        [str(java), "-cp", runtime_cp, "verify178.EmbeddingShadowVerify178", str(output)],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
    if verified.returncode or "RAG178_EMBEDDING_SHADOW_OK" not in verified.stdout:
        raise RuntimeError("RAG178 embedding shadow verification failed: " + verified.stdout[-2000:] + verified.stderr[-2000:])
    return output
