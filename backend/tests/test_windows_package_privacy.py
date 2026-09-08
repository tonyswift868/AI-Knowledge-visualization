from __future__ import annotations

import ast
import json
import hashlib
import os
import sqlite3
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from scripts import verify_windows_package as verifier


_BUILD_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "build_distributable.ps1"
_SPEC_FILE = Path(__file__).resolve().parents[2] / "MentorFlow.spec"


def test_manifest_exclusion_needs_no_second_entry_resolution(tmp_path, monkeypatch):
    root = tmp_path / 'verified'
    root.mkdir()
    manifest = root / 'manifest.json'
    payload = root / 'MentorFlow.exe'
    # The complete security iterator remains the validation boundary. This
    # fixture isolates only the post-validation manifest-name exclusion step.
    monkeypatch.setattr(verifier, '_iter_package_entries', lambda _root: iter([(manifest, False), (payload, False)]))
    def redundant_resolution(*args, **kwargs):
        raise AssertionError('already validated entries must not be resolved again only for name comparison')
    with monkeypatch.context() as comparison_only:
        comparison_only.setattr(Path, 'resolve', redundant_resolution)
        assert list(verifier._iter_package_files(root, manifest)) == [payload]


def test_verification_reuses_one_security_inventory(tmp_path, monkeypatch):
    root = _package(tmp_path)
    verifier.build_manifest(root)
    original = verifier._iter_package_entries
    walks = []
    def counted(package_root, *, resolve_paths=True):
        walks.append(resolve_paths)
        yield from original(package_root, resolve_paths=resolve_paths)
    monkeypatch.setattr(verifier, "_iter_package_entries", counted)
    verifier.verify_package(root, data_root=None, check_data=False, smoke=False, smoke_seconds=0)
    assert walks == [True, False]
    # Same length, unchanged timestamp: actual content remains mandatory.
    exe = root / "MentorFlow.exe"
    timestamp = exe.stat()
    exe.write_bytes(b"X" * timestamp.st_size)
    os.utime(exe, ns=(timestamp.st_atime_ns, timestamp.st_mtime_ns))
    with pytest.raises(verifier.PackageError, match="tampered:MentorFlow.exe"):
        verifier.verify_package(root, data_root=None, check_data=False, smoke=False, smoke_seconds=0)


def test_late_extra_file_is_rejected_after_hashing(tmp_path, monkeypatch):
    root = _package(tmp_path)
    verifier.build_manifest(root)
    original = verifier.sha256_file
    def hash_and_add(path):
        result = original(path)
        (root / "unexpected.dll").write_bytes(b"late addition")
        return result
    monkeypatch.setattr(verifier, "sha256_file", hash_and_add)
    with pytest.raises(verifier.PackageError, match="unexpected:unexpected.dll"):
        verifier.verify_package(root, data_root=None, check_data=False, smoke=False, smoke_seconds=0)


def test_package_entry_resolves_once(tmp_path, monkeypatch):
    root = tmp_path.resolve()
    payload = root / "payload.dll"
    payload.write_bytes(b"protected")
    original = Path.resolve
    calls = []
    def counted(path, *args, **kwargs):
        calls.append(path)
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "resolve", counted)
    verifier._validate_package_entry(payload, root, directory=False)
    assert calls == [payload]


def _run_cleanup_guard(root: Path, candidate: Path) -> subprocess.CompletedProcess[str]:
    powershell = shutil.which("powershell.exe")
    if powershell is None:
        pytest.skip("Windows PowerShell 5.1 unavailable")
    return subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(_BUILD_SCRIPT),
            "-ValidateCleanupOnly",
            "-CleanupRoot",
            str(root),
            "-CleanupPath",
            str(candidate),
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def _package(tmp_path: Path) -> Path:
    root = tmp_path / "MentorFlow"
    self_check = root / "SELF-CHECK"
    catalog = root / "_internal" / "content" / "narrative_catalog" / "v2"
    self_check.mkdir(parents=True)
    catalog.mkdir(parents=True)
    (root / "MentorFlow.exe").write_bytes(b"placeholder")
    (root / verifier.VERSION_FILE_NAME).write_text(
        json.dumps(
            {
                "schema": verifier.VERSION_SCHEMA,
                "product": "MentorFlow",
                "version": "1.2.3",
            }
        ),
        encoding="utf-8",
    )
    for name in (
        "MentorFlowSelfCheck.exe",
        "verify_windows_package.py",
        "mentorflow-check.bat",
        "mentorflow-update.bat",
    ):
        (self_check / name).write_bytes(b"placeholder")
    (catalog / "narrative_catalog_v2.sqlite").write_bytes(verifier.SQLITE_MAGIC + b"public-catalog")
    return root


