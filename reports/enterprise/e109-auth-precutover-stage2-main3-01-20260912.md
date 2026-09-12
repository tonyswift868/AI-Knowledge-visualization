# E109 Auth pre-cutover qualification — MAIN3 / 01, stage 2

TASK_ID: `E109_AUTH_PRECUTOVER_ARTIFACT_AND_AUTHORITY_QUALIFICATION_V2`

DISPATCH_ID: `MF-MAIN3-01-AUTH-PRECUTOVER-STAGE2-20260912-02`

STATUS: **PARTIAL**. Core desktop binaries and backend source/build context are
prepared and verified. No compatible backend image was built on this Windows
host; OLD local transport, live client readback and installed real-login gates
remain open. This receipt does not authorize activation or hostname cutover.

The companion [machine receipt](e109-auth-precutover-stage2-main3-01-20260912.json)
contains artifact hashes, public TLS observations, test results, source identities
and explicitly empty writer/window/rollback fields. No Auth source defect requiring
a new fix was found: `NO_CODE_CHANGE_REQUIRED_IN_AUTH_SCOPE`. This delivery adds
these two reports only; executable source remains the previous 01 delivery.

## Office continuity and shared authority

Phase 0 ran before writes, including `git worktree list --porcelain` and the
following commands. The existing isolated office was reused, with no branch
conflict, switch, reset, new worktree or baseline replacement.

| Command | Starting result |
| --- | --- |
| `git rev-parse --show-toplevel` | `E:/Codex/Data/worktrees/8831/MentorFlow` |
| `git rev-parse --git-dir` | `E:/Codex/Projects/MentorFlow/.git/worktrees/MentorFlow1` |
| `git rev-parse --git-common-dir` | `E:/Codex/Projects/MentorFlow/.git` |
| `git rev-parse --git-path index` | `E:/Codex/Projects/MentorFlow/.git/worktrees/MentorFlow1/index` |
| `git rev-parse HEAD` | `f5eab83483c827ef9be03e67800dba14af7d8e74` |
| `git branch --show-current` | `worker/main3/01` |
| `git status --porcelain` | empty |

Build source tree: `23ad1a004a1aa4a8c14af059a32302be01d2d7e6`.
`integration/main3` was read only at the expected snapshot
`15f6d0dda0968be1b39295577a139a9017615e82`. Read CURRENT_STATE, MAIN3,
DEPENDENCY_QUEUE and E109 migration authority, then the current board, durable
staging writer rules and historical lease/ownership records. Exact document Git
blob identities are in the machine receipt. No integration commits were consumed
as this office's development baseline.

Latest consulted authority is
`MF-MAIN3-A-WORKER-BATCH01-INTEGRATION-20260912-03`, with durable writer rules from
`MF-MAIN3-A-KNOWLEDGE-INTEGRATION-WEB-HANDOFF-20260912-02`. Gate A's owner `01`
is a planning domain: `NOT_DISPATCHED`, `PLANNING ONLY / NOT_RUN`. The dependency
queue explicitly says that these assignments are not live writer leases.
Historical E1 employee numbers and September 11 infrastructure receipts do not
authorize this office to modify services today. `LIVE_WRITER_FOUND=NO`.

## Current public authority evidence

Fresh direct HTTPS GETs at 2026-09-12 12:15 UTC used Python's default certificate
verification and hostname validation, with no proxy, redirect following or Host
override. The retained public CA was added only for the existing IP endpoint.
No tokens, credentials, cookies, login POSTs or administrative calls were used.

| Observation | `auth.clannad.win:443` | `89.45.45.28:443` |
| --- | --- | --- |
| TLS | PASS, TLS 1.3, default trust | PASS, TLS 1.3, retained CA plus default trust |
| Certificate SAN | `clannad.win`, `*.clannad.win` | IP `89.45.45.28` |
| Certificate expiry, UTC | 2026-12-09 07:13:18 | 2026-09-16 00:07:29 |
| Discovery issuer | `https://localhost:18080/realms/mentorflow` | same OLD issuer |
| RSA signing authority | one RS256 signing key | identical key |
| S256 advertised | yes | yes |

Both discoveries have exact OLD issuer-derived authorization, token, JWKS,
userinfo and logout endpoints. For example, token is OLD plus
`/protocol/openid-connect/token`, and JWKS is OLD plus
`/protocol/openid-connect/certs`. Public mirror JWKS GETs used those fixed paths
on the two audited hosts; they were not installed as runtime endpoint overrides.
Canonical RSA/use=sig SHA-256 remains
`58b30e499176955656b3f6edf0b3cfaa305eed75933c2245315ca8ba10ee5a24`, matching the
September 11 authority. There is no observed public authority divergence.

