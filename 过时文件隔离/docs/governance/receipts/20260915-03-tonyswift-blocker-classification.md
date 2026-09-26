# TonySwift exact identity blocker classification

EMPLOYEE_SLOT = 03
DISPATCH_ID = MF-MAIN3-03-TONYSWIFT-RBAC-BLOCKER-CLASSIFY-20260915-27
TASK_ID = CLASSIFY_EXACT_TONYSWIFT_RELEASE_BLOCKER_V1
STATUS = PASS_BLOCKER_CLASSIFIED

Reviewed control: `69b7e210ce7ff4abeb14337e4d969b0420db476a`
Control tree: `d55f893a169a63a1f83cf8a9fafa2725796a011a`
Worker start: `80b619d263d81f5e684af67528da898cbfbda178`

## Decision

TONYSWIFT_BLOCKER_CLASS = B. OWNER_OPERATOR_BOOTSTRAP_POST_ACCEPTANCE
TONYSWIFT_RELEASE_BLOCKER = NO
Disposition = POST_ACCEPTANCE_OPERATOR_BOOTSTRAP

No explicit formal contract requiring username EXACTLY `tonyswift` before
Main3 release was found in the reviewed plan, ADR/accepted decisions, account
requirements, acceptance checklist, role contract, implementation or retained
dispatch/governance evidence. This decision follows Dispatch27's explicit rule:
without a formal contract, remove this exact-name item from release blockers.
It does not approve release or remove any other package/security/UAT gates.

## Requirement trace (all paths refer to reviewed control)

- `docs/APPLICATION_ROLES.md`: “Obtain the canonical ID from the authenticated
  account identity contract; never match a username, email, client role assertion,
  or Keycloak role claim.” The default allowlist is empty; default role LEARNER.
  No named administrator is mandatory. This is operator provisioning, not
  account creation or a username-based product dependency.
- `backend/app/enterprise/identity/application_roles.py`: `role_for` checks a
  HUMAN actor's `account_id(actor.issuer, actor.subject_id)` against provisioned
  canonical IDs. Neither spelling appears as an authorization special case.
- `docs/governance/ACCOUNT_MVP_USER_TEST.md`, test A: “用 tonyswift555 及已知测试密码登录；
  服务器角色为 SUPER_ADMIN；进入学习空间。” This is a historical isolated TEST
  acceptance identity, not exact `tonyswift`; the document excludes production
  release from that round's scope. It does not authorize substituting identities.
- `docs/exec-plans/active/MAIN3.md`: Gate A/B/C, packaged/user acceptance and formal
  promotion remain explicit. External role/UAT work requires independent
  authority; no exact `tonyswift` prerequisite appears. Account MVP is historical.
- `docs/ADR_NATIVE_CREDENTIAL_AUTH_V1.md` and `docs/decisions`: no TonySwift exact
  username requirement found by case-insensitive targeted source search.
- `docs/governance/receipts/20260915-control-convergence.json` lists “exact TonySwift
  role authority” among external blockers, without a release-critical contract.
- `docs/governance/receipts/20260915-main3-authority-uat-requalification.json`
  identifies dispatch `MF-MAIN3-A-ACCEPTANCE-AUTHORITY-UAT-REQUALIFY-20260915-26`.
  Its `tonyswift_identity.exact_username` cites “this dispatch; bounded read-only
  evidence”, and mapping is `BLOCKED_EXTERNAL_AUTHORITY` because no mapping/create/
  credential mutation was authorized. This establishes an investigation target
  and write-authorization boundary, not a formal release prerequisite. Original
  Dispatch26 text was not supplied; its retained receipt is the history evidence.
- `GPT_WEB_MENTORFLOW_MASTER_CONTEXT.md` repeats that pending exact mapping.
  A derived handoff is not an independent release requirement.
- Git history attributes the role contract to `385b30f9a2a7dfd81fc21f6e1de3975a756e6bbf`
  and the Dispatch26 receipt to `eb68df7ac2b649d0289bb4c8ee958855c31136bc`.

## Identity evidence scope

TONYSWIFT_EXACT_FOUND = NO (retained read-only evidence, not a new live lookup)
TONYSWIFT555_FOUND = YES (same retained evidence)

The Dispatch26 receipt reports exact SQL metadata checks of retained VPS
`mentorflow` and old local test realm `mf-ab-f0daac9477`, finding only
`tonyswift555`. No role transfers between these names are inferred. No real
identity lookup, account creation, password operation or role mutation occurred.

## Synthetic RBAC recheck

Tests executed from an extracted `git archive` of the exact reviewed control,
not the worker source. Network services and real identities were not used.

Command (cwd `E:/Codex/Temp/MF03-RBAC27/source`):

