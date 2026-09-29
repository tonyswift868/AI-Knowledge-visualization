"""Authenticated sync identity and visible errors; no legacy plan projection."""
from __future__ import annotations

from dataclasses import dataclass

import hashlib

class PlanError(RuntimeError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)

@dataclass(frozen=True)
class PlanScope:
    account_id: str
    issuer: str
    subject_id: str
    organization_id: str
    workspace_id: str

    def __post_init__(self):
        # Stable algorithm extracted from backend/app/account_identity_contract.py.
        if any(not isinstance(v, str) or not v or v != v.strip() for v in (self.issuer, self.subject_id, self.organization_id, self.workspace_id)):
            raise ValueError("authenticated scope required")
        issuer_hash, subject_hash = (hashlib.sha256(value.encode("utf-8")).hexdigest() for value in (self.issuer, self.subject_id))
        expected = "mfa1_" + hashlib.sha256(f"mentorflow.account.v1:{issuer_hash}:{subject_hash}".encode("ascii")).hexdigest()
        if self.account_id != expected:
            raise ValueError("stable account identity mismatch")

    def snapshot_scope(self):
        return {key: getattr(self, key) for key in ("issuer", "subject_id", "organization_id", "workspace_id")}
