# Exact Frozen GUI A/B acceptance — dispatch 29

STATUS = BLOCKED_ARCHITECTURAL_PRODUCT_SYNC_WRITE_GAP

The mandatory STOP condition for an architectural defect was reached. No product code was changed, no replacement candidate was built, and no server-side entity seeding was used to replace GUI business actions.

## Authority and runtime

- Dispatch: MF-MAIN3-02-EXACT-FROZEN-GUI-AB-ACCEPTANCE-20260916-29.
- `git fetch --all --prune` completed; remote control exact SHA: `1087091598037b8b4077fd44328ae3312b9ebe4d`.
- Worker evidence parent: `6227abafd5d444e4c71d0e25bd9440d066b12f52`, branch `worker/main3/02`.
- Frozen source: `20e976c7878401123ca2b20f63b2cf70a73e5f3e`; tree: `bf0e13143177a3ec9e9b602f4bd65635961bc241`.
- Initial/final build ID: `MF-A-FROZEN-28-20e976c7`.
- EXE: `E:/Codex/Temp/MF-A-28/dist/MentorFlow/MentorFlow.exe`.
- Recomputed SHA256: `1b5371c22416b579f286e6a411c2c957fa0d9331869a9bfa64e5ac4594be4f93`, MATCH.
- UAT: `MF-A-UAT-28-5ebe30cf`, reused after current lease/process/health checks; replacement not needed.
- Backend `https://127.0.0.1:55303`; issuer `https://127.0.0.1:55302/realms/mf-uat28-fad456f1`; PG `127.0.0.1:57760`.
- At 2026-09-15T18:29:18Z, lease valid through 20:11:36Z. Backend live/startup/ready and JWKS HTTP200. Migration head0040 confirmed. App role is neither superuser nor BYPASSRLS; no-context workspace/sync reads return zero rows. See runtime-recheck.json and upload-correlation.json. These checks are not a full fresh RLS regression suite.

## Actual GUI observations

Fresh owned profiles were prepared at `E:/Codex/TestDevices/MentorFlow/A` and `/B`, with independent generation/credential namespace, vault, preferences and caches. Prior owned test roots were retained at `retained-before-02-uat29`, after ownership, storage and busy checks. No files were copied from A to B. B was not launched before the mandatory STOP.

Actual frozen A process PID90216 displayed native login and authenticated the existing isolated verified synthetic control `uat28-member`. No real account or email lifecycle was used. Its startup rehydration page displayed zero plans/progress and “复习 暂不支持 · 历史 暂不支持”. The enabled entry button reached the workspace without a migration pack.

Through the Frozen planner UI, the operator selected the generated synthetic `中文学习资料.txt`, confirmed its use, and submitted plan creation. The UI then displayed **已保存资料 1 份 / 已索引，可检索 / 1 个可引用分块 / 45 字符**, the Chinese filename and SHA256 prefix. Read-only local SQLite inspection correlated this to `material_5d5109f953284f3098c3`, workspace `learning-task-0fa29d22`, status `ready`, and matching stored bytes hash. See the screenshots and upload-correlation.json.

**Coverage correction:** the persisted ordinary path is 258 characters. Despite deep account hash directories, this run did not reach the requested >260-character/long-workspace boundary. LONG_PATH_UPLOAD is NOT_COVERED, not PASS. Chinese filename upload is PASS; short/ordinary file path upload is PASS for this single 258-character specimen only. No broader short/long path matrix is claimed.

Plan generation then correctly refused to proceed: **知识地图 v1 需要已验证的真实 Provider**. The model connection page showed **尚未连接**. No approved model credential was supplied with this UAT configuration. This is an additional test prerequisite, not a proven product defect; no mock provider, user credential reuse or authority bypass was introduced.

## Architectural defect MF02-GUI-DOMAIN-SYNC-WRITE-GAP-29

The successful GUI resource mutation stays in the account's local active-teaching database. A read-only query of the isolated server's `structured_sync_entities` found zero rows for its exact resource ID. Source tracing against the exact frozen source confirms the missing product connection:

1. `desktop/main_window.py`, `_teacher_chat` (~2166): the planner loads from `self.origin`, the account-local service.
2. `frontend/src/features/active-teaching/api.ts`, `activeRequest` (~265) and `uploadActiveMaterial` (~430): mutation is a relative `/api/active-teaching/...` request to that local origin.
3. `desktop/account_backend.py`, `run_account_backend` (~14): spawned backend selects PERSONAL_LOCAL and the account's local SQLite paths; server database URLs are removed.
4. `backend/app/active_teaching.py`, `upload_material` (~2308): uploaded material and conversion metadata are committed to local storage. This path has no server structured-sync publication/outbox operation.
5. `desktop/structured_sync.py` defines `write` and `delete`, but the non-test desktop consumer found by source search is `desktop/rehydration_transport.py` (~311), which constructs the client only to call `rehydrate`. The main window's authenticated transport is used for password lifecycle, not this planner's domain writes.

This is a product mutation-to-sync integration gap, not a hash/path bug or failed PostgreSQL health check. A zero-row server lookup by itself would only establish non-publication at the observation time; the source call chain establishes why this GUI path cannot satisfy the requested server continuity. Implementing all requested plan/task, progress/review, learner/history, setting and resource write/revision/domain bindings is outside the permitted narrow-fix scope. Dispatch section12 requires STOP for an architectural defect. No new engine or ad-hoc publish step was implemented.

The provider prerequisite prevented creating the remaining representative entities. Even a model connection would not supply the missing mutation-to-sync binding. Full A/B tests must wait for that product integration, a qualified new candidate, and the real provider configuration.

## Gate receipt

```text
LONG_PATH_UPLOAD = NOT_COVERED
SHORT_PATH_UPLOAD = PASS_ORDINARY_258_CHARACTER_SPECIMEN_ONLY
CHINESE_FILENAME_UPLOAD = PASS
RESOURCE_DELETE = NOT_RUN
ACCOUNT_STORAGE_ISOLATION = PROFILES_PREPARED_NOT_NEGATIVE_TESTED
WORKSPACE_STORAGE_ISOLATION = NOT_RUN
DELETION_BOUNDARY = NOT_RUN
LOGIN_UI = PASS_A
WORKSPACE_UI = PASS_A
PLAN_UI = RENDERED_BLOCKED_PROVIDER
PROGRESS_UI = NOT_RUN
RESOURCE_UI = PASS_PLANNER_INDEXED_RESOURCE_VIEW
LONG_PATH_UPLOAD_UI = NOT_COVERED
MISSING_LOCAL_RESOURCE_UI = NOT_RUN
SYNC_UI = NOT_RUN
RESTART_UI = NOT_RUN
ERROR_RECOVERY_UI = PROVIDER_ERROR_VISIBLE_RECOVERY_NOT_RUN
A_TO_SERVER = FAIL_GUI_RESOURCE_NOT_PUBLISHED
SERVER_TO_B = NOT_RUN
SAME_WORKSPACE_ID = NOT_RUN
PLAN_TASK_IDS = NOT_RUN
PLAN_TASK_VALUES = NOT_RUN
PROGRESS_REVIEW_IDS = NOT_RUN
PROGRESS_REVIEW_VALUES = NOT_RUN
LEARNER_HISTORY_IDS = NOT_RUN
LEARNER_HISTORY_VALUES = NOT_RUN
NONSECRET_SETTINGS = NOT_RUN
RESOURCE_METADATA = LOCAL_READY_SERVER_ABSENT
LATEST_SERVER_REVISIONS = NOT_RUN
FILESYSTEM_COPY_A_TO_B = 0
B_TO_SERVER = NOT_RUN
SERVER_TO_A = NOT_RUN
UPDATED_VALUES = NOT_RUN
DEVICE_B_RESTART_REHYDRATION = NOT_RUN
ACCOUNT_ISOLATION = NOT_RUN
DEFECT_COUNT = 1_ARCHITECTURAL
DEFECT_1 = MF02-GUI-DOMAIN-SYNC-WRITE-GAP-29
DEFECT_2 = NONE
PRODUCT_CODE_CHANGED = NO
TEST_RESULTS = GUI_AND_READ_ONLY_CORRELATION; SOURCE_SUITES_NOT_RERUN
EMAIL_LIFECYCLE_NOT_CLAIMED = YES
REAL_USER_DATA_MUTATED = NO
REAL_SMTP_SEND_COUNT = 0
SECRET_EXPOSURE_COUNT = 0
FROZEN_AB_PRODUCT_UAT = BLOCKED_NOT_PASS
```

The tested A window was closed normally on stopping. Existing UAT services and all evidence were retained; no shared runtime teardown, protected ref mutation, push, Workbench CURRENT update, VPS deployment or agents. This is an evidence-only delivery for A; the existing 596-source/51-transport test results were not rerun or relabeled as GUI acceptance.