def _frontend_tree_collector() -> tuple[dict[str, object], object]:
    """Load only the spec's asset collector, without running PyInstaller hooks."""

    tree = ast.parse(_SPEC_FILE.read_text(encoding="utf-8"), filename=str(_SPEC_FILE))
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "add_frontend_tree"
    )
    namespace: dict[str, object] = {"os": os, "Path": Path, "datas": []}
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(_SPEC_FILE), "exec"), namespace)
    return namespace, namespace["add_frontend_tree"]


def test_public_frontend_assets_exclude_private_teacher_model(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "frontend"
    public_manifest = source / "teacher2d" / "original-ai-teacher-blonde-fang" / "manifest.json"
    private_manifest = source / "teacher2d" / "tsubaki-live2d" / "manifest.json"
    public_manifest.parent.mkdir(parents=True)
    private_manifest.parent.mkdir(parents=True)
    public_manifest.write_text("{}", encoding="utf-8")
    private_manifest.write_text("{}", encoding="utf-8")

    monkeypatch.delenv("MENTORFLOW_INCLUDE_PRIVATE_TEACHER_ASSETS", raising=False)
    namespace, collector = _frontend_tree_collector()
    collector(source, "frontend/dist")
    collected = [Path(str(item[0])).relative_to(source).as_posix() for item in namespace["datas"]]
    assert "teacher2d/original-ai-teacher-blonde-fang/manifest.json" in collected
    assert not any(path.startswith("teacher2d/tsubaki-live2d/") for path in collected)

    monkeypatch.setenv("MENTORFLOW_INCLUDE_PRIVATE_TEACHER_ASSETS", "1")
    private_namespace, private_collector = _frontend_tree_collector()
    private_collector(source, "frontend/dist")
    private_collected = [
        Path(str(item[0])).relative_to(source).as_posix()
        for item in private_namespace["datas"]
    ]
    assert "teacher2d/tsubaki-live2d/manifest.json" in private_collected


def test_pyinstaller_uses_canonical_app_namespace_and_drops_conflicting_root_icu() -> None:
    source = _SPEC_FILE.read_text(encoding="utf-8")

    assert 'collect_submodules("app")' in source
    assert 'collect_submodules("backend.app")' not in source
    assert 'str(root), str(backend_root)' in source
    assert '(backend_root / "app").rglob("*.py")' in source
    assert 'relative_to(backend_root).parent' in source
    assert 'destination = Path("backend") / relative_parent' in source
    assert 'binaries = collect_dynamic_libs("ortools")' in source
    assert '_conflicting_root_icu = {"icuuc.dll", "icudt78.dll"}' in source
    assert 'Path(str(entry[0])).parent == Path(".")' in source


def test_public_catalog_and_required_self_check_pass(tmp_path: Path) -> None:
    package = _package(tmp_path)
    verifier.build_manifest(package)
    report = verifier.verify_package(package, data_root=None, check_data=False, smoke=False, smoke_seconds=1)
    assert report["files"] == 7


@pytest.mark.parametrize(
    "relative",
    (
        Path("backend") / "alembic" / "versions" / "0018_local_user_authentication.py",
        Path("_internal") / "qmldbg_local.dll",
        Path("_internal") / "fake_profile.py",
        Path("_internal") / "instructor" / "cache" / "schema.py",
        Path("_internal") / "torch" / "_vendor" / "quack" / "cache" / "__init__.py",
        Path("_internal") / "torch" / "export" / "_remove_effect_tokens_pass.py",
        Path("_internal") / "transformers" / "pipelines" / "token_classification.py",
        Path("_internal") / "PySide6" / "qml" / "Qt5Compat" / "GraphicalEffects" / "private" / "qmldir",
    ),
)
def test_pyinstaller_dependency_names_are_not_private_paths(tmp_path: Path, relative: Path) -> None:
    package = _package(tmp_path)
    target = package / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"public dependency")
    verifier.build_manifest(package)


