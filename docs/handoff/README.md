# Web session handoff contract

Authority: `MF-MAIN3-A-KNOWLEDGE-INTEGRATION-WEB-HANDOFF-20260912-02`.
Repository: MentorFlow / `tonyswift868/AI-Knowledge-visualization`, generation main3.
Use only `control/main3-current` and [current master context](../../GPT_WEB_MENTORFLOW_MASTER_CONTEXT.md).
`integration/main3` is a separately authorized stage branch, never auto-advanced; default branch
and local absolute paths are not assumptions about what a web session can read.

## Read order

1. [WEB_SESSION_LATEST](WEB_SESSION_LATEST.md): compact DERIVED pointer; verify
   repository, source SHA and publication ref against the latest receipt.
2. [AGENTS](../../AGENTS.md) and [ARCHITECTURE](../../ARCHITECTURE.md): boundaries.
3. [CURRENT_STATE](../project/CURRENT_STATE.md), [ACTIVE_BOARD](../governance/ACTIVE_BOARD.json),
   [DEPENDENCY_QUEUE](../governance/DEPENDENCY_QUEUE.json), [MAIN3](../exec-plans/active/MAIN3.md):
   current source, blockers, dispatch evidence and acceptance dependencies.
4. [PRODUCT_FLOW](../PRODUCT_FLOW.md), the authority index below, then
   [COMPONENT_MAP](../project/COMPONENT_MAP.md) and [decisions](../decisions/index.md)
   for the assigned task. Read only the relevant exact receipts and code/tests.

If the web session lacks repository access, provide the exact ref/commit and
grant read access or paste this bounded generated entry. Do not copy all chat
history or private configuration. Publication does not itself grant account access.

当前跨设备长期 authority：[Cross-device / Portable Pack V2](../decisions/CROSS_DEVICE_DATA_CONTINUITY_V2.md)。
要求已接受不等于同步／导入已实现；迁移保持未执行。

## Truth layers and conflict priority

| Layer | Authority and use |
| --- | --- |
| Durable truth | Product/security/workflow contracts; persist until explicitly superseded by authorized decisions. |
| Dynamic state | CURRENT_STATE, board, queue and active plan, updated from latest authorized dispatch/receipt. Snapshots are not live process monitors. |
| Immutable receipt | Exact source/parent/tree, scope, checks, candidate/environment and limits; identifies observed facts at one time. Keep normalized evidence/pointers, not raw private payloads. |
| Derived handoff | WEB_SESSION_LATEST and local compact output; navigation only, never source of truth. Regenerate after authoritative inputs change. |

An explicit current user/architect decision governs its scope. For requirements,
durable contracts outrank temporary tests or receipts; only explicit authority
can change the requirement. For state, the latest applicable immutable evidence
outranks a stale board/text; A reconciles the shared files, preserving scope.
An offline/source PASS cannot become live/package/user PASS. Unknown/conflicting
evidence stays UNKNOWN/PENDING. Handoff and legacy transcripts never override
either requirement or evidence. A newly received external receipt is an update
input; reconcile it before regenerating the handoff.

## Generation and exact commit semantics

Use the existing [compact contract](../governance/HANDOFF_CONTRACT.md). From a clean,
committed integration source snapshot:

```text
python -B scripts/context/build_compact_handoff.py
python -B scripts/context/build_compact_handoff.py --web
python -B scripts/context/build_compact_handoff.py --web --check
python -B scripts/context/check_repo_knowledge.py
python -B -m unittest discover -s scripts/context -p test_repo_knowledge.py -v
```

Commit only `docs/handoff/WEB_SESSION_LATEST.md` after generation. Each output is
at most 4096 UTF-8 bytes, with no truncation. Its exact-path `.gitattributes`
rule preserves LF on Windows clones for byte-reproducible checks. The web generator uses the latest
HEAD ancestor that changed anything except this derived file. Its
CURRENT_INTEGRATION_COMMIT is that **source snapshot**, not a claim of the
containing publication commit's hash. Read the latter from the remote ref and
final immutable receipt. Check rejects unrelated ancestry, source changes,
uncommitted inputs, wrong knowledge tree, stale output, secrets and overflow.
`generated_at` is the pinned source commit time in UTC, for deterministic checks.
`GENERATED_FROM` lists the snapshot and source files; it never implies real-time
worker, branch-picker, deployment or remote verification.

