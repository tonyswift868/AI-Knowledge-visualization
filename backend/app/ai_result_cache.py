from __future__ import annotations

import json
import os
import secrets
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Mapping

from .providers.telemetry import (
    provider_request_context,
    record_local_result_cache_hit,
)


LOCAL_AI_RESULT_CACHE_SCHEMA_VERSION = "local_ai_result_cache.v1"
DEFAULT_CACHE_TTL_SECONDS = 7 * 24 * 60 * 60
MAX_CACHE_ENTRY_BYTES = 1024 * 1024
MAX_CACHE_DIRECTORY_BYTES = 64 * 1024 * 1024
_LOCK_STRIPES = 64
_SHA256_LENGTH = 64
_CACHE_LOCKS = tuple(threading.Lock() for _ in range(_LOCK_STRIPES))
_CACHE_DIRECTORY_LOCK = threading.Lock()


class LocalAiCachePolicy(str, Enum):
    ENABLED = "enabled"
    DISABLED = "disabled"


DETERMINISTIC_CACHE_TASK_TYPES = frozenset(
    {
        "knowledge_extract",
        "knowledge_verify",
        "map_relation_candidate",
        "transcript_terminology_normalization",
        "static_material_analysis",
    }
)


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _sha256_text(value: str) -> str:
    import hashlib

    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def canonical_fingerprint(value: object) -> str:
    """Hash caller content in memory without retaining the raw value."""

    return _sha256_text(_canonical_json(value))


def _required_text(value: object, *, field: str) -> str:
    result = str(value or "").strip()
    if not result:
        raise ValueError(f"{field} is required")
    return result


def _optional_sha256(value: object, *, field: str) -> str | None:
    result = str(value or "").strip().casefold()
    if not result:
        return None
    if len(result) != _SHA256_LENGTH or any(char not in "0123456789abcdef" for char in result):
        raise ValueError(f"{field} must be a SHA-256 hex digest")
    return result


@dataclass(frozen=True)
class AiResultCacheIdentity:
    provider_family: str
    model: str
    task_type: str
    prompt_version: str
    schema_version: str
    stable_prefix_hash: str | None
    dynamic_input_hash: str
    source_identity_hash: str | None
    generation_parameters_hash: str
    processor_version: str

    def canonical_payload(self) -> dict[str, str | None]:
        # Every field is always present.  Inapplicable values use explicit null
        # rather than disappearing and creating ambiguous cache identities.
        return {
            "provider_family": self.provider_family,
            "model": self.model,
            "task_type": self.task_type,
            "prompt_version": self.prompt_version,
            "schema_version": self.schema_version,
            "stable_prefix_hash": self.stable_prefix_hash,
            "dynamic_input_hash": self.dynamic_input_hash,
            "source_identity_hash": self.source_identity_hash,
            "generation_parameters_hash": self.generation_parameters_hash,
            "processor_version": self.processor_version,
        }

    @property
    def cache_key(self) -> str:
        return canonical_fingerprint(self.canonical_payload())


def build_ai_result_cache_identity(
    *,
    provider_family: str,
    model: str,
    task_type: str,
    prompt_version: str,
    schema_version: str,
    stable_prefix_hash: str | None,
    dynamic_input: object,
    source_identity: object | None,
    generation_parameters: Mapping[str, Any] | None,
    processor_version: str,
) -> AiResultCacheIdentity:
    return AiResultCacheIdentity(
        provider_family=_required_text(provider_family, field="provider_family"),
        model=_required_text(model, field="model"),
        task_type=_required_text(task_type, field="task_type"),
        prompt_version=_required_text(prompt_version, field="prompt_version"),
        schema_version=_required_text(schema_version, field="schema_version"),
        stable_prefix_hash=_optional_sha256(
            stable_prefix_hash,
            field="stable_prefix_hash",
        ),
        dynamic_input_hash=canonical_fingerprint(dynamic_input),
        source_identity_hash=(
            canonical_fingerprint(source_identity)
            if source_identity is not None
            else None
        ),
        generation_parameters_hash=canonical_fingerprint(
            dict(generation_parameters or {})
        ),
        processor_version=_required_text(
            processor_version,
            field="processor_version",
        ),
    )


