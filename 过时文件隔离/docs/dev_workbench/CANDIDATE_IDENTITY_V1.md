# Build source and governance publication identity

The manifest's existing `source_sha` / `source_tree` fields mean
**BUILD_SOURCE_SHA / BUILD_SOURCE_TREE**, never the publication commit. They remain
schema-compatible; no duplicate writable aliases are added to the manifest.
`RepoSource.sha` is **GOVERNANCE_PUBLICATION_SHA**. Workbench derives
`candidate_identity` from the pinned Git snapshot, including both identities,
ARTIFACT_SHA256, BUILD_ID, TEST_ID and the pinned build-receipt digest. It appears
in device engineering details and disposable exports, never as a new authority.

## Publication and admission

1. Build exact integrated source S and record its Git tree, build ID and artifact
   SHA256 in the existing build identity receipt. The artifact path must be trusted.
2. Commit the complete CURRENT manifest to `ACTIVE_TEST_TARGET.json`, or register
   the existing hash-pinned receipt through `ACTIVE_BOARD.workbench`. That Git
   snapshot T explicitly binds test ID, build ID, source SHA/tree, artifact hash,
   receipt hash and path. T is obtained from Git; trying to embed T's own hash in
   T would introduce another impossible self-reference.
3. S must exist, have the exact tree and be an ancestor of T on integration.
   S..T may change only `docs/governance/**`, `docs/project/CURRENT_STATE.md`,
   `docs/exec-plans/active/MAIN3.md`, and `docs/handoff/WEB_SESSION_LATEST.md`.
   Any other final tree difference requires a new build. This conservative rule
   also rejects ordinary old artifacts after runtime source has advanced; merely
   being a historical ancestor is insufficient. A deliberately reapproved build
   with identical runtime source is valid, rather than an arbitrary local selection.
4. Each launch reloads CURRENT from Git, compares the selection with that exact
   approval, verifies schema, source/tree, receipt identity and artifact bytes.
   Immediately before process creation it checks that integration still points
   at T. A superseding publication, including one during hashing, cancels launch.
   Existing Windows artifact and parent leases remain held through process spawn.

The governance branch remains the existing trust boundary. A local edited
manifest, cached projection or observation cannot approve an artifact. No
signing key, feed authority, updater transaction or user data is changed.

## Explicit frozen test-device configuration

A/B still use the same build source, tree, artifact digest and build ID, with
separate owned roots, preferences, caches and credential namespaces. The existing
identity receipt must additionally attest `device_profile_contract` as
`MENTORFLOW_TEST_DEVICE_V1`, `packaged_entrypoint` as `desktop_entry.py`, and
`runtime_contract` as `MENTORFLOW_TEST_RUNTIME_V1` (the compatible frozen wire
contract). New receipts need no disposable endpoint or CA. Test ID remains
bound by the exact governance publication; if repeated in the identity receipt
it must match. Existing hash-pinned receipts may retain their historical
`test_device_configuration`: only its `contract` identifies compatibility.
Its endpoint/CA values never authorize a device launch.

The fixed coordination-owned [ephemeral runtime lease](EPHEMERAL_RUNTIME_LEASE_V2.md)
provides current transport configuration. It must match CURRENT test ID, source
SHA/tree and artifact digest. Workbench validates publication ownership, bounded
schema, freshness, trusted paths and CA bytes/fingerprint/validity, holding file
leases through spawn. Missing, expired or superseded leases refuse launch.
There is no fallback to historical configuration.

The existing frozen entry consumes the validated object through
`MENTORFLOW_TEST_RUNTIME_CONFIGURATION` before desktop imports. It rechecks
owned root/generation and CA digest, binds the existing Account Core/OIDC client
environment and skips the ordinary package sidecar. Storage still comes
exclusively from the owned profile. Existing auth verification is unchanged.

The ordinary non-device package sidecar retains its existing behavior. Protected
GUI wrappers which clean/replace environment are not attested by this V1 seam;
`desktop_gui_entry.py` is not an accepted device entrypoint. Do not retrofit a
local sidecar to make an old EXE appear compatible. New candidate source must
include the seam, and its producer must attest the actual build entrypoint.

## Batch04 frozen audit (read-only; no product launch)

Dispatch integration: `04e2d61e3e39729886b3a2e6c7aff022b65a382a`.
Frozen source: `852f8c2c1874bf1dd8b6855da492a97313b0080d`.
The 703,067,817-byte EXE hash was recomputed and matches
`2a641ea0a2a981dcacf2a29d9425149ef56efa0377dd5804797715a9561907ab`.
Its archive and saved PyInstaller spec show:

- Direct `desktop_entry.py`; core Desktop/profile and `app.main` modules in PYZ.
- `frontend/dist/index.html`, teacher catalog and Cubism core are present at the
  extraction-root paths used by `desktop.main`; `frontend/public` is absent.
- Alembic and public account capabilities are present. `backend/app` source files
  are absent (modules are in PYZ); runtime backend imports and multiprocessing
  still require a packaged launch check, not an assumption from source mode.
- `mentorflow-version.json` is absent from the archive and EXE directory. The
  current version loader therefore lacks a packaged version/auth-authority input.
- No packaged CA or Account Core launch sidecar was found in the EXE directory.
  The old sidecar expects `../Config/edge-tls-ca.crt`; source environment is not a
  replacement for explicit packaged endpoint/client/TLS qualification.
- The new runtime configuration module is absent. The integration target remains
  WAITING with incomplete launch and build-receipt identity. It is not promoted.

Result: source harness repair is tested; **new candidate required**. Producer must
package resources/version authority and the new seam, publish a complete pinned
receipt/configuration, and separately perform packaged startup and server-backed
A/B scenario acceptance. This work does not build another large package or claim
cloud recovery, login, migration, TLS handshake or packaged launch PASS.

Bounded tests: `python -m pytest -q tools/dev_workbench/test_candidate_identity.py
tools/dev_workbench/test_workbench.py tools/dev_workbench/test_clarity.py
tools/dev_workbench/test_devices.py`. Real Git publication tests use isolated
temporary repositories; spawned native executables are inert harness fixtures.
