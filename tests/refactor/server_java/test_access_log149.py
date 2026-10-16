"""Access logging records route metadata without any request secrets."""
from __future__ import annotations

import ssl
import time
import re

import httpx

from test_lifecycle_http146 import lifecycle_http146


def test_access_log149_covers_early_auth_controller_and_unknown_404(lifecycle_http146):
    endpoint=lifecycle_http146["servers"]["java"]
    verify=ssl.create_default_context(cafile=str(lifecycle_http146["tls"]["ca"]))
    with httpx.Client(base_url=endpoint["base_url"],verify=verify,timeout=10,trust_env=False) as client:
        early=client.get("/api/enterprise/account/workspaces")
        success=client.get("/health/live")
        native_transport=client.post("/api/enterprise/auth/native/login",
            headers={"Origin":"https://untrusted.invalid"},
            json={"username":"fake-user","password":"never-log-this-password"})
        closure_prepare=client.post("/api/enterprise/account/closure/prepare")
        closure_operation="a"*32
        closure_status=client.get(f"/api/enterprise/account/closure/{closure_operation}")
        unknown=client.get("/private-account-secret-149?token=never-log-this-value")

    assert early.status_code==401
    assert success.status_code==200
    assert native_transport.status_code==403
    assert closure_prepare.status_code==401
    assert unknown.status_code==404

    log_path=endpoint["log_path"]
    deadline=time.monotonic()+2
    content=""
    while time.monotonic()<deadline:
        content=log_path.read_text(encoding="utf-8",errors="replace")
        if all(term in content for term in ("route=/api/enterprise/account/workspaces status=401",
                "route=/health/live status=200","route=/api/enterprise/auth/native/login status=403",
                "route=/api/enterprise/account/closure/prepare status=401",
                "route=/api/enterprise/account/closure/{operation_id} status=",
                "route=UNMATCHED status=404")):
            break
        time.sleep(.02)

    assert "http method=GET route=/api/enterprise/account/workspaces status=401 elapsed_ms=" in content
    assert "http method=GET route=/health/live status=200 elapsed_ms=" in content
    assert "http method=POST route=/api/enterprise/auth/native/login status=403 elapsed_ms=" in content
    assert "http method=POST route=/api/enterprise/account/closure/prepare status=401 elapsed_ms=" in content
    assert f"http method=GET route=/api/enterprise/account/closure/{{operation_id}} status={closure_status.status_code} elapsed_ms=" in content
    assert "http method=GET route=UNMATCHED status=404 elapsed_ms=" in content
    lines=[line for line in content.splitlines() if "http method=" in line]
    assert len(re.findall(r"http method=[A-Z]+ route=.* status=[0-9]{3} elapsed_ms=[0-9]+", "\n".join(lines)))>=6
    assert "private-account-secret-149" not in content
    assert "never-log-this-value" not in content
    assert "never-log-this-password" not in content
    assert closure_operation not in content
    assert "token=" not in content
