"""Runtime paths for the first-stage MentorFlow account shell."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_ROOT = Path(r"D:\MentorFlowData\account-shell")
CACHE_ROOT = DATA_ROOT / "cache"
SHARED_CACHE_ROOT = PROJECT_ROOT / ".local" / "cache"
RESOURCE_ROOT = Path(__file__).resolve().parent / "resources"
LIFECYCLE_ROOT = RESOURCE_ROOT / "account-lifecycle"
PUBLIC_RUNTIME_CONFIG = RESOURCE_ROOT / "public-runtime.json"
ACCOUNT_API_TRUST = RESOURCE_ROOT / "trust" / "account-api.pem"

_RUNTIME_CONFIG_KEYS = frozenset(
    {
        "MENTORFLOW_DESKTOP_OIDC_ISSUER",
        "MENTORFLOW_DESKTOP_IDENTITY_ISSUER",
        "MENTORFLOW_DESKTOP_OIDC_CLIENT_ID",
        "MENTORFLOW_DESKTOP_OIDC_AUDIENCE",
        "MENTORFLOW_ENTERPRISE_API_BASE_URL",
    }
)


@dataclass(frozen=True, slots=True)
class ShellRuntime:
    data_root: Path
    cache_root: Path
    lifecycle_root: Path
    ca_bundle: Path

    def configure_web_profile(self, profile: object) -> None:
        """Bind a WebEngine profile's persistent storage and cache to D."""
        storage_root = self.data_root / "webengine" / "storage"
        profile_cache = self.cache_root / "webengine"
        storage_root.mkdir(parents=True, exist_ok=True)
        profile_cache.mkdir(parents=True, exist_ok=True)
        profile.setPersistentStoragePath(str(storage_root))
        profile.setCachePath(str(profile_cache))


def _public_runtime_config() -> dict[str, str]:
    try:
        value = json.loads(PUBLIC_RUNTIME_CONFIG.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError("MentorFlow public runtime configuration is unavailable") from exc
    if not isinstance(value, dict) or not _RUNTIME_CONFIG_KEYS.issubset(value):
        raise RuntimeError("MentorFlow public runtime configuration is incomplete")
    config = {key: value[key] for key in _RUNTIME_CONFIG_KEYS}
    if any(not isinstance(item, str) or not item.strip() for item in config.values()):
        raise RuntimeError("MentorFlow public runtime configuration is invalid")
    return config


def _write_if_changed(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        if path.read_bytes() == content:
            return
    except FileNotFoundError:
        pass
    with tempfile.NamedTemporaryFile(
        mode="wb", prefix=f".{path.name}.", suffix=".tmp", dir=path.parent, delete=False
    ) as target:
        temporary_path = Path(target.name)
        target.write(content)
        target.flush()
        os.fsync(target.fileno())
    try:
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def _build_ca_bundle() -> Path:
    try:
        import certifi

        roots = Path(certifi.where()).read_bytes()
        account_api = ACCOUNT_API_TRUST.read_bytes()
    except (ImportError, OSError) as exc:
        raise RuntimeError("MentorFlow account API trust inputs are unavailable") from exc
    if b"-----BEGIN CERTIFICATE-----" not in account_api:
        raise RuntimeError("MentorFlow account API trust input is not a PEM certificate")
    bundle = SHARED_CACHE_ROOT / "trust" / "account-api-ca.pem"
    _write_if_changed(bundle, roots.rstrip() + b"\n" + account_api.strip() + b"\n")
    return bundle


def prepare_shell_runtime() -> ShellRuntime:
    """Prepare the D-drive account-shell runtime before creating QApplication."""
    data_root = DATA_ROOT
    cache_root = CACHE_ROOT
    local_root = data_root / "local"
    roaming_root = data_root / "roaming"
    config_root = data_root / "config"
    temp_root = data_root / "temp"
    for path in (data_root, cache_root, local_root, roaming_root, config_root, temp_root):
        path.mkdir(parents=True, exist_ok=True)

    os.environ.update(_public_runtime_config())
    os.environ.update(
        APPDATA=str(roaming_root),
        LOCALAPPDATA=str(local_root),
        TEMP=str(temp_root),
        TMP=str(temp_root),
        TMPDIR=str(temp_root),
        XDG_CACHE_HOME=str(cache_root),
        XDG_CONFIG_HOME=str(config_root),
        XDG_DATA_HOME=str(data_root / "data"),
        QT_DISABLE_SHADER_DISK_CACHE="1",
        PYTHONDONTWRITEBYTECODE="1",
    )
    tempfile.tempdir = str(temp_root)

    bundle = _build_ca_bundle()
    os.environ["SSL_CERT_FILE"] = str(bundle)
    os.environ["REQUESTS_CA_BUNDLE"] = str(bundle)
    return ShellRuntime(
        data_root=data_root,
        cache_root=cache_root,
        lifecycle_root=LIFECYCLE_ROOT,
        ca_bundle=bundle,
    )
