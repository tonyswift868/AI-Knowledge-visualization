# Provider secret governance

Provider credentials enter governance only as `ProviderCredentialRef`, an
opaque reference resolved by a trusted Provider runtime. Raw API keys are not
accepted by this contract.

## Public projection

API/UI projections expose only whether the credential is configured, the
provider, approved model names, status, and rotation time. They never expose
credential plaintext, the opaque reference, tenant identifiers, or internal
revision state.

Credential material is excluded from audit metadata, logs, traces, exception
messages, cache identity, and session grants. Cache identity uses the config
ID, provider, model, scope, tenant identifiers, and revision only.

## Lifecycle

Supported states are `ACTIVE`, `ROTATING`, `REVOKED`, and `INVALID`.

- Only `ACTIVE` may authorize a new Provider session.
- Entering `ROTATING` immediately blocks new sessions.
- Completing rotation increments the config revision and records rotation time.
- Existing session grants are fenced to their original revision and become
  stale after rotation.
- `REVOKED` and `INVALID` block both new and existing session authorization.

Rotation, revoke, invalidation, reads, and registration pass through tenant and
resource authorization and therefore produce auditable allow/deny decisions.
