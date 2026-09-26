"""Password mutation outcomes and subject-scoped session revocation.

The existing HTTP boundary must reauthenticate the current password and match
the principal to its server-resolved actor before calling change_password.
No username, account ID, workspace or deletion target comes from this module's
HTTP input. This is not an account deletion implementation.

Provider logout uses Keycloak 26.7.3 POST /admin/realms/{realm}/users/{id}/logout.
A confirmed logout invokes Keycloak's online-session logout and user not-before
update. Offline-session removal is not confirmed by that endpoint. Already-issued
OIDC bearer JWTs depend on the backend's configured introspection/expiry policy.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import re


@dataclass(frozen=True)
class PasswordActionResult:
    status: int
    state: str
    password_changed: bool | None
    native_sessions_revoked: bool
    backend_oidc_sessions_revoked: bool
    keycloak_logout_confirmed: bool

    def payload(self) -> dict:
        value = {
            "state": self.state,
            "reauthentication_required": True,
            "revocation_scope": "CURRENT_PROCESS_AND_KEYCLOAK_ONLINE_SESSIONS",
            "oidc_bearer_policy": "PROVIDER_VALIDATION_OR_EXPIRY",
            "oidc_offline_sessions": "NOT_CONFIRMED",
            "native_sessions_revoked": self.native_sessions_revoked,
            "backend_oidc_sessions_revoked": self.backend_oidc_sessions_revoked,
            "keycloak_logout_confirmed": self.keycloak_logout_confirmed,
        }
        if self.state.startswith("PASSWORD_"):
            value["password_changed"] = self.password_changed
        if self.status != 200:
            value["code"] = self.state
        return value


def _subject(runtime, actor) -> str:
    # The caller passes a verified ActorContext, never a body-supplied subject.
    native = getattr(runtime, "native_sessions", None)
    issuer = getattr(native, "issuer", None)
    subject = getattr(actor, "subject_id", None)
    kind = getattr(actor, "actor_kind", None)
    if (
        not issuer or getattr(actor, "issuer", None) != issuer
        or getattr(kind, "value", kind) != "HUMAN"
        or not isinstance(subject, str)
        or re.fullmatch(r"[A-Za-z0-9_-]{1,255}", subject) is None
    ):
        raise ValueError("PASSWORD_ACTION_IDENTITY_INVALID")
    return subject


def _revoke_local(runtime, subject: str) -> tuple[bool, bool]:
    # Attempt both independently. A failure in one store cannot skip the other.
    native_done = oidc_done = False
    try:
        runtime.native_sessions.revoke_subject(subject)
        native_done = True
    except Exception:
        pass
    try:
        runtime.client.store.revoke_by_subject_or_sid(subject=subject, sid=None)
        oidc_done = True
    except Exception:
        pass
    return native_done, oidc_done


async def _logout_provider(admin, subject: str) -> bool:
    try:
        # Reuse the existing resolver, verified TLS, bounded response handling
        # and no-redirect client. Do not retry or inspect provider error bodies.
        await admin._call("POST", "/users/" + subject + "/logout", expected=204)
        return True
    except Exception:
        return False


async def change_password(runtime, admin, actor, new_password: str) -> PasswordActionResult:
    """Called only after the old password and exact actor have been verified.

    A failed mutation response is ambiguous: do not claim the old password is
    unchanged or retry the write. Fence local sessions even on cancellation.
    The client must discard its saved password and reauthenticate explicitly.
    """
    subject = _subject(runtime, actor)
    if not isinstance(new_password, str) or not 8 <= len(new_password) <= 128:
        raise ValueError("PASSWORD_INPUT_INVALID")
    changed = None
    try:
        try:
            await admin.change_password(subject, new_password)
            changed = True
        except asyncio.CancelledError:
            raise
        except Exception:
            # Existing admin._call deliberately hides provider status details.
            # Even a 5xx classified there as REJECTED must remain uncertain here.
            pass
    finally:
        new_password = None
        native_done, oidc_done = _revoke_local(runtime, subject)
    provider_done = await _logout_provider(admin, subject)
    if changed is None:
        state, status = "PASSWORD_CHANGE_OUTCOME_UNKNOWN", 503
    elif not (native_done and oidc_done and provider_done):
        state, status = "PASSWORD_CHANGED_REVOCATION_PENDING", 503
    else:
        state, status = "PASSWORD_CHANGED", 200
    return PasswordActionResult(status, state, changed, native_done, oidc_done, provider_done)


async def revoke_sessions(runtime, admin, actor) -> PasswordActionResult:
    """Reconcile revocation after explicit reauthentication; never write a password.

    Repeating this action is safe, but it also signs out the new session used to
    authorize it. It is not an automatic replay of a failed password change.
    """
    subject = _subject(runtime, actor)
    native_done, oidc_done = _revoke_local(runtime, subject)
    provider_done = await _logout_provider(admin, subject)
    complete = native_done and oidc_done and provider_done
    return PasswordActionResult(
        200 if complete else 503,
        "SESSION_REVOCATION_CONFIRMED" if complete else "SESSION_REVOCATION_PENDING",
        None, native_done, oidc_done, provider_done,
    )