@pytest.mark.parametrize(
    "relative",
    (
        Path("cache") / "uploaded-backup.bin",
        Path("data") / "uploads" / "material.pdf",
        Path("_internal") / "content" / "cache" / "catalog-backup.bin",
        Path("_internal") / "config" / "credentials.json",
    ),
)
def test_application_managed_lookalike_paths_still_fail(tmp_path: Path, relative: Path) -> None:
    package = _package(tmp_path)
    target = package / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("{}" if target.suffix == ".json" else "private fixture", encoding="utf-8")
    with pytest.raises(verifier.PackageError, match="private|database|secret"):
        verifier.build_manifest(package)


def test_nonempty_secret_config_is_rejected_without_echoing_value(tmp_path: Path) -> None:
    package = _package(tmp_path)
    secret = "actual-secret-value-never-print-this"
    (package / "config.json").write_text(json.dumps({"api_key": secret}), encoding="utf-8")
    with pytest.raises(verifier.PackageError) as caught:
        verifier.build_manifest(package)
    assert "config.json" in str(caught.value)
    assert secret not in str(caught.value)


def test_empty_or_example_secret_schema_config_is_allowed(tmp_path: Path) -> None:
    package = _package(tmp_path)
    (package / "config.json").write_text(
        json.dumps({"api_key": "", "access_token": None, "password": "YOUR_API_KEY"}),
        encoding="utf-8",
    )
    verifier.build_manifest(package)


@pytest.mark.parametrize(
    "relative",
    (
        Path("private") / "notes.txt",
        Path("data") / "uploads" / "material.pdf",
        Path("SELF-CHECK") / "__pycache__" / "verify_windows_package.cpython-313.pyc",
        Path("private.db"),
    ),
)
def test_private_stale_or_database_paths_fail(tmp_path: Path, relative: Path) -> None:
    package = _package(tmp_path)
    target = package / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(verifier.SQLITE_MAGIC if target.suffix == ".db" else b"private")
    with pytest.raises(verifier.PackageError, match="private|database|bytecode"):
        verifier.build_manifest(package)


def test_hardlink_escape_is_rejected(tmp_path: Path) -> None:
    package = _package(tmp_path)
    link = package / "copied.exe"
    try:
        os.link(package / "MentorFlow.exe", link)
    except OSError as exc:
        pytest.skip(f"hardlink privilege unavailable: {exc}")
    with pytest.raises(verifier.PackageError, match="hardlink"):
        verifier.build_manifest(package)


def test_symlink_escape_is_rejected_when_supported(tmp_path: Path) -> None:
    package = _package(tmp_path)
    link = package / "outside.txt"
    outside = tmp_path / "outside.txt"
    outside.write_text("not public", encoding="utf-8")
    try:
        link.symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"symlink privilege unavailable: {exc}")
    with pytest.raises(verifier.PackageError, match="symlink|reparse|outside"):
        verifier.build_manifest(package)


