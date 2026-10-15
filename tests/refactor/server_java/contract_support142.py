"""HTTP-only contract fixtures for the Python and Java server implementations."""
from __future__ import annotations

import base64
from dataclasses import dataclass
import hashlib
import json
import os
import secrets
import socket
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest
import uvicorn
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa


ISSUER_PATH = "/realms/mentorflow-142"
ISSUER = "http://127.0.0.1:{port}" + ISSUER_PATH
ORG = "org-142"
WORKSPACE = "ws-142"
SUBJECT = "user-142"
REQUEST_ID = "contract142"


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _json_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


class LocalJwks:
    """A local-only RS256 issuer; private key never leaves this test process."""
    def __init__(self, directory: Path):
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        numbers = self.key.public_key().public_numbers()
        self.kid = "contract142-rsa"
        self.jwks = {"keys": [{"kty": "RSA", "use": "sig", "alg": "RS256", "kid": self.kid,
                               "n": _b64(numbers.n.to_bytes((numbers.n.bit_length()+7)//8, "big")),
                               "e": _b64(numbers.e.to_bytes((numbers.e.bit_length()+7)//8, "big"))}]}
        self._handler = self._handler_type()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler)
        self.server.daemon_threads = True
        self.server.jwks = self.jwks
        self.server.jwks_status = 200
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_address[1]
        self.issuer = ISSUER.format(port=self.port)
        self.directory = directory

    @staticmethod
    def _handler_type():
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path != ISSUER_PATH + "/protocol/openid-connect/certs":
                    self.send_error(404)
                    return
                data = _json_bytes(self.server.jwks)
                status = self.server.jwks_status
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                if status == 200:
                    self.wfile.write(data)

            def log_message(self, *_args):
                return
        return Handler

    def token(self, *, subject: str = SUBJECT, audience: object = "mentorflow-api",
              extra_claims: dict | None = None, header: dict | None = None,
              lifetime: int = 300) -> str:
        now = int(time.time())
        claims = {"iss": self.issuer, "aud": audience, "exp": now + lifetime,
                  "iat": now, "jti": "jti-142", "sub": subject,
                  "azp": "mentorflow-desktop", "auth_time": now,
                  "acr": "password_verified", "preferred_username": "user142"}
        claims.update(extra_claims or {})
        head = {"alg": "RS256", "kid": self.kid, "typ": "JWT"}
        head.update(header or {})
        encoded = _b64(_json_bytes(head)) + "." + _b64(_json_bytes(claims))
        signature = self.key.sign(encoded.encode("ascii"), padding.PKCS1v15(), hashes.SHA256())
        return encoded + "." + _b64(signature)

    def raw_payload_token(self, payload: bytes, *, header: dict | None = None) -> str:
        head = {"alg": "RS256", "kid": self.kid, "typ": "JWT"}
        head.update(header or {})
        encoded = _b64(_json_bytes(head)) + "." + _b64(payload)
        signature = self.key.sign(encoded.encode("ascii"), padding.PKCS1v15(), hashes.SHA256())
        return encoded + "." + _b64(signature)

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)


class FakeDirectory142:
    """Deterministic external identity state, shared semantically with Java fixture."""
    def resolve_membership(self, *, issuer, subject, organization_id, workspace_id):
        from refactor.server.enterprise.identity.models import (
            MembershipGrant, MembershipStatus, BusinessRole,
        )
        if (issuer != self.issuer or subject != SUBJECT or organization_id != ORG
                or workspace_id not in (WORKSPACE, None)):
            return None
        return MembershipGrant(
            membership_id="membership-142", issuer=issuer, subject=subject,
            organization_id=organization_id, workspace_id=workspace_id,
            roles=(BusinessRole.LEARNER,), status=MembershipStatus.ACTIVE)

    def resolve_service_identity(self, *, issuer, subject, client_id):
        from refactor.server.enterprise.identity.models import (
            ServiceIdentityGrant, ServicePermission, MembershipStatus,
        )
        if (issuer == self.issuer and subject == "svc-sub142"
                and client_id == "mentorflow-desktop"):
            return ServiceIdentityGrant(service_id="service-142", issuer=issuer, subject=subject,
                client_id=client_id, organization_id=ORG, workspace_id=WORKSPACE,
                permissions=(ServicePermission.DOCUMENT_READ,), status=MembershipStatus.ACTIVE)
        return None


class FixtureClosure142:
    def __init__(self, state_file): self.state_file=Path(state_file)
    def ensure_open(self, _issuer, _subject):
        from refactor.server.account.closure import ClosureError
        try: state=json.loads(self.state_file.read_text(encoding="utf-8"))
        except Exception: raise ClosureError("CLOSURE_STORAGE_UNAVAILABLE") from None
        mode=state.get("closure","open")
        if mode=="closed": raise ClosureError("ACCOUNT_CLOSURE_IN_PROGRESS",403)
        if mode=="unavailable": raise ClosureError("CLOSURE_STORAGE_UNAVAILABLE")


def _free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture(scope="session")
def local_jwks142(tmp_path_factory):
    value = LocalJwks(tmp_path_factory.mktemp("jwks142"))
    try:
        yield value
    finally:
        value.close()


@dataclass
class ProcessEndpoint142:
    base_url: str
    process: subprocess.Popen
    log_path: Path
    health_file: Path


def _start_python_process(issuer: str, catalog_dir: Path, directory: Path) -> ProcessEndpoint142:
    port=_free_port()
    script=Path(__file__).with_name("python_server142.py")
    log_path=directory/"python-server.log"
    env=dict(os.environ)
    repo=Path(__file__).resolve().parents[3]
    env["PYTHONPATH"]=os.pathsep.join([str(repo),str(script.parent),env.get("PYTHONPATH","")])
    state_file=directory/"health-state.json"
    state_file.write_text('{"started":true,"postgres":true,"keycloak":true}',encoding="utf-8")
    env.update({"MENTORFLOW_CATALOG_DIR":str(catalog_dir),"CONTRACT142_ISSUER":issuer,
                "CONTRACT142_PORT":str(port),"CONTRACT142_HEALTH_FILE":str(state_file),
                "PYTHONDONTWRITEBYTECODE":"1"})
    log=log_path.open("wb")
    process=subprocess.Popen([sys.executable,"-B",str(script)],cwd=repo,env=env,
                             stdout=log,stderr=subprocess.STDOUT)
    base=f"http://127.0.0.1:{port}"
    deadline=time.monotonic()+20
    while time.monotonic()<deadline:
        if process.poll() is not None: break
        try:
            if httpx.get(base+"/health/live",timeout=.3,trust_env=False).status_code==200:
                log.close()
                return ProcessEndpoint142(base,process,log_path,state_file)
        except Exception: time.sleep(.05)
    process.terminate()
    try: process.wait(timeout=4)
    except subprocess.TimeoutExpired: process.kill()
    log.close()
    raise RuntimeError("Python HTTP reference failed to start: "+log_path.read_text(encoding="utf-8",errors="replace")[-4000:])


def _stop_process(endpoint: ProcessEndpoint142):
    endpoint.process.terminate()
    try: endpoint.process.wait(timeout=8)
    except subprocess.TimeoutExpired:
        endpoint.process.kill(); endpoint.process.wait(timeout=5)


@pytest.fixture(scope="session")
def python_http142(local_jwks142, tmp_path_factory, catalog_dir142):
    folder=tmp_path_factory.mktemp("python-http142")
    endpoint=_start_python_process(local_jwks142.issuer,catalog_dir142,folder)
    try: yield endpoint
    finally: _stop_process(endpoint)


@pytest.fixture(scope="session")
def health_state_file142(python_http142):
    return python_http142.health_file


@pytest.fixture(scope="session")
def catalog_dir142(tmp_path_factory):
    root = tmp_path_factory.mktemp("catalog142")
    (root / "source-whitelist.json").write_bytes(b'{"version":"test-142","sites":[]}\n')
    (root / "model-prices.json").write_bytes(b'{"version":"test-142","prices":[]}\n')
    return root


@pytest.fixture(scope="session")
def auth_headers142(local_jwks142):
    return {"Authorization": "Bearer " + local_jwks142.token(),
            "X-MentorFlow-Organization-ID": ORG,
            "X-MentorFlow-Workspace-ID": WORKSPACE,
            "X-Request-ID": REQUEST_ID}


@pytest.fixture(scope="session")
def python_client142(python_http142, catalog_dir142):
    with httpx.Client(base_url=python_http142.base_url, timeout=10, trust_env=False) as client:
        yield client
