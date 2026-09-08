"""Source-order guards supplement actual final-package shortcut timings."""
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[2] / 'scripts/enterprise/start_personal_test_package.ps1'


def test_connection_can_overlap_but_application_requires_integrity_pass():
    text = SOURCE.read_text(encoding='utf-8')
    body = text[text.index('\ntry {'):]
    assert body.index('Release trust anchor integrity check failed') < body.index('$check = [Diagnostics.Process]::Start')
    assert body.index('$check = [Diagnostics.Process]::Start') < body.index('Ensure-SupportedTunnel') < body.index('$check.WaitForExit(100)')
    assert body.index('$check.ExitCode -ne 0') < body.index('$appProcess = [Diagnostics.Process]::Start')
    assert "if (-not $ValidateOnly)" in body
    assert 'Close-OwnedTunnel' in body[body.index('} catch {'):]
    assert 'launch-gate --package-root' in body
    assert '--state-path' in body and '--expected-manifest-sha256' in body
    assert 'security_feedback_visible_ms' in body


def test_personal_launcher_preserves_fixed_trust_and_no_tenant_hint():
    text = SOURCE.read_text(encoding='utf-8')
    assert 'StrictHostKeyChecking=yes' in text and '--cacert $certificate' in text
    assert "discovery.issuer -ceq 'https://localhost:18080/realms/mentorflow'" in text
    assert 'PRIVATE KEY-----' not in text
    assert 'MENTORFLOW_DESKTOP_WORKSPACE_ID' not in text
    assert 'MENTORFLOW_DESKTOP_ORGANIZATION_ID' not in text
    assert "MENTORFLOW_DESKTOP_FORCE_LOGIN_GATE = '1'" in text
    assert "E:\\Codex\\Data\\MentorFlow-E107-PackageUAT" in text


def test_config_and_child_environment_validation_match_regressed_template():
    text = SOURCE.read_text(encoding='utf-8')
    template = SOURCE.with_name('start_account_core_package.ps1').read_text(encoding='utf-8')
    original = template[template.index('function Assert-NoAlias'):template.index('\ntry {')].strip()
    personal = text[text.index('function Assert-NoAlias'):text.index('\n# Existing preview transport')].strip()
    assert personal == original
