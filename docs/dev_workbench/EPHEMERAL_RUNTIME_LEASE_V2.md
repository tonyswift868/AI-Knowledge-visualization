# Ephemeral runtime lease authority

Immutable candidate admission remains Git CURRENT publication + approved exact
source SHA/tree + build/test IDs + identity receipt digest + artifact SHA256.
The existing source ancestry and publication-only tree checks are unchanged.
Device A/B share that artifact but keep independent owned profiles and storage.

Disposable transport has a separate local coordination authority, compiled into
`tools/dev_workbench/runtime_lease.py`:

`E:/Codex/Coordination/MentorFlow/MAIN3/dev-workbench/acceptance-runtime`

There is no UI, environment variable, manifest field, URL or caller argument for
selecting a different lease path. `LATEST.json` is the complete current lease;
the identical named `<runtime_lease_id>.json` publication must also exist there.
The runtime operator publishes the named record, then replaces LATEST. V1 is
accepted for the existing live runtime; V2 uses the same narrowly typed field
names and `desktop_runtime_configuration` object. The code-owned JSON Schema is
the normative schema. The public configuration wire contract stays
`MENTORFLOW_TEST_RUNTIME_V1`, supported by the current frozen candidate.

Required lease fields are schema, runtime_lease_id, status, test_id,
build_source_sha, build_source_tree, desktop_artifact_sha256, created_at,
expires_at, ca_fingerprint and desktop_runtime_configuration. Configuration
contains exactly contract, issuer, api_base_url, client_id, audience,
organization_id, workspace_id, tls_ca_path and tls_ca_sha256.

Only READY_FOR_ACCEPTANCE with created_at <= now < expires_at is usable.
Superseded records, including records with superseded_by, are refused. The
runtime binding must exactly match CURRENT test ID, source SHA/tree and artifact
hash. No historical endpoint, localhost preview or TLS bypass fallback exists.
LATEST is the operator's current authority; this is not a signed network feed or
a defense against compromise of the authorized local Windows account.

Both metadata files are limited to 32 KiB and reject duplicate JSON keys,
unknown fields, command/environment/storage overrides, secret fields and PEM
private-key content. Existing V1 operator metadata includes reference paths for
credentials and backend keys: these are never opened, forwarded to Desktop or
used as client authority. No secret material is required by this resolver.

File/ancestor paths reject symlinks, reparse points, UNC, traversal and aliases.
Metadata file and authority directory ownership must be the current Windows
account, SYSTEM or Administrators. The named record and pointer are held with
deny-write/delete file leases through CreateProcess. Certificate reads use the
existing trusted artifact roots, are bounded to 1 MiB and require exact PEM byte
SHA256, X509 DER SHA256 fingerprint, BasicConstraints CA=true and current validity.
The lease and CA validity are checked again immediately before spawn. The public
CA can rotate without changing immutable artifact evidence; publication must
update its byte hash, fingerprint and configuration together.

Only Workbench-owned A/B profile launches read this authority. Ordinary launches
do not read it. The sanitized device environment receives only the validated
configuration object and an informational MENTORFLOW_TEST_RUNTIME_LEASE_ID.
The frozen entry independently revalidates schema, owned profile and CA digest.
It contains no resolver for coordination files or secret references.

The same-account authority can replace an entire lease deliberately; named-file
agreement detects pointer-only edits, not hostile control of that authority.
An expired service may refuse login after launch: launch-time validity is not a
promise that a runtime remains alive. Runtime ownership and cleanup stay with
the runtime operator; Workbench never stops its services or removes its keys.

Run `python -m pytest tools/dev_workbench -q`. Tests redirect authority to isolated
temporary publications and use generated public test CAs; no live runtime or
real user data is altered. Exact frozen native login remains a separate live
acceptance result and must not be inferred from these tests.
