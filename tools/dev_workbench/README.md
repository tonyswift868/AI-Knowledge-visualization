# MentorFlow Dev Workbench V1.1

Double-click [Start-MentorFlow-Dev-Workbench.cmd](../../Start-MentorFlow-Dev-Workbench.cmd).
This is a separate Python/Qt developer window, outside the Desktop product entry.
It needs an existing Python with `PySide6` and `jsonschema`; the launcher checks the
repo environment and audited existing E: runtimes. `MENTORFLOW_DEV_PYTHON` can name
another explicitly provisioned `python.exe`. It never downloads or installs tools.

The first view has five Chinese summary areas: current stage, user action, test
version, business workflow and A/01/02/03/04 worker status. **现在需要你做什么**
has the highest visual priority. Without a registered action it explicitly says
**当前无需你操作**; unknown action authority stays unknown. A ready target gives
one primary **启动测试版本** button in the action center. The version area keeps
the same launch operation as a secondary button, plus directory and details.

Select a worker for task/dispatch/head/blocker/receipt details. **工程详情** holds
the complete source identities, snapshot timestamp, paths and read-only history;
it is closed by default. Unknown statuses show **状态未知** in summaries; raw
UNKNOWN and original machine values remain in details. Only one eight-character
integration SHA is on the main screen; **复制** copies its full identity. A
delivery snapshot is not live worker/server telemetry. No polling, fetch, merge,
push, deployments, protocol registration or product startup occurs on opening.

## Authority and the initial candidate

Each refresh pins `refs/heads/integration/main3` to one commit and reads
CURRENT_STATE, ACTIVE_BOARD, DEPENDENCY_QUEUE and MAIN3 from that exact tree.
Receipt references, when present, are admitted only by SHA256 pinned in that
board. Unregistered files and conversations never change worker/gate status.
The Git remote-tracking SHA is labelled as a local snapshot, not a fresh remote
query. Source schema and actual database revision are separate fields.

The initial [canonical target](../../docs/governance/ACTIVE_TEST_TARGET.json) is
`MF-MAIN3-GATE-A-NATIVE-LOGIN / WAITING_FOR_ARTIFACT`, bound to the supplied d5169081
integration/tree. A source-backed native candidate and shortcut were observed in
01's retained Gate A receipt, but no complete V1 build ID, launch contract and
repo approval were present. No old shortcut or external wrapper is auto-admitted.
Before A integrates the new manifest, the worker's checked-in bootstrap may only
display WAITING; a local CURRENT file is not executable authority.

The current initial governance snapshot says USER_ACTION_REQUIRED=NONE. The UI
labels itself as the most recently registered state; its exact timestamp is in
details. It does not claim silent workers are currently executing a task.

## Chinese presentation contract

`resources.py` groups labels, statuses, actions, workflow names, empty states and
errors under the default `zh-CN` catalog. `Resources(locale, catalogs)` supports
another language without changing Qt business code. A missing translation falls
back to Chinese; an unknown status stays **状态未知**. `presentation.py` derives
short summaries without changing the raw authority view or launch manifest.
Chinese `summary_zh` on a worker row takes precedence over its Chinese objective.
Machine-heavy/English-only prose falls back to a short Chinese placeholder and
remains available verbatim in engineering details; no automatic translation or
task/status inference occurs.

The five workflow summaries represent native login, old-data continuity, account
lifecycle, final install/update and formal main3. Specific acceptance rows take
priority over broad planning gates; missing continuity evidence stays unknown.
Source integration/schema completion does not imply native login acceptance.
SMTP transport, actual mailbox delivery and overall account/reset completion
remain separate evidence. Adding a partial UI or mail PASS never completes the
whole account lifecycle. See the ADR for optional registered stage identifiers.

The layout uses two workflow/worker columns on desktop and stacks at narrow
widths. It supports 320-pixel width with vertical scrolling and readable text;
long paths and task IDs remain in a wrapping secondary details view. No product
UI, launch admission, trusted-root policy or update engine is changed.

## Producers and disposable projections

The UI never writes authority or test results. A/worker producers can normalize a
public receipt without editing the workbench:

```powershell
python -m tools.dev_workbench --prepare-receipt E:/Codex/Temp/example/public-receipt.json --test-id MF-EXACT-TEST --output E:/Codex/Temp/example/draft
```

Canonical receipts use a `test_target` object conforming to the schema. Flat
receipts support the same lowercase/uppercase field names. Missing evidence stays
null/UNKNOWN and cannot become CURRENT. No generic prose/command parser exists.
A `READY` or `USER_ACTION_REQUIRED_*` receipt only becomes CURRENT when ALL required
identity and action fields exist. A draft is never launch approval: A registers its
exact public receipt checksum in the repo contract described in the ADR.

Optional, rebuildable export (not consumed as authority):

```powershell
python -m tools.dev_workbench --export
```

This creates `WORKBENCH_VIEW.json` and `ACTIVE_TEST_TARGET.json` under
`E:/Codex/Coordination/MentorFlow/MAIN3/dev-workbench`. Delete these derived files
and run export again; the view rebuilds from repo/approved receipts. There is no
worker database. Tampering with export files has no effect on launch admission.

## Launch and navigation

Only CURRENT is launchable. Every click reloads repo approval, validates schema,
checks local trusted roots and rejects aliases/reparse points, hashes the exact
artifact, compares source SHA/tree against Git and verifies the pinned build
identity evidence. Windows read leases prevent write/delete/rename during admission.
Errors stay inline; hash mismatch says “这个文件不是当前指定测试版本。”

DESKTOP_EXE and INSTALLER start a verified `.exe` directly, with no JSON arguments,
shell, environment or working-directory override. V1 does not add elevation or
an installer engine. SCRIPT_ID only admits the built-in WORKBENCH_SELF_CHECK; new
IDs require code review. It cannot run arbitrary `.py`, `.ps1`, `.cmd`, `.lnk` or
commands from JSON. A successful process start is not a product acceptance PASS.

Receipt/log buttons only navigate to allowed local document types and trusted
roots. Secrets are references only, never opened/resolved. History is read-only;
double-clicking an old target warns “这是旧测试版本” and does not launch it. To retest
an old build, A must explicitly designate it CURRENT in governance.

See [launch/deep-link ADR and governance extension](../../docs/dev_workbench/ADR_TEST_TARGET_LAUNCH_V1.md).
Run focused tests with `python -m pytest -q tools/dev_workbench/test_workbench.py tools/dev_workbench/test_clarity.py`.