@dataclass(frozen=True)
class AiResultCacheEntry:
    cache_key: str
    created_at: str
    expires_at: str
    identity: dict[str, str | None]
    result_payload: Any
    result_hash: str
    validation_status: str = "validated"
    cache_schema_version: str = LOCAL_AI_RESULT_CACHE_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "cache_schema_version": self.cache_schema_version,
            "cache_key": self.cache_key,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "identity": self.identity,
            "result_payload": self.result_payload,
            "result_hash": self.result_hash,
            "validation_status": self.validation_status,
        }


@dataclass(frozen=True)
class CacheLookupResult:
    hit: bool
    entry: AiResultCacheEntry | None
    corrupt: bool
    reason: str
    lookup_ms: int


@dataclass(frozen=True)
class CacheWriteResult:
    stored: bool
    reason: str
    write_ms: int


@dataclass(frozen=True)
class AiComputationResult:
    payload: Any
    validated: bool

    @classmethod
    def validated_result(cls, payload: Any) -> "AiComputationResult":
        return cls(payload=payload, validated=True)

    @classmethod
    def unvalidated_result(cls, payload: Any) -> "AiComputationResult":
        return cls(payload=payload, validated=False)


@dataclass(frozen=True)
class AiResultCacheExecution:
    payload: Any
    cache_hit: bool
    cache_miss: bool
    cache_corrupt: bool
    cache_lookup_ms: int
    cache_write_ms: int | None
    provider_called: bool
    cache_write_stored: bool
    cache_reason: str


def default_ai_result_cache_root() -> Path:
    explicit = str(os.getenv("MENTORFLOW_AI_RESULT_CACHE_ROOT") or "").strip()
    if explicit:
        return Path(explicit).expanduser().resolve()
    user_root = str(os.getenv("MENTORFLOW_USER_DATA_ROOT") or "").strip()
    if not user_root:
        raise RuntimeError("MentorFlow user data root is required for local AI result cache")
    return (Path(user_root).expanduser().resolve() / "cache" / "ai-results")


def effective_cache_policy(
    task_type: str,
    requested: LocalAiCachePolicy = LocalAiCachePolicy.DISABLED,
) -> LocalAiCachePolicy:
    if requested is not LocalAiCachePolicy.ENABLED:
        return LocalAiCachePolicy.DISABLED
    if str(task_type or "").strip() not in DETERMINISTIC_CACHE_TASK_TYPES:
        return LocalAiCachePolicy.DISABLED
    return LocalAiCachePolicy.ENABLED


def _nonempty_payload(value: object) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, dict, set)):
        return bool(value)
    return True


