# Account Core development checkpoint — 2026-09-05

Dispatch: `MF-A-CHECKPOINT-LOCAL-CLEAN-20260905-01`.
This is a development checkpoint, not a formal release or deployment.

## Exact delivery provenance

The A branch starts at backend delivery
`9a1a6049420e11b6429fa8f3f5f6f41c26110052`
(tree `9adeeac6ca3405ffd6dd0edcdbb955215dd2d7e2`).
It integrates the frozen E1-07 endpoint
`e7594205374382248de91d86c37af22aecdc31ad`
(tree `5e48bb780faf4ac5bd35b4834a00b9590d0f4ff2`), preserving original commits
as merge ancestry. The common base is
`bc9bcf5dc7ca1a8e690abba730eeef2542ab049d`.

The complete E1-07 ordered dependency chain is:

1. `7bb77040e99f7f283d4ac91b2610ba229ba228f4`: frozen Windows package startup dependencies.
2. `bac3741632d0ced69d539c2d7de3dadcdda31738`: Desktop login gate and account/device recovery UX.
3. `dc6b8bbb24a9fbc5cc878841674bffca2a51519a`: isolation of startup verification account data;
   UX delivery tree `fdd5f3dc90840220a2ce0eb515cc0b3e74993c30`.
4. `e7594205374382248de91d86c37af22aecdc31ad`: external runtime configuration and immutable
   package verification boundary.

There is no branch-tip substitution, repeated cherry-pick, or new Desktop API binding.
Backend business implementation remains byte-identical to its frozen delivery.

## Verified state and remaining boundary

```text
SERVER_WORKSPACE_BOOTSTRAP = LIVE_PASS
DB_HEAD = 0035_account_workspace_bootstrap
DESKTOP_LOGIN_GATE = IMPLEMENTED
DESKTOP_CONFIG_PACKAGE = VERIFIED
DESKTOP_NEW_WORKSPACE_API_BINDING = PENDING
FINAL_DESKTOP_ONLINE_UX = NOT_YET_QUALIFIED
```

`LIVE_PASS` is backed by the E1-06 frozen local evidence for
`MF-E106-WORKSPACE-BOOTSTRAP-LIVE-20260905-01`: exact deployed backend SHA/tree,
canonical migration, 123 live checks, returning A/B accounts and genuinely empty
C-account initialization, fresh login/discovery, and cross-account API/RLS denial.
This checkpoint operation does not redeploy, access production databases, or repeat
those live checks. The E1-06 storage-guard incident during image unpack remains part
of the historical evidence; functional PASS is not an incident-free deployment claim.

Backup verification in that evidence means SHA-256, archive TOC and complete decode
passed. **Actual database restore testing = NOT_RUN.** No restore drill is claimed.

The E1-07 configuration receipt for `MF-E107-LOGIN-UX-RESUME-20260905-02`
records 92 focused tests passed / 4 platform skips, ten full-payload tamper/config
checks passed, and native packaged login-gate launch/close passed. It explicitly
does not qualify the final online Desktop workspace flow. The earlier UX receipt
also distinguishes synthetic device/recovery tests from real account use.

Current immutable Desktop payload:

- EXE SHA-256: `9684ca57c7da5e91f4b91433e1ffe6200234121de97102538c6d556d518e5ef8`.
- Manifest SHA-256: `2400a7ee3ed63f3496a1f70d2f5b646b1d1412fa4e21964c6ba54aae5c1cefe7`.
- The full program directory, external runtime config, trusted launcher and CA
  remain local. No EXE/DLL payload, user data, credentials or raw evidence is added
  to Git by this checkpoint.

## Publication and cleanup scope

Use the existing GitHub remote and A branch `codex/e1-account-core-r1-a`.
The current workflow push branch filters do not match that branch; no workflow is
manually dispatched and no security/CI rule is changed. `[skip ci]` is additional
commit metadata, not a guarantee against all GitHub events. Actual remote SHA/tree
readback and observed workflow runs are recorded in the external final receipt.

The user's scope correction cancels the local deletion phase. All directories,
packages, worktrees, caches, sessions and Temp files remain untouched by cleanup.
The earlier read-only MentorFlow size inventory is retained as local evidence.

```text
LOCAL_CLEANUP_SCOPE_CORRECTED = true
LOCAL_DELETIONS_BEFORE_CORRECTION = 0
LOCAL_DELETIONS_AFTER_CORRECTION = 0
NEXT_ACTION = DEDICATED_CODEX_LOCAL_STORAGE_CLEANUP_BY_B
```

No Codex home scan, employee dispatch, source history rewrite, Release, release tag,
default-branch merge, automatic deployment, rebuild, or repository visibility change
is performed by this task.
