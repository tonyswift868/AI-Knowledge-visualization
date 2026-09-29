"""Account closure coordinator; request-driven, durable, no worker/queue."""
from __future__ import annotations
import asyncio
from dataclasses import dataclass
import hashlib
import re
import secrets

from refactor.server.account_identity_contract import account_id
from .password_sessions import _revoke_local

TOKEN = re.compile(r"mfc1_[A-Za-z0-9_-]{43}")
OPERATION = re.compile(r"[0-9a-f]{32}")


class ClosureError(RuntimeError):
    def __init__(self, code, status=503):
        self.code, self.status = code, status
        super().__init__(code)


def token_digest(operation_id, token):
    if not isinstance(operation_id, str) or not OPERATION.fullmatch(operation_id):
        raise ClosureError("CLOSURE_CREDENTIAL_INVALID", 403)
    if not isinstance(token, str) or not TOKEN.fullmatch(token):
        raise ClosureError("CLOSURE_CREDENTIAL_INVALID", 403)
    return hashlib.sha256(("mentorflow.closure.v1\0" + operation_id + "\0" + token).encode()).hexdigest()


MESSAGES = {
    "PREPARED": "注销已准备，账号尚未删除。",
    "IN_PROGRESS": "账号注销正在处理。",
    "RETRYABLE_FAILURE": "注销尚未完成，请使用原恢复凭证重试。",
    "COMPLETED": "账号注销已完成。",
}


def public_result(record):
    state = record["state"]
    if state not in MESSAGES:
        raise ClosureError("CLOSURE_STORAGE_INVALID")
    return dict(state=state, operation_id=record["operation_id"], account_id=record["account_id"],
                code=record.get("code") or "CLOSURE_" + state, message=MESSAGES[state])


@dataclass(frozen=True)
class ClosureResult:
    status: int
    payload: dict


class AccountClosure:
    def __init__(self, store, provider, runtime):
        self.store, self.provider, self.runtime = store, provider, runtime

    async def prepare(self, principal, password):
        # Only the HTTP adapter can supply a verified native principal. No target
        # comes from the request body; verify the password against that same ID.
        if (principal.issuer != self.store.issuer or principal.actor_kind.value != "HUMAN"
                or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", principal.subject)):
            raise ClosureError("CLOSURE_IDENTITY_INVALID", 403)
        username = await self.provider.admin.username_for_subject(principal.subject)
        try:
            from refactor.server.enterprise.identity.oidc import IdentityError
            try:
                checked = await self.runtime.native_sessions.verifier.authenticate(username, password)
            except IdentityError as exc:
                if exc.code == "NATIVE_CREDENTIALS_INVALID":
                    raise ClosureError("CURRENT_PASSWORD_INVALID", 400) from None
                raise ClosureError("CLOSURE_IDENTITY_UNAVAILABLE") from None
        finally:
            password = None
        if (checked.issuer, checked.subject) != (principal.issuer, principal.subject):
            raise ClosureError("CLOSURE_IDENTITY_INVALID", 403)
        operation = secrets.token_hex(16)
        token = "mfc1_" + secrets.token_urlsafe(32)
        digest = token_digest(operation, token)
        record = await asyncio.to_thread(self.store.prepare, principal.issuer, principal.subject,
            account_id(principal.issuer, principal.subject), operation, digest)
        # Replacement is allowed only before commit. The DB invalidates the old
        # operation ID AND digest atomically, with one unique subject record.
        return ClosureResult(200, dict(state="PREPARED", operation_id=record["operation_id"],
                                      account_id=record["account_id"], recovery_token=token))

    async def status(self, operation, token):
        record = await asyncio.to_thread(self.store.status, operation, token_digest(operation, token))
        return ClosureResult(200, public_result(record))

    async def commit(self, operation, token):
        digest = token_digest(operation, token)
        claim = secrets.token_hex(16)
        record = await asyncio.to_thread(self.store.acquire, operation, digest, claim)
        if not record.get("acquired"):
            return ClosureResult(200 if record["state"] == "COMPLETED" else 202, public_result(record))
        try:
            # Each DB call has its own bounded transaction. A crashed request
            # leaves a lease that the next commit can reclaim; GET never retries.
            async with asyncio.timeout(75):
                subject = record["subject_id"]
                if not all(_revoke_local(self.runtime, subject)):
                    raise ClosureError("CLOSURE_SESSION_REVOCATION_PENDING")
                if self.provider is None and not record["identity_deleted"]:
                    raise ClosureError("CLOSURE_PROVIDER_UNAVAILABLE")
                if not record["data_cleaned"]:
                    record = await asyncio.to_thread(self.store.clean, operation, digest, claim)
                if not record["identity_deleted"]:
                    await self.provider.ensure_deleted(subject)
                    record = await asyncio.to_thread(self.store.provider_done, operation, digest, claim)
                if not all(_revoke_local(self.runtime, subject)):
                    raise ClosureError("CLOSURE_SESSION_REVOCATION_PENDING")
                record = await asyncio.to_thread(self.store.complete, operation, digest, claim)
            return ClosureResult(200, public_result(record))
        except asyncio.CancelledError:
            # No shielded deletion or background job. The lease records the
            # interrupted operation and a later commit safely resumes it.
            raise
        except Exception as exc:
            code = exc.code if isinstance(exc, ClosureError) else "CLOSURE_RETRY_REQUIRED"
            try:
                record = await asyncio.to_thread(self.store.fail, operation, digest, claim, code)
            except Exception:
                # Do not invent COMPLETED or an account ID from client input.
                record = dict(record, state="RETRYABLE_FAILURE", code="CLOSURE_STORAGE_UNAVAILABLE")
            return ClosureResult(200 if record["state"] == "COMPLETED" else
                                 202 if record["state"] == "IN_PROGRESS" else 503, public_result(record))
