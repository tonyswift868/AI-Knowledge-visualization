"""Minimal product roles, independent of tenant roles and infrastructure roles.

The deployment operator provisions immutable canonical account IDs in Backend
configuration. All other accounts, including newly registered accounts, are
LEARNER. Usernames, emails, IdP role claims and client input cannot grant roles.
No SQL privilege or tenant/RLS policy is changed by this projection.
"""
from dataclasses import dataclass
from enum import Enum
import json
import re
from collections.abc import Mapping

from refactor.server.account_identity_contract import account_id
from refactor.server.enterprise.contracts_v1 import ActorKind
from .oidc import IdentityError


SUPER_ADMIN_IDS_ENV = "MENTORFLOW_APPLICATION_SUPER_ADMIN_ACCOUNT_IDS"


class ApplicationRole(str, Enum):
    SUPER_ADMIN = "SUPER_ADMIN"
    LEARNER = "LEARNER"


@dataclass(frozen=True)
class ApplicationRolePolicy:
    super_admin_account_ids: frozenset[str] = frozenset()

    def __post_init__(self):
        values = self.super_admin_account_ids
        if not isinstance(values, frozenset) or len(values) > 32 or any(
            not isinstance(value, str) or not re.fullmatch(r"mfa1_[0-9a-f]{64}", value)
            for value in values
        ):
            raise ValueError("Application roles require canonical account IDs")

    @classmethod
    def from_env(cls, source: Mapping[str, str]):
        raw = source.get(SUPER_ADMIN_IDS_ENV, "[]")
        if not isinstance(raw, str) or len(raw) > 4096:
            raise ValueError("Invalid application-role configuration")
        try:
            values = json.loads(raw)
        except (ValueError, TypeError) as exc:
            raise ValueError("Invalid application-role configuration") from exc
        if not isinstance(values, list) or any(not isinstance(v, str) for v in values) or len(values) != len(set(values)):
            raise ValueError("Application-role configuration must be a unique account ID list")
        return cls(frozenset(values))

    def role_for(self, actor) -> ApplicationRole:
        if actor.actor_kind is ActorKind.HUMAN and account_id(actor.issuer, actor.subject_id) in self.super_admin_account_ids:
            return ApplicationRole.SUPER_ADMIN
        return ApplicationRole.LEARNER


def reject_client_role_assertions(request) -> None:
    if any(name in request.headers for name in (
        "X-MentorFlow-Role", "X-MentorFlow-Product-Role", "X-MentorFlow-Account-Role",
    )) or any(name in request.query_params for name in ("role", "product_role", "account_role")):
        raise IdentityError("APPLICATION_ROLE_CLIENT_ASSERTION_REJECTED",
                            "Application roles are assigned by the server", status=403)
