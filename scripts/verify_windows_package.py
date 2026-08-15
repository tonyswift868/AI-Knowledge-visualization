"""Build and verify a MentorFlow Windows package.

This module intentionally uses only the Python standard library.  The copy in
``SELF-CHECK`` is safe to run from a shipped package (or from the source tree)
and never opens a user database for writing.  Update downloads are opt-in and
are staged under E: so an archive can be reviewed and installed manually.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
VERSION_FILE_NAME = "mentorflow-version.json"
MANIFEST_RELATIVE = Path("SELF-CHECK") / "package-manifest.json"
MANIFEST_SCHEMA = "mentorflow.windows-package-manifest.v1"
VERSION_SCHEMA = "mentorflow.version.v1"
VERSION_RE = re.compile(r"^(?P<major>0|[1-9]\d*)\.(?P<minor>0|[1-9]\d*)\.(?P<patch>0|[1-9]\d*)(?:-(?P<pre>[0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
USER_DATA_NAMES = {
    "app.db",
    "mentorflow_v2.db",
    "minimax_api_key.txt",
    "ui-review-manifest.json",
    "preset_settings.json",
}
# Read-only catalogs shipped as application assets are not user data.  Keep
# this allowlist exact: any other SQLite file in the distributable remains a
# hard failure.
PACKAGED_STATIC_DATABASES = {
    "_internal/content/narrative_catalog/v2/narrative_catalog_v2.sqlite",
}


class PackageError(RuntimeError):
    """An actionable package/update validation error."""


def _json_load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PackageError(f"cannot read JSON manifest: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise PackageError(f"JSON manifest must be an object: {path}")
    return value


def load_version(path: Path) -> dict[str, Any]:
    payload = _json_load(path)
    if payload.get("schema") != VERSION_SCHEMA:
        raise PackageError(f"unsupported version manifest schema in {path}")
    if payload.get("product") != "MentorFlow":
        raise PackageError(f"version manifest product is not MentorFlow: {path}")
    version = str(payload.get("version") or "")
    if not VERSION_RE.fullmatch(version):
        raise PackageError(f"invalid MentorFlow version: {version!r}")
    update = payload.get("update")
    if update is not None and not isinstance(update, dict):
        raise PackageError("version manifest update settings must be an object")
    return payload


def sha256_file(path: Path, *, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(chunk_size), b""):
                digest.update(chunk)
    except OSError as exc:
        raise PackageError(f"cannot hash {path}: {exc}") from exc
    return digest.hexdigest()


def _relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(root.resolve(strict=False))
    except ValueError:
        return False
    return True


def _ensure_e_drive(path: Path, *, label: str) -> None:
    """Reject accidental C: staging on Windows while remaining testable elsewhere."""
    if os.name == "nt" and path.drive and path.drive.upper() != "E:":
        raise PackageError(f"{label} must be on E: (got {path})")


def _find_user_data_files(package_root: Path) -> list[str]:
    found: list[str] = []
    for candidate in package_root.rglob("*"):
        if not candidate.is_file():
            continue
        if candidate.name.casefold() in USER_DATA_NAMES:
            found.append(_relative(candidate, package_root))
        relative = _relative(candidate, package_root)
        if (
            candidate.suffix.casefold() in {".db", ".sqlite", ".sqlite3"}
            and relative not in PACKAGED_STATIC_DATABASES
        ):
            found.append(relative)
    return sorted(set(found))


def _iter_package_files(package_root: Path, manifest_path: Path) -> Iterable[Path]:
    manifest_resolved = manifest_path.resolve(strict=False)
    for candidate in sorted(package_root.rglob("*")):
        if candidate.is_file() and candidate.resolve(strict=False) != manifest_resolved:
            yield candidate


def build_manifest(package_root: Path, *, version_file: Path | None = None) -> Path:
    package_root = package_root.resolve()
    if not package_root.is_dir():
        raise PackageError(f"package root does not exist: {package_root}")
    version_path = version_file or package_root / VERSION_FILE_NAME
    version = load_version(version_path)
    user_data = _find_user_data_files(package_root)
    if user_data:
        raise PackageError("package contains user data or database files: " + ", ".join(user_data))
    app = package_root / "MentorFlow.exe"
    if not app.is_file():
        raise PackageError(f"frozen executable is missing: {app}")
    manifest_path = package_root / MANIFEST_RELATIVE
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    files = []
    for candidate in _iter_package_files(package_root, manifest_path):
        files.append(
            {
                "path": _relative(candidate, package_root),
                "size": candidate.stat().st_size,
                "sha256": sha256_file(candidate),
            }
        )
    files.sort(key=lambda item: item["path"])
    payload = {
        "schema": MANIFEST_SCHEMA,
        "product": "MentorFlow",
        "version": version["version"],
        "executable": "MentorFlow.exe",
        "files": files,
    }
    temporary = manifest_path.with_suffix(".json.partial")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(manifest_path)
    return manifest_path


def verify_package(package_root: Path, *, data_root: Path | None, check_data: bool, smoke: bool, smoke_seconds: float) -> dict[str, Any]:
    package_root = package_root.resolve()
    manifest_path = package_root / MANIFEST_RELATIVE
    if not manifest_path.is_file():
        raise PackageError(f"package manifest is missing: {manifest_path}")
    manifest = _json_load(manifest_path)
    if manifest.get("schema") != MANIFEST_SCHEMA or manifest.get("product") != "MentorFlow":
        raise PackageError(f"unsupported package manifest: {manifest_path}")
    version_path = package_root / VERSION_FILE_NAME
    version = load_version(version_path)
    if manifest.get("version") != version["version"]:
        raise PackageError(
            f"package version mismatch: manifest={manifest.get('version')!r}, version={version['version']!r}"
        )
    required = (
        Path("MentorFlow.exe"),
        Path(VERSION_FILE_NAME),
        MANIFEST_RELATIVE,
        Path("SELF-CHECK") / "MentorFlowSelfCheck.exe",
        Path("SELF-CHECK") / "verify_windows_package.py",
        Path("SELF-CHECK") / "mentorflow-check.bat",
        Path("SELF-CHECK") / "mentorflow-update.bat",
    )
    missing = [str(path) for path in required if not (package_root / path).is_file()]
    if missing:
        raise PackageError("required package files are missing: " + ", ".join(missing))
    user_data = _find_user_data_files(package_root)
    if user_data:
        raise PackageError("package contains user data or database files: " + ", ".join(user_data))
    mismatches: list[str] = []
    entries = manifest.get("files")
    if not isinstance(entries, list):
        raise PackageError("package manifest files must be a list")
    listed_paths: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
            raise PackageError("package manifest has an invalid file entry")
        relative = Path(entry["path"])
        if relative.is_absolute() or ".." in relative.parts:
            raise PackageError(f"unsafe package manifest path: {entry['path']}")
        listed_paths.add(relative.as_posix())
        candidate = package_root / relative
        if not candidate.is_file():
            mismatches.append(f"missing:{entry['path']}")
            continue
        expected = str(entry.get("sha256") or "").lower()
        if not SHA256_RE.fullmatch(expected):
            raise PackageError(f"invalid SHA-256 in package manifest: {entry['path']}")
        actual = sha256_file(candidate)
        if actual != expected:
            mismatches.append(f"tampered:{entry['path']}")
    actual_paths = {
        _relative(candidate, package_root)
        for candidate in _iter_package_files(package_root, manifest_path)
    }
    for extra in sorted(actual_paths - listed_paths):
        mismatches.append(f"unexpected:{extra}")
    if mismatches:
        raise PackageError("package integrity check failed: " + ", ".join(mismatches))
    data_report: dict[str, Any] = {"databases": 0, "errors": []}
    if check_data:
        data_report = check_sqlite_root(data_root or Path(r"E:\MentorFlowData"), package_root=package_root)
        if data_report["errors"]:
            raise PackageError("SQLite integrity check failed: " + "; ".join(data_report["errors"]))
    smoke_report: dict[str, Any] = {"checked": False}
    if smoke:
        smoke_report = smoke_launch(package_root / "MentorFlow.exe", package_root, seconds=smoke_seconds)
        if not smoke_report.get("ok"):
            raise PackageError(str(smoke_report.get("error") or "MentorFlow startup smoke failed"))
    return {
        "version": version["version"],
        "files": len(entries),
        "data": data_report,
        "smoke": smoke_report,
    }


def check_sqlite_root(data_root: Path, *, package_root: Path | None = None) -> dict[str, Any]:
    data_root = data_root.expanduser().resolve()
    if package_root and _is_within(data_root, package_root):
        raise PackageError("data root must not be inside the installer/package directory")
    _ensure_e_drive(data_root, label="data root")
    report: dict[str, Any] = {"root": str(data_root), "databases": 0, "errors": []}
    if not data_root.exists():
        return report
    for candidate in sorted(data_root.rglob("*.db")) + sorted(data_root.rglob("*.sqlite")) + sorted(data_root.rglob("*.sqlite3")):
        if not candidate.is_file() or candidate.name.endswith(("-wal", "-shm")):
            continue
        report["databases"] += 1
        try:
            connection = sqlite3.connect(candidate.resolve().as_uri() + "?mode=ro", uri=True)
            try:
                result = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
                if result.lower() != "ok":
                    report["errors"].append(f"{candidate}: integrity_check={result}")
                foreign = connection.execute("PRAGMA foreign_key_check").fetchall()
                if foreign:
                    report["errors"].append(f"{candidate}: foreign_key_check={len(foreign)}")
            finally:
                connection.close()
        except (OSError, sqlite3.Error) as exc:
            report["errors"].append(f"{candidate}: {exc}")
    return report


def smoke_launch(executable: Path, package_root: Path, *, seconds: float = 5.0) -> dict[str, Any]:
    """Launch using a disposable E: data root and await a clean smoke exit.

    The canonical user root is never passed to the child.  ``desktop.main``
    honours ``MENTORFLOW_DESKTOP_SMOKE_SECONDS`` and exits cleanly after its
    local health check.  A merely alive process is not a successful smoke test;
    a forced terminate is only a last-resort cleanup after a timeout.
    """
    if not executable.is_file():
        return {"ok": False, "error": f"executable is missing: {executable}"}
    staging = Path(os.getenv("MENTORFLOW_UPDATE_STAGING", r"E:\MentorFlowUpdates"))
    _ensure_e_drive(staging, label="smoke staging root")
    staging.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix="mentorflow-package-smoke-", dir=str(staging)))
    environment = os.environ.copy()
    environment.update(
        {
            "MENTORFLOW_USER_DATA_ROOT": str(temporary),
            "MENTORFLOW_LOCAL_DATA_MODE": "1",
            "MENTORFLOW_AUTH_REQUIRED": "0",
            "MENTORFLOW_DESKTOP_SMOKE_SECONDS": str(max(1.0, seconds)),
            "MENTORFLOW_DISABLE_PROVIDER_AUTORESTORE": "1",
            "MENTORFLOW_UI_REVIEW_MODE": "0",
        }
    )
    process: subprocess.Popen[bytes] | None = None
    started = time.monotonic()
    try:
        process = subprocess.Popen(
            [str(executable)],
            cwd=str(package_root),
            env=environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        # The native application owns the smoke duration and exits after its
        # health-controlled check.  Give it a bounded startup margin, but do
        # not treat a process that remains alive as success.
        result = process.wait(timeout=max(10.0, seconds + 8.0))
        if result == 0:
            return {"ok": True, "returncode": result, "elapsed": round(time.monotonic() - started, 2)}
        stderr = (process.stderr.read(4096) if process.stderr else b"").decode(errors="replace").strip()
        return {"ok": False, "error": f"MentorFlow exited with code {result}: {stderr[-500:]}"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "MentorFlow did not complete its health-aware smoke exit before timeout"}
    except OSError as exc:
        return {"ok": False, "error": f"cannot launch MentorFlow: {exc}"}
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        shutil.rmtree(temporary, ignore_errors=True)


def _version_key(version: str) -> tuple[int, int, int, int, str]:
    match = VERSION_RE.fullmatch(version)
    if not match:
        raise PackageError(f"release tag is not a supported SemVer: {version}")
    pre = match.group("pre") or ""
    return (int(match.group("major")), int(match.group("minor")), int(match.group("patch")), 0 if pre else 1, pre)


def _release_version(tag: str) -> str:
    value = str(tag or "").strip()
    if value.lower().startswith("v"):
        value = value[1:]
    if not VERSION_RE.fullmatch(value):
        raise PackageError(f"GitHub release tag is not a supported SemVer: {tag!r}")
    return value


def _github_release(owner: str, repo: str) -> dict[str, Any]:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", owner) or not re.fullmatch(r"[A-Za-z0-9_.-]+", repo):
        raise PackageError("GitHub owner/repo must contain only letters, digits, '.', '_' or '-'")
    url = f"https://api.github.com/repos/{owner}/{repo}/releases/latest"
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/vnd.github+json", "User-Agent": "MentorFlow-update-checker/1"},
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        raise PackageError(f"GitHub Releases API request failed: {exc}") from exc
    if not isinstance(payload, dict):
        raise PackageError("GitHub Releases API returned a non-object response")
    return payload


def _find_release_asset(release: dict[str, Any], *, version: str, pattern: str) -> dict[str, Any]:
    assets = release.get("assets")
    if not isinstance(assets, list):
        raise PackageError("GitHub release has no assets list")
    expected_name = pattern.format(version=version, tag=release.get("tag_name", ""))
    candidates = [asset for asset in assets if isinstance(asset, dict) and asset.get("name") == expected_name]
    if len(candidates) != 1:
        raise PackageError(f"required release asset is missing or ambiguous: {expected_name}")
    asset = candidates[0]
    digest = str(asset.get("digest") or "").strip().lower()
    if not digest.startswith("sha256:") or not SHA256_RE.fullmatch(digest[7:]):
        raise PackageError(f"release asset {expected_name} is missing a valid SHA-256 digest")
    if not str(asset.get("browser_download_url") or "").startswith("https://github.com/"):
        raise PackageError(f"release asset {expected_name} has no official GitHub download URL")
    return asset


def stage_update(version_file: Path, *, owner: str | None, repo: str | None, release_json: Path | None, staging_root: Path | None, download: bool) -> dict[str, Any]:
    current = load_version(version_file)
    config = current.get("update") if isinstance(current.get("update"), dict) else {}
    owner_value = (owner or os.getenv("MENTORFLOW_GITHUB_OWNER") or config.get("owner") or "").strip()
    repo_value = (repo or os.getenv("MENTORFLOW_GITHUB_REPO") or config.get("repo") or "").strip()
    if not owner_value or not repo_value or owner_value == "example-owner":
        raise PackageError("configure a real GitHub owner/repo before checking for updates")
    release = _json_load(release_json) if release_json else _github_release(owner_value, repo_value)
    if release.get("draft") or release.get("prerelease"):
        raise PackageError("latest release is draft/prerelease; refusing automatic staging")
    release_version = _release_version(str(release.get("tag_name") or ""))
    if _version_key(release_version) <= _version_key(str(current["version"])):
        return {"update": False, "current": current["version"], "latest": release_version}
    pattern = str(config.get("asset_name_pattern") or "MentorFlow-{version}-win-x64.zip")
    asset = _find_release_asset(release, version=release_version, pattern=pattern)
    digest = str(asset["digest"])[7:].lower()
    result: dict[str, Any] = {
        "update": True,
        "current": current["version"],
        "latest": release_version,
        "release_page": str(release.get("html_url") or ""),
        "asset": str(asset.get("name")),
        "sha256": digest,
    }
    if not download:
        result["action"] = "open release page and manually install after review"
        return result
    target_root = (staging_root or Path(str(config.get("staging_root") or r"E:\MentorFlowUpdates"))).expanduser().resolve()
    _ensure_e_drive(target_root, label="update staging root")
    asset_name = Path(str(asset.get("name"))).name
    if asset_name != str(asset.get("name")) or asset_name in {"", ".", ".."}:
        raise PackageError("release asset name is unsafe")
    target_dir = target_root / release_version
    target_dir.mkdir(parents=True, exist_ok=True)
    destination = target_dir / asset_name
    if destination.exists():
        if sha256_file(destination) != digest:
            raise PackageError(f"existing staged asset checksum mismatch; refusing overwrite: {destination}")
    else:
        temporary = destination.with_suffix(destination.suffix + ".partial")
        if temporary.exists():
            temporary.unlink()
        request = urllib.request.Request(
            str(asset["browser_download_url"]),
            headers={"Accept": "application/octet-stream", "User-Agent": "MentorFlow-update-checker/1"},
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response, temporary.open("wb") as output:
                shutil.copyfileobj(response, output, length=1024 * 1024)
        except (OSError, urllib.error.URLError) as exc:
            temporary.unlink(missing_ok=True)
            raise PackageError(f"release asset download failed: {exc}") from exc
        if sha256_file(temporary) != digest:
            temporary.unlink(missing_ok=True)
            raise PackageError("downloaded release asset SHA-256 does not match GitHub digest")
        temporary.replace(destination)
    metadata = {
        "schema": "mentorflow.update-staging.v1",
        "product": "MentorFlow",
        "current_version": current["version"],
        "release_version": release_version,
        "release_page": str(release.get("html_url") or ""),
        "asset": asset_name,
        "sha256": digest,
        "staged_path": str(destination),
        "staged_at": datetime.now(timezone.utc).isoformat(),
        "install": "Close MentorFlow, review this archive, then manually install. User data is never replaced by this tool.",
    }
    metadata_path = target_dir / "update-staging.json"
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    result["staged_path"] = str(destination)
    result["metadata"] = str(metadata_path)
    result["action"] = "review staged archive and manually install; no files were replaced"
    return result


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="MentorFlow Windows package integrity and update helper")
    subparsers = parser.add_subparsers(dest="command", required=True)
    build = subparsers.add_parser("build-manifest", help="write SELF-CHECK/package-manifest.json")
    build.add_argument("--package-root", type=Path, required=True)
    build.add_argument("--version-file", type=Path)
    verify = subparsers.add_parser("verify", help="verify package files, data databases and optional startup")
    verify.add_argument("--package-root", type=Path, required=True)
    verify.add_argument("--data-root", type=Path)
    verify.add_argument("--no-data-check", action="store_true")
    verify.add_argument("--no-smoke", action="store_true")
    verify.add_argument("--smoke-seconds", type=float, default=5.0)
    update = subparsers.add_parser("check-update", help="check and optionally stage a GitHub release")
    update.add_argument("--version-file", type=Path)
    update.add_argument("--owner")
    update.add_argument("--repo")
    update.add_argument("--release-json", type=Path, help="offline API response fixture for tests/review")
    update.add_argument("--staging-root", type=Path)
    update.add_argument("--download", action="store_true", help="download to E: staging; never installs")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        if args.command == "build-manifest":
            path = build_manifest(args.package_root, version_file=args.version_file)
            print(f"PACKAGE_MANIFEST={path}")
            return 0
        if args.command == "verify":
            report = verify_package(
                args.package_root,
                data_root=args.data_root,
                check_data=not args.no_data_check,
                smoke=not args.no_smoke,
                smoke_seconds=args.smoke_seconds,
            )
            print(json.dumps(report, ensure_ascii=False, indent=2))
            print("MENTORFLOW_PACKAGE_CHECK=PASS")
            return 0
        version_file = args.version_file or ROOT / VERSION_FILE_NAME
        report = stage_update(
            version_file,
            owner=args.owner,
            repo=args.repo,
            release_json=args.release_json,
            staging_root=args.staging_root,
            download=args.download,
        )
        print(json.dumps(report, ensure_ascii=False, indent=2))
        print("MENTORFLOW_UPDATE_CHECK=PASS")
        return 0
    except PackageError as exc:
        print(f"MENTORFLOW_CHECK_ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
