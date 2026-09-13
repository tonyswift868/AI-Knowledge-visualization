# ADR: a read-only development projection and exact target launcher

Status: V1 source contract. Owner: MAIN3 02 tooling; A retains governance authority.
The workbench is not a product entry, worker database, updater, auth client or
deployment controller. Its cache and visible status are never authorization.

## Repo authority extension (optional, additive)

Existing CURRENT_STATE, ACTIVE_BOARD, DEPENDENCY_QUEUE and MAIN3 stay authoritative.
No current worker rows are changed by this delivery. A can add the following
machine-readable fields to ACTIVE_BOARD under `workbench`:

```json
{
  "current_phase": "Gate A acceptance",
  "current_gate": "GATE_A_AUTH_LIVE_ACTIVATION",
  "staging_status": "DEPLOYED_PENDING_USER_ACCEPTANCE",
  "database_revision": "0036_account_import_ledger",
  "current_test_id": "MF-EXACT-TEST",
  "test_targets": [
    {"test_id": "MF-EXACT-TEST", "receipt_path": "docs/governance/receipts/exact-test.json", "receipt_sha256": "<64 hex SHA256 of exact receipt bytes>"}
  ]
}
```

Each referenced public receipt contains `test_target`, or explicit flat field
names, matching [TEST_TARGET_MANIFEST.schema.json](../governance/TEST_TARGET_MANIFEST.schema.json).
Receipts may also live in audited local artifact roots, but their exact hash must
still be pinned in repo governance. Board writes are performed by A/worker receipt
integration, never by Workbench. A single canonical ACTIVE_TEST_TARGET.json in the
same Git tree is also supported. Missing/ambiguous selected IDs fail closed.

Workers can add explicit `objective`, `summary_zh`, `blocker`, `next_gate`, `head`, `receipt` fields to their
existing board rows. Workbench does not merge an unregistered receipt over a row.
The optional `summary_zh` is concise public Chinese prose, not execution authority.
Unknown worker statuses display 状态未知; the raw status remains in details.
B is excluded from the active board.

DEPENDENCY_QUEUE tasks keep their existing shape. DONE maps to PASS;
NOT_DISPATCHED maps to NOT_RUN; PASS/RUNNING/BLOCKED/LOCKED/NOT_RUN pass through.
New `REAL_SMTP_TRANSPORT` / `REAL_QQ_DELIVERY` task rows can represent separate
evidence; until present they display UNKNOWN. Other rows can opt into the timeline
with `workbench_visible: true` and a public `title`. A user-action row uses
`user_action_required` (public instruction) and `success_criteria`. No natural
language stage inference or execution dispatch is performed by the workbench.

V1.1 maps those registered rows to five business summaries. Optional specific
acceptance rows use `workbench_visible: true`: `REAL_NATIVE_LOGIN`,
`OLD_DATA_CONTINUITY` (aliases `DATA_CONTINUITY`, `ACCOUNT_DATA_CONTINUITY`),
`ACCOUNT_LIFECYCLE_ACCEPTANCE`, and `FINAL_INSTALL_UPDATE`. They take priority
over their corresponding Gate A/B/C planning row; `MAIN3_PROMOTION` retains its
formal-branch meaning. The ordered mapping is in `presentation.STAGES`. Missing
rows remain unknown; the tool never opens an unregistered receipt to fill them.
`control_plane.formal_main3=NOT_CREATED` is displayed as 未创建. These optional
fields do not add, change, or approve a CURRENT test target.

## Build identity and trust boundary

CURRENT requires all schema identity fields. `identity_evidence_path` names a
producer's public build identity JSON, itself pinned by `identity_evidence_sha256`.
It contains `schema_version: 1` and the exact `source_sha`, `source_tree`,
`product_version`, `build_id`, `artifact_type`, `artifact_sha256`, `environment`,
`deployment_identity`. The producer attests the artifact's build provenance; the
launcher compares that independently pinned attestation, the actual artifact hash,
and locally resolved Git SHA/tree. It does not infer source provenance from a file
name, shortcut or directory. It is not a reproducible-build or code-signing proof.

Trust chain: reviewed repo commit → pinned public receipt → target manifest →
pinned build identity + artifact bytes. Local projection files cannot replace
that chain. Local administrators or writers able to alter trusted repo objects,
tooling, or reviewed authority remain outside the network/input threat boundary.

Allowed artifact/document roots are E:/Codex/Temp, E:/Codex/Artifacts, the canonical
project's artifacts/release roots, and this checkout's artifacts/release roots.
They are code-owned, never widened by JSON. UNC/device/system/unknown-profile paths,
ADS, dot traversal, reparse points and symlinks are rejected. DESKTOP_EXE/INSTALLER
must target the exact hashed `.exe`; no arbitrary arguments are supported. Launch
uses an argv list with shell=False. Windows leases remain held until CreateProcess
returns. Cancellation before admission does not start a process.

V1 SCRIPT_ID registry contains only WORKBENCH_SELF_CHECK, a fixed repo module that
accepts an opaque schema-validated TEST_ID. SCRIPT_INPUT artifact bytes and build
evidence still require approval/hash checks; they are never executed as code.

`secret_ref` is an opaque `secret://...` ID or an absolute local reference path.
Workbench never resolves/opens it. Producers must not place secrets in any public
field or log. Unknown secret/password/token receipt fields are not projected;
plain text is displayed, common credential assignments are masked, and errors do
not echo rejected JSON. No credential URL, shell text or secret is a launch input.

## Future deep link

Reserved contract: `mentorflow-dev://test/<TEST_ID>`. V1 does not register a Windows
protocol handler. A future handler must accept exactly this scheme/host plus one
opaque `[A-Za-z0-9][A-Za-z0-9_-]{0,95}` ID; reject userinfo, port, query, fragment,
percent escapes, extra path segments, executable paths, commands and secrets.
Resolve the ID ONLY through current repo-approved manifests and then run the SAME
verification/launch path. Unknown/history IDs are not automatically runnable.
Registration must be per-user, removable, and restricted to this dedicated scheme.
Web Handoff may carry CURRENT_TEST_ID; it never supplies executable authority.

## Acceptance scope

Source/offscreen/tool launch tests are separate from real MentorFlow login/UAC,
SMTP, production and final package acceptance. WAITING is a valid initial target
state; this delivery creates no READY claim for an incompletely registered build.
