# E109 disposable native security lab

Dispatch: `MF-MAIN3-04-NATIVE-SECURITY-LAB-20260912-03`.
Lease: `E109_SECURITY_EPHEMERAL_FIXTURE_04`.

This lab found a blocking verification-token contract gap in native Keycloak
26.7.3. One recorded pair of concurrent requests used the identical native
confirmation URL and authentic cookie copies: both returned 302 and committed
two `VERIFY_EMAIL` success events. A consumed verification token also worked
again after a later `VERIFY_EMAIL` required action was requested for the same
synthetic user. Ordinary replay while the account stays verified was rejected.
These are different properties; ordinary replay does not prove strict single use.

The reset-token tests observed at most one successful password update, including
replay after a new required action. A competing redemption can return 500 with a
native JPA optimistic-lock exception. The lab reports that separately from the
single-use count. Concurrency is nondeterministic: a later passing race does not
erase the recorded double-success counterexample.

`CROSS_WORKER_DEPENDENCY`: issuer-owned Keycloak action handling and deployment
must provide an atomic, purpose-bound consume operation before verification has
an effect. A used nonce must stay consumed until expiry even if a new required
action is added. Concurrent losers must return a controlled stale/used-token
response rather than an uncaught storage exception. Native implementation points:

- `org.keycloak.authentication.actiontoken.verifyemail.VerifyEmailActionTokenHandler`
  (`handleToken` and repeated-use policy).
- `org.keycloak.services.resources.LoginActionsService.handleActionToken` and the
  native single-use-object transaction boundary.
- Reset action flow transaction/error handling for the observed optimistic-lock
  race. Preserve one committed password update.
- The issuer owner must select and bind the corrected Keycloak artifact/provider
  at the `keycloak` service in
  `deploy/enterprise/staging/rp05/compose.account-core-mvp.yml`. This dispatch does
  not change that file, issuer migration files, canonical services, or a hostname.

