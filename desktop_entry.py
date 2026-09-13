"""PyInstaller entry point that preserves the desktop package context."""

import json
import os
import sys
from pathlib import Path


def apply_account_core_package_config() -> None:
    """Load this distribution's non-secret startup config before app imports.

    Other distributions without this sidecar retain their existing launch mode.
    The Account Core UAT distribution ships it so double-clicking the EXE has
    the same isolated login-first configuration as its PowerShell launcher.
    """
    if not getattr(sys, "frozen", False):
        return
    package = Path(sys.executable).resolve().parent
    config_file = package / "account-core-launch.json"
    if not config_file.is_file():
        return
    config = json.loads(config_file.read_text(encoding="utf-8"))
    allowed = {
        "MENTORFLOW_ACCOUNT_VAULT_ROOT", "MENTORFLOW_DATA_DIR",
        "MENTORFLOW_DESKTOP_OIDC_ISSUER", "MENTORFLOW_DESKTOP_OIDC_CLIENT_ID",
        "MENTORFLOW_DESKTOP_OIDC_AUDIENCE", "MENTORFLOW_ENTERPRISE_API_BASE_URL",
        "MENTORFLOW_DESKTOP_ORGANIZATION_ID", "MENTORFLOW_DESKTOP_WORKSPACE_ID",
        "MENTORFLOW_DESKTOP_OIDC_CALLBACK_PORT", "QTWEBENGINE_CHROMIUM_FLAGS",
        "MENTORFLOW_ACCOUNT_STORAGE_TEST_ONLY",
    }
    values = config.get("environment")
    required = {
        "MENTORFLOW_ACCOUNT_VAULT_ROOT", "MENTORFLOW_DATA_DIR",
        "MENTORFLOW_DESKTOP_OIDC_ISSUER", "MENTORFLOW_DESKTOP_OIDC_CLIENT_ID",
        "MENTORFLOW_DESKTOP_OIDC_AUDIENCE", "MENTORFLOW_ENTERPRISE_API_BASE_URL",
    }
    if not isinstance(values, dict) or set(values) - allowed or required - set(values):
        raise RuntimeError("Invalid Account Core package configuration")
    for key, value in values.items():
        if not isinstance(value, str) or not value.strip():
            raise RuntimeError("Invalid Account Core package configuration value")
        if key == 'MENTORFLOW_ACCOUNT_STORAGE_TEST_ONLY' and value not in {'0', '1'}:
            raise RuntimeError('Invalid Account Core storage test marker')
        os.environ.setdefault(key, value)
    from desktop.account_storage import device_storage_root, prelaunch_storage_environment
    vault = device_storage_root()
    # A package configured for Account Core never opens an inherited Personal
    # Local database before an authenticated profile replaces these paths.
    os.environ.update(prelaunch_storage_environment(vault))
    os.environ["MENTORFLOW_DESKTOP_FORCE_LOGIN_GATE"] = "1"
    os.environ["MENTORFLOW_DISABLE_PROVIDER_AUTORESTORE"] = "1"
    os.environ.pop("MENTORFLOW_DEPLOYMENT_MODE", None)
    certificate = package.parent / "Config" / "edge-tls-ca.crt"
    if not certificate.is_file():
        raise RuntimeError("Account Core TLS certificate is missing")
    os.environ["SSL_CERT_FILE"] = str(certificate)
    os.environ["REQUESTS_CA_BUNDLE"] = str(certificate)


apply_account_core_package_config()

from desktop.main import main


if __name__ == "__main__":
    raise SystemExit(main())
