"""Windowless installed launch; pinned existing preview transport, no PowerShell.

This module has no Qt/application imports. The protected gate must pass before
MentorFlow.exe executes. Transport failure is not authentication success.
"""
import ctypes
import json
import os
from pathlib import Path
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor

ISSUER = "https://localhost:18080/realms/mentorflow"
CA_SHA = "ef9ea28e93e89c83b2379fca65cfac2d945cce856ec814d63cba018b0362c005"
SPKI = "abqvIMwFmsvQQW2qdQ47FYQb7JJZcu4Iy7Qr5hoIcRs="


def read_profile(path):
    if not path.exists():
        return "personal"
    from scripts import verify_windows_package as v
    v._validate_existing_parent_chain(path.parent, label="external runtime")
    v._validate_staging_entry(path, path.parent, directory=False, allow_missing=False)
    if path.stat().st_size > 1024:
        raise ValueError("runtime config too large")
    raw = path.read_text(encoding="utf-8")
    pair = r'"(?:schema|data_profile)"\s*:\s*"[A-Za-z0-9._-]+"'
    if not re.fullmatch(r"\s*\{\s*" + pair + r"\s*,\s*" + pair + r"\s*\}\s*", raw):
        raise ValueError("invalid runtime keys/structure")
    pairs = json.loads(raw, object_pairs_hook=list)
    if len({key for key, value in pairs}) != 2:
        raise ValueError("duplicate runtime keys")
    data = dict(pairs)
    if data.get("schema") != "mentorflow.desktop-runtime.v1" or not re.fullmatch(r"[a-z][a-z0-9-]{0,39}", data.get("data_profile", "")):
        raise ValueError("invalid runtime values")
    return data["data_profile"]


def clean_environment(temp):
    names = ("SystemRoot", "WINDIR", "SystemDrive", "COMSPEC", "USERPROFILE", "LOCALAPPDATA", "APPDATA", "USERNAME", "USERDOMAIN", "PROGRAMDATA", "PROGRAMFILES", "PROGRAMFILES(X86)")
    env = {key: os.environ[key] for key in names if key in os.environ}
    system = Path(os.environ["SystemRoot"])
    env.update(PATH=str(system / "System32") + ";" + str(system), TEMP=str(temp), TMP=str(temp))
    return env


def launch(root, verify):
    from scripts import verify_windows_package as v
    from scripts import windows_install_trust as trust
    root = trust._root(root)
    trust.assert_protected(root)
    app = root / "App"
    certificate = app / "Config/edge-tls-ca.crt"
    for path in (app, certificate.parent, certificate):
        trust.assert_protected(path)
    if v.sha256_file(certificate) != CA_SHA:
        raise trust.RepairRequired("TLS certificate pin mismatch")
    local = Path(os.environ["LOCALAPPDATA"]) / "MentorFlow"
    profile = read_profile(local / "Runtime/launch.json")
    vault = local / "Accounts" / profile
    temp = vault / "runtime-temp"
    v._validate_existing_parent_chain(temp, label="external runtime")
    temp.mkdir(parents=True, exist_ok=True)
    env = clean_environment(temp)
    system = Path(os.environ["SystemRoot"]) / "System32"
    owned = []

    def endpoint():
        try:
            response = subprocess.run([str(system / "curl.exe"), "--silent", "--fail", "--noproxy", "*", "--connect-timeout", "2", "--max-time", "4", "--cacert", str(certificate), ISSUER + "/.well-known/openid-configuration"], capture_output=True, timeout=5, env=env, cwd=system, creationflags=subprocess.CREATE_NO_WINDOW)
            return response.returncode == 0 and json.loads(response.stdout).get("issuer") == ISSUER
        except (OSError, ValueError, subprocess.TimeoutExpired):
            return False

    def connect():
        if endpoint():
            return
        # Preserve the existing preview transport only. No server mutation or
        # credential copying; production transport is an integration decision.
        key = Path("E:/Codex/Secrets/MentorFlow/ssh/mentorflow-e1-ops-ed25519")
        if not key.is_file():
            return
        v._validate_existing_parent_chain(key.parent, label="preview transport key")
        v._validate_staging_entry(key, key.parent, directory=False, allow_missing=False)
        owned.append(subprocess.Popen([str(system / "OpenSSH/ssh.exe"), "-N", "-T", "-i", str(key), "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes", "-o", "ExitOnForwardFailure=yes", "-o", "ConnectTimeout=8", "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=2", "-L", "127.0.0.1:18080:127.0.0.1:443", "mentorflow-admin@89.45.45.28"], env=env, cwd=system, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW))

    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            ready = pool.submit(connect)
            verify()  # failure raises before application execution
            ready.result()
        env.update({
            "MENTORFLOW_ACCOUNT_VAULT_ROOT": str(vault), "MENTORFLOW_DATA_DIR": str(vault),
            "MENTORFLOW_USER_DATA_ROOT": str(vault / "prelaunch"),
            "MENTORFLOW_DB": str(vault / "prelaunch/data/app.db"),
            "MENTORFLOW_ORM_DB": str(vault / "prelaunch/data/mentorflow_v2.db"),
            "MENTORFLOW_DESKTOP_FORCE_LOGIN_GATE": "1", "MENTORFLOW_DISABLE_PROVIDER_AUTORESTORE": "1",
            "MENTORFLOW_DESKTOP_OIDC_ISSUER": ISSUER, "MENTORFLOW_DESKTOP_OIDC_CLIENT_ID": "mentorflow-desktop",
            "MENTORFLOW_DESKTOP_OIDC_AUDIENCE": "mentorflow-api", "MENTORFLOW_ENTERPRISE_API_BASE_URL": "https://localhost:18080",
            "MENTORFLOW_DESKTOP_OIDC_CALLBACK_PORT": "0", "SSL_CERT_FILE": str(certificate), "REQUESTS_CA_BUNDLE": str(certificate),
            "QTWEBENGINE_CHROMIUM_FLAGS": "--ignore-certificate-errors-spki-list=" + SPKI,
        })
        # No organization/workspace hints. The server controls post-login scope.
        return subprocess.call([str(app / "MentorFlow.exe")], env=env, cwd=app, creationflags=subprocess.CREATE_NO_WINDOW)
    finally:
        for process in owned:
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=10)


def show_repair(message):
    ctypes.windll.user32.MessageBoxW(None, "安装验证失败，请运行安装器进行完整验证或修复。\n" + message, "MentorFlow — 需要修复安装", 0x10)
