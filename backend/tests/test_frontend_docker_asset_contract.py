from __future__ import annotations

import re
import shlex
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
FRONTEND_ROOT = REPO_ROOT / "frontend"
SOURCE_ROOT = FRONTEND_ROOT / "src"
DOCKERFILE = REPO_ROOT / "Dockerfile"
DOCKERIGNORE = REPO_ROOT / ".dockerignore"
SOURCE_SUFFIXES = {".js", ".jsx", ".ts", ".tsx"}
RELATIVE_JSON_IMPORT = re.compile(
    r"(?:from\s+|import\s*\(\s*)[\"'](?P<path>(?:\.\.?/)+[^\"']+\.json)[\"']"
)


def _repo_path(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


def _external_json_imports() -> set[str]:
    required: set[str] = set()
    for source_path in SOURCE_ROOT.rglob("*"):
        if not source_path.is_file() or source_path.suffix not in SOURCE_SUFFIXES:
            continue
        source = source_path.read_text(encoding="utf-8")
        for match in RELATIVE_JSON_IMPORT.finditer(source):
            target = (source_path.parent / match.group("path")).resolve()
            target.relative_to(REPO_ROOT)
            try:
                target.relative_to(FRONTEND_ROOT)
            except ValueError:
                required.add(_repo_path(target))
    return required


def _docker_copy_statements() -> list[list[str]]:
    source = DOCKERFILE.read_text(encoding="utf-8").replace("\\\r\n", " ").replace("\\\n", " ")
    statements: list[list[str]] = []
    for line in source.splitlines():
        stripped = line.strip()
        if not stripped.startswith("COPY "):
            continue
        tokens = shlex.split(stripped, posix=True)[1:]
        while tokens and tokens[0].startswith("--"):
            tokens.pop(0)
        statements.append(tokens)
    return statements


def test_frontend_repo_root_json_imports_are_explicit_docker_inputs() -> None:
    required = _external_json_imports()
    assert len(required) == 4, sorted(required)
    assert all(path.startswith("legacy/koikatsu-3d/resources/web/") for path in required)

    tracked = set(
        subprocess.run(
            ["git", "ls-files"],
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        ).stdout.splitlines()
    )
    assert required <= tracked

    asset_copy_statements = [
        tokens for tokens in _docker_copy_statements() if required.intersection(tokens[:-1])
    ]
    assert len(asset_copy_statements) == 1
    copy_tokens = asset_copy_statements[0]
    assert set(copy_tokens[:-1]) == required
    assert copy_tokens[-1] == "/build/legacy/koikatsu-3d/resources/web/"

    rules = {
        line.strip()
        for line in DOCKERIGNORE.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    assert "legacy/**" in rules
    expected_includes = {
        "!legacy/koikatsu-3d/",
        "!legacy/koikatsu-3d/resources/",
        "!legacy/koikatsu-3d/resources/web/",
        *(f"!{path}" for path in required),
    }
    actual_includes = {rule for rule in rules if rule.startswith("!legacy/")}
    assert actual_includes == expected_includes
