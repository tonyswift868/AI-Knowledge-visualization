"""Read a commit through an isolated Git index, then archive only its allowlist."""
from __future__ import annotations

import fnmatch
import hashlib
import os
from pathlib import Path, PurePosixPath
import subprocess
import tarfile


def git(repo: Path, *args: str, env=None) -> bytes:
    return subprocess.check_output(["git", "--no-optional-locks", "-c", "core.autocrlf=false",
                                    "-c", "core.eol=lf", "-C", str(repo), *args], env=env)


def matches(path: str, pattern: str) -> bool:
    # Leading **/ also matches the repository root.
    return fnmatch.fnmatchcase(path, pattern) or (
        pattern.startswith("**/") and fnmatch.fnmatchcase(path, pattern[3:]))


def verify_snapshot(source: Path, files: list[dict]):
    """A resumed archive must still contain exactly the recorded Git blob bytes."""
    for row in files:
        path = source / row['path']
        size = path.stat().st_size
        value = hashlib.new('sha1' if len(row['blob']) == 40 else 'sha256')
        value.update(f'blob {size}\0'.encode())
        with path.open('rb') as f:
            for block in iter(lambda: f.read(1024 * 1024), b''):
                value.update(block)
        if value.hexdigest() != row['blob']:
            raise ValueError(f"快照字节与 Git blob 不符：{row['path']}")


def prepare_snapshot(repo: Path, commit: str, work: Path, excludes: list[str]):
    work.mkdir(parents=True, exist_ok=True)
    source = work / "snapshot"
    if source.exists():
        raise ValueError("工作目录已有 snapshot；请换一个空工作目录，避免混用快照")
    oid = git(repo, "rev-parse", "--verify", f"{commit}^{{commit}}").decode().strip()
    env = os.environ.copy()
    env["GIT_INDEX_FILE"] = str((work / "snapshot.index").resolve())
    env["GIT_OPTIONAL_LOCKS"] = "0"
    git(repo, "read-tree", oid, env=env)
    raw = git(repo, "ls-files", "--stage", "-z", env=env)
    files, excluded = [], []
    for record in raw.split(b"\0"):
        if not record:
            continue
        head, name = record.split(b"\t", 1)
        mode, blob, stage = head.decode().split()
        path = name.decode("utf-8")
        reason = next((p for p in excludes if matches(path, p)), None)
        if mode not in {"100644", "100755"}:
            reason = "非普通文件（不跟随链接或子模块）"
        if reason:
            excluded.append({"path": path, "reason": reason})
        else:
            files.append({"path": path, "blob": blob, "mode": mode})
    source.mkdir()
    names = [row["path"] for row in files]
    # Bound Windows command length. Excluded objects are never archived/opened.
    batches, batch, length = [], [], 0
    for path in names:
        if batch and length + len(path) > 18000:
            batches.append(batch)
            batch, length = [], 0
        batch.append(path)
        length += len(path) + 4
    if batch:
        batches.append(batch)
    for batch in batches:
        proc = subprocess.Popen(["git", "--no-optional-locks", "-c", "core.autocrlf=false",
                                 "-c", "core.eol=lf", "-C", str(repo),
                                 "archive", "--format=tar", oid, "--", *batch],
                                stdout=subprocess.PIPE)
        allowed = set(batch)
        assert proc.stdout is not None
        try:
            with tarfile.open(fileobj=proc.stdout, mode="r|") as archive:
                for member in archive:
                    if member.isdir():
                        continue
                    if member.name not in allowed or not member.isfile():
                        raise ValueError("archive 返回了范围外文件")
                    rel = PurePosixPath(member.name)
                    if rel.is_absolute() or ".." in rel.parts:
                        raise ValueError("archive 路径无效")
                    dest = source.joinpath(*rel.parts)
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    stream = archive.extractfile(member)
                    assert stream is not None
                    with stream, dest.open("wb") as target:
                        while chunk := stream.read(1024 * 1024):
                            target.write(chunk)
            # tar readers stop at the end marker. Git can still be writing tar
            # padding; drain it before wait() so a Windows pipe cannot fill.
            while proc.stdout.read(1024 * 1024):
                pass
            if proc.wait():
                raise RuntimeError("git archive 失败")
        finally:
            proc.stdout.close()
            if proc.poll() is None:
                proc.terminate()
                proc.wait()
    for row in files:
        path = source / row["path"]
        if not path.is_file():
            raise ValueError(f"archive 未包含 {row['path']}（检查 export-ignore）")
        row["bytes"] = path.stat().st_size
    return oid, source, files, excluded
