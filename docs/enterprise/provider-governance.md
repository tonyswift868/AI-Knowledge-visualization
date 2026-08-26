# Enterprise Provider governance

Provider configuration stores only `ProviderCredentialRef`, an opaque secret
manager reference. The Admin API rejects plaintext credential values before a
configuration mutation. API and UI types expose config ID, Provider name,
approved models, configured state, lifecycle state, and rotation time only;
they never return plaintext or the opaque reference.

## Scope and policy

Configurations may be personal, organization-scoped, or workspace-scoped.
Organization and workspace identifiers use the frozen enterprise context and
resource scope contracts. An approved-model policy is versioned with the
configuration; changing it increments the revision and fences prior sessions.

## Runtime lifecycle

- Registration, rotation start/completion, approved-model update, revoke, and
  read all pass through authorization and audit.
- Only `ACTIVE` configurations can authorize a new Provider session.
- Rotation, model-policy changes, invalidation, or revocation change the
  revision or state, invalidating old session grants.
- A revoked credential reference denies every new session without resolving or
  calling the Provider.

## Quota composition

`QuotaEnforcementAdapter` evaluates all applicable organization, workspace,
and user policies in broad-to-narrow order. The policies are conjunctive: any
hard stop, exhausted feature quota, disallowed model, unknown required cost, or
currency mismatch prevents the Provider callback. A permissive organization
limit cannot override a stricter workspace or user limit.

This module performs zero real Provider calls and reads no account balance or
user billing data.
