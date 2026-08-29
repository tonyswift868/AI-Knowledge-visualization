from __future__ import annotations

import hashlib
import importlib
import re
import shutil
import subprocess
from importlib.metadata import version as package_version
from pathlib import Path

import msgpack


REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = REPO_ROOT / "backend"
UV_VERSION = "0.11.32"
PYTHON_VERSION = "3.11"
PYTHON_PLATFORM = "x86_64-pc-windows-msvc"
PACKAGE_INDEX = "https://pypi.org/simple"
PACKAGE_INDEX_CUTOFF = "2026-08-29T15:58:02Z"
BASE_SOURCE_COMMIT = "4de2b2d98f062a19db1c81d07019cafde2f14425"
BASE_LOCK_SHA256 = "0bd2001d84d84a9bca98e46d23cc2d00810341959ea8bcbbc9dab5bbe9a4abf1"
EXPECTED_CONSTRAINTS_SHA256 = (
    "e03506fd35d4d1147ab87568278b765b9f9f73a9e83e2d17969d10509673c865"
)
EXPECTED_LOCK_SHA256 = "a41b0c8b95936e77a177b2e6c0ab5d127fa6ede56d6070416b1aa8e536b825e8"
EXPECTED_LOCK_PIN_COUNT = 142
EXPECTED_ADDED_PACKAGES = {
    "backports-tarfile",
    "doclang",
    "docling-core",
    "importlib-metadata",
    "jsonref",
    "jsonschema",
    "jsonschema-specifications",
    "latex2mathml",
    "pydantic-settings",
    "referencing",
    "rpds-py",
    "zipp",
}
EXPECTED_VERSION_CHANGES = {
    "numpy": ("2.5.1", "2.4.6"),
    "typer": ("0.27.1", "0.26.8"),
}
EXPECTED_CONSTRAINT_HEADER = (
    f"# Frozen canonical base graph: {BASE_SOURCE_COMMIT}.",
    "# Authorized compatibility substitutions: numpy==2.4.6 and typer==0.26.8.",
)
FROZEN_RELOCK_COMMAND = (
    "uvx",
    "--from",
    f"uv=={UV_VERSION}",
    "uv",
    "--no-config",
    "pip",
    "compile",
    "requirements.txt",
    "--constraint",
    "requirements.constraints.txt",
    "--python-version",
    PYTHON_VERSION,
    "--python-platform",
    PYTHON_PLATFORM,
    "--default-index",
    PACKAGE_INDEX,
    "--index-strategy",
    "first-index",
    "--resolution",
    "highest",
    "--exclude-newer",
    PACKAGE_INDEX_CUTOFF,
    "--upgrade",
    "--no-annotate",
    "--no-cache",
    "--no-header",
    "--output-file",
    "requirements.lock.txt",
)
FROZEN_LOCK_METADATA = (
    f"# Frozen lock resolver: uv=={UV_VERSION}",
    f"# Frozen lock target: CPython {PYTHON_VERSION} on {PYTHON_PLATFORM}",
    "# Frozen lock inputs: requirements.txt + requirements.constraints.txt",
    f"# Frozen lock index: {PACKAGE_INDEX} through {PACKAGE_INDEX_CUTOFF}",
    "# Frozen lock command: " + " ".join(FROZEN_RELOCK_COMMAND),
)
REQUIREMENT_NAME = re.compile(r"^([A-Za-z0-9_.-]+)(?:\[[^\]]+\])?")
PIN = re.compile(r"^([A-Za-z0-9_.-]+)==(.+)$")


def _canonical_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def _requirement_names(lines: list[str]) -> list[str]:
    names: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = REQUIREMENT_NAME.match(stripped)
        assert match is not None, f"unparseable requirement: {stripped}"
        names.append(_canonical_name(match.group(1)))
    return names


def _canonical_lf_bytes(path: Path) -> bytes:
    return path.read_bytes().replace(b"\r\n", b"\n")