def test_fresh_install_report_is_read_only_and_clean(tmp_path: Path) -> None:
    if not Path("E:/Codex/Temp").is_dir():
        pytest.skip("E: disposable staging root unavailable")
    root = Path(tempfile.mkdtemp(prefix="mentorflow-privacy-test-", dir="E:/Codex/Temp"))
    try:
        package = root / "package"
        package.mkdir()
        data_root = root / "fresh"
        data_root.joinpath("data").mkdir(parents=True)
        for name in ("app.db", "mentorflow_v2.db"):
            connection = sqlite3.connect(data_root / "data" / name)
            connection.execute("CREATE TABLE workspaces (id TEXT)")
            connection.execute("CREATE TABLE orm_workspaces (id TEXT)")
            connection.commit()
            connection.close()
        report = verifier._fresh_install_report(data_root, package_root=package)
        assert report["clean"] is True
        assert all(value == 0 for value in report["counts"].values())
        (data_root / "sms_gateway.json").write_text("{}", encoding="utf-8")
        assert verifier._fresh_install_report(data_root, package_root=package)["clean"] is False
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_fresh_install_rejects_database_links_and_hardlinks(tmp_path: Path) -> None:
    if not Path("E:/Codex/Temp").is_dir():
        pytest.skip("E: disposable staging root unavailable")
    root = Path(tempfile.mkdtemp(prefix="mentorflow-link-test-", dir="E:/Codex/Temp"))
    try:
        package = root / "package"
        package.mkdir()
        data_root = root / "fresh"
        data_root.joinpath("data").mkdir(parents=True)
        source = root / "outside.db"
        connection = sqlite3.connect(source)
        connection.execute("CREATE TABLE credentials (id TEXT)")
        connection.close()
        linked = data_root / "data" / "app.db"
        try:
            linked.symlink_to(source)
        except OSError:
            try:
                os.link(source, linked)
            except OSError as exc:
                pytest.skip(f"link privilege unavailable: {exc}")
        with pytest.raises(verifier.PackageError, match="symlink|hardlink|outside|data path"):
            verifier._fresh_install_report(data_root, package_root=package)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_fresh_install_rejects_noncanonical_hardlink_text() -> None:
    if not Path("E:/Codex/Temp").is_dir():
        pytest.skip("E: disposable staging root unavailable")
    root = Path(tempfile.mkdtemp(prefix="mentorflow-text-link-test-", dir="E:/Codex/Temp"))
    try:
        package = root / "package"
        package.mkdir()
        data_root = root / "fresh" / "data"
        data_root.mkdir(parents=True)
        for name in ("app.db", "mentorflow_v2.db"):
            connection = sqlite3.connect(data_root / name)
            connection.execute("CREATE TABLE workspaces (id TEXT)")
            connection.commit()
            connection.close()
        source = root / "notes-source.txt"
        source.write_text("api_key=must-not-be-read", encoding="utf-8")
        linked = data_root / "notes.txt"
        try:
            os.link(source, linked)
        except OSError as exc:
            pytest.skip(f"hardlink privilege unavailable: {exc}")
        with pytest.raises(verifier.PackageError, match="hardlink"):
            verifier._fresh_install_report(data_root.parent, package_root=package)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_fresh_install_rejects_noncanonical_sqlite_database() -> None:
    if not Path("E:/Codex/Temp").is_dir():
        pytest.skip("E: disposable staging root unavailable")
    root = Path(tempfile.mkdtemp(prefix="mentorflow-extra-db-test-", dir="E:/Codex/Temp"))
    try:
        package = root / "package"
        package.mkdir()
        data_root = root / "fresh" / "data"
        data_root.mkdir(parents=True)
        for name in ("app.db", "mentorflow_v2.db"):
            connection = sqlite3.connect(data_root / name)
            connection.execute("CREATE TABLE workspaces (id TEXT)")
            connection.commit()
            connection.close()
        connection = sqlite3.connect(data_root / "extra.db")
        connection.execute("CREATE TABLE secrets (value TEXT)")
        connection.execute("INSERT INTO secrets VALUES ('opaque-secret')")
        connection.commit()
        connection.close()
        report = verifier._fresh_install_report(data_root.parent, package_root=package)
        assert report["clean"] is False
        assert any("noncanonical SQLite database" in error for error in report["errors"])
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_package_root_reparse_alias_is_rejected_when_supported() -> None:
    if not Path("E:/Codex/Temp").is_dir():
        pytest.skip("E: disposable staging root unavailable")
    root = Path(tempfile.mkdtemp(prefix="mentorflow-package-alias-test-", dir="E:/Codex/Temp"))
    try:
        package = _package(root)
        alias = root / "package-alias"
        try:
            alias.symlink_to(package, target_is_directory=True)
        except OSError as exc:
            pytest.skip(f"symlink privilege unavailable: {exc}")
        with pytest.raises(verifier.PackageError, match="symlink|reparse"):
            verifier.build_manifest(alias)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_distributable_cleanup_guard_accepts_missing_path_on_powershell_51() -> None:
    if not Path("E:/Codex/Temp").is_dir():
        pytest.skip("E: disposable staging root unavailable")
    root = Path(tempfile.mkdtemp(prefix="mentorflow-cleanup-missing-test-", dir="E:/Codex/Temp"))
    try:
        candidate = root / "generated" / "missing"
        result = _run_cleanup_guard(root, candidate)
        assert result.returncode == 0, result.stdout + result.stderr
        assert not candidate.exists()
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_distributable_cleanup_guard_refuses_reparse_path_when_supported() -> None:
    if not Path("E:/Codex/Temp").is_dir():
        pytest.skip("E: disposable staging root unavailable")
    root = Path(tempfile.mkdtemp(prefix="mentorflow-cleanup-reparse-test-", dir="E:/Codex/Temp"))
    alias = root / "generated-alias"
    target = root / "outside-target"
    try:
        target.mkdir()
        sentinel = target / "sentinel.txt"
        sentinel.write_text("must survive", encoding="utf-8")
        try:
            alias.symlink_to(target, target_is_directory=True)
        except OSError as exc:
            pytest.skip(f"symlink privilege unavailable: {exc}")
        result = _run_cleanup_guard(root, alias)
        combined = (result.stdout + result.stderr).lower()
        assert result.returncode != 0
        assert "reparse" in combined or "symlink" in combined
        assert sentinel.read_text(encoding="utf-8") == "must survive"
    finally:
        if alias.is_symlink():
            alias.unlink()
        shutil.rmtree(root, ignore_errors=True)


