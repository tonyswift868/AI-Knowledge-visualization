"""Enterprise identity foundation consuming ``enterprise-context.v1``."""

from .config import EnterpriseIdentityConfig, IdentityConfigurationError, KeycloakOidcConfig
from .context import (
    IdentityContextError,
    RequestIdentityContext,
    enterprise_request_context,
    personal_request_context,
)
from .models import (
    AuthenticatedPrincipal,
    BusinessRole,
    EnterpriseSession,
    IdentityDirectoryPort,
    MembershipGrant,
    MembershipStatus,
    ServiceIdentityGrant,
    ServicePermission,
)
from .directory import PostgresIdentityDirectory, service_identity_grants_from_env
from .keycloak import (
    CompletedAuthorization,
    InMemoryEnterpriseSessionStore,
    KeycloakOidcClient,
    PendingAuthorization,
)
from .oidc import (
    HttpJwksSigningKeyResolver,
    IdentityError,
    IdentityProviderPort,
    OidcIdentityAdapter,
    SigningKeyResolver,
)

__all__ = [
    "AuthenticatedPrincipal",
    "BusinessRole",
    "CompletedAuthorization",
    "EnterpriseIdentityConfig",
    "EnterpriseSession",
    "IdentityConfigurationError",
    "IdentityContextError",
    "IdentityDirectoryPort",
    "IdentityError",
    "IdentityProviderPort",
    "HttpJwksSigningKeyResolver",
    "InMemoryEnterpriseSessionStore",
    "KeycloakOidcClient",
    "KeycloakOidcConfig",
    "MembershipGrant",
    "MembershipStatus",
    "RequestIdentityContext",
    "OidcIdentityAdapter",
    "PendingAuthorization",
    "PostgresIdentityDirectory",
    "ServiceIdentityGrant",
    "ServicePermission",
    "SigningKeyResolver",
    "enterprise_request_context",
    "personal_request_context",
    "service_identity_grants_from_env",
]
