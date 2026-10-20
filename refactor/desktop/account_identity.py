"""Account identity, nonsecret preferences, and secure token namespacing."""

from __future__ import annotations

import hashlib
from refactor.desktop.account_identity_contract import account_id_from_hashes
from dataclasses import dataclass
from enum import Enum
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from refactor.desktop.credential_store import (
    CredentialStoreError,
    delete_account_secret,
    get_account_secret,
    store_account_secret,
)


class ProfileMode(str, Enum):
    PERSONAL_LOCAL = "PERSONAL_LOCAL"
    ENTERPRISE_ACCOUNT = "ENTERPRISE_ACCOUNT"


def canonicalize_issuer(value: str) -> str:
    raw = str(value or "").strip()
    parsed = urlsplit(raw)
    if (
        parsed.scheme.casefold() != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Enterprise issuer must be a canonical HTTPS URL")
    host = parsed.hostname.encode("idna").decode("ascii").casefold()
    port = parsed.port
    netloc = host if port in {None, 443} else f"{host}:{port}"
    path = parsed.path.rstrip("/")
    return urlunsplit(("https", netloc, path, "", ""))


def identity_hash(value: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError("identity value must not be blank")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def issuer_hash(issuer: str) -> str:
    return identity_hash(canonicalize_issuer(issuer))


def subject_hash(subject: str) -> str:
    return identity_hash(subject)


def _validate_hash(value: str, field_name: str) -> str:
    normalized = str(value or "").strip().lower()
    if len(normalized) != 64 or any(
        character not in "0123456789abcdef" for character in normalized
    ):
        raise ValueError(f"{field_name} must be lowercase SHA-256")
    return normalized


@dataclass(frozen=True, slots=True)
class AccountProfile:
    mode: ProfileMode
    display_name: str
    issuer_hash: str | None = None
    subject_hash: str | None = None

    def __post_init__(self) -> None:
        display_name = " ".join(str(self.display_name or "").split()).strip()
        if self.mode is ProfileMode.PERSONAL_LOCAL:
            if self.issuer_hash is not None or self.subject_hash is not None:
                raise ValueError("Personal Local profile cannot carry account hashes")
            object.__setattr__(self, "display_name", "Personal Local")
            return
        if self.issuer_hash is None or self.subject_hash is None:
            raise ValueError("Enterprise Account requires issuer and subject hashes")
        object.__setattr__(self, "issuer_hash", _validate_hash(self.issuer_hash, "issuer_hash"))
        object.__setattr__(self, "subject_hash", _validate_hash(self.subject_hash, "subject_hash"))
        object.__setattr__(
            self,
            "display_name",
            display_name or f"Enterprise Account · {self.subject_hash[:8]}",
        )

    @classmethod
    def personal_local(cls) -> "AccountProfile":
        return cls(mode=ProfileMode.PERSONAL_LOCAL, display_name="Personal Local")

    @property
    def safe_account_label(self) -> str:
        return self.display_name

    @property
    def account_id(self) -> str | None:
        if self.mode is ProfileMode.PERSONAL_LOCAL:
            return None
        return account_id_from_hashes(self.issuer_hash, self.subject_hash)


@dataclass(frozen=True, slots=True)
class AccountIdentity:
    issuer: str
    subject: str
    display_name: str
    organization_id: str | None = None
    workspace_id: str | None = None

    def __post_init__(self) -> None:
        canonical = canonicalize_issuer(self.issuer)
        subject = str(self.subject or "").strip()
        if not subject:
            raise ValueError("Enterprise subject must not be blank")
        object.__setattr__(self, "issuer", canonical)
        object.__setattr__(self, "subject", subject)

    @property
    def issuer_hash(self) -> str:
        return identity_hash(self.issuer)

    @property
    def subject_hash(self) -> str:
        return identity_hash(self.subject)

    def profile(self) -> AccountProfile:
        return AccountProfile(
            mode=ProfileMode.ENTERPRISE_ACCOUNT,
            display_name=self.display_name,
            issuer_hash=self.issuer_hash,
            subject_hash=self.subject_hash,
        )

    @property
    def account_id(self) -> str:
        return account_id_from_hashes(self.issuer_hash, self.subject_hash)


class LocalOnlyIdentity:
    """Stable local account binding with no fabricated online identity scope."""
    __slots__ = ('_profile', 'issuer', 'subject', 'organization_id', 'workspace_id', '_local_only')

    def __init__(self, profile: AccountProfile):
        if profile.mode is not ProfileMode.ENTERPRISE_ACCOUNT or not profile.account_id:
            raise ValueError('ENTERPRISE_PROFILE_REQUIRED')
        self._profile = profile
        self.issuer = self.subject = self.organization_id = self.workspace_id = None
        self._local_only = True

    @property
    def account_id(self):
        return self._profile.account_id

    @property
    def display_name(self):
        return self._profile.safe_account_label

    @property
    def issuer_hash(self):
        return self._profile.issuer_hash

    @property
    def subject_hash(self):
        return self._profile.subject_hash

    def profile(self):
        return self._profile


NONSECRET_ACCOUNT_PREFERENCE_KEYS = frozenset(
    {
        "account/profile_mode",
        "account/issuer_hash",
        "account/subject_hash",
        "account/display_name",
        "account/sync_cursor",
        "account/model_selection",
        "account/remember_me",
        "account/auto_login",
    }
)


class AccountPreferenceStore:
    """QSettings-compatible storage restricted to nonsecret account fields."""

    def __init__(self, settings: Any) -> None:
        self.settings = settings

    def save_target(
        self,
        profile: AccountProfile,
        *,
        sync_cursor: str = "",
        model_selection: str = "",
        remember_me: bool | None = None,
        auto_login: bool | None = None,
    ) -> None:
        same_account = (
            self.settings.value("account/issuer_hash", "") == profile.issuer_hash
            and self.settings.value("account/subject_hash", "") == profile.subject_hash
        )
        previous_remember, previous_auto = self.login_options()
        remember = same_account and previous_remember if remember_me is None else bool(remember_me)
        automatic = same_account and previous_auto if auto_login is None else bool(auto_login)
        values = {
            "account/profile_mode": profile.mode.value,
            "account/issuer_hash": profile.issuer_hash or "",
            "account/subject_hash": profile.subject_hash or "",
            "account/display_name": profile.safe_account_label,
            "account/sync_cursor": str(sync_cursor or ""),
            "account/model_selection": str(model_selection or ""),
            "account/remember_me": "1" if remember else "0",
            "account/auto_login": "1" if remember and automatic else "0",
        }
        if set(values) != NONSECRET_ACCOUNT_PREFERENCE_KEYS:
            raise RuntimeError("account preference allowlist changed unexpectedly")
        for key, value in values.items():
            self.settings.setValue(key, value)
        self.settings.sync()

    def login_options(self) -> tuple[bool, bool]:
        remember = str(self.settings.value("account/remember_me", "0")) == "1"
        return remember, remember and str(self.settings.value("account/auto_login", "0")) == "1"

    def disable_restore(self) -> None:
        self.settings.setValue("account/remember_me", "0")
        self.settings.setValue("account/auto_login", "0")
        self.settings.sync()

    def load_unvalidated(self) -> AccountProfile:
        """Read only the non-secret profile selection.

        Startup UI uses this narrow reader before it schedules network token
        restoration on a worker thread.  ``load`` remains the fail-closed
        convenience API for callers that require an already validated session.
        """

        mode = str(
            self.settings.value(
                "account/profile_mode",
                ProfileMode.PERSONAL_LOCAL.value,
            )
            or ProfileMode.PERSONAL_LOCAL.value
        )
        if mode != ProfileMode.ENTERPRISE_ACCOUNT.value:
            return AccountProfile.personal_local()
        return AccountProfile(
            mode=ProfileMode.ENTERPRISE_ACCOUNT,
            display_name=str(
                self.settings.value("account/display_name", "Enterprise Account")
                or "Enterprise Account"
            ),
            issuer_hash=str(self.settings.value("account/issuer_hash", "") or ""),
            subject_hash=str(self.settings.value("account/subject_hash", "") or ""),
        )


    def clear(self) -> None:
        for key in NONSECRET_ACCOUNT_PREFERENCE_KEYS:
            self.settings.remove(key)
        self.settings.sync()



class AccountCredentialVault:
    """Store OAuth and Provider account secrets only in the existing Locker."""

    ACCESS_TOKEN_PURPOSE = "oauth-access-token"
    REFRESH_TOKEN_PURPOSE = "oauth-refresh-token"
    OFFLINE_IDENTITY_PURPOSE = "offline-identity-v1"

    def store_oauth_tokens(
        self,
        identity: AccountIdentity,
        *,
        access_token: str,
        refresh_token: str,
    ) -> dict[str, str]:
        access = str(access_token or "")
        refresh = str(refresh_token or "")
        if not access or not refresh:
            raise CredentialStoreError(
                "CREDENTIAL_REQUIRED",
                "OAuth access and refresh token material is required",
            )
        previous: dict[str, str] = {}
        for purpose in (self.ACCESS_TOKEN_PURPOSE, self.REFRESH_TOKEN_PURPOSE):
            try:
                previous[purpose] = get_account_secret(
                    issuer_hash=identity.issuer_hash,
                    subject_hash=identity.subject_hash,
                    purpose=purpose,
                )
            except CredentialStoreError as exc:
                if exc.code != "CREDENTIAL_NOT_FOUND":
                    raise
        references: dict[str, str] = {}
        written: list[str] = []
        values = (
            (self.ACCESS_TOKEN_PURPOSE, access, "access_token_ref"),
            (self.REFRESH_TOKEN_PURPOSE, refresh, "refresh_token_ref"),
        )
        try:
            for purpose, secret, result_key in values:
                references[result_key] = store_account_secret(
                    secret,
                    issuer_hash=identity.issuer_hash,
                    subject_hash=identity.subject_hash,
                    purpose=purpose,
                )
                written.append(purpose)
        except BaseException:
            for purpose in reversed(written):
                try:
                    if purpose in previous:
                        store_account_secret(
                            previous[purpose],
                            issuer_hash=identity.issuer_hash,
                            subject_hash=identity.subject_hash,
                            purpose=purpose,
                        )
                    else:
                        delete_account_secret(
                            issuer_hash=identity.issuer_hash,
                            subject_hash=identity.subject_hash,
                            purpose=purpose,
                        )
                except CredentialStoreError:
                    pass
            raise
        return references

    def load_oauth_tokens(self, profile: AccountProfile) -> tuple[str, str]:
        if profile.mode is not ProfileMode.ENTERPRISE_ACCOUNT:
            raise CredentialStoreError(
                "CREDENTIAL_NAMESPACE_INVALID",
                "Enterprise Account profile is required",
            )
        assert profile.issuer_hash is not None and profile.subject_hash is not None
        values: list[str] = []
        for purpose in (self.ACCESS_TOKEN_PURPOSE, self.REFRESH_TOKEN_PURPOSE):
            values.append(
                get_account_secret(
                    issuer_hash=profile.issuer_hash,
                    subject_hash=profile.subject_hash,
                    purpose=purpose,
                )
            )
        return values[0], values[1]

    def logout(self, profile: AccountProfile) -> None:
        if profile.mode is not ProfileMode.ENTERPRISE_ACCOUNT:
            return
        assert profile.issuer_hash is not None and profile.subject_hash is not None
        for purpose in (self.ACCESS_TOKEN_PURPOSE, self.REFRESH_TOKEN_PURPOSE,
                        self.OFFLINE_IDENTITY_PURPOSE):
            try:
                delete_account_secret(
                    issuer_hash=profile.issuer_hash,
                    subject_hash=profile.subject_hash,
                    purpose=purpose,
                )
            except CredentialStoreError as exc:
                if exc.code != "CREDENTIAL_NOT_FOUND":
                    raise

    def store_offline_identity(self, identity: AccountIdentity, value: str) -> None:
        from .credential_store import store_account_secret
        store_account_secret(value, issuer_hash=identity.issuer_hash,
                             subject_hash=identity.subject_hash,
                             purpose=self.OFFLINE_IDENTITY_PURPOSE)

    def load_offline_identity(self, profile: AccountProfile) -> str:
        from .credential_store import get_account_secret
        if profile.mode is not ProfileMode.ENTERPRISE_ACCOUNT:
            raise CredentialStoreError("CREDENTIAL_NAMESPACE_INVALID", "Enterprise Account profile is required")
        return get_account_secret(issuer_hash=profile.issuer_hash,
                                  subject_hash=profile.subject_hash,
                                  purpose=self.OFFLINE_IDENTITY_PURPOSE)

    def delete_offline_identity(self, profile: AccountProfile) -> None:
        from .credential_store import delete_account_secret
        if profile.mode is ProfileMode.ENTERPRISE_ACCOUNT:
            delete_account_secret(issuer_hash=profile.issuer_hash,
                                  subject_hash=profile.subject_hash,
                                  purpose=self.OFFLINE_IDENTITY_PURPOSE)


__all__ = [
    "AccountCredentialVault",
    "AccountIdentity",
    "LocalOnlyIdentity",
    "AccountPreferenceStore",
    "AccountProfile",
    "NONSECRET_ACCOUNT_PREFERENCE_KEYS",
    "ProfileMode",
    "canonicalize_issuer",
    "identity_hash",
    "issuer_hash",
    "subject_hash",
]