def test_fresh_install_rejects_nonempty_credential_table(tmp_path: Path) -> None:
    if not Path("E:/Codex/Temp").is_dir():
        pytest.skip("E: disposable staging root unavailable")
    root = Path(tempfile.mkdtemp(prefix="mentorflow-credential-test-", dir="E:/Codex/Temp"))
    try:
        package = root / "package"
        package.mkdir()
        data_root = root / "fresh" / "data"
        data_root.mkdir(parents=True)
        for name in ("app.db", "mentorflow_v2.db"):
            connection = sqlite3.connect(data_root / name)
            connection.execute("CREATE TABLE credentials (id TEXT)")
            connection.execute("INSERT INTO credentials VALUES ('opaque-ref')")
            connection.commit()
            connection.close()
        report = verifier._fresh_install_report(data_root.parent, package_root=package)
        assert report["clean"] is False
        assert any("credential table" in error for error in report["errors"])
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_fresh_install_rejects_retired_local_auth_state_tables() -> None:
    if not Path("E:/Codex/Temp").is_dir():
        pytest.skip("E: disposable staging root unavailable")
    root = Path(tempfile.mkdtemp(prefix="mentorflow-retired-auth-test-", dir="E:/Codex/Temp"))
    try:
        package = root / "package"
        package.mkdir()
        data_root = root / "fresh" / "data"
        data_root.mkdir(parents=True)
        connection = sqlite3.connect(data_root / "app.db")
        for table in (
            "orm_local_sessions",
            "orm_workspace_ownership",
            "orm_local_phone_challenges",
        ):
            connection.execute(f'CREATE TABLE "{table}" (id TEXT)')
            connection.execute(f'INSERT INTO "{table}" VALUES (?)', (table + "-historical-row",))
        connection.commit()
        connection.close()
        for name in ("mentorflow_v2.db",):
            sqlite3.connect(data_root / name).close()
        report = verifier._fresh_install_report(data_root.parent, package_root=package)
        assert report["clean"] is False
        assert report["counts"]["local_auth"] == 3
        assert any("local_auth=3" in error for error in report["errors"])
    finally:
        shutil.rmtree(root, ignore_errors=True)


class _FakeHttpResponse:
    def __init__(self, body: bytes, *, content_length: str | None = None) -> None:
        self._body = body
        self._offset = 0
        self.headers = {} if content_length is None else {"Content-Length": content_length}

    def __enter__(self) -> "_FakeHttpResponse":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self, size: int = -1) -> bytes:
        if size < 0:
            size = len(self._body) - self._offset
        chunk = self._body[self._offset : self._offset + size]
        self._offset += len(chunk)
        return chunk


def _update_fixture(root: Path) -> tuple[Path, dict[str, object], bytes]:
    version_file = root / "mentorflow-version.json"
    version_file.write_text(
        json.dumps(
            {
                "schema": verifier.VERSION_SCHEMA,
                "product": "MentorFlow",
                "version": "1.2.3",
                "update": {"owner": "owner", "repo": "repo"},
            }
        ),
        encoding="utf-8",
    )
    body = b"safe update archive"
    asset = {
        "name": "MentorFlow-2.0.0-win-x64.zip",
        "digest": "sha256:" + hashlib.sha256(body).hexdigest(),
        "browser_download_url": "https://github.com/owner/repo/releases/download/v2.0.0/MentorFlow-2.0.0-win-x64.zip",
        "size": len(body),
    }
    release = {"tag_name": "v2.0.0", "draft": False, "prerelease": False, "assets": [asset]}
    release_json = root / "release.json"
    release_json.write_text(json.dumps(release), encoding="utf-8")
    return version_file, release, body


