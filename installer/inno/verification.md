# V2 candidate verification — 2026-09-08

Dispatch: `MF-E108-MODERN-INSTALLER-VISUAL-REWORK-20260908-02`.
`MODERN_UI_READY = NO`. Native acceptance is incomplete; this is not a release.

## Current authority boundary

The host must remain at 100% scaling. No Windows theme/DPI/settings changes.
Following the user's objection, no installer window, silent fixture installer,
product runtime or installation was launched during this V2 continuation.
No prior sandbox-install approval is assumed. No NSIS build was performed.
A new explicit agreement is needed before native UI acceptance resumes.

## Evidence acquired

| Requirement | Evidence | Limit |
| --- | --- | --- |
| Real product, not inert UX payload | Existing manifest verifier passed on MentorFlow 1.2.3, 14,198 listed files; manifest itself makes packed count 14,199 | No product smoke/installation |
| Single user artifact | Old named UAT NSIS candidate recoverably archived with matching SHA-256 | Final directory readback and exact hash recorded in delivery JSON after build |
| Branded pages | CreateCustomPage + native Inno controls; official icon copied byte-for-byte; product palette, strong hierarchy, location card, progress and finish | Source/compile, not native screenshots |
| Location contract | Existing parent validation retained; final-child-only CreateDir, unknown target rejection, human space errors | V2 compiled-policy runtime not executed |
| Launch/close | Run defaults unchecked; Launch selects it, Close deselects it; original Inno engine handlers retained | Source and compile, not runtime |
| Progress | Original CurInstallProgressChanged callback; real percentage calculation; bounded opt-in details | No actual extraction progression measured |
| Theme | Dynamic Windows 11 style; separate dark palette; contrast checks include card/body/primary; high-contrast fallback | Native light/dark appearance unverified |
| 100/125/150/200% | Source-geometry bounds and in-memory native Win32 font measurement at four virtual DPI values | Not live VCL layout; long paths and native control rendering still require review |
| Default UI removal | Normal pages overlay native branded surface; no large images, no bevels, template headings replaced | Actual screenshots pending |
| Single-instance defense | Official SetupMutex in compiled source | Duplicate-launch behavior not exercised |
| Scope | NSIS hash unchanged; no updater/feed/trust/account/database/server edits | Unrelated pre-existing untracked documentation preserved |

Focused checks on the final source:

- Build wrapper: **5 passed**, including changed-source/changed-manifest refusal.
- Design contract: **7 passed**.
- Native font metrics: **2 passed**; no window was created.
- Production-sections compilation with small fixture input: **1 passed**.
- Total: **15 passed**; no full test suite.
- Full real-payload build, exact hash and source-hash readback: see final receipt.

The metric checks found and corrected two genuine issues: small text contrast on
the location card, and insufficient wordmark height. A compile attempted while
the latter changed was rejected by the source-hash guard and is not deliverable.
A mistakenly selected full-product input to the small compile-profile test hit
its 60-second timeout; the test now refuses non-fixture receipts. The correctly
selected small-profile check passes. These are recorded, not silently relabeled
as successful runs. No installer was executed in either case.

## Missing native acceptance

All of these are **NOT_CAPTURED**, not blank placeholders or simulated images:

- `01-location-light.png`
- `02-progress-light.png`
- `03-finish-light.png`
- `04-location-dark.png`
- `05-progress-dark.png`
- `06-finish-dark.png`
- At least one 150% screenshot of the same exact product candidate.

Also pending: native keyboard/accessibility behavior, long-path presentation,
actual extraction progress, completion Launch versus Close, and visual error
recovery. No old V1 screenshot proves V2 acceptance. No HTML, mock image, forced
second binary or changed desktop setting has been used to manufacture evidence.

Consequently the visual hard gate, native light/dark claims, screenshot gate,
and full end-to-end acceptance remain unproven. Do not set MODERN_UI_READY=YES
or mark the goal complete from these offline tests.

## Artifact and recovery provenance

The frozen product build succeeded without compiler warnings. Its 912,052,432
byte EXE was copied (not launched) to the final folder and independently hashed:
`8008b7512278ea0132b86d9a321e3a526baea294597bf68c269a26d5e8178a47`.
Directory readback found exactly one EXE. This real product build packs
2,166,463,248 payload bytes and reserves 2,434,898,704 target bytes. The original
829 × 797 product PNG is copied unchanged, not an invented or upscaled logo.

Final candidate and all output fields are in
`E:\Downloads\MentorFlow-UAT-Current\Installer\acceptance.json`.
The final build receipt binds the EXE to source/brand and input-manifest hashes.
Temporary build directories are not user-facing alternatives.

Archived original:
`E:\Codex\Temp\mf-modern-installer-20260908\v2-retired-uat\MentorFlow-1.2.3-win-x64-Setup.exe.archived`

SHA-256:
`0cc09509415f3715fb821d74e556fef4e5b4feba710ac6a61cadc28f9c07af34`.
Original file was 23,305,631 bytes in the user-designated UAT Installer folder.
No unrelated file was moved or deleted. Recovery instructions are beside it.

Unchanged NSIS source SHA-256:
`6ad0608cb162a7fdacd6b7aa221f83b643db5b63a845857d836278cc2926c678`


## Safety accounting

REMOTE_PUSH=NOT_RUN; GITHUB_ACTIONS_EXECUTED=0; SERVER_MUTATIONS=0;
FORMAL_USER_DATA_WRITES=0; HOST_SETTINGS_MUTATIONS=0;
V2_INSTALLER_LAUNCHES=0; NSIS_USER_ARTIFACT_BUILT=NO;
NSIS_USER_ARTIFACT_LAUNCHED=NO.

Only installer/inno and scripts/build_inno_installer.py are included in the
source delivery. The unrelated untracked docs/windows-distribution-reference-v1.md
is preserved; do not claim the whole worktree is clean while it remains.

V1 history is not V2 evidence: the earlier host scaling change and concurrent
installer windows were operator mistakes. The user objected; they are not an
approved testing method and were not repeated in this continuation.
