# Stable account identity and durable root — source/fixture receipt

Dispatch: `MF-MAIN3-01-STABLE-IDENTITY-DURABLE-ROOT-20260913-13`.
Start: `b8cc25d266c2ff549b9af86ec23b187a200d991c`, `worker/main3/01`, clean isolated office.
Read-only integration authority: `1ef95a71c5e7422cb3fe1435ccd703193629b591`.
No checkout/merge/rebase/new worktree or shared-ref change was used.

## Result and authority

The accepted immutable canonical issuer+subject anchor is reused. The new `mfa1_` account
key is a deterministic versioned projection; no independent account-ID table, tenant truth,
membership ID, workspace ID or existing Vault directory is replaced.

See [the ADR](../decisions/STABLE_ACCOUNT_IDENTITY_V1.md) for the audited graph, trust model,
configuration contract and precise future deployment/migration gates.

Backend requires a deployment-pinned, Ed25519-signed, per-subject mapping before treating
NEW external identity as OLD canonical identity. An E109 public-issuer configuration without
the OLD anchor fails at startup. Unknown issuers, changed/unmapped subjects, altered mappings,
duplicate entries/JSON fields and wrong trust anchors fail closed. No username/email match
can authorize a link. Existing JWT, mTLS, audience/client/expiry, PKCE and tenant gates remain.

Desktop consumes Backend's authenticated account key before choosing the canonical namespace
on cross-issuer OIDC login/refresh. Missing/wrong server account keys never persist rotated
credentials in a second namespace. Native sessions already carry Backend-verified canonical
identity; their controlled credential verification flow is unchanged.

`desktop.account_storage` is the common resolver used by launchers, Vault layout, bootstrap,
runtime and account switching. New installs default to `E:/MentorFlowData/accounts`;
existing accepted durable device roots remain in place. Temp/staging/worktree/artifact roots
cannot be production account targets. Explicit TEST_ONLY targets always report storage
readiness false. Both existing import entry points check the resolver before writing staging
or target bytes. Account packages must target the configured authenticated account namespace;
legacy local archives cannot write into enterprise account namespaces.

## Current test account

[Safe continuity proof](MAIN3_01_STABLE_ACCOUNT_CONTINUITY_PROOF.json) binds the previously
authenticated `tonyswift555` subject to retained active membership/workspace evidence, checks
the existing personal-scope derivation, and demonstrates the same account key and Vault
namespace under a disposable signed OLD→NEW mapping. It records evidence digests and subject
hash only, with no password, token, signing key or deployable production mapping.

This is retained evidence plus a conditional fixture proof, not a new live login or mapping
approval. The current running Temp candidate remains `TARGET_STORAGE_STABLE=NO`. Production
mapping/cutover is not approved or performed, so its live `IDENTITY_ISSUER_STABLE` gate remains
NO. The future resolver target is durable by policy; no directory/data was migrated there.

## Validation

Consolidated pytest: **222 PASS, 2 SKIP**, recorded in `E:/Codex/Temp/g13-final-results.xml`.
It covers the new signed-map and storage matrix, actual Backend OIDC/native adapters,
Desktop server-account contract, old credential refresh/Locker/Vault continuity, SQLite
workspace/resource continuity, original account Vault and package/import protections,
first-start import rejection, login gate and native login regressions.
After the final TEST_ONLY process gate, the affected storage/import/launcher suite passed
**60 PASS, 1 SKIP**, recorded in `E:/Codex/Temp/g13-gate-results.xml`. These suites overlap;
their counts are not added as unique tests. Skips are the unavailable Windows symlink
privilege and the optional isolated PostgreSQL application DSN (consolidated run only).

The complete delivery patch applies cleanly to exact integration `1ef95a71...` using
`git apply --check --cached` with an isolated scratch index. Integration's worktree, index
and ref were not changed. This proves patch applicability, not an integrated runtime rerun.

Keycloak **26.7.3** disposable H2 + actual TLS Backend + Qt native fixture: **32/32 PASS**.
Receipt: `E:/Codex/Temp/g13-native/mf-native-e9f627b612fb-receipt.json`.
It includes real mTLS password verification, wrong/unknown/disabled denial, workspace
bootstrap, existing membership, main-window gate, Credential Locker, logout/revalidation,
expiry and unavailable-verifier checks. All owned processes stopped and private keys removed.
This is a real native regression fixture; the exact OLD/NEW hostname continuity rehearsal
uses disposable signed JWTs/mock HTTPS and real adapters, not a production hostname change.
PostgreSQL RLS was not rerun; this change does not alter schema/RLS or tenant repositories.

The initial long Windows pytest path issue was resolved by using an E: short basetemp.
Tests that previously treated Temp as an ordinary import target now explicitly test the
production rejection and use TEST_ONLY only for their disposable positive import cases.
The refresh regression found during this task was fixed: server account proof now precedes
cross-issuer Vault/Locker selection. Retained intermediate failed runs are superseded by
the final consolidated run; they are not presented as passing evidence.

## Boundaries

Real user data migration, public issuer cutover, production Keycloak mutation, SMTP,
Cloudflare/DNS, account capability activation and GitHub Actions: **0 / not performed**.
`release/auth-authority.json` remains PREPARED_NOT_ACTIVE with no live dates;
register/forgot-password build capabilities remain false. Worker 04's server sync,
worker 03's UX and worker 02's workbench/harness are not modified.
Legacy ad-hoc Temp test launchers must explicitly declare TEST_ONLY before using this
new storage contract; the running candidate is not silently reconfigured or repackaged.