class LocalAiResultCache:
    def __init__(
        self,
        root: Path | None = None,
        *,
        ttl_seconds: int = DEFAULT_CACHE_TTL_SECONDS,
        max_entry_bytes: int = MAX_CACHE_ENTRY_BYTES,
        max_directory_bytes: int = MAX_CACHE_DIRECTORY_BYTES,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if int(ttl_seconds) <= 0:
            raise ValueError("ttl_seconds must be positive")
        if int(max_entry_bytes) <= 0 or int(max_directory_bytes) <= 0:
            raise ValueError("cache size limits must be positive")
        self.root = (root or default_ai_result_cache_root()).resolve()
        self.ttl_seconds = int(ttl_seconds)
        self.max_entry_bytes = int(max_entry_bytes)
        self.max_directory_bytes = int(max_directory_bytes)
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def _path(self, cache_key: str) -> Path:
        return self.root / cache_key[:2] / f"{cache_key}.json"

    def cache_path(self, identity: AiResultCacheIdentity) -> Path:
        return self._path(identity.cache_key)

    def _lock(self, cache_key: str) -> threading.Lock:
        return _CACHE_LOCKS[int(cache_key[:8], 16) % len(_CACHE_LOCKS)]

    def _directory_size(self) -> int:
        if not self.root.is_dir():
            return 0
        total = 0
        try:
            for path in self.root.rglob("*.json"):
                if path.is_file():
                    total += path.stat().st_size
        except OSError:
            return self.max_directory_bytes
        return total

    def lookup(
        self,
        identity: AiResultCacheIdentity,
        *,
        cache_policy: LocalAiCachePolicy = LocalAiCachePolicy.DISABLED,
    ) -> CacheLookupResult:
        started = time.perf_counter()

        def result(
            *,
            hit: bool = False,
            entry: AiResultCacheEntry | None = None,
            corrupt: bool = False,
            reason: str,
        ) -> CacheLookupResult:
            return CacheLookupResult(
                hit=hit,
                entry=entry,
                corrupt=corrupt,
                reason=reason,
                lookup_ms=max(0, int((time.perf_counter() - started) * 1000)),
            )

        if effective_cache_policy(identity.task_type, cache_policy) is LocalAiCachePolicy.DISABLED:
            return result(reason="policy_disabled")
        path = self._path(identity.cache_key)
        try:
            if not path.is_file():
                return result(reason="not_found")
            if path.stat().st_size > self.max_entry_bytes:
                return result(corrupt=True, reason="entry_oversized")
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return result(corrupt=True, reason="cache_corrupt")
        if not isinstance(payload, dict):
            return result(corrupt=True, reason="cache_corrupt")
        try:
            if payload.get("cache_schema_version") != LOCAL_AI_RESULT_CACHE_SCHEMA_VERSION:
                return result(corrupt=True, reason="schema_mismatch")
            if payload.get("cache_key") != identity.cache_key:
                return result(corrupt=True, reason="key_mismatch")
            if payload.get("identity") != identity.canonical_payload():
                return result(corrupt=True, reason="identity_mismatch")
            if payload.get("validation_status") != "validated":
                return result(corrupt=True, reason="not_validated")
            created_at = datetime.fromisoformat(str(payload.get("created_at") or ""))
            expires_at = datetime.fromisoformat(str(payload.get("expires_at") or ""))
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            now = self._clock()
            if now.tzinfo is None:
                now = now.replace(tzinfo=timezone.utc)
            if created_at > now:
                return result(corrupt=True, reason="created_at_invalid")
            if expires_at <= created_at:
                return result(corrupt=True, reason="expires_at_invalid")
            if expires_at > created_at + timedelta(seconds=self.ttl_seconds):
                return result(corrupt=True, reason="ttl_mismatch")
            if expires_at <= now:
                return result(reason="expired")
            result_payload = payload.get("result_payload")
            result_hash = str(payload.get("result_hash") or "")
            if not _nonempty_payload(result_payload):
                return result(corrupt=True, reason="empty_result")
            if canonical_fingerprint(result_payload) != result_hash:
                return result(corrupt=True, reason="result_hash_mismatch")
            entry = AiResultCacheEntry(
                cache_key=identity.cache_key,
                created_at=created_at.isoformat(),
                expires_at=expires_at.isoformat(),
                identity=dict(payload["identity"]),
                result_payload=result_payload,
                result_hash=result_hash,
            )
        except (TypeError, ValueError, OverflowError):
            return result(corrupt=True, reason="cache_corrupt")
        return result(hit=True, entry=entry, reason="hit")

    def store(
        self,
        identity: AiResultCacheIdentity,
        result_payload: Any,
        *,
        validated: bool,
        cache_policy: LocalAiCachePolicy = LocalAiCachePolicy.DISABLED,
    ) -> CacheWriteResult:
        started = time.perf_counter()

        def result(stored: bool, reason: str) -> CacheWriteResult:
            return CacheWriteResult(
                stored=stored,
                reason=reason,
                write_ms=max(0, int((time.perf_counter() - started) * 1000)),
            )

        if effective_cache_policy(identity.task_type, cache_policy) is LocalAiCachePolicy.DISABLED:
            return result(False, "policy_disabled")
        if not validated:
            return result(False, "not_validated")
        if not _nonempty_payload(result_payload):
            return result(False, "empty_result")
        try:
            now = self._clock()
            entry = AiResultCacheEntry(
                cache_key=identity.cache_key,
                created_at=now.isoformat(),
                expires_at=(now + timedelta(seconds=self.ttl_seconds)).isoformat(),
                identity=identity.canonical_payload(),
                result_payload=result_payload,
                result_hash=canonical_fingerprint(result_payload),
            )
            encoded = _canonical_json(entry.to_dict()).encode("utf-8")
        except (TypeError, ValueError, OverflowError):
            return result(False, "result_not_serializable")
        if len(encoded) > self.max_entry_bytes:
            return result(False, "entry_too_large")
        path = self._path(identity.cache_key)
        with _CACHE_DIRECTORY_LOCK:
            try:
                existing_size = path.stat().st_size if path.is_file() else 0
            except OSError:
                return result(False, "write_failed")
            if self._directory_size() - existing_size + len(encoded) > self.max_directory_bytes:
                return result(False, "directory_limit")
            temporary: Path | None = None
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                temporary = path.parent / (
                    f".{identity.cache_key}.{os.getpid()}.{secrets.token_hex(6)}.tmp"
                )
                with temporary.open("xb") as handle:
                    handle.write(encoded)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, path)
            except OSError:
                if temporary is not None:
                    try:
                        temporary.unlink(missing_ok=True)
                    except OSError:
                        pass
                return result(False, "write_failed")
        return result(True, "stored")

    def get_or_compute(
        self,
        identity: AiResultCacheIdentity,
        compute: Callable[[], AiComputationResult],
        *,
        cache_policy: LocalAiCachePolicy = LocalAiCachePolicy.DISABLED,
    ) -> AiResultCacheExecution:
        effective = effective_cache_policy(identity.task_type, cache_policy)
        if effective is LocalAiCachePolicy.DISABLED:
            computed = compute()
            if not isinstance(computed, AiComputationResult):
                raise TypeError("compute must return AiComputationResult")
            return AiResultCacheExecution(
                payload=computed.payload,
                cache_hit=False,
                cache_miss=False,
                cache_corrupt=False,
                cache_lookup_ms=0,
                cache_write_ms=None,
                provider_called=True,
                cache_write_stored=False,
                cache_reason="policy_disabled",
            )

        with self._lock(identity.cache_key):
            lookup = self.lookup(identity, cache_policy=effective)
            if lookup.hit and lookup.entry is not None:
                record_local_result_cache_hit(
                    provider=identity.provider_family,
                    model=identity.model,
                    task_type=identity.task_type,
                    prompt_version=identity.prompt_version,
                    stable_prefix_hash=identity.stable_prefix_hash,
                    lookup_ms=lookup.lookup_ms,
                    cache_corrupt=lookup.corrupt,
                )
                return AiResultCacheExecution(
                    payload=lookup.entry.result_payload,
                    cache_hit=True,
                    cache_miss=False,
                    cache_corrupt=False,
                    cache_lookup_ms=lookup.lookup_ms,
                    cache_write_ms=None,
                    provider_called=False,
                    cache_write_stored=False,
                    cache_reason="hit",
                )
            with provider_request_context(
                task_type=identity.task_type,
                prompt_version=identity.prompt_version,
                local_result_cache_hit=False,
                stable_prefix_hash=identity.stable_prefix_hash,
                local_cache_miss=True,
                local_cache_corrupt=lookup.corrupt,
                cache_lookup_ms=lookup.lookup_ms,
            ):
                computed = compute()
            if not isinstance(computed, AiComputationResult):
                raise TypeError("compute must return AiComputationResult")
            write = self.store(
                identity,
                computed.payload,
                validated=computed.validated,
                cache_policy=effective,
            )
            return AiResultCacheExecution(
                payload=computed.payload,
                cache_hit=False,
                cache_miss=True,
                cache_corrupt=lookup.corrupt,
                cache_lookup_ms=lookup.lookup_ms,
                cache_write_ms=write.write_ms,
                provider_called=True,
                cache_write_stored=write.stored,
                cache_reason=write.reason,
            )