The repository currently uses the native handlers; adding a production provider
or replacing the issuer artifact would cross that ownership boundary. This
delivery adds a reproducer and evidence, not an unreviewed production override.
Upstream reference implementations:
[verification handler](https://github.com/keycloak/keycloak/blob/26.7.3/services/src/main/java/org/keycloak/authentication/actiontoken/verifyemail/VerifyEmailActionTokenHandler.java),
[action-token dispatch](https://github.com/keycloak/keycloak/blob/26.7.3/services/src/main/java/org/keycloak/services/resources/LoginActionsService.java),
[reset handler](https://github.com/keycloak/keycloak/blob/26.7.3/services/src/main/java/org/keycloak/authentication/actiontoken/resetcred/ResetCredentialsActionTokenHandler.java).

## Reproduce within a disposable-fixture lease

Use Python with the repository test dependencies plus `psycopg`, `cryptography`,
and `httpx`; PostgreSQL 17.6 native binaries; JDK 21; a pristine Keycloak 26.7.3
ZIP extraction; and the native nginx executable. Tool paths are explicit. No
password, token, pre-existing DSN, production user, SMTP account, or host is an
input. Start only when the host has at least 2 GiB free disk and enough available
memory for the stated JVM/PostgreSQL limits (the admission run had 3.6 GiB free).

From the existing repository office:

```powershell
& 'E:\Codex\Temp\mf-e1s1-rp01-test-venv\Scripts\python.exe' scripts/e109_native_lab/run.py --pg-bin 'E:\Codex\Temp\postgresql-17.6-windows-x64\pgsql\bin' --java-home 'E:\Codex\Temp\mf-e109-sec04-tools-20260912\jdk-21.0.12.1+1' --keycloak 'E:\Codex\Temp\mf-e109-sec04-tools-20260912\keycloak-26.7.3' --nginx-exe 'E:\Codex\Temp\MF-MAIN3-04-native-stage2-tools\nginx-1.31.5\nginx.exe' --only all
```

Each invocation creates a fresh `E:\Codex\Temp\mf-e109-sec04-<random>` directory,
PostgreSQL cluster and application/Keycloak databases. It copies the pristine
Keycloak distribution into that directory. Only loopback HTTP/HTTPS/PostgreSQL
ports are used. JVM heap is 128–512 MiB with two active processors; PostgreSQL
uses 64 MiB shared buffers, 2 MiB work memory and 30 connections; Keycloak's DB
pool is limited to five. A short-lived local certificate is trusted explicitly
by the test client; Secure cookies are exercised over HTTPS.

`NativeTokenFixture.java` is a **lab-only** SPI. It constructs the pinned native
`VerifyEmailActionToken`/`ResetCredentialsActionToken` classes and calls native
realm signing. It does not replace either redemption handler, user persistence,
signature verification, expiry or nonce storage. Its endpoint requires an
in-memory random lease header, the unique lab realm prefix, and loopback source;
an invalid lease is tested. Never install this signer in a product distribution.

SMTP is unconfigured. Tokens are returned only to the test process in memory.
The native sender rejects the missing sender configuration before connecting to
a transport. No Mailpit, mock issuer, SQLite RLS substitute, or email-delivery
PASS is used. Transport/delivery and public issuer acceptance remain Gate A.

The PostgreSQL phase applies the actual Alembic chain through 0035 as
`mentorflow_migrator`. Fixture administration seeds only synthetic data.
Every isolation probe logs in directly as `mentorflow_app`, with no superuser,
bypass-RLS, ownership, or `SET ROLE` credential shortcut. It checks 18 table
owners/FORCE-RLS flags, and exercises all eight canonical resource table kinds:
bidirectional SELECT/INSERT/UPDATE/DELETE, positive own-write controls, USING,
WITH CHECK, absent/invalid context, transaction rollback, A→B→A on one backend
PID, exception cleanup, and the existing native concurrent workspace-bootstrap
test. There are 120 primary resource-denial probes, plus controls and context
checks. This is not a claim that every product endpoint was exercised natively.

The Keycloak phase uses native committed events as the success oracle, plus
state/form checks. The 16 token cases include wrong email, wrong signature,
malformed, expired, ordinary replay, replay after rearming, and two concurrent
requests. Password confirmation follows native redirects. Existing/absent-user
credential responses must match in status and JSON; Keycloak 26.7.3 uses HTTP 400.
Native brute-force policy must disable the synthetic user without changing the
external response. The quick-login boundary can engage before failureFactor=5.
Default LEARNER mapping and unchanged release capability flags are checked.

The nginx phase reuses the exact existing map/zone/limit extraction and lifecycle
helper, now with a selectable native upstream health path. The live configuration
and 30/10/3-per-minute thresholds remain unchanged. Encoded `execution` variants,
duplicate query arguments, password required actions and forged forwarding headers
must still hit the same exhausted email bucket in front of native Keycloak.

Any failed check or infrastructure error returns nonzero. Raw output is never
printed or retained. Evidence includes only statuses, counts, versions, code and
binary hashes, public local ports, exception class/stack locations and resource
identity. It contains no action tokens, passwords, cookies, private keys or user
records. The runner stops owned processes, verifies closed ports and removes its
manifest-owned disposable directory in `finally`.

## Cleanup exception from initial harness bring-up

The initial Windows `pg_ctl` pipe-inheritance attempt was stopped and its command
session cancelled. That attempt did not run schema, user, token or Keycloak tests.
Its directory remains at
`E:\Codex\Temp\mf-e109-sec04-5072a026f73a`, recorded in the sibling
`mf-e109-sec04-5072a026f73a-manifest.json`. It contains an initialized, stopped
disposable cluster. The one-use initialization password file was already removed;
the cluster still contains its synthetic credential verifier. Deletion was
rejected by automatic approval with `blocked by policy`. It was not bypassed.
All subsequent fixture directories were removed by their owned lifecycle.
Overall cleanup must remain `PARTIAL` until this exact residual is removed through
an approved mechanism. Do not prune shared processes, databases or directories.

## Remaining acceptance gates

1. Issuer owner supplies the strict-consumption and controlled-race-error fix;
   rerun this native lab against that exact artifact and preserve counterexamples.
2. Remove the manifest-owned residual above; confirm no owned ports/processes or
   credential-bearing volumes remain.
3. Gate A proves the actual new public issuer and real SMTP verification/reset
   delivery with separate authorization. Use the existing post-cutover checklist;
   this native lab cannot satisfy transport or public-host checks.
4. Keep `release/account-capabilities.json` at register=false and
   forgot_password=false until those acceptance gates pass.
