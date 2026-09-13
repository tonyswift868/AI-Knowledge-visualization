# Two-device harness V1

Scope: developer harness/source delivery, not server synchronization or product
acceptance. Authoritative integration at dispatch:
`1ef95a71c5e7422cb3fe1435ccd703193629b591`. That revision records sync as
`REQUIRED_NOT_FULLY_IMPLEMENTED`; no local report can promote it to implemented.

## Profiles and admission

The two fixed production harness paths are `E:/Codex/TestDevices/MentorFlow/A`
and `E:/Codex/TestDevices/MentorFlow/B`. Each has a creation-recorded owner,
generation, `.running` lifetime lock, vault/prelaunch/data, preferences, cache,
temporary directory and local/roaming test profile. They are durable-like test
roots, not real user migration destinations. Test fixtures use owned E: Temp roots.
No real vault/database/settings root is copied in or discovered automatically.

`DEVICE_A_TARGET` and `DEVICE_B_TARGET` are disposable projections of one approved
manifest: source SHA/tree, build ID and artifact SHA256 are identical; roots,
generations and credential namespaces differ. They are regenerated from repo
approval, never read back as launch authority. `.target.json` records the admitted
device target after process creation and is not an approval file.
While one device is running, the other must use the same verified build identity.
After a harness restart, an already-running device has no trusted in-memory build
binding; close both devices before starting a new pair. Local target files cannot
authorize mixing builds or recover that binding.

Only a CURRENT DESKTOP_EXE in TEST/STAGING with a qualified integrated build source may
launch. In addition to the existing complete pinned build identity, the producer
must include this field in the identity evidence JSON and pin its new checksum:

```json
{"device_profile_contract": "MENTORFLOW_TEST_DEVICE_V1", "packaged_entrypoint": "desktop_entry.py"}
```

The pinned receipt must also provide the explicit `test_device_configuration`
described in [candidate identity V1](CANDIDATE_IDENTITY_V1.md). That contract
supersedes the original requirement that build source equal publication SHA.
This is an additional producer attestation, not a replacement for artifact hash,
source/tree or identity checks. The integrated source must also contain the
adapter and its Desktop startup hook. Build a direct Desktop entry that consumes
this contract before initializing preferences/storage. Legacy wrappers that
strip or replace the profile environment are not compatible and must not be
attested. This task does not qualify a packaged wrapper or produce a large build.

`launch_exact` remains the only verification/launch engine. Its optional device
environment is constructed exclusively by harness code, never taken from JSON.
The manifest schema still rejects command/environment/argv additions. Executable
argv contains the exact approved file only, with `shell=False`; no installer or
script launch is admitted as a test device. Unknown/old/tampered targets fail closed.

## Runtime isolation adapter

`desktop/test_device_profile.py` is opt-in via the code-owned device environment.
The Desktop application validates the fixed root, owner marker, generation and
storage links before acquiring a process-lifetime device lock. Existing account
root selection continues below that device's vault. First start chooses a blank
test space. Public test connection configuration comes from the pinned build
receipt; server identity and auth policy remain with the existing product engine.

Every product QSettings constructor uses `create_settings`: without a device it
delegates unchanged; with a device it supplies a root-specific INI filename and
disables fallback. Named/default browser profiles use root-specific cache and
persistent storage paths; the existing unnamed login browser stays off-the-record.
Provider/account credentials keep their existing Locker implementation and
issuer/subject references, but use a service namespace containing device and
generation. Cross-device reads cannot find the other namespace's tokens.

Reset checks both the owner marker and an independent creation record. It holds
directory/ancestor leases, rejects reparse/symlink entries and checks the running
lock before deleting contents. Recursive traversal remains under the fixed owned
root; it never follows links. The root and owner survive, but generation changes.
Old test Locker entries are retained, inaccessible to the fresh namespace; no
global credential enumeration/deletion and no server revocation occurs.

## Scenario and observation producer

Supported harness identifiers:

- `SCENARIO_01_CLOUD_REHYDRATION`: prepare old/new environments for cloud restore.
- `SCENARIO_02_MISSING_LOCAL_RESOURCE`: observe missing local resource references.
- `SCENARIO_03_PORTABLE_PACK`: reserved, disabled and rejected by launch admission.

The selected ID is a fixed enum in `MENTORFLOW_TEST_SCENARIO`; it is not a command.
The product/sync owner may emit `<device root>/observation.json` with:

```json
{
  "target": {"...": "the exact derived DEVICE_A_TARGET or DEVICE_B_TARGET object"},
  "scenario": "SCENARIO_01_CLOUD_REHYDRATION",
  "values": {
    "account": "public test account label",
    "server_workspace": "public workspace identity",
    "last_sync_revision": "public revision identity",
    "cloud_entities_restored_count": 12,
    "missing_local_resources_count": 3
  }
}
```

The example counts are synthetic schema examples, not current project facts.
Target fields are `target_id`, `device`, `scenario`, `profile_contract`, `root`,
`generation`, `source_sha`, `source_tree`, `build_source_sha`, `build_source_tree`,
`test_id`, `artifact_sha256`, `build_id`,
`scope=DERIVED_NOT_LAUNCH_AUTHORITY`, and the original `candidate_status`.
Reports must match the selected build, device generation and scenario. Limits:
8 KiB file, 160-character public text values, nonnegative integer counts up to
1 billion; unknown value fields are rejected. Never emit passwords, tokens,
credential references or private record content. Engineering values are folded
into details; the main view uses Chinese summaries and counts.

Reports are untrusted local observations (`LOCAL_OBSERVATION_NOT_ACCEPTANCE`).
They neither unlock launch nor set PASS nor prove authenticated server persistence.
Repo `acceptance_boundaries.server_sync_tier=IMPLEMENTED` only changes the prompt
to waiting for observations; it still does not assert product acceptance.
Missing/corrupt/stale reports stay unknown. No fake sync writer or Portable Pack
implementation is included.

## Validation boundaries

Focused tests cover real independent QSettings INIs, SQLite files, secure-store
namespaces through a fake Locker, actual inert native EXE launches for both
profiles, running locks, reset ownership/link attacks, source/build/hash gates,
scenario binding and the Chinese narrow UI. Real cloud recovery, real account
login, user migration and packaged two-device acceptance remain separate gates.

Run `python -m pytest -q tools/dev_workbench/test_workbench.py tools/dev_workbench/test_clarity.py tools/dev_workbench/test_devices.py`.