The distinct migration-contract hash remains
`5662cb790710dde77eaf1d8d49edd6e4d476426def0c2ded73146d38d8d0d550`.
Do not confuse that contract hash with the current JWKS hash.

Direct `localhost:18080` refused connection in this office. No listener/tunnel
was created. Therefore public realm/signing agreement is verified, but actual
OLD desktop transport and external-browser OLD TLS trust are not. Public
discovery cannot prove the running `mentorflow-desktop` client configuration.
The next owner must read it back, without changing it merely to perform this audit.
The retained IP certificate's remaining validity must cover the eventual operation.

## Prepared consumer artifacts

All local evidence is under
`E:/Codex/Temp/MF-MAIN3-01-AUTH-PRECUTOVER-STAGE2-20260912-02/`.
Artifacts bind to source `f5eab83483c827ef9be03e67800dba14af7d8e74`, not to
integration/main3 or to the later documentation-only receipt commit.

| Artifact | Identity and qualification |
| --- | --- |
| `backend-build-context.tar.gz` | 146,030,807 bytes; SHA-256 `dab1434d6c0fa2c0bd8a4c2a67917a1c11493e0f0508ce49f5be23e793bd85f9`; 2,098 files matched exact Git blobs |
| `consumer-build/pyi-app/real-delivery-app/MentorFlow.exe` | 102,621,836 bytes; SHA-256 `638b21de9d820b3918f1217a02c9de205db6281cff76370fa7ea918cbc60f1f3` |
| `consumer-build/native/UserRuntime/UserRuntime.exe` | 8,687,807 bytes; SHA-256 `06c0148f076be56d0ff6f26b91957dd4c11bbc4e2970751b30eb0fa07a238d37` |

Backend context was exported using `git -c core.autocrlf=false -c core.eol=lf
archive` from the exact source. It contains the existing root Dockerfile,
`.dockerignore`, backend, content, story_packs and enterprise deployment assets.
This supplies the inputs consumed by the Dockerfile's `backend-image` target;
it is not an OCI image. Ten auth/readiness Python files compiled under Python
3.12.10. Existing Dockerfile requirements resolve dependencies during image
building, so the Linux build must separately record its base digest and actual
resolved dependency inventory. Docker/Compose are unavailable here.

Desktop qualification reused `release/real-delivery-app.spec`, the exact existing
UserRuntime PyInstaller command, `scripts.build_desktop_icon`, and
`build_e108_user_candidate.verify_binaries`. Toolchain: Python 3.13.14,
PyInstaller 6.21.0, PySide6 6.11.1. Frozen dependency TOCs came from the retained
E108 build directory; their current hashes and paths are pinned in build-input.json.
All 92 desktop project modules and the five explicitly rebound backend auth
modules matched current compiled source; all 31 runtime project modules also
matched. Both entries and public authority matched, both PE manifests were
asInvoker, and observation/test-only modules were excluded by the existing verifier.

The first isolated spec invocation lacked its generated icon. Running the existing
icon prerequisite and retrying the same spec succeeded; no source workaround was
introduced. Both failure and successful retry logs are retained. Only public
delivery trust input was reused; no private signing key was read or generated.
No full seed payload was copied/qualified, no final installer or release feed was
assembled, and no binary was executed. These cores are qualification artifacts,
not complete distributable or installed-consumer evidence. A later distribution
must use the authorized full build path and exact integrated source selection.

`release/auth-authority.json` remains `PREPARED_NOT_ACTIVE`, `migration=null`,
SHA-256 `3ecc97e9c40a55347d554abce0a2c1b8d5d0a209b9449c3237062e2716fd9830`.
No active auth metadata was generated. API authority stays
`https://localhost:18080`; callback port stays `0` on IPv4 `127.0.0.1`, independent
of API/transport port 18080. The NEW-primary system-browser route and strict
protocol checks are present in the compiled code, and are activated only by the
separately approved configuration. Both account capability flags remain false.

## Tests and limits

- Desktop, protocol, actual dialog/system-browser seams, startup/installed entry,
  auth/capability build binding and feed input tests: **153 passed**, no skips.
- Backend signed OLD/NEW token and refresh continuity, membership, OIDC,
  runtime/context, recovery seams and expanded release assets: **103 passed,
  1 failed**, no skips. The sole failure is
  `test_e1_release_assets.py::test_release_assets_are_static_provider_zero_and_lease_gated`.
- That verifier requires `ubuntu-24.04` and `windows-latest`, while the committed
  workflow uses self-hosted runners. Both workflow and verifier are byte-identical
  to main2 `04faf2a9fbb50c5f9403dad5abf46354c9b53d82`. The failure is retained and
  left outside Auth scope; GitHub Actions were not edited or executed.
