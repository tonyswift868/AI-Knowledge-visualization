from __future__ import annotations

import hashlib
import ipaddress
import json
from dataclasses import dataclass, field
from enum import Enum
from urllib.parse import urlsplit

from .capabilities import ProviderCapabilityProfile


class BaseURLClass(str, Enum):
    UNCONFIGURED = "unconfigured"
    LOCAL = "local"
    OFFICIAL_OPENAI = "official_openai"
    OFFICIAL_DEEPSEEK = "official_deepseek"
    OFFICIAL_ANTHROPIC = "official_anthropic"
    OFFICIAL_GOOGLE = "official_google"
    CUSTOM_REMOTE = "custom_remote"
    UNCLASSIFIED = "unclassified"


class ProviderProfileSource(str, Enum):
    USER_DECLARED = "user_declared"
    ADAPTER_DEFAULT = "adapter_default"
    TRUSTED_CATALOG = "trusted_catalog"
    RUNTIME_QUALIFICATION = "runtime_qualification"


def normalize_adapter_identity(value: str) -> str:
    normalized = str(value or "").strip().lower().replace("-", "_")
    if not normalized:
        raise ValueError("adapter_identity must be non-empty")
    return normalized


def _known_base_url_class(host: str) -> BaseURLClass | None:
    suffixes = (
        ("openai.com", BaseURLClass.OFFICIAL_OPENAI),
        ("deepseek.com", BaseURLClass.OFFICIAL_DEEPSEEK),
        ("anthropic.com", BaseURLClass.OFFICIAL_ANTHROPIC),
        ("googleapis.com", BaseURLClass.OFFICIAL_GOOGLE),
    )
    return next(
        (
            classification
            for suffix, classification in suffixes
            if host == suffix or host.endswith("." + suffix)
        ),
        None,
    )


def classify_base_url(configured_base_url: str | None) -> BaseURLClass:
    raw = str(configured_base_url or "").strip()
    if not raw:
        return BaseURLClass.UNCONFIGURED
    try:
        parsed = urlsplit(raw)
        host = (parsed.hostname or "").strip().lower().rstrip(".")
    except ValueError:
        return BaseURLClass.UNCLASSIFIED
    if not host:
        return BaseURLClass.UNCLASSIFIED
    if host == "localhost" or host.endswith(".localhost"):
        return BaseURLClass.LOCAL
    try:
        if ipaddress.ip_address(host).is_loopback:
            return BaseURLClass.LOCAL
    except ValueError:
        pass
    return _known_base_url_class(host) or BaseURLClass.CUSTOM_REMOTE


def _endpoint_scope(configured_base_url: str | None) -> str:
    raw = str(configured_base_url or "").strip()
    if not raw:
        return "endpoint_none"
    try:
        parsed = urlsplit(raw)
        host = (parsed.hostname or "").strip().lower().rstrip(".")
        scheme = parsed.scheme.strip().lower()
        port = parsed.port
        if port is None:
            port = 443 if scheme == "https" else 80 if scheme == "http" else 0
        path = "/" + "/".join(part for part in parsed.path.split("/") if part)
        canonical = f"{scheme}|{host}|{port}|{path}"
    except ValueError:
        canonical = "unclassified|" + raw
    return "endpoint_" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:20]


def capability_profile_id(profile: ProviderCapabilityProfile) -> str:
    payload = {
        "capabilities": sorted(capability.value for capability in profile.capabilities),
        "context_window": profile.context_window,
        "routing_classes": sorted(value.value for value in profile.routing_classes),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return "cap_" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:20]


@dataclass(frozen=True, slots=True)
class ProviderProfileRegistration:
    adapter_identity: str
    profile: ProviderCapabilityProfile
    configured_base_url: str | None = field(default=None, repr=False)
    source: ProviderProfileSource = ProviderProfileSource.USER_DECLARED

    def __post_init__(self) -> None:
        adapter_identity = normalize_adapter_identity(self.adapter_identity)
        profile_adapter = normalize_adapter_identity(self.profile.provider)
        source = self.source
        if not isinstance(source, ProviderProfileSource):
            source = ProviderProfileSource(str(source).strip().lower())
        if adapter_identity != profile_adapter:
            raise ValueError("profile provider must match adapter_identity")
        object.__setattr__(self, "adapter_identity", adapter_identity)
        object.__setattr__(self, "source", source)


@dataclass(frozen=True, slots=True)
class CanonicalProviderIdentity:
    canonical_id: str
    provider_scope_id: str
    adapter_identity: str
    base_url_class: BaseURLClass
    endpoint_scope: str
    model: str
    capability_profile_id: str
    profile_source: ProviderProfileSource

    @classmethod
    def from_registration(
        cls,
        registration: ProviderProfileRegistration,
    ) -> CanonicalProviderIdentity:
        base_url_class = classify_base_url(registration.configured_base_url)
        endpoint_scope = _endpoint_scope(registration.configured_base_url)
        scope_payload = f"{registration.adapter_identity}|{endpoint_scope}"
        provider_scope_id = "provider_scope_" + hashlib.sha256(
            scope_payload.encode("utf-8")
        ).hexdigest()[:20]
        profile_id = capability_profile_id(registration.profile)
        canonical_payload = {
            "adapter_identity": registration.adapter_identity,
            "base_url_class": base_url_class.value,
            "endpoint_scope": endpoint_scope,
            "model": registration.profile.model,
            "capability_profile_id": profile_id,
            "profile_source": registration.source.value,
        }
        encoded = json.dumps(
            canonical_payload,
            sort_keys=True,
            separators=(",", ":"),
        )
        canonical_id = "provider_" + hashlib.sha256(
            encoded.encode("utf-8")
        ).hexdigest()[:24]
        return cls(
            canonical_id=canonical_id,
            provider_scope_id=provider_scope_id,
            adapter_identity=registration.adapter_identity,
            base_url_class=base_url_class,
            endpoint_scope=endpoint_scope,
            model=registration.profile.model,
            capability_profile_id=profile_id,
            profile_source=registration.source,
        )

    @property
    def cost_lookup_key(self) -> str:
        return ":".join(
            (
                "provider_cost_lookup_v1",
                self.adapter_identity,
                self.base_url_class.value,
                self.model,
            )
        )

    def to_public_dict(self) -> dict[str, object]:
        return {
            "canonical_id": self.canonical_id,
            "provider_scope_id": self.provider_scope_id,
            "adapter_identity": self.adapter_identity,
            "base_url_class": self.base_url_class.value,
            "endpoint_scope": self.endpoint_scope,
            "model": self.model,
            "capability_profile_id": self.capability_profile_id,
            "profile_source": self.profile_source.value,
        }
