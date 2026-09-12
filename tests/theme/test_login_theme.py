"""Offline theme contract tests; not a Keycloak rendering/authentication result."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
THEME = ROOT / "deploy/enterprise/themes/mentorflow-desktop-v1"
LOGIN = THEME / "login"
BASE = "f458db4261af6f62786d551ac9bc0974a3429b46"
REALM = "deploy/enterprise/config/keycloak-realm.json"
COMPOSE = "deploy/enterprise/staging/rp05/compose.account-core-mvp.yml"
HEADER = "<#-- Based on Keycloak 26.7.3 base/login/login.ftl (Apache-2.0).\n     Only change: omit the server Remember Me control; Desktop owns persistence.\n     See UPSTREAM.md. Keep authentication/challenge logic upstream-owned. -->\n"
REMOVED = "                            <#if realm.rememberMe && !usernameHidden??>\n                                <div class=\"checkbox\">\n                                    <label>\n                                        <#if login.rememberMe??>\n                                            <input tabindex=\"5\" id=\"rememberMe\" name=\"rememberMe\" type=\"checkbox\" checked> ${msg(\"rememberMe\")}\n                                        <#else>\n                                            <input tabindex=\"5\" id=\"rememberMe\" name=\"rememberMe\" type=\"checkbox\"> ${msg(\"rememberMe\")}\n                                        </#if>\n                                    </label>\n                                </div>\n                            </#if>\n"
UPSTREAM_SHA256 = "8b179d40968e07776b9c9c4b46103f8e3489eaaa249d18be202ddcd3da565e34"


def original(path):
    return subprocess.check_output(["git", "show", f"{BASE}:{path}"], cwd=ROOT).decode()


def properties(path):
    return dict(line.split("=", 1) for line in path.read_text(encoding="utf-8").splitlines()
                if line and not line.startswith("#"))


def test_regular_login_delta_is_only_remember_me_removal():
    template = (LOGIN / "login.ftl").read_text(encoding="utf-8")
    assert template.startswith(HEADER)
    restored = template[len(HEADER):].replace(
        '                        <div id="kc-form-options">\n',
        '                        <div id="kc-form-options">\n' + REMOVED,
        1,
    )
    assert hashlib.sha256(restored.encode()).hexdigest() == UPSTREAM_SHA256
    assert 'name="rememberMe"' not in template
    assert sorted(path.name for path in LOGIN.glob("*.ftl")) == ["login-update-password.ftl", "login.ftl"]


def test_form_uses_upstream_post_errors_and_password_visibility():
    template = (LOGIN / "login.ftl").read_text(encoding="utf-8")
    assert template.count('id="kc-form-login"') == 1
    assert template.count('id="kc-login"') == 1
    for expected in ('action="${url.loginAction}" method="post"',
                     'name="username"', 'name="password" type="password"',
                     'autocomplete="current-password"', 'data-password-toggle tabindex="4"',
                     'aria-controls="password"', 'js/passwordVisibility.js',
                     "kcSanitize(messagesPerField.getFirstError('username','password'))",
                     'aria-live="polite"', '<@passkeys.conditionalUIData />',
                     'name="credentialId"', 'realm.resetPasswordAllowed',
                     'realm.registrationAllowed', 'social.providers'):
        assert expected in template
    assert [path.name for path in LOGIN.rglob("*.js")] == ["password-pair.js"]
    for forbidden in ("fetch(", "window.open", "postMessage", "/api/auth/login", "<iframe"):
        assert forbidden not in template


def test_realm_changes_only_desktop_theme_not_oidc_or_other_clients():
    before = json.loads(original(REALM))
    after = json.loads((ROOT / REALM).read_text(encoding="utf-8"))
    desktop = next(c for c in after["clients"] if c["clientId"] == "mentorflow-desktop")
    assert desktop["attributes"].pop("login_theme") == THEME.name
    assert after == before


def test_existing_mvp_only_adds_readonly_theme_and_scoped_gate():
    before = yaml.safe_load(original(COMPOSE))
    after = yaml.safe_load((ROOT / COMPOSE).read_text(encoding="utf-8"))
    mount = "../../themes/mentorflow-desktop-v1:/opt/keycloak/themes/mentorflow-desktop-v1:ro"
    after["services"]["keycloak"]["volumes"].remove(mount)
    assert after["services"]["keycloak-ready"]["environment"].pop(
        "MENTORFLOW_DESKTOP_LOGIN_THEME") == THEME.name
    assert after == before
    assert ((ROOT / COMPOSE).parent / mount.split(":")[0]).resolve() == THEME.resolve()
    assert (THEME / "login/theme.properties").is_file()


def test_inheritance_tokens_and_no_clipping_or_external_assets():
    props = properties(LOGIN / "theme.properties")
    assert props["parent"] == "keycloak"
    assert props["styles"] == "css/login.css css/mentorflow.css"
    assert props["kcBodyClass"] == "mentorflow-login"
    css = (LOGIN / "resources/css/mentorflow.css").read_text(encoding="utf-8")
    desktop_tokens = (ROOT / "desktop/style.py").read_text(encoding="utf-8")
    for token in ("#fff8fb", "#fffdfd", "#3b2b3f", "#edbfd2", "#a94f79",
                  "#c95f8d", "Microsoft YaHei UI"):
        assert token in css and token in desktop_tokens
    for forbidden in ("overflow: hidden", "overflow-y: hidden", "height: 100vh",
                      "position: fixed", "url(", "@import"):
        assert forbidden not in css
    assert ":focus-visible" in css
    assert "max-width: 420px" in css
    assert "min-width: 0" in css


def test_messages_do_not_leak_account_existence_or_duplicate_brand():
    for locale in ("en", "zh_CN"):
        msgs = properties(LOGIN / f"messages/messages_{locale}.properties")
        assert msgs["loginAccountTitle"] == "MentorFlow"
        assert msgs["usernameOrEmail"] == "账号"
        assert msgs["doLogIn"] == "登录"
        assert msgs["invalidUserMessage"] == msgs["invalidPasswordMessage"] == msgs["invalidUsernameMessage"]
        assert "rememberMe" not in msgs


def test_readiness_change_is_readonly_scoped_and_fail_closed():
    path = "deploy/enterprise/staging/rp05/keycloak-ready.sh"
    before = original(path)
    after = (ROOT / path).read_text(encoding="utf-8")
    start = after.index('if [ -n "${MENTORFLOW_DESKTOP_LOGIN_THEME:-}" ]; then')
    end = after.index("\nfi\n", start) + len("\nfi\n")
    assert after[:start] + after[end:] == before
    guard = after[start:end]
    assert "exit 31" in guard and "login_theme" in guard
    assert "kcadm.sh update" not in guard


@pytest.mark.parametrize("expected,actual,exit_code", [
    ("mentorflow-desktop-v1", "mentorflow-desktop-v1", 0),
    ("mentorflow-desktop-v1", "keycloak", 31),
    ("mentorflow-desktop-v1", None, 31),
    ("", "keycloak", 0),
])
def test_readiness_guard_shell_behavior(expected, actual, exit_code):
    shell = shutil.which("sh")
    if not shell:
        candidate = Path(shutil.which("git")).parent.parent / "usr/bin/sh.exe"
        if candidate.is_file():
            shell = str(candidate)
    if not shell:
        pytest.skip("POSIX shell unavailable; static guard check still runs")
    script = (ROOT / "deploy/enterprise/staging/rp05/keycloak-ready.sh").read_text(encoding="utf-8")
    start = script.index('if [ -n "${MENTORFLOW_DESKTOP_LOGIN_THEME:-}" ]; then')
    end = script.index("\nfi\n", start) + len("\nfi\n")
    client = {} if actual is None else {"attributes": {"login_theme": actual}}
    result = subprocess.run([shell, "-c", script[start:end]], capture_output=True, text=True,
                            env={**os.environ, "PATH": str(Path(shell).parent) + os.pathsep + os.environ.get("PATH", ""),
                                 "MENTORFLOW_DESKTOP_LOGIN_THEME": expected,
                                 "desktop_client": json.dumps(client, indent=2, separators=(",", " : "))})
    assert result.returncode == exit_code, result.stderr
    if exit_code:
        assert "Desktop login theme is not bound" in result.stderr