- Direct invocation of the existing RP-05 `verify()` function: **PASS**, no
  failures; Compose validation explicitly `NOT_RUN_TOOL_UNAVAILABLE`.
- Existing binary/source verifier: **PASS** for both freshly built cores.

The 257-test combined run is not all green: **256 passed, 1 failed**. Test keys,
HTTPS responses, credential storage and directory results are synthetic; test
fixtures use disposable SQLite databases only. This is not live PostgreSQL/RLS,
real browser authentication, backend deployment, or installed desktop acceptance.

## LIVE_WRITER_REQUIREMENTS

A new formal receipt and current board/lease evidence must identify a single
writer for each affected domain, exact host/service, allowed operations,
exclusive lease acquisition, handover/release boundary, artifact/config hashes,
evidence location and rollback owner. None of the four domains currently has
such a receipt authorizing 01 in the consulted snapshot.

| Domain | Required scope before a later mutation |
| --- | --- |
| Backend staging deployment | Pinned backend image, mounted readiness script and backend-only auth configuration; exact release pointer/service and bounded restart method |
| Keycloak / identity | Separately authorized hostname operation, current realm/client readback and unchanged signing/users evidence; no implicit realm import, key rotation or client rewrite |
| Proxy / public auth hostname | Named route/TLS/DNS owner and current route/certificate evidence; explicit no-change scope if the existing public route suffices |
| Database / formal data | Current data/schema owner and protected recovery references; confirm no auth migration is required and that backend activation cannot start migrate/provision services |

The generic staging deploy orchestrator starts dependencies and performs backups/
migrations. It is not an approved backend-only activation command. The later
writer must review an exact service-scoped plan and its effective Compose graph;
source access or this checklist does not authorize running the generic deploy path.

## LIVE_DEPLOYMENT_INPUTS and pre-cutover checklist

1. **Backend artifact identity.** Accept an exact source/tree, build the existing
   `backend-image` target with a reviewed immutable Python base, retain dependency
   resolution/SBOM, image digest and image/source readback. Pair the image with
   the mounted `runtime-entrypoint.py` SHA-256
   `f164ab983096e8bcb307b975f8c23d26f327685783c0bef4debe2ab8fe1d4f0b`
   from the LF deployment archive. The machine receipt separately records
   Windows workspace byte hashes; do not substitute a CRLF workspace hash for
   a mounted file's actual bytes.
   A source archive or Git SHA alone cannot fill the image-digest field.
2. **Desktop artifact/build identity.** Pin accepted source/tree, seed payload
   manifest, seed TOCs, toolchain, public signing/feed authority and full binary
   verification. Name the distribution/install owner and paths. Current cores
   prove compilation, but do not replace verified dependency payload, signing,
   installer or installed manifest identity. If a different integrated source
   is selected, repeat qualification for that exact source.
3. **TLS/discovery/JWKS authority.** Recheck both public routes and actual OLD
   transport immediately before activation. Require trusted chains/hostnames,
   exact endpoints, same closed realm/signing authority and sufficient certificate
   lifetime. Obtain live client readback: enabled public `mentorflow-desktop`,
   standard authorization-code flow, S256, direct grants disabled, audience
   `mentorflow-api`, registered `http://127.0.0.1/oauth/callback` with dynamic
   loopback port behavior. Stop on authority divergence; never infer client
   configuration from discovery or dynamically trust Host headers.
4. **Compatibility configuration.** Preserve NEW primary / OLD identity namespace
   and OLD backend TLS token/JWKS transport. Overlay is
   `compose.auth-issuer-migration.yml`, SHA-256
   `1e169942f2756caf473ac1e096170381432010d25dd76733d58bf32b17fd11b7`
   in the LF deployment archive.
   Keep API base, existing API tunnel, loopback proxy and callback port `0`.
   Do not globally replace PUBLIC_ORIGIN/PUBLIC_HOSTNAME: they also configure
   Keycloak and API routing. Inspect effective config and dependency graph.
5. **OLD real-login requirements.** Have an authorized existing test account,
   recorded opaque account/workspace identity references and working OLD TLS
   transport in the actual system browser. Validate normal installed launch,
   Code + S256, dynamic IPv4 callback, strict state/nonce/token verification,
   API/workspace bootstrap, restart/credential restoration and refresh. Store
   only sanitized results, never token/cookie/password values.