def test_stage_update_rejects_negative_content_length(monkeypatch: pytest.MonkeyPatch) -> None:
    if not Path("E:/Codex/Temp").is_dir():
        pytest.skip("E: disposable staging root unavailable")
    root = Path(tempfile.mkdtemp(prefix="mentorflow-update-header-test-", dir="E:/Codex/Temp"))
    try:
        version_file, release, body = _update_fixture(root)
        release_json = root / "release.json"
        monkeypatch.setattr(
            verifier.urllib.request,
            "urlopen",
            lambda *args, **kwargs: _FakeHttpResponse(body, content_length="-1"),
        )
        with pytest.raises(verifier.PackageError, match="invalid Content-Length"):
            verifier.stage_update(
                version_file,
                owner="owner",
                repo="repo",
                release_json=release_json,
                staging_root=root / "staging",
                download=True,
            )
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_stage_update_rejects_reparse_staging_root_when_supported() -> None:
    if not Path("E:/Codex/Temp").is_dir():
        pytest.skip("E: disposable staging root unavailable")
    root = Path(tempfile.mkdtemp(prefix="mentorflow-update-root-alias-test-", dir="E:/Codex/Temp"))
    try:
        version_file, _, _ = _update_fixture(root)
        outside = root / "outside"
        outside.mkdir()
        alias = root / "staging-alias"
        try:
            alias.symlink_to(outside, target_is_directory=True)
        except OSError as exc:
            pytest.skip(f"symlink privilege unavailable: {exc}")
        with pytest.raises(verifier.PackageError, match="symlink|reparse"):
            verifier.stage_update(
                version_file,
                owner="owner",
                repo="repo",
                release_json=root / "release.json",
                staging_root=alias,
                download=True,
            )
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_stage_update_rejects_hardlinked_existing_asset() -> None:
    if not Path("E:/Codex/Temp").is_dir():
        pytest.skip("E: disposable staging root unavailable")
    root = Path(tempfile.mkdtemp(prefix="mentorflow-update-hardlink-test-", dir="E:/Codex/Temp"))
    try:
        version_file, _, body = _update_fixture(root)
        target_dir = root / "staging" / "2.0.0"
        target_dir.mkdir(parents=True)
        source = root / "outside.zip"
        source.write_bytes(body)
        destination = target_dir / "MentorFlow-2.0.0-win-x64.zip"
        try:
            os.link(source, destination)
        except OSError as exc:
            pytest.skip(f"hardlink privilege unavailable: {exc}")
        with pytest.raises(verifier.PackageError, match="hardlink"):
            verifier.stage_update(
                version_file,
                owner="owner",
                repo="repo",
                release_json=root / "release.json",
                staging_root=root / "staging",
                download=True,
            )
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_github_release_response_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    oversized = _FakeHttpResponse(b"{}", content_length=str(verifier.MAX_RELEASE_JSON_BYTES + 1))
    monkeypatch.setattr(verifier.urllib.request, "urlopen", lambda *args, **kwargs: oversized)
    with pytest.raises(verifier.PackageError, match="response limit"):
        verifier._github_release("owner", "repo")

    for malformed in ("-1", "", "not-a-number"):
        malformed_response = _FakeHttpResponse(b"{}", content_length=malformed)
        monkeypatch.setattr(verifier.urllib.request, "urlopen", lambda *args, **kwargs: malformed_response)
        with pytest.raises(verifier.PackageError, match="invalid Content-Length"):
            verifier._github_release("owner", "repo")

    streaming = _FakeHttpResponse(b"x" * (verifier.MAX_RELEASE_JSON_BYTES + 1))
    monkeypatch.setattr(verifier.urllib.request, "urlopen", lambda *args, **kwargs: streaming)
    with pytest.raises(verifier.PackageError, match="response limit"):
        verifier._github_release("owner", "repo")


def test_release_asset_size_is_bounded() -> None:
    asset = {
        "name": "MentorFlow-2.0.0-win-x64.zip",
        "digest": "sha256:" + "a" * 64,
        "browser_download_url": "https://github.com/example/mentorflow/releases/download/v2.0.0/MentorFlow-2.0.0-win-x64.zip",
        "size": verifier.MAX_UPDATE_DOWNLOAD_BYTES + 1,
    }
    with pytest.raises(verifier.PackageError, match="download limit"):
        verifier._find_release_asset(
            {"assets": [asset]},
            version="2.0.0",
            pattern="MentorFlow-{version}-win-x64.zip",
        )