```powershell
$env:PYTHONPATH='E:/Codex/Temp/MF03-RBAC27/source;E:/Codex/Temp/MF03-RBAC27/source/backend;E:/Codex/Temp/mf-recovery-desktop-r1-venv/Lib/site-packages'
$env:QT_QPA_PLATFORM='offscreen'
& E:/Codex/Temp/mf-account-core-r1-venv/Scripts/python.exe -m pytest backend/tests/test_admin_overview.py::test_super_admin_200_member_403 backend/tests/test_application_roles.py::test_me_and_admin_authorize_from_server_policy_not_membership_roles -q --basetemp E:/Codex/Temp/MF03-RBAC27/pytest-qualified
```

Result: **2 passed, 0 failed, 0 skipped**, 2 dependency deprecation warnings.
Initial collection attempts lacked PySide6; existing isolated Qt environment was
reused without installation. Those collection errors are not product failures.

- `/api/enterprise/auth/admin/overview`: synthetic SUPER_ADMIN 200;
  synthetic ordinary account (canonical role LEARNER, user label MEMBER) 403
  with `APPLICATION_SUPER_ADMIN_REQUIRED`.
- `/api/enterprise/auth/admin/check`: authenticated synthetic account defaults
  to 403, server policy grant permits admin response, grant withdrawal restores
  403. `/auth/me` role is SUPER_ADMIN while tenant membership stays LEARNER.

## Prepared operator plan — NOT EXECUTED

ROLE_MAPPING_PLAN: After acceptance, owner approves the exact deployment,
existing human identity and canonical issuer+subject/account ID via authenticated
identity evidence. Display name is only a confirmation aid. Append that ID to
Backend-only `MENTORFLOW_APPLICATION_SUPER_ADMIN_ACCOUNT_IDS`, preserving existing
entries and the 32-ID limit; approved operator restarts Backend. Do not create,
rename, merge accounts or assign Keycloak realm-admin/SQL privileges. If the
intended account is absent, stop this mapping procedure for separate owner scope.

ROLLBACK_PLAN: Preserve prior approved allowlist and configuration revision;
remove only the newly authorized ID (or restore that exact prior configuration),
restart Backend, verify admin endpoint rejects the removed grant and unaffected
approved administrators still work. No account/data deletion.

NEGATIVE_MEMBER_TEST: Authenticated unrelated synthetic ordinary account must
receive 403 at both admin endpoints, including after grant and rollback; no role
may be granted by username, email, client header/query or IdP role claim.

AUDIT_REQUIREMENTS: Record approver, operator, environment/issuer reference,
canonical account reference, reason, change ticket, before/after config digest,
timestamps, restart identity, response status/code and rollback verification.
Use restricted audit storage for identity references. Never record passwords,
access/refresh tokens, private keys or sensitive response bodies.

## Delivery boundary

Only this additive governance receipt is changed. A should consume the exact
worker delivery and remove only the exact-name item from its current blocker
projection, retaining operator bootstrap as a post-acceptance task. This worker
does not move control/integration/main2 or overwrite their handoffs.

TONYSWIFT_ACCOUNT_MUTATION = 0
PRODUCT_CODE_CHANGED = NO
SECRET_EXPOSURE_COUNT = 0
REMAINING_BLOCKER = NONE_IN_EXACT_USERNAME_RBAC_CLASSIFICATION_SCOPE

## Reviewed immutable blobs

- `docs/APPLICATION_ROLES.md`: `6687b4d801568af6df7fa6d2c0e811f9f8ef6108`
- `docs/exec-plans/active/MAIN3.md`: `b14eb5f4bca09b44343fb6b793a9ac08ce27c1fa`
- `docs/ADR_NATIVE_CREDENTIAL_AUTH_V1.md`: `58343dd69ecb72b3e0014c26561ca38be9320b68`
- `docs/governance/ACCOUNT_MVP_USER_TEST.md`: `c1da0fc932528da41700896ff647bf74502234f9`
- `docs/governance/receipts/20260915-control-convergence.json`: `82ccefc6205437eac6827fcf96e6980facecc704`
- `docs/governance/receipts/20260915-main3-authority-uat-requalification.json`: `7de40f5c81627643227b56a60ed4bf08ec45c55a`
- `GPT_WEB_MENTORFLOW_MASTER_CONTEXT.md`: `075cd3926ddb673bcf38d7a9d6410c0749c8e9af`
- `backend/app/enterprise/identity/application_roles.py`: `00a4dd8fcf62e66b345e38de08c5f17a985b8b18`
- `backend/app/enterprise/identity/admin_overview.py`: `dfc08dc8c426caf14682fe3a186337cc64edc8ce`
- `backend/tests/test_application_roles.py`: `298a6ae310d3d202172a508dc34329345f3be0f6`
- `backend/tests/test_admin_overview.py`: `c0adbaf6c81ac8e8cfe703070b95992134d1fd08`
