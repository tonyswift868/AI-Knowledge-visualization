# E109 pre-cutover candidate gate — OLD/PREPARED browser source fix

TASK_ID: `E109_AUTH_PRECUTOVER_DEPLOYABLE_CANDIDATE_AND_OLD_LOGIN_GATE_V1`

DISPATCH_ID: `MF-MAIN3-01-AUTH-DEPLOYABLE-CANDIDATE-20260912-03`

STATUS: **SOURCE_FIX_DELIVERED_REQUIRES_LATER_BATCH_INTEGRATION**.
The required integrated snapshot cannot satisfy this dispatch's OLD system-browser
login gate while keeping NEW inactive and migration dates unassigned. The defect
was reproduced and fixed on 01's branch. Per the dispatch's Phase 1 source-fix
rule, candidate build/deployment/install progression stopped; the unintegrated
fix was not inserted into a shared candidate.

## Office and exact source evidence

Phase 0 ran before writes. Existing office, branch and HEAD matched; status was
empty, and worktree ownership had no conflict. No new worktree, switch, reset,
merge, rebase, shared ref edit or other-worker operation occurred.

| Field | Starting evidence |
| --- | --- |
| Toplevel | `E:/Codex/Data/worktrees/8831/MentorFlow` |
| Git directory | `E:/Codex/Projects/MentorFlow/.git/worktrees/MentorFlow1` |
| Common directory | `E:/Codex/Projects/MentorFlow/.git` |
| Index | `E:/Codex/Projects/MentorFlow/.git/worktrees/MentorFlow1/index` |
| Branch | `worker/main3/01` |
| HEAD | `5079681154c0ca34ba2427e072962fe69024bef5` |
| Integration SHA, read only | `15f6d0dda0968be1b39295577a139a9017615e82` |
| Integration tree | `53ed1867beb9afcb3944045cc837b52563d0f3b5` |

Read the exact integration staging-orchestrator, CURRENT_STATE and E109 migration
documents without switching. The new user dispatch explicitly grants the narrow
`E109_GATE_A_PRECUTOVER_BACKEND_CANDIDATE` lease; it was **not exercised** because
the source-fix stop condition was reached first. This is not a claim that the
lease was absent. Staging identity, deployed image, configuration and server
rollback inputs were not inspected or qualified after this stop condition.

## Reproduced defect and bounded fix

`desktop/enterprise_oidc.py` had identical Git blob
`3d64d80ffab418e9d4842f4aa6306f35657faf07` in the specified integration snapshot
and 01's starting HEAD. Its `requires_system_browser` predicate was true only for
NEW. The actual dialog uses that predicate to choose the OS browser or embedded
WebEngine, regardless of which desktop login surface owns the dialog.

`release/auth-authority.json` in PREPARED state produces no active `auth_authority`
manifest field. Normal launch therefore retains OLD issuer and no migration.
The new regression constructs that actual build manifest and uses the existing
launcher defaults plus `bind_auth_authority`; both login surfaces consequently
reached embedded authentication before the fix. An isolated evaluation of the
predicate extracted by `git show` from the exact integration snapshot also
returned `OLD=false`, `NEW=true`.

The only executable production change is the browser predicate: the two existing
exact E109 issuers, OLD and NEW, now require the system browser. This is a
user-agent selection, not an issuer allowlist. OLD without migration still
rejects NEW discovery and tokens. NEW behavior remains independent of migration
expiry/removal. Other provider configurations retain their prior behavior.

Tests cover the PREPARED build binding, both dialog surfaces, Code + S256,
state/nonce and exact transaction issuer, dynamic IPv4 callback, browser launch
failure/exception without embedded fallback, cancellation, and rejection of NEW
discovery/callback when configured for OLD without migration. The existing signed
token, refresh and identity continuity tests remain passing.

No change was made to the shared dialog/capability implementation delivered by
04, the build system, backend, release inputs, transport, or JWT validation. An
AST comparison, excluding the changed predicate and module docstring, verified
that all other production logic in `enterprise_oidc.py` is identical to START_HEAD.
Python 3.11 syntax compilation passed.

## Validation and retained evidence

Evidence directory:
`E:/Codex/Temp/MF-MAIN3-01-AUTH-DEPLOYABLE-CANDIDATE-20260912-03/`.
`source-fix-evidence.json` records source/config identities and log/XML hashes.

| Check | Result |
| --- | --- |
| Initial regression, `browser-red.log` / XML | 7 failed, 15 passed; OLD/PREPARED browser selection reproduced |
| Final desktop, browser, launch/build binding regressions | 162 passed, no failures/skips; `desktop-final.log` / XML |
| Backend migration, signed-token continuity and OIDC | 54 passed, no failures/skips; `backend-final.log` / XML |
| Exact integration predicate audit | OLD false / NEW true before fix; `integration-defect.json` |
| Production AST scope and Python 3.11 compilation | PASS |

Final focused total: **216 passed**, zero failures/skips. The backend suite has
one existing FastAPI/Starlette deprecation warning. These are offline synthetic
OIDC/account fixtures with disposable test databases, not real login, deployed
readiness, PostgreSQL continuity, packaged-install or whole-product acceptance.
The prior stage's unrelated CI static failure was not rerun or silently fixed.
The OS browser opener was mocked; no interactive browser/installer/UAC was launched.

`release/auth-authority.json` remains `PREPARED_NOT_ACTIVE`, `migration=null`,
SHA-256 `3ecc97e9c40a55347d554abce0a2c1b8d5d0a209b9449c3237062e2716fd9830`.
`release/account-capabilities.json` remains unchanged, both flags false, SHA-256
`bc2fe593f99e7fd88ab6064a0f100e3cc02f9029eb06a236cc26d7c02f126deb`.
API remains `https://localhost:18080`; callback port remains `0` on `127.0.0.1`.
No real window dates were assigned.

## Candidate, lease and rollback boundary

No deployable backend artifact or installable desktop candidate was produced from
the required integration snapshot in this turn. Earlier worker-source archives
and core binaries do not qualify as that integrated candidate. Backend deployment,
live strict oidc-ready, OLD transport/discovery recheck, real login/refresh and
account/workspace acceptance were not entered. No source fix was smuggled into
an exported integrated candidate.

`WRITER_LEASE_USED=NO`; `SERVER_MUTATION=0`; `KEYCLOAK_MUTATION=0`;
`DNS_MUTATION=0`; `SMTP_SEND=0`; `GITHUB_ACTIONS_EXECUTED=0`; `REMOTE_PUSH=0`;
`MIGRATION_WINDOW_STARTED=NO`. No service, proxy, tunnel, realm/client, production
user data, database migration or account capability mutation occurred.
Server rollback is **not qualified**: no server identity/configuration was
captured and no deployment occurred. The source commit is not a server, database
or identity rollback plan.

NEXT_GATE: later formal batch integration of this bounded fix, then an explicitly
accepted new integrated SHA/tree for the existing backend/installer pipelines.
The current `15f6d0d...` snapshot remains unchanged and cannot satisfy this
dispatch's OLD/PREPARED system-browser requirement. Subsequent deployment still
needs staging identity and rollback evidence under the applicable writer lease,
followed by installable desktop and trusted OLD transport. Only after those
conditions may the user be asked to perform one ordinary OLD-account browser
login. External-browser trust of OLD HTTPS must pass without certificate bypass.

USER_ACTION_REQUIRED: **NO** at this source-fix gate. No browser login was
requested or awaited. Root performed all writes; no children were spawned.
`OPEN_CHILDREN=0`; `ACTIVE_CHILDREN=0`; `WORKER_STATUS=SILENT`.
