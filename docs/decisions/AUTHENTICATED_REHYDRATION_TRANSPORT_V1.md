# Authenticated rehydration transport V1

Dispatch: MF-MAIN3-01-AUTHENTICATED-REHYDRATION-TRANSPORT-20260913-14.
Starting worker HEAD: `16f5b9ad36e7191a16089fd5a353af469cb9adef`.
Integration contract: `04e2d61e3e39729886b3a2e6c7aff022b65a382a`, including
`docs/dev_workbench/MAIN3_BATCH04_INTEGRATION_GAPS.md` and 04's unchanged
`StructuredSyncClient`. This delivery owns authentication and transport only.

## Authority and bootstrap

The native credential client validates the Backend-issued session, discovers or
initializes the workspace through the existing account API, then confirms
`/api/enterprise/auth/me`. The transport requires that response's canonical
`account_id`, subject, organization and workspace to match the validated identity.
It never uses username, email, preferences or a locally guessed issuer namespace
as authentication evidence. Backend's existing identity middleware verifies the
opaque bearer session and membership on every sync request; tenant headers are
scope selectors, not proof of authority.

The login result carries an **inactive** transport. Only `LoginSession.accept`
activates it after the existing login generation gate accepts the result. A late
login response cannot independently activate a session. Product bootstrap shares
this token owner with the existing account metadata/progress API, then calls
`transport.bootstrap()`, which invokes 04's `StructuredSyncClient.rehydrate()`.
The resulting `AccountBootstrapResult.rehydration_result` is a guarded envelope.
No sync schema, snapshot interpretation, cache projection or presentation changes
are part of this delivery.

The synchronous adapter exposes the exact existing sync GET/PUT/POST contract.
It fixes the API authority, bearer token and confirmed tenant headers internally;
callers cannot supply arbitrary URLs, headers or context overrides. HTTPS, normal
certificate verification, bounded timeout and disabled redirects remain required.
No authentication-hostname substitution changes the API or local service address.

## Consumer contract for 03/04

Use `bootstrap.rehydration_result.provenance` to distinguish:

| State | Meaning |
| --- | --- |
| `SERVER_CONFIRMED` | Complete producer snapshot, belonging to the current unexpired account/session epoch. |
| `SESSION_STALE` | Logout, cancellation, replacement, expiry or rotation invalidated that result. |
| `UNAUTHENTICATED` | Session was not activated, or server authentication/authorization failed. |
| `TRANSPORT_UNAVAILABLE` | Missing integrated producer, unavailable endpoint/network or invalid/incomplete response. |

Publish only through the envelope's final commit gate:

```python
envelope = bootstrap.rehydration_result
# Run on the consumer's final commit thread (for example, inside the Qt callback).
envelope.publish(lambda payload, context: owned_store.commit(payload, context.account_id))
```

`owned_store` above represents 03/04's existing consumer; it is not a new store
implemented here. The callback must be a bounded synchronous commit. If dispatch
to a UI thread is needed, dispatch the **envelope**, then call `publish` on that
thread. Do not extract a payload and queue a later unchecked cache/UI write. The
gate serializes the commit with account invalidation and supplies a defensive
copy. `provenance` alone is an observation, not authorization for a later write.
Old A envelopes and old A teardown cannot affect an active B session.

No cached metadata or previous snapshot is promoted to `SERVER_CONFIRMED` on
network failure. Existing metadata cache contents remain under their existing
owner. Producer write/delete response semantics also remain owned by 04; any
future deferred write-response projection must use an equivalent final epoch gate.

## Lifecycle

Expiry or a first 401 triggers one serialized native refresh and a new `/auth/me`
confirmation of the **same** canonical account and tenant. Rotation advances the
epoch, invalidating earlier envelopes. Concurrent requests share one rotation.
Remember consent controls whether rotated material is written to the existing
Windows Locker; no consent means memory only. The existing account session lock
serializes refresh with restore/logout. Failed or ambiguous rotation fails closed;
it never retries an already claimed refresh token.

Logout captures the latest in-memory refresh token and synchronously invalidates
the transport before executor drains or remote logout. Account switch, cancelled
login/bootstrap, gate closure and application shutdown also fence the active
transport. An in-flight request may finish on the wire, but cannot publish after
the epoch changes. Network handles close when the in-flight operation returns;
invalidation does not wait for that request. Remote revocation still requires a
reachable Backend; local invalidation does not claim remote logout succeeded.

Server revocation is observed on the next authenticated request (401/403). This is
not a push revocation subscription. A 403 never changes tenant context to seek an
alternative workspace. No password grant or direct grant is introduced.

## Integration and validation boundaries

The worker's base does not contain 04's producer. Tests load the exact three 04
source blobs (producer, HTTP route and schemas) from the integration commit into
disposable Python modules, without changing another worktree or copying 04 source
into this delivery. Running the worker alone without that integrated dependency
returns `TRANSPORT_UNAVAILABLE` rather than pretending to hydrate successfully.

The Backend contract test runs actual native sessions, workspace initialization,
identity middleware, membership checks and the exact 04 route. Credential
verification and snapshot contents are explicitly synthetic; its isolated SQLite
fixture does not qualify PostgreSQL RLS or deploy migration 0037. Desktop tests
exercise actual product bootstrap, shared token rotation, logout, cancellation,
account switch and late-result rejection. No live issuer, real account data,
SMTP, public deployment or Portable Pack V2 work is included.

Before a runtime acceptance run, integrate this commit with the specified 04
producer/API, provision the separately approved sync service/schema, and let 03/04
bind their consumer through `publish`. Packaging/live activation and the other
Batch04 gaps remain separate gates.