6. **One shared window, only after preparation.** Obtain a separate approval
   receipt for integer UTC `[not_before, not_after)` of at most 172800 seconds
   and the exact contract hash above. The JSON template deliberately has null
   dates and is not executable. Require semantic equality between overlay input
   `MENTORFLOW_E109_ISSUER_MIGRATION_JSON`, effective backend
   `MENTORFLOW_KEYCLOAK_ISSUER_MIGRATION_JSON`, desktop build `migration` and
   runtime `MENTORFLOW_DESKTOP_ISSUER_MIGRATION_JSON`. Dates must not be derived
   from this receipt. Allocate installation, testing and rollback time before
   approval; there is no automatic renewal.
7. **Backend activation order.** The named backend writer first installs the
   compatible image/readiness script, then applies the approved NEW-primary,
   OLD-identity, OLD-transport window configuration while Keycloak remains OLD.
   Read back exact image and config. Run the existing in-container
   `runtime-entrypoint.py oidc-ready`; its invalid-code POST must produce HTTP
   400 `invalid_grant`, never merely `invalid_client`/another error. Then pass
   OLD token/refresh and real membership/API tests. No migrate/provision,
   Keycloak, proxy or data mutation may occur as an implicit dependency action.
8. **Desktop activation order.** After backend acceptance and before hostname
   cutover, the separately authorized desktop delivery changes the sole build
   input to ACTIVE with the approved shared window, builds while that window
   is valid, installs through the approved path and reads back the installed
   metadata. Verify NEW primary / OLD identity / unchanged API / callback `0`,
   then perform the OLD-discovery system-browser tests in item 5. An old installed
   desktop or these uninstalled qualification cores cannot satisfy this gate.
9. **Hostname cutover prerequisites.** Require signed-off backend and installed
   desktop receipts, OLD real-login/refresh/continuity PASS, fresh authority/TLS
   readback, named exclusive hostname writer, prepared rollback inputs and enough
   remaining approved window for acceptance and rollback. Only then may a future
   separately authorized operation change Keycloak hostname. No such operation
   is authorized by this stage 2 receipt.
10. **Rollback points / ROLLBACK_INPUTS.** Before activation, preserve exact old
    backend image/effective config/mounted script, desktop package/config, OLD
    Keycloak hostname and realm/client/signing readback, old API/proxy routes,
    protected backup references, data owner and account/workspace baseline.
    Record checkpoints before consumer activation, before hostname change and
    after NEW acceptance. Within the approved window, recover compatible
    consumers and hostname coherently, then retest tokens, refresh and membership.
    If rollback cannot finish before expiry, stop for an explicit recovery
    decision; never extend dates or weaken issuer checks automatically. Git revert
    is not server/database/identity rollback; no row rewrite or blind DB downgrade.
11. **NEW issuer system-browser acceptance.** After authorized cutover, fetch NEW
    discovery and use a fresh ordinary login. Confirm NEW browser authorization
    and token endpoints, verified NEW tokens, Code + S256, strict state/nonce,
    ephemeral `127.0.0.1` callback, API access and refresh. An OLD transaction
    spanning cutover may be retried; its issuer/state/nonce binding is not relaxed.
    Preserve old-token continuity only until the shared deadline.
12. **Account/workspace continuity acceptance.** Match the same existing account,
    organization/workspace membership, permissions, issuer-derived vault and
    account fingerprint before/after activation, restart, refresh and cutover.
    Require actual workspace bootstrap before application entry. Unknown issuer,
    invalid signature/audience/client/expiry and bad state/nonce must still fail.
    After acceptance, OLD trust expires automatically; later cleanup/transport
    changes require their own readback and authority. Keep OLD identity namespace
    and both register/forgot-password capabilities unchanged.

## Mutation boundary and next gate

`SERVER_MUTATION=0`, `KEYCLOAK_MUTATION=0`, `DNS_MUTATION=0`, `NGINX_MUTATION=0`,
`SMTP_SEND=0`, `GITHUB_ACTIONS_EXECUTED=0`, `REMOTE_PUSH=0`,
`MIGRATION_WINDOW_STARTED=NO`. No live database migration/data access, account
capability enable, installed App/installer run or other-worker operation occurred.
Root retained all writes; `OPEN_CHILDREN=0`, `ACTIVE_CHILDREN=0`.

NEXT_REQUIRED_AUTHORITY: a fresh Gate A dispatch with resource-specific exclusive
writer/lease and desktop distribution ownership; not a historical employee label.
NEXT_GATE: resolve the image/tooling, dependency payload and distribution path,
OLD transport/client/browser-trust and test-account prerequisites, then separately
approve one shared window. Build/install with the ACTIVE input and validate the
compatible consumers against OLD only after that approval and before hostname
cutover. Keep the window unassigned until preparation and rollback inputs are ready.
`WORKER_STATUS=SILENT`.
