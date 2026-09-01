"""Tenant-scoped server metadata for account-vault assets.

The Enterprise server persists manifests and structured references only. Raw
asset bytes and device-local filesystem paths never cross this boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import math
import re
from typing import Any, Final, Literal, Mapping, Sequence
from urllib.parse import parse_qsl, urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import text

from app.enterprise.contracts_v1 import ActorContext, TenantContext

from .context import TenantDbSession, TenantTransactionContext
from .models import EnterprisePersistenceRequestError


ASSET_MANIFEST_SCHEMA_VERSION: Final = "mentorflow.asset-manifest.v1"
ASSET_MANIFEST_CREATE_CONTRACT: Final = "mentorflow.asset-manifest.create.v1"
ASSET_MANIFEST_READ_CONTRACT: Final = "mentorflow.asset-manifest.read.v1"
LOCAL_ASSET_REFERENCE_CONTRACT: Final = "mentorflow.local-asset-reference.v1"
MISSING_ASSET_STATE_CONTRACT: Final = "mentorflow.missing-asset-state.v1"
EXPORT_MANIFEST_SCHEMA_VERSION: Final = "mentorflow.asset-manifest-export.v1"

ASSET_MANIFEST_REQUIRED_FIELDS: Final = (
    "local_asset_id",
    "filename",
    "mime_type",
    "size_bytes",
    "sha256",
    "import_mode",
    "portability",
)
ASSET_MANIFEST_OPTIONAL_FIELDS: Final = (
    "verified_source_url",
    "device_id",
    "derived_artifact_manifest",
)
ASSET_AVAILABILITY_STATES: Final = ("AVAILABLE", "MISSING", "DELETED")

_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,159}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_MIME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9!#$&^_.+\-/]{0,159}$")
_ABSOLUTE_LOCAL_PATH = re.compile(r"^(?:[A-Za-z]:[\\/]|\\\\|/)")
_SENSITIVE_QUERY = re.compile(
    r"(?:access|refresh)?token|secret|credential|password|signature|api[_-]?key",
    re.IGNORECASE,
)
_FORBIDDEN_STRUCTURED_KEY = re.compile(
    r"(?:^|[_-])(?:raw|raw[_-]?bytes?|file[_-]?bytes?|content|payload|base64|"
    r"absolute[_-]?path|local[_-]?path|"
    r"model[_-]?weights?|embedding|vector|cache|access[_-]?token|refresh[_-]?token|"
    r"credential|password|secret|authorization|cookie)(?:$|[_-])",
    re.IGNORECASE,
)


def _error(code: str, message: str, status: int) -> EnterprisePersistenceRequestError:
    return EnterprisePersistenceRequestError(code, message, status=status)


def _reject_local_path(value: str, field_name: str) -> str:
    normalized = value.strip()
    if _ABSOLUTE_LOCAL_PATH.match(normalized) or normalized.casefold().startswith(
        "file:"
    ):
        raise ValueError(f"{field_name} must not contain an absolute local path")
    return normalized


def _validate_structured_reference(value: Any, *, path: str = "derived") -> None:
    if value is None or isinstance(value, (bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{path} numeric values must be finite")
        return
    if isinstance(value, str):
        _reject_local_path(value, path)
        if len(value) > 2_048:
            raise ValueError(f"{path} string values must not exceed 2048 characters")
        return
    if isinstance(value, list):
        if len(value) > 256:
            raise ValueError(f"{path} lists must not exceed 256 items")
        for index, item in enumerate(value):
            _validate_structured_reference(item, path=f"{path}[{index}]")
        return
    if isinstance(value, dict):
        if len(value) > 256:
            raise ValueError(f"{path} objects must not exceed 256 fields")
        for raw_key, item in value.items():
            key = str(raw_key).strip()
            if not key or len(key) > 160 or _FORBIDDEN_STRUCTURED_KEY.search(key):
                raise ValueError(f"{path} contains a forbidden field")
            _validate_structured_reference(item, path=f"{path}.{key}")
        return
    raise ValueError(f"{path} must be JSON structured metadata")


class AssetManifestRegistrationIn(BaseModel):
    """Manifest supplied by the authenticated Desktop account vault."""

    model_config = ConfigDict(extra="forbid", strict=True)

    local_asset_id: str = Field(min_length=1, max_length=160)
    filename: str = Field(min_length=1, max_length=320)
    mime_type: str = Field(min_length=1, max_length=160)
    size_bytes: int = Field(ge=0, le=9_223_372_036_854_775_807)
    sha256: str = Field(min_length=64, max_length=64)
    import_mode: Literal["MANAGED", "LINKED"]
    portability: Literal["PORTABLE", "NONPORTABLE"]
    verified_source_url: str | None = Field(default=None, max_length=2_048)
    device_id: str | None = Field(default=None, max_length=160)
    derived_artifact_manifest: dict[str, Any] = Field(default_factory=dict)
    availability: Literal["AVAILABLE", "MISSING"] = "AVAILABLE"

    @field_validator("local_asset_id")
    @classmethod
    def validate_local_asset_id(cls, value: str) -> str:
        normalized = _reject_local_path(value, "local_asset_id")
        if not _IDENTIFIER.fullmatch(normalized):
            raise ValueError("local_asset_id must be an opaque identifier")
        return normalized

    @field_validator("device_id")
    @classmethod
    def validate_device_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = _reject_local_path(value, "device_id")
        if not _IDENTIFIER.fullmatch(normalized):
            raise ValueError("device_id must be an opaque identifier")
        return normalized

    @field_validator("filename")
    @classmethod
    def validate_filename(cls, value: str) -> str:
        normalized = _reject_local_path(value, "filename")
        if not normalized or "/" in normalized or "\\" in normalized:
            raise ValueError("filename must be a basename without directories")
        if any(ord(character) < 32 for character in normalized):
            raise ValueError("filename contains control characters")
        return normalized

    @field_validator("mime_type")
    @classmethod
    def validate_mime_type(cls, value: str) -> str:
        normalized = value.strip().casefold()
        if not _MIME.fullmatch(normalized) or "/" not in normalized:
            raise ValueError("mime_type is invalid")
        return normalized

    @field_validator("sha256")
    @classmethod
    def validate_sha256(cls, value: str) -> str:
        normalized = value.strip().casefold()
        if not _SHA256.fullmatch(normalized):
            raise ValueError("sha256 must be 64 lowercase hexadecimal characters")
        return normalized

    @field_validator("verified_source_url")
    @classmethod
    def validate_verified_source_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = _reject_local_path(value, "verified_source_url")
        parsed = urlsplit(normalized)
        if (
            parsed.scheme.casefold() != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
        ):
            raise ValueError("verified_source_url must be a credential-free HTTPS URL")
        if any(_SENSITIVE_QUERY.search(key) for key, _value in parse_qsl(parsed.query)):
            raise ValueError("verified_source_url must not contain credential query fields")
        return normalized

    @field_validator("derived_artifact_manifest")
    @classmethod
    def validate_derived_artifact_manifest(
        cls, value: dict[str, Any]
    ) -> dict[str, Any]:
        _validate_structured_reference(value)
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(encoded) > 65_536:
            raise ValueError("derived_artifact_manifest must not exceed 65536 bytes")
        return value

    @model_validator(mode="after")
    def validate_import_contract(self) -> "AssetManifestRegistrationIn":
        if self.import_mode == "LINKED":
            if self.portability != "NONPORTABLE" or self.device_id is None:
                raise ValueError(
                    "LINKED assets require device_id and NONPORTABLE portability"
                )
        elif self.portability != "PORTABLE":
            raise ValueError("MANAGED assets must be PORTABLE")
        return self


class AssetManifestSelectionIn(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    assets: tuple[AssetManifestRegistrationIn, ...] = Field(
        min_length=1,
        max_length=20,
    )

    @field_validator("assets", mode="before")
    @classmethod
    def accept_json_array(cls, value: Any) -> Any:
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def require_unique_local_assets(self) -> "AssetManifestSelectionIn":
        identities = [item.local_asset_id for item in self.assets]
        if len(identities) != len(set(identities)):
            raise ValueError("assets must not contain duplicate local_asset_id values")
        return self


@dataclass(frozen=True, slots=True)
class AssetManifestRecord:
    asset_id: str
    asset_revision_id: str
    issuer: str
    subject_id: str
    organization_id: str
    workspace_id: str
    local_asset_id: str
    device_id: str | None
    filename: str
    mime_type: str
    size_bytes: int
    sha256: str
    source_kind: str
    import_mode: str
    portability: str
    verified_source_url: str | None
    availability: str
    derived_artifact_manifest: Mapping[str, Any]
    created_at: datetime | str
    updated_at: datetime | str

    def public_contract(self) -> dict[str, Any]:
        return {
            "schema_version": ASSET_MANIFEST_READ_CONTRACT,
            "asset_id": self.asset_id,
            "asset_revision_id": self.asset_revision_id,
            "issuer": self.issuer,
            "subject_id": self.subject_id,
            "organization_id": self.organization_id,
            "workspace_id": self.workspace_id,
            "local_asset_id": self.local_asset_id,
            "device_id": self.device_id,
            "filename": self.filename,
            "mime_type": self.mime_type,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
            "source_kind": self.source_kind,
            "import_mode": self.import_mode,
            "portability": self.portability,
            "verified_source_url": self.verified_source_url,
            "availability": self.availability,
            "derived_artifact_manifest": dict(self.derived_artifact_manifest),
            "created_at": (
                self.created_at.isoformat()
                if isinstance(self.created_at, datetime)
                else str(self.created_at)
            ),
            "updated_at": (
                self.updated_at.isoformat()
                if isinstance(self.updated_at, datetime)
                else str(self.updated_at)
            ),
        }

    def local_reference(self) -> dict[str, Any]:
        return {
            "schema_version": LOCAL_ASSET_REFERENCE_CONTRACT,
            "asset_id": self.asset_id,
            "asset_revision_id": self.asset_revision_id,
            "local_asset_id": self.local_asset_id,
            "device_id": self.device_id,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "mime_type": self.mime_type,
            "availability": self.availability,
        }


def _account_identity(actor: ActorContext) -> str:
    return f"{actor.issuer}\0{actor.subject_id}"


def _asset_id(actor: ActorContext, tenant: TenantContext, local_asset_id: str) -> str:
    digest = hashlib.sha256(
        (
            f"{_account_identity(actor)}\0{tenant.organization_id}\0"
            f"{tenant.workspace_id}\0{local_asset_id}"
        ).encode("utf-8")
    ).hexdigest()
    return f"asset_{digest[:48]}"


def _revision_id(
    actor: ActorContext,
    tenant: TenantContext,
    local_asset_id: str,
    sha256: str,
) -> str:
    digest = hashlib.sha256(
        (
            f"{_account_identity(actor)}\0{tenant.organization_id}\0"
            f"{tenant.workspace_id}\0{local_asset_id}\0{sha256}"
        ).encode("utf-8")
    ).hexdigest()
    return f"assetrev_{digest[:48]}"


def _json_parameter(connection, value: Mapping[str, Any]) -> str:
    return json.dumps(
        dict(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _json_expression(connection, parameter: str) -> str:
    return (
        f"CAST(:{parameter} AS JSONB)"
        if connection.dialect.name == "postgresql"
        else f":{parameter}"
    )


class AssetManifestRepository:
    """Exact tenant/account queries supplement database-enforced RLS."""

    _SELECT = """
        SELECT asset_id, current_revision_id, issuer, subject_id,
          organization_id, workspace_id, local_asset_id, device_id,
          filename, mime_type, size_bytes, sha256, source_kind,
          import_mode, portability, verified_source_url, availability,
          derived_artifact_json, created_at, updated_at
        FROM asset_manifests
    """

    @staticmethod
    def _record(row: Any) -> AssetManifestRecord:
        values = row._mapping
        derived = values["derived_artifact_json"]
        if isinstance(derived, str):
            derived = json.loads(derived)
        return AssetManifestRecord(
            asset_id=str(values["asset_id"]),
            asset_revision_id=str(values["current_revision_id"]),
            issuer=str(values["issuer"]),
            subject_id=str(values["subject_id"]),
            organization_id=str(values["organization_id"]),
            workspace_id=str(values["workspace_id"]),
            local_asset_id=str(values["local_asset_id"]),
            device_id=(
                None if values["device_id"] is None else str(values["device_id"])
            ),
            filename=str(values["filename"]),
            mime_type=str(values["mime_type"]),
            size_bytes=int(values["size_bytes"]),
            sha256=str(values["sha256"]),
            source_kind=str(values["source_kind"]),
            import_mode=str(values["import_mode"]),
            portability=str(values["portability"]),
            verified_source_url=(
                None
                if values["verified_source_url"] is None
                else str(values["verified_source_url"])
            ),
            availability=str(values["availability"]),
            derived_artifact_manifest=dict(derived or {}),
            created_at=values["created_at"],
            updated_at=values["updated_at"],
        )

    @staticmethod
    def _scope(transaction: TenantDbSession) -> dict[str, Any]:
        if transaction.connection.dialect.name == "postgresql":
            transaction.connection.execute(
                text(
                    "SELECT set_config("
                    "'mentorflow.actor_issuer', :actor_issuer, true)"
                ),
                {"actor_issuer": transaction.actor.issuer},
            )
        return {
            "issuer": transaction.actor.issuer,
            "subject_id": transaction.actor.subject_id,
            "organization_id": transaction.tenant.organization_id,
            "workspace_id": transaction.tenant.workspace_id,
        }

    def upsert(
        self,
        transaction: TenantDbSession,
        manifest: AssetManifestRegistrationIn,
    ) -> AssetManifestRecord:
        connection = transaction.connection
        asset_id = _asset_id(transaction.actor, transaction.tenant, manifest.local_asset_id)
        revision_id = _revision_id(
            transaction.actor,
            transaction.tenant,
            manifest.local_asset_id,
            manifest.sha256,
        )
        source_kind = (
            "ACCOUNT_VAULT" if manifest.import_mode == "MANAGED" else "DEVICE_LINK"
        )
        parameters = {
            **self._scope(transaction),
            "asset_id": asset_id,
            "asset_revision_id": revision_id,
            "local_asset_id": manifest.local_asset_id,
            "device_id": manifest.device_id,
            "filename": manifest.filename,
            "mime_type": manifest.mime_type,
            "size_bytes": manifest.size_bytes,
            "sha256": manifest.sha256,
            "source_kind": source_kind,
            "import_mode": manifest.import_mode,
            "portability": manifest.portability,
            "verified_source_url": manifest.verified_source_url,
            "availability": manifest.availability,
            "derived_artifact_json": _json_parameter(
                connection, manifest.derived_artifact_manifest
            ),
        }
        derived_expression = _json_expression(connection, "derived_artifact_json")
        connection.execute(
            text(
                f"""
                INSERT INTO asset_manifests(
                  asset_id, current_revision_id, issuer, subject_id,
                  organization_id, workspace_id, local_asset_id, device_id,
                  filename, mime_type, size_bytes, sha256, source_kind,
                  import_mode, portability, verified_source_url, availability,
                  derived_artifact_json
                ) VALUES (
                  :asset_id, :asset_revision_id, :issuer, :subject_id,
                  :organization_id, :workspace_id, :local_asset_id, :device_id,
                  :filename, :mime_type, :size_bytes, :sha256, :source_kind,
                  :import_mode, :portability, :verified_source_url, :availability,
                  {derived_expression}
                )
                ON CONFLICT (asset_id) DO UPDATE SET
                  current_revision_id = EXCLUDED.current_revision_id,
                  device_id = EXCLUDED.device_id,
                  filename = EXCLUDED.filename,
                  mime_type = EXCLUDED.mime_type,
                  size_bytes = EXCLUDED.size_bytes,
                  sha256 = EXCLUDED.sha256,
                  source_kind = EXCLUDED.source_kind,
                  import_mode = EXCLUDED.import_mode,
                  portability = EXCLUDED.portability,
                  verified_source_url = EXCLUDED.verified_source_url,
                  availability = EXCLUDED.availability,
                  derived_artifact_json = EXCLUDED.derived_artifact_json,
                  deleted_at = NULL,
                  updated_at = CURRENT_TIMESTAMP
                """
            ),
            parameters,
        )
        connection.execute(
            text(
                f"""
                INSERT INTO asset_revisions(
                  asset_revision_id, asset_id, issuer, subject_id,
                  organization_id, workspace_id, local_asset_id, device_id,
                  filename, mime_type, size_bytes, sha256, source_kind,
                  import_mode, portability, verified_source_url,
                  derived_artifact_json
                ) VALUES (
                  :asset_revision_id, :asset_id, :issuer, :subject_id,
                  :organization_id, :workspace_id, :local_asset_id, :device_id,
                  :filename, :mime_type, :size_bytes, :sha256, :source_kind,
                  :import_mode, :portability, :verified_source_url,
                  {derived_expression}
                )
                ON CONFLICT (asset_revision_id) DO NOTHING
                """
            ),
            parameters,
        )
        record = self.get(transaction, asset_id)
        if record is None or record.asset_revision_id != revision_id:
            raise RuntimeError("asset manifest upsert did not preserve revision identity")
        return record

    def get(
        self,
        transaction: TenantDbSession,
        asset_id: str,
    ) -> AssetManifestRecord | None:
        row = transaction.connection.execute(
            text(
                self._SELECT
                + """
                WHERE asset_id = :asset_id
                  AND issuer = :issuer
                  AND subject_id = :subject_id
                  AND organization_id = :organization_id
                  AND workspace_id = :workspace_id
                """
            ),
            {**self._scope(transaction), "asset_id": asset_id},
        ).first()
        return None if row is None else self._record(row)

    def get_current_revision(
        self,
        transaction: TenantDbSession,
        asset_revision_id: str,
    ) -> AssetManifestRecord | None:
        row = transaction.connection.execute(
            text(
                self._SELECT
                + """
                WHERE current_revision_id = :asset_revision_id
                  AND issuer = :issuer
                  AND subject_id = :subject_id
                  AND organization_id = :organization_id
                  AND workspace_id = :workspace_id
                """
            ),
            {
                **self._scope(transaction),
                "asset_revision_id": asset_revision_id,
            },
        ).first()
        return None if row is None else self._record(row)

    def list_records(
        self,
        transaction: TenantDbSession,
        *,
        include_deleted: bool = False,
        limit: int = 200,
    ) -> tuple[AssetManifestRecord, ...]:
        if limit < 1 or limit > 10_000:
            raise ValueError("limit must be between 1 and 10000")
        deleted = "" if include_deleted else " AND availability <> 'DELETED'"
        rows = transaction.connection.execute(
            text(
                self._SELECT
                + f"""
                WHERE issuer = :issuer
                  AND subject_id = :subject_id
                  AND organization_id = :organization_id
                  AND workspace_id = :workspace_id
                  {deleted}
                ORDER BY updated_at DESC, asset_id
                LIMIT :limit
                """
            ),
            {**self._scope(transaction), "limit": limit},
        )
        return tuple(self._record(row) for row in rows)

    def set_availability(
        self,
        transaction: TenantDbSession,
        asset_id: str,
        availability: Literal["AVAILABLE", "MISSING"],
    ) -> AssetManifestRecord | None:
        transaction.connection.execute(
            text(
                """
                UPDATE asset_manifests
                SET availability = :availability,
                    deleted_at = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE asset_id = :asset_id
                  AND issuer = :issuer
                  AND subject_id = :subject_id
                  AND organization_id = :organization_id
                  AND workspace_id = :workspace_id
                """
            ),
            {
                **self._scope(transaction),
                "asset_id": asset_id,
                "availability": availability,
            },
        )
        return self.get(transaction, asset_id)

    def tombstone(
        self,
        transaction: TenantDbSession,
        asset_id: str,
    ) -> AssetManifestRecord | None:
        transaction.connection.execute(
            text(
                """
                UPDATE asset_manifests
                SET availability = 'DELETED',
                    deleted_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE asset_id = :asset_id
                  AND issuer = :issuer
                  AND subject_id = :subject_id
                  AND organization_id = :organization_id
                  AND workspace_id = :workspace_id
                """
            ),
            {**self._scope(transaction), "asset_id": asset_id},
        )
        return self.get(transaction, asset_id)


class EnterpriseAssetManifestService:
    """Public tenant-safe manifest CRUD for Desktop and export owners."""

    def __init__(self, transaction_manager, *, repository=None) -> None:
        self.transactions = transaction_manager
        self.repository = repository or AssetManifestRepository()

    @staticmethod
    def _context(actor: ActorContext, tenant: TenantContext) -> TenantTransactionContext:
        return TenantTransactionContext(tenant=tenant, actor=actor)

    def get(
        self,
        *,
        actor: ActorContext,
        tenant: TenantContext,
        asset_id: str,
    ) -> dict[str, Any]:
        with self.transactions.transaction(self._context(actor, tenant)) as transaction:
            record = self.repository.get(transaction, asset_id)
            if record is None:
                raise _error("ASSET_MANIFEST_NOT_FOUND", "Asset manifest was not found", 404)
            return record.public_contract()

    def list(
        self,
        *,
        actor: ActorContext,
        tenant: TenantContext,
    ) -> dict[str, Any]:
        with self.transactions.transaction(self._context(actor, tenant)) as transaction:
            records = self.repository.list_records(transaction)
            return {
                "schema_version": ASSET_MANIFEST_READ_CONTRACT,
                "assets": [record.public_contract() for record in records],
            }

    def export_manifest(
        self,
        *,
        actor: ActorContext,
        tenant: TenantContext,
    ) -> dict[str, Any]:
        """Return metadata and tombstones only; E1-05 owns raw-vault packaging."""

        with self.transactions.transaction(self._context(actor, tenant)) as transaction:
            records = self.repository.list_records(
                transaction,
                include_deleted=True,
                limit=10_000,
            )
            return {
                "schema_version": EXPORT_MANIFEST_SCHEMA_VERSION,
                "assets": [record.public_contract() for record in records],
            }

    def set_missing(
        self,
        *,
        actor: ActorContext,
        tenant: TenantContext,
        asset_id: str,
    ) -> dict[str, Any]:
        with self.transactions.transaction(self._context(actor, tenant)) as transaction:
            record = self.repository.set_availability(transaction, asset_id, "MISSING")
            if record is None:
                raise _error("ASSET_MANIFEST_NOT_FOUND", "Asset manifest was not found", 404)
            payload = record.public_contract()
            payload["missing_state_contract"] = MISSING_ASSET_STATE_CONTRACT
            return payload

    def tombstone(
        self,
        *,
        actor: ActorContext,
        tenant: TenantContext,
        asset_id: str,
    ) -> dict[str, Any]:
        with self.transactions.transaction(self._context(actor, tenant)) as transaction:
            record = self.repository.tombstone(transaction, asset_id)
            if record is None:
                raise _error("ASSET_MANIFEST_NOT_FOUND", "Asset manifest was not found", 404)
            payload = record.public_contract()
            payload["missing_state_contract"] = MISSING_ASSET_STATE_CONTRACT
            return payload


def validate_asset_references(
    values: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Any], ...]:
    """Fail closed before durable jobs persist Desktop manifest references."""

    if not values or len(values) > 20:
        raise ValueError("asset manifest references must contain between 1 and 20 items")
    normalized: list[dict[str, Any]] = []
    revision_ids: set[str] = set()
    allowed = {
        "schema_version",
        "asset_id",
        "asset_revision_id",
        "local_asset_id",
        "device_id",
        "sha256",
        "size_bytes",
        "mime_type",
        "availability",
    }
    for value in values:
        reference = dict(value)
        if set(reference) != allowed:
            raise ValueError("asset manifest reference fields are invalid")
        if reference.get("schema_version") != LOCAL_ASSET_REFERENCE_CONTRACT:
            raise ValueError("asset manifest reference schema is invalid")
        for field in ("asset_id", "asset_revision_id", "local_asset_id"):
            identifier = str(reference.get(field) or "")
            if not _IDENTIFIER.fullmatch(identifier):
                raise ValueError(f"{field} is not a valid opaque identifier")
        device_id = reference.get("device_id")
        if device_id is not None and not _IDENTIFIER.fullmatch(str(device_id)):
            raise ValueError("device_id is not a valid opaque identifier")
        sha256 = str(reference.get("sha256") or "")
        if not _SHA256.fullmatch(sha256):
            raise ValueError("asset manifest reference sha256 is invalid")
        size_bytes = reference.get("size_bytes")
        if type(size_bytes) is not int or size_bytes < 0:
            raise ValueError("asset manifest reference size_bytes is invalid")
        mime_type = str(reference.get("mime_type") or "")
        if not _MIME.fullmatch(mime_type) or "/" not in mime_type:
            raise ValueError("asset manifest reference mime_type is invalid")
        if reference.get("availability") != "AVAILABLE":
            raise ValueError("asset manifest reference is not locally available")
        revision_id = str(reference.get("asset_revision_id") or "")
        if not _IDENTIFIER.fullmatch(revision_id) or revision_id in revision_ids:
            raise ValueError("asset revision reference is invalid or duplicated")
        revision_ids.add(revision_id)
        _validate_structured_reference(reference, path="asset_reference")
        normalized.append(reference)
    return tuple(normalized)


__all__ = [
    "ASSET_AVAILABILITY_STATES",
    "ASSET_MANIFEST_CREATE_CONTRACT",
    "ASSET_MANIFEST_OPTIONAL_FIELDS",
    "ASSET_MANIFEST_READ_CONTRACT",
    "ASSET_MANIFEST_REQUIRED_FIELDS",
    "ASSET_MANIFEST_SCHEMA_VERSION",
    "AssetManifestRecord",
    "AssetManifestRegistrationIn",
    "AssetManifestRepository",
    "AssetManifestSelectionIn",
    "EnterpriseAssetManifestService",
    "EXPORT_MANIFEST_SCHEMA_VERSION",
    "LOCAL_ASSET_REFERENCE_CONTRACT",
    "MISSING_ASSET_STATE_CONTRACT",
    "validate_asset_references",
]