## Long-term requirement authority index (12/12)

This index assigns authority, not another copy of the contracts or a product PASS.

| # | Requirement | Existing authority |
| --- | --- | --- |
| 1 | Evidence-based learning loop; Free Chat write isolation | [PRODUCT_FLOW](../PRODUCT_FLOW.md) |
| 2 | Open-source-first; preserve working systems | [Workflow](../governance/DEVELOPMENT_WORKFLOW.md), [ADRs](../decisions/index.md) |
| 3 | Native username/password → authenticated backend-only verifier → Keycloak → session → bootstrap; no normal browser or password grant/storage | [Native auth ADR](../ADR_NATIVE_CREDENTIAL_AUTH_V1.md); [legacy issuer contract](../E109_AUTH_ISSUER_MIGRATION.md) retains protocol/infrastructure scope only |
| 4 | Channels/notes/checks/restart/defer/rollback/incremental fallback | [Update contract](../windows-update-release-contract-v1.md) |
| 5 | One installer, parent semantics, optional defaults OFF, repair/errors | [Installer contract](../../installer/inno/README.md) |
| 6 | Explain QA actions; no surprise windows, DPI changes or repeated configuration | [Workflow](../governance/DEVELOPMENT_WORKFLOW.md) |
| 7 | No automatic hosted compute; local/owned infrastructure first | [CI policy](../github-actions-compute-policy.md) |
| 8 | Domain-specific unique writers and separate rollback authority | [Staging contract](../enterprise/staging-orchestrator.md) |
| 9 | Mandatory final Interview + Acceptance bundle | [MAIN3 plan](../exec-plans/active/MAIN3.md), [queue](../governance/DEPENDENCY_QUEUE.json) |
| 10 | Code learning and rationale, business-to-code/test trace | [COMPONENT_MAP](../project/COMPONENT_MAP.md), [decisions](../decisions/index.md) |
| 11 | Secret location/owner/access method only | [Workflow](../governance/DEVELOPMENT_WORKFLOW.md), [secret governance](../enterprise/provider-secret-governance.md) |
| 12 | Latest evidence for state; durable requirements survive tests | Truth layers above; [CURRENT_STATE](../project/CURRENT_STATE.md) |

## LEGACY / SUPERSEDED

Superseded as new-session entry points; retained for scoped historical evidence.
No full inbound-reference audit or broad deletion is claimed in this task.

| Existing path | Status |
| --- | --- |
| [GPT_WEB_MENTORFLOW_MASTER_CONTEXT.md](../../GPT_WEB_MENTORFLOW_MASTER_CONTEXT.md) | LEGACY / SUPERSEDED |
| [GPT_WEB_RECOVERY_MANIFEST.json](../../GPT_WEB_RECOVERY_MANIFEST.json) | LEGACY / SUPERSEDED |
| [READ_FIRST_GPT_WEB_RECOVERY_HANDOFF.md](../../READ_FIRST_GPT_WEB_RECOVERY_HANDOFF.md) | LEGACY / SUPERSEDED |
| [HANDOFF_NEW_SESSION_KKS_UNIVERSAL_RUNTIME_20260726.md](../../HANDOFF_NEW_SESSION_KKS_UNIVERSAL_RUNTIME_20260726.md) | LEGACY / SUPERSEDED |

Older dated GPT_WEB documents under docs are likewise historical pointers; follow
their date/scope only when needed. Raw chat transcripts, passwords, tokens, SMTP
keys, private signing/SSH keys and formal user data must never enter the repo.
Existing legacy retention does not authorize adding new sensitive content.

### Temporary durable-requirements bridge

`13_DURABLE_REQUIREMENTS_BRIDGE_UNTIL_REPO_KNOWLEDGE_LANDS.md` is
**SUPERSEDED / BRIDGE CLOSED** under batch01 dispatch. It is not present among
tracked paths in this checkout; this registry records the external historical
name without inventing a repository link or deleting history. The 12/12
repo-native authority index above is the current entry point; CURRENT_STATE,
board, queue and the MAIN3 plan own current state. New sessions do not require
the temporary bridge. No full external inbound-reference audit is claimed.
