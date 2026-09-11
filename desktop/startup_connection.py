"""Bounded hidden preview transport for the protected GUI entry.

No UI, account operation, provider override or server mutation. Close owns only
process handles created here, including a spawn racing with cancellation.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
import subprocess
import threading
import time

from scripts.windows_installed_launch import ISSUER, CA_SHA, SPKI, clean_environment, read_profile


def prepare_environment(root: Path, *, per_user=False):
    from scripts import verify_windows_package as v
    from scripts import windows_install_trust as trust
    from scripts.per_user_install import app_path
    certificate = (app_path(root) if per_user else root / 'App') / 'Config/edge-tls-ca.crt'
    if per_user:
        from scripts.per_user_install import plain
        plain(certificate)
    else:
        trust.assert_safe_parents(certificate)
        trust.assert_protected(certificate)
    if v.sha256_file(certificate) != CA_SHA:
        raise trust.RepairRequired("TLS certificate pin mismatch")
    from scripts.install_product import configured
    product=configured()
    if product.uat:
        from mentorflow_delivery_authority import DATA_ROOT, WORK_ROOT
        vault=Path(DATA_ROOT).absolute()
        if not vault.is_relative_to(Path(WORK_ROOT).absolute()):
            raise ValueError('UAT data must remain within compiled isolated work root')
        product.validate_root(vault)
    else:
        local = Path(os.environ["LOCALAPPDATA"]) / "MentorFlow"
        profile = read_profile(local / "Runtime/launch.json")
        vault = local / "Accounts" / profile
    temp = vault / "runtime-temp"
    v._validate_existing_parent_chain(temp, label="external runtime")
    temp.mkdir(parents=True, exist_ok=True)
    env = clean_environment(temp)
    env.update({
        "MENTORFLOW_ACCOUNT_VAULT_ROOT": str(vault), "MENTORFLOW_DATA_DIR": str(vault),
        "MENTORFLOW_USER_DATA_ROOT": str(vault / "prelaunch"),
        "MENTORFLOW_DB": str(vault / "prelaunch/data/app.db"),
        "MENTORFLOW_ORM_DB": str(vault / "prelaunch/data/mentorflow_v2.db"),
        "MENTORFLOW_DESKTOP_FORCE_LOGIN_GATE": "1", "MENTORFLOW_DISABLE_PROVIDER_AUTORESTORE": "1",
        "MENTORFLOW_DESKTOP_OIDC_ISSUER": ISSUER,
        "MENTORFLOW_DESKTOP_OIDC_CLIENT_ID": "mentorflow-desktop",
        "MENTORFLOW_DESKTOP_OIDC_AUDIENCE": "mentorflow-api",
        "MENTORFLOW_ENTERPRISE_API_BASE_URL": "https://localhost:18080",
        "MENTORFLOW_DESKTOP_OIDC_CALLBACK_PORT": "0",
        "SSL_CERT_FILE": str(certificate), "REQUESTS_CA_BUNDLE": str(certificate),
        "QTWEBENGINE_CHROMIUM_FLAGS": "--ignore-certificate-errors-spki-list=" + SPKI,
    })
    from desktop.enterprise_oidc import bind_auth_authority
    from desktop.version import VERSION_MANIFEST
    return bind_auth_authority(env, VERSION_MANIFEST.get('auth_authority')), certificate


def preview_discovery_issuer_allowed(value, env=None):
    """The old API tunnel remains independent of the new public auth frontend."""
    from desktop.enterprise_oidc import DesktopOidcConfig, bind_auth_authority
    from desktop.version import VERSION_MANIFEST
    source = {'MENTORFLOW_DESKTOP_OIDC_ISSUER': ISSUER,
              'MENTORFLOW_DESKTOP_OIDC_CLIENT_ID': 'mentorflow-desktop',
              'MENTORFLOW_DESKTOP_OIDC_AUDIENCE': 'mentorflow-api',
              'MENTORFLOW_ENTERPRISE_API_BASE_URL': 'https://localhost:18080'}
    source.update(env or {})
    config = DesktopOidcConfig.from_env(bind_auth_authority(source, VERSION_MANIFEST.get('auth_authority')))
    return config.accepts_issuer(value)


class ConnectionPreparation:
    def __init__(self, env, certificate):
        self.env = env
        self.certificate = certificate
        self.system = Path(env["SystemRoot"]) / "System32"
        self.cancelled = threading.Event()
        self._lock = threading.Lock()
        self._owned = []

    def endpoint_ready(self):
        if self.cancelled.is_set():
            return False
        process = None
        try:
            with self._lock:
                if self.cancelled.is_set():
                    return False
                process = subprocess.Popen([
                    str(self.system / "curl.exe"), "--silent", "--fail", "--noproxy", "*",
                    "--connect-timeout", "2", "--max-time", "4", "--cacert", str(self.certificate),
                    ISSUER + "/.well-known/openid-configuration",
                ], env=self.env, cwd=self.system, stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    creationflags=subprocess.CREATE_NO_WINDOW)
                self._owned.append(process)
            output, _ = process.communicate(timeout=5)
            document = json.loads(output) if process.returncode == 0 else None
            return (isinstance(document, dict) and preview_discovery_issuer_allowed(document.get('issuer'), self.env)
                    and not self.cancelled.is_set())
        except (OSError, ValueError, subprocess.TimeoutExpired):
            return False
        finally:
            if process is not None:
                self._stop(process)
                with self._lock:
                    if process in self._owned:
                        self._owned.remove(process)

    def connect(self):
        from scripts.install_product import configured
        if configured().uat:
            # Delivery testing needs the real pre-login application and update
            # UI, not a server/account session. Do not probe OIDC, read SSH keys
            # or create a preview tunnel in this compiled isolated product.
            return False
        if self.endpoint_ready():
            return True
        if self.cancelled.is_set():
            return False
        key = Path(os.environ['LOCALAPPDATA']) / 'MentorFlow/Runtime/preview-ssh.key'
        if not key.is_file():
            return False
        from scripts import verify_windows_package as v
        v._validate_existing_parent_chain(key.parent, label="preview transport key")
        v._validate_staging_entry(key, key.parent, directory=False, allow_missing=False)
        with self._lock:
            if self.cancelled.is_set():
                return False
            process = subprocess.Popen([
                str(self.system / "OpenSSH/ssh.exe"), "-N", "-T", "-i", str(key),
                "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
                "-o", "ExitOnForwardFailure=yes", "-o", "ConnectTimeout=8",
                "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=2",
                "-L", "127.0.0.1:18080:127.0.0.1:443", "mentorflow-admin@89.45.45.28",
            ], env=self.env, cwd=self.system, stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW)
            self._owned.append(process)
        deadline = time.monotonic() + 12
        while not self.cancelled.is_set() and time.monotonic() < deadline:
            if self.endpoint_ready():
                return True
            if process.poll() is not None:
                break
            self.cancelled.wait(.2)
        return False

    def close(self):
        self.cancelled.set()
        with self._lock:
            owned, self._owned = self._owned, []
        for process in owned:
            self._stop(process)

    @staticmethod
    def _stop(process):
        try:
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        except ProcessLookupError:
            pass