def _pins(lines: list[str]) -> dict[str, str]:
    pins: dict[str, str] = {}
    for line in lines:
        match = PIN.match(line.strip())
        if match is not None:
            pins[_canonical_name(match.group(1))] = match.group(2)
    return pins


def _frozen_base_lock() -> bytes:
    result = subprocess.run(
        ("git", "show", f"{BASE_SOURCE_COMMIT}:backend/requirements.lock.txt"),
        cwd=REPO_ROOT,
        capture_output=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout.replace(b"\r\n", b"\n")


def _run_frozen_relock(root: Path, uvx: str, initial_lock: bytes | None = None) -> bytes:
    root.mkdir()
    shutil.copyfile(BACKEND_ROOT / "requirements.txt", root / "requirements.txt")
    shutil.copyfile(
        BACKEND_ROOT / "requirements.constraints.txt",
        root / "requirements.constraints.txt",
    )
    if initial_lock is not None:
        (root / "requirements.lock.txt").write_bytes(initial_lock)
    command = (uvx, *FROZEN_RELOCK_COMMAND[1:])
    result = subprocess.run(
        command,
        cwd=root,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return _canonical_lf_bytes(root / "requirements.lock.txt")


def test_manifest_and_lock_share_one_canonical_runtime_contract() -> None:
    manifest_lines = (BACKEND_ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
    constraint_lines = (BACKEND_ROOT / "requirements.constraints.txt").read_text(
        encoding="utf-8"
    ).splitlines()
    lock_lines = (BACKEND_ROOT / "requirements.lock.txt").read_text(encoding="utf-8").splitlines()
    direct = _requirement_names(manifest_lines)
    locked = _requirement_names(lock_lines)

    assert tuple(manifest_lines[:5]) == FROZEN_LOCK_METADATA
    assert tuple(constraint_lines[:2]) == EXPECTED_CONSTRAINT_HEADER
    assert hashlib.sha256(
        _canonical_lf_bytes(BACKEND_ROOT / "requirements.constraints.txt")
    ).hexdigest() == EXPECTED_CONSTRAINTS_SHA256
    assert len(direct) == len(set(direct))
    assert len(locked) == len(set(locked))
    assert set(direct) <= set(locked)
    assert direct.count("docling-core") == 1
    assert locked.count("docling-core") == 1
    assert direct.count("msgpack") == 1
    assert locked.count("msgpack") == 1
    assert "psd-tools" not in direct
    assert "psd-tools" not in locked


def test_frozen_relock_reproduces_the_canonical_lock_twice(tmp_path: Path) -> None:
    uvx = shutil.which("uvx")
    assert uvx is not None, "uvx is required to execute the frozen production relock"

    version_result = subprocess.run(
        (uvx, "--from", f"uv=={UV_VERSION}", "uv", "--version"),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert version_result.returncode == 0, version_result.stdout + version_result.stderr
    assert version_result.stdout.startswith(f"uv {UV_VERSION} ")

    checked_lock = _canonical_lf_bytes(BACKEND_ROOT / "requirements.lock.txt")
    base_lock = _frozen_base_lock()
    first = _run_frozen_relock(tmp_path / "first", uvx)
    second = _run_frozen_relock(tmp_path / "second", uvx)
    overwrite = _run_frozen_relock(tmp_path / "overwrite", uvx, base_lock)

    assert first == second == overwrite == checked_lock
    assert hashlib.sha256(base_lock).hexdigest() == BASE_LOCK_SHA256
    assert hashlib.sha256(checked_lock).hexdigest() == EXPECTED_LOCK_SHA256
    assert len(_requirement_names(checked_lock.decode("utf-8").splitlines())) == (
        EXPECTED_LOCK_PIN_COUNT
    )


def test_frozen_lock_changes_only_the_authorized_docling_graph() -> None:
    base = _pins(_frozen_base_lock().decode("utf-8").splitlines())
    constraints = _pins(
        (BACKEND_ROOT / "requirements.constraints.txt")
        .read_text(encoding="utf-8")
        .splitlines()
    )
    locked = _pins(
        (BACKEND_ROOT / "requirements.lock.txt").read_text(encoding="utf-8").splitlines()
    )

    assert len(base) == 130
    assert set(constraints) == set(base)
    assert set(locked) - set(base) == EXPECTED_ADDED_PACKAGES
    assert set(base) - set(locked) == set()

    constraint_changes = {
        name: (base[name], constraints[name])
        for name in base
        if base[name] != constraints[name]
    }
    lock_changes = {
        name: (base[name], locked[name])
        for name in base
        if base[name] != locked[name]
    }
    assert constraint_changes == EXPECTED_VERSION_CHANGES
    assert lock_changes == EXPECTED_VERSION_CHANGES
    assert all(locked[name] == version for name, version in constraints.items())


def test_docling_core_is_a_direct_locked_python_311_runtime_dependency() -> None:
    manifest = (BACKEND_ROOT / "requirements.txt").read_text(encoding="utf-8")
    lock = (BACKEND_ROOT / "requirements.lock.txt").read_text(encoding="utf-8")

    assert manifest.splitlines().count("docling-core==2.91.0") == 1
    assert lock.splitlines().count("docling-core==2.91.0") == 1
    assert lock.splitlines().count("numpy==2.4.6") == 1
    assert lock.splitlines().count("typer==0.26.8") == 1
    assert package_version("docling-core") == "2.91.0"

    document_types = importlib.import_module("docling_core.types.doc")
    assert document_types.DoclingDocument.__module__ == "docling_core.types.doc.document"

    execution_source = (
        BACKEND_ROOT / "app" / "sources" / "document" / "ingestion" / "execution.py"
    ).read_text(encoding="utf-8")
    assert "from docling_core.types.doc import DoclingDocument" in execution_source


def test_msgpack_is_a_direct_locked_production_dependency() -> None:
    manifest = (BACKEND_ROOT / "requirements.txt").read_text(encoding="utf-8")
    lock = (BACKEND_ROOT / "requirements.lock.txt").read_text(encoding="utf-8")

    assert manifest.splitlines().count("msgpack==1.2.1") == 1
    assert lock.splitlines().count("msgpack==1.2.1") == 1
    assert msgpack.version == (1, 2, 1)


def test_backend_startup_reaches_real_msgpack_runtime_modules() -> None:
    main_source = (BACKEND_ROOT / "app" / "main.py").read_text(encoding="utf-8")
    assert "from .koikatsu_card import" in main_source

    card = importlib.import_module("app.koikatsu_card")
    abmx = importlib.import_module("app.koikatsu_abmx")
    assert card.msgpack is msgpack
    assert abmx.msgpack is msgpack

    payload = {"runtime": "backend", "dependency": "msgpack"}
    encoded = card.msgpack.packb(payload, use_bin_type=True)
    assert card.msgpack.unpackb(encoded, raw=False) == payload


def test_container_and_sbom_consume_the_authoritative_dependency_truth() -> None:
    dockerfile = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
    sbom_generator = (
        REPO_ROOT / "scripts" / "enterprise" / "generate_sbom.py"
    ).read_text(encoding="utf-8")

    assert "COPY backend/requirements.txt ./requirements.txt" in dockerfile
    assert "pip wheel --wheel-dir /wheels -r requirements.txt" in dockerfile
    assert "FROM backend-base AS backend-image" in dockerfile
    assert "FROM backend-base AS worker-image" in dockerfile
    assert "FROM backend-base AS document-worker-image" in dockerfile
    assert 'PYTHON_LOCK = ROOT / "backend" / "requirements.lock.txt"' in sbom_generator
    assert 'PYTHON_DIRECT = ROOT / "backend" / "requirements.txt"' in sbom_generator
