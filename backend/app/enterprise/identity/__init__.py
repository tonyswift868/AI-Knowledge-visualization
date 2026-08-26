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
from .keycloak import InMemoryEnterpriseSessionStore, KeycloakOidcClient, PendingAuthorization
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
    "ServiceIdentityGrant",
    "ServicePermission",
    "SigningKeyResolver",
    "enterprise_request_context",
    "personal_request_context",
]
