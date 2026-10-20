"""Local-only lifecycle contract doubles for batch 146.

The HTTP authority below is deliberately a protocol substitute for Keycloak's
native-identity SPI. It drives backend routing and response mapping, and sends
mail to an in-process SMTP sink. It does NOT implement or qualify the SPI's
production code hashing, expiry, attempt counters, or cooldown algorithms.
"""
from __future__ import annotations

import json
import smtplib
import ssl
import threading
import time
from email import message_from_bytes
from email.message import EmailMessage
from email.policy import default
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from socketserver import StreamRequestHandler, ThreadingTCPServer
from urllib.parse import parse_qs, urlsplit

import contract_support144
from contract_support144 import (OLD_ISSUER,
    make_tls144, prepare_databases144, seed_tenant144, start_java144,
    start_postgres144, start_python144, stop_postgres144, stop_server144)


class SMTPInbox146:
    """Small SMTP receiver bound to loopback; never relays or sends externally."""
    def __init__(self):
        self.messages: list[dict] = []
        inbox = self

        class Handler(StreamRequestHandler):
            def handle(self):
                self.wfile.write(b"220 localhost ESMTP local-test-only\r\n")
                data = bytearray()
                in_data = False
                while True:
                    line = self.rfile.readline(65536)
                    if not line:
                        break
                    command = line.rstrip(b"\r\n")
                    upper = command.upper()
                    if in_data:
                        if command == b".":
                            inbox.messages.append({"raw": bytes(data)})
                            data.clear(); in_data = False
                            self.wfile.write(b"250 queued locally\r\n")
                        else:
                            data.extend(line[1:] if line.startswith(b"..") else line)
                        continue
                    if upper.startswith((b"EHLO", b"HELO")):
                        self.wfile.write(b"250-localhost\r\n250 8BITMIME\r\n")
                    elif upper.startswith((b"MAIL FROM", b"RCPT TO", b"RSET", b"NOOP")):
                        self.wfile.write(b"250 ok\r\n")
                    elif upper == b"DATA":
                        in_data = True
                        self.wfile.write(b"354 end with dot\r\n")
                    elif upper == b"QUIT":
                        self.wfile.write(b"221 bye\r\n"); break
                    else:
                        self.wfile.write(b"250 ok\r\n")

        class Server(ThreadingTCPServer):
            allow_reuse_address = True
            daemon_threads = True

        self.server = Server(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def clear(self):
        self.messages.clear()

    def parsed(self):
        output = []
        for item in self.messages:
            msg = message_from_bytes(item["raw"], policy=default)
            output.append({"to": str(msg.get("To", "")), "subject": str(msg.get("Subject", "")),
                "body": msg.get_body(preferencelist=("plain",)).get_content()})
        return output

    def close(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(timeout=2)


class FakeLifecycleKeycloak146:
    """mTLS Keycloak/Admin + lifecycle SPI protocol fake, loopback only."""
    def __init__(self, tls: dict[str, Path], smtp: SMTPInbox146):
        self.tls = tls
        self.smtp = smtp
        self.external_issuer = OLD_ISSUER
        self._users: dict[str, dict] = {}
        self._by_username: dict[str, str] = {}
        self._by_email: dict[str, str] = {}
        self._codes: dict[str, dict] = {}
        self.operations: list[dict] = []
        self.http_events: list[dict] = []
        self.next_registration_status: str | None = None
        # Optional protocol override used only by isolated mixed-version
        # compatibility tests; production and ordinary fixtures leave it unset.
        self.registration_start_override = None
        self.next_reset_status: str | None = None
        self.reset_observation_status: str | None = None
        self.reset_observation_schema: str | None = None
        self.next_registration_resend_status: str | None = None
        self.admin_logout_status = 204
        self.admin_reset_password_status = 204
        self.admin_username_override = None
        self.admin_logouts: list[str] = []
        self.password_created = 1770000000000
        self.enabled = True
        self.not_before = 0
        owner = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def _send(self, status, payload=None, headers=None, raw=None):
                parsed = urlsplit(self.path)
                owner.http_events.append({"method": self.command, "path": parsed.path,
                    "query_fields": sorted(parse_qs(parsed.query).keys()),
                    "route": self._route_kind(), "status": int(status)})
                body = raw if raw is not None else json.dumps(payload if payload is not None else {}, ensure_ascii=False,
                    separators=(",", ":")).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                for k, v in (headers or {}).items(): self.send_header(k, v)
                self.end_headers(); self.wfile.write(body)

            def _body(self):
                size = int(self.headers.get("Content-Length", "0"))
                return json.loads(self.rfile.read(size) or b"{}")

            def _route_kind(self):
                if self.path in ("/mentorflow-native-identity/v1", "/realms/mentorflow/mentorflow-native-identity/v1"):
                    return "native_identity"
                if self.path.endswith("/logout"):
                    return "admin_logout"
                if self.path.endswith("/reset-password"):
                    return "admin_password"
                if self.path.endswith("/credentials"):
                    return "admin_credentials"
                if self.path == "/admin/realms/mentorflow/users":
                    return "admin_users"
                if self.path.endswith("/token"):
                    return "admin_token"
                if self.path.endswith("/certs"):
                    return "jwks"
                return "admin_other"

            def _mtls(self):
                if not self.connection.getpeercert():
                    self._send(401, {"error": "client_certificate_required"}); return False
                return True

            def _admin(self):
                return (self._mtls() and self.headers.get("Authorization") == "Bearer fake-admin-token")

            def _identity(self, value):
                op = value.get("operation")
                owner.operations.append({"operation": str(op or "unknown")})
                if op in ("email-transport-health",):
                    return {"status": "READY"}
                if op == "reset-watch":
                    state = value.get("state", "")
                    return {"schema": owner.reset_observation_schema or "mentorflow.reset-completion.v1",
                        "transaction_id": state[:43].ljust(43, "W"),
                        "status": owner.reset_observation_status or "PENDING"}
                if op == "reset-status":
                    return {"schema": owner.reset_observation_schema or "mentorflow.reset-completion.v1",
                        "transaction_id": value.get("transaction_id", ""),
                        "status": owner.reset_observation_status or "PENDING"}
                if op in ("registration-start", "registration-resend"):
                    if op == "registration-start" and owner.registration_start_override is not None:
                        return owner.registration_start_override(value)
                    if op == "registration-resend" and owner.next_registration_resend_status:
                        status = owner.next_registration_resend_status
                        owner.next_registration_resend_status = None
                        return {"status": status}
                    credential = value.get("credential") or "R" * 43
                    code = "123456"
                    entry = owner._codes.setdefault(credential, {"code": code, "username": value.get("username"),
                        "email": value.get("email"), "operation": "registration", "state": "pending"})
                    entry.update({"username": value.get("username", entry.get("username")),
                        "email": value.get("email", entry.get("email")),
                        "password": value.get("password", entry.get("password", "correct")), "state": "pending"})
                    owner._mail(entry["email"], "MentorFlow 注册验证码",
                        f"您的 MentorFlow 注册验证码是：{code}\n有效期为 10 分钟。请在 MentorFlow 注册页面输入。\n请勿向他人提供验证码。如非本人操作，请忽略此邮件。")
                    return {"status": "CODE_SENT", "credential": credential,
                        "expires_in": "600", "resend_after": "60"}
                if op == "registration-verify":
                    entry = owner._codes.get(value.get("credential"), {})
                    if owner.next_registration_status:
                        status = owner.next_registration_status; owner.next_registration_status = None
                        return {"status": status}
                    if value.get("code") != entry.get("code"):
                        return {"status": "WRONG_CODE"}
                    entry["state"] = "verified"
                    return {"status": "PENDING_COMMIT"}
                if op == "registration-status":
                    entry = owner._codes.get(value.get("credential"), {})
                    if entry.get("state") == "verified":
                        owner._create_user(entry["username"], entry["email"], entry.get("password", "correct"))
                        entry["state"] = "completed"
                        return {"status": "VERIFIED"}
                    return {"status": "INVALID"}
                if op == "email-code-request":
                    ticket = value.get("transaction_id", "T" * 43)
                    owner._codes[ticket] = {"code": "234567", "email": value.get("email"),
                        "operation": "reset", "state": "pending"}
                    owner._mail(value.get("email"), "MentorFlow 密码重置验证码",
                        "您的 MentorFlow 密码重置验证码是：234567\n有效期为 10 分钟。请在 MentorFlow 软件的找回密码页面输入验证码。\n请勿向他人提供验证码。如非本人操作，请忽略此邮件。")
                    return {"status": "ACCEPTED", "delivery": "SUBMITTED"}
                if op == "email-code-verify":
                    entry = owner._codes.get(value.get("transaction_id"), {})
                    if owner.next_reset_status:
                        status = owner.next_reset_status; owner.next_reset_status = None
                        return {"status": status}
                    if value.get("code") == entry.get("code"):
                        entry["state"] = "verified"; entry["proof"] = "P" * 43
                        return {"status": "VERIFIED", "reset_proof": entry["proof"], "expires_in": "300"}
                    return {"status": "INVALID"}
                if op == "email-code-complete":
                    entry = next((v for v in owner._codes.values() if v.get("proof") == value.get("reset_proof")), {})
                    if not entry: return {"status": "INVALID"}
                    subject = owner._by_email.get(entry.get("email"), "")
                    if not subject: return {"status": "INVALID"}
                    owner._users[subject]["password"] = value.get("new_password")
                    owner.password_created += 1
                    entry["state"] = "completed"
                    return {"status": "COMPLETED", "subject": subject}
                if op == "email-code-status":
                    entry = next((v for v in owner._codes.values() if v.get("proof") == value.get("reset_proof")), {})
                    return {"status": "COMPLETED", "subject": owner._by_email.get(entry.get("email"), "")}
                if op == "email-binding-profile":
                    user = owner._users.get(value.get("subject"), {})
                    email = user.get("email") or ""
                    return {"status": "PROFILE", "email": email, "email_verified": "true" if user.get("email_verified") else "false"}
                if op == "email-binding-request":
                    ticket = value.get("transaction_id", "E" * 43)
                    owner._codes[ticket] = {"code": "345678", "email": value.get("email"), "subject": value.get("subject"), "operation": "binding", "state": "pending"}
                    owner._mail(value.get("email"), "MentorFlow 邮箱验证验证码",
                        "您的 MentorFlow 邮箱验证验证码是：345678\n有效期为 10 分钟。请在 MentorFlow 账号设置中输入。此验证码不能用于重置密码。")
                    return {"status": "SENT"}
                if op == "email-binding-verify":
                    entry = owner._codes.get(value.get("transaction_id"), {})
                    if value.get("code") != entry.get("code"): return {"status": "INVALID"}
                    entry["state"] = "verified"; return {"status": "PENDING_COMMIT"}
                if op == "email-binding-status":
                    entry = owner._codes.get(value.get("transaction_id"), {})
                    if entry.get("state") == "verified":
                        user = owner._users.get(value.get("subject"), {})
                        old_email = user.get("email")
                        if old_email: owner._by_email.pop(old_email.casefold(), None)
                        user["email"] = entry.get("email"); user["email_verified"] = True
                        owner._by_email[user["email"].casefold()] = value.get("subject")
                        return {"status": "PROFILE", "email": user["email"], "email_verified": "true"}
                    return {"status": "INVALID"}
                username = value.get("username", "")
                user = owner._users.get(owner._by_username.get(username, ""), {})
                if op == "email-transport-health": return {"status": "READY"}
                if value.get("password") != user.get("password"):
                    self._send(401, {"code": "NATIVE_CREDENTIALS_INVALID", "message": "账号或密码不正确"}); return None
                return {"schema": "mentorflow.native-identity.v1", "outcome": "authenticated",
                    "issuer": owner.external_issuer, "subject": user["id"], "display_name": username,
                    "authentication_strength": "password_verified", "verification_handle": "V" * 43}

            def do_POST(self):
                if self.path in ("/mentorflow-native-identity/v1", "/realms/mentorflow/mentorflow-native-identity/v1"):
                    if not self._mtls(): return
                    value = self._body()
                    result = self._identity(value)
                    if result is not None: self._send(200, result)
                    return
                if self.path == "/realms/mentorflow/protocol/openid-connect/token":
                    if not self._mtls(): return
                    form = parse_qs(self.rfile.read(int(self.headers.get("Content-Length", "0"))).decode())
                    if form.get("client_id") == ["test-admin"] and form.get("client_secret") == ["local-test-secret"]:
                        self._send(200, {"access_token": "fake-admin-token", "expires_in": 300}); return
                    self._send(401, {}); return
                if not self._admin(): return
                prefix = "/admin/realms/mentorflow/users/"
                if self.path.startswith(prefix) and self.path.endswith("/logout"):
                    owner.admin_logouts.append(self.path[len(prefix):-7])
                    self._send(owner.admin_logout_status, raw=b""); return
                if self.path == "/admin/realms/mentorflow/users":
                    payload = self._body()
                    username, email = payload.get("username"), payload.get("email")
                    if username in owner._by_username or (email and email.casefold() in owner._by_email):
                        self._send(409, {"error":"exists"}); return
                    subject = owner._create_user(username, email,
                        (payload.get("credentials") or [{}])[0].get("value", "correct"))
                    self._send(201, {}, {"Location": owner.origin + "/admin/realms/mentorflow/users/" + subject}); return
                self._send(404, {})

            def do_GET(self):
                if self.path == "/realms/mentorflow/protocol/openid-connect/certs":
                    self._send(200, {"keys": []}); return
                if not self._admin(): return
                parsed = urlsplit(self.path)
                if parsed.path == "/admin/realms/mentorflow/users":
                    q = parse_qs(parsed.query)
                    username = q.get("username", [None])[0]
                    email = q.get("email", [None])[0]
                    subject = owner._by_username.get(username) if username is not None else owner._by_email.get(email)
                    self._send(200, [owner._users[subject]] if subject else []); return
                prefix = "/admin/realms/mentorflow/users/"
                if parsed.path.startswith(prefix):
                    tail = parsed.path[len(prefix):]
                    if tail.endswith("/credentials"):
                        subject = tail[:-12]
                        if subject not in owner._users: self._send(404, {}); return
                        self._send(200, [{"type":"password","id":"pwd-"+subject,"createdDate":owner.password_created}]); return
                    subject = tail
                    user = owner._users.get(subject)
                    if user is None: self._send(404, {"error": "User not found"}); return
                    payload = {"id": subject, "username": user["username"] if owner.admin_username_override is None else owner.admin_username_override, "email": user.get("email"),
                        "enabled": owner.enabled, "notBefore": owner.not_before, "emailVerified": user.get("email_verified", False)}
                    self._send(200, payload); return
                self._send(404, {})

            def do_PUT(self):
                if not self._admin(): return
                parsed = urlsplit(self.path); prefix = "/admin/realms/mentorflow/users/"
                if not parsed.path.startswith(prefix): self._send(404, {}); return
                tail = parsed.path[len(prefix):]; raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                if tail.endswith("/reset-password"):
                    subject = tail[:-15]
                    if subject not in owner._users: self._send(404, {}); return
                    if owner.admin_reset_password_status != 204:
                        self._send(owner.admin_reset_password_status, {"error": "local_fixture_failure"}); return
                    owner._users[subject]["password"] = json.loads(raw).get("value")
                    owner.password_created += 1; self._send(204, raw=b""); return
                if tail.endswith("/send-verify-email") or tail.endswith("/execute-actions-email"):
                    self._send(204, raw=b""); return
                self._send(404, {})

            def do_DELETE(self):
                if not self._admin(): return
                prefix = "/admin/realms/mentorflow/users/"
                if not self.path.startswith(prefix): self._send(404, {}); return
                subject = self.path[len(prefix):]
                user = owner._users.pop(subject, None)
                if user:
                    owner._by_username.pop(user["username"], None)
                    if user.get("email"): owner._by_email.pop(user["email"], None)
                    self._send(204, raw=b"")
                else: self._send(404, {})

            def _create(self, raw):
                payload = json.loads(raw or b"{}")
                username, email = payload.get("username"), payload.get("email")
                if username in owner._by_username or email in owner._by_email:
                    self._send(409, {"error":"exists"}); return
                subject = owner._create_user(username, email, (payload.get("credentials") or [{}])[0].get("value", "correct"))
                self._send(201, {}, {"Location": owner.origin + "/admin/realms/mentorflow/users/" + subject})

            def log_message(self, *_args): pass

        class Server(ThreadingHTTPServer):
            allow_reuse_address = True
            daemon_threads = True

        self.server = Server(("127.0.0.1", 0), Handler)
        context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        context.load_cert_chain(str(tls["server_cert"]), str(tls["server_key"]))
        context.load_verify_locations(str(tls["ca"]))
        context.verify_mode = ssl.CERT_OPTIONAL
        self.server.socket = context.wrap_socket(self.server.socket, server_side=True)
        self.port = self.server.server_address[1]
        self.origin = f"https://localhost:{self.port}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def _create_user(self, username, email, password):
        subject = "subject146-" + str(username).replace("_", "-")
        user = {"id": subject, "username": username, "email": email,
            "email_verified": bool(email), "password": password}
        self._users[subject] = user; self._by_username[username] = subject
        if email: self._by_email[email.casefold()] = subject
        return subject

    def add_user(self, username, email, password="correct"):
        return self._create_user(username, email, password)

    def _mail(self, recipient, subject, body):
        msg = EmailMessage()
        msg["To"] = recipient
        msg["From"] = "noreply@mentorflow.local"
        msg["Subject"] = subject
        msg.set_content(body, charset="utf-8")
        with smtplib.SMTP("127.0.0.1", self.smtp.port, timeout=3) as client:
            client.send_message(msg)

    def close(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(timeout=2)


def build_pair146(run_dir: Path):
    """Standalone PG17 + local HTTPS/mTLS authority, without the 144 server fixture."""
    # Read at call time: conftest rebinds the path after this module is imported (BUG-108).
    classpath_file = contract_support144.CLASSPATH_FILE
    if not classpath_file.is_file():
        raise RuntimeError(f"Prepared Java/Zonky classpath missing: {classpath_file}")
    run_dir.mkdir(parents=True, exist_ok=True)
    tls = make_tls144(run_dir / "tls")
    postgres_dir = run_dir / "postgres"
    postgres_dir.mkdir(parents=True, exist_ok=True)
    pg = start_postgres144(postgres_dir)
    servers = {}
    identities = {name: FakeLifecycleKeycloak146(tls, SMTPInbox146()) for name in ("python", "java")}
    pair = {"identity": identities["python"], "identities": identities,
        "smtp": identities["python"].smtp, "smtps": {k:v.smtp for k,v in identities.items()},
        "pg": pg, "tls": tls, "servers": servers, "databases": {}, "run_dir": run_dir}
    try:
        databases = prepare_databases144(pg["port"], ("py146", "java146"))
        for db_name in databases:
            seed_url = f"postgresql+psycopg://postgres@127.0.0.1:{pg['port']}/{db_name}"
            for subject in ("subject146-alice", "subject146-bob", "subject146-new-user146", "subject146-binding-user146"):
                seed_tenant144(seed_url, OLD_ISSUER, subject)
        pair["databases"] = databases
        for identity in identities.values():
            identity.add_user("alice", "alice146@fixture.invalid", "correct")
            identity.add_user("bob", "bob146@fixture.invalid", "correct")
        for name, launch in (("python", start_python144), ("java", start_java144)):
            directory = run_dir / name
            directory.mkdir(parents=True, exist_ok=True)
            endpoint = launch(databases["py146" if name == "python" else "java146"],
                identities[name], tls, directory)
            servers[name] = endpoint
        return pair
    except Exception:
        for endpoint in reversed(list(servers.values())): stop_server144(endpoint)
        for identity in identities.values(): identity.close(); identity.smtp.close()
        stop_postgres144(pg)
        raise


def shutdown_pair146(pair):
    for endpoint in reversed(list(pair["servers"].values())): stop_server144(endpoint)
    for identity in pair["identities"].values():
        identity.close(); identity.smtp.close()
    stop_postgres144(pair["pg"])
