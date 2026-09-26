# Worker 02 — Frozen A/B UAT 27 and upload defect repair

Dispatch: MF-MAIN3-02-FROZEN-PRODUCT-AB-UAT-20260915-27.

Status: PARTIAL_SOURCE_FIX_GUI_RETEST_PENDING. The user stopped desktop automation and requested source repair, with subsequent GUI testing to be performed by the user. No GUI interaction occurred after that instruction.

## Exact identities

- Approved control source: `69b7e210ce7ff4abeb14337e4d969b0420db476a`.
- Approved source tree: `d55f893a169a63a1f83cf8a9fafa2725796a011a`.
- Worker patch parent: `ac75a90b4c6f26d4ecb381ca3ea23cdcd2b8f0f8` on `worker/main3/02`.
- Frozen build ID: `MF-02-AB-UAT-27-69b7e210`.
- Frozen artifact: `E:/Codex/Temp/MF-02-AB-UAT-27-69b7e210/dist/MentorFlow/MentorFlow.exe`.
- Frozen artifact SHA256: `ea32e6eeaae935b60e4ecf8b80525da90642c04c21f5a77f22764f3170534136`.
- Build reproducibility: commands and package hashes recorded; a second bitwise build was NOT performed.
- Local build receipt: `E:/Codex/Temp/MF-02-AB-UAT-27-69b7e210/build-receipt.json`.

The TEST package used the exact source archive, unmodified spec, generated lifecycle resources and built frontend, collected into their spec destinations. It was not published to Workbench CURRENT or promoted. The existing EXE predates this repair and must not be described as containing it. The affected product file was identical between the worker patch parent and approved control source. Integration must consume this patch onto its approved baseline, not treat the entire worker branch as the current integration tree.

## Observed product behavior

The actual Frozen EXE displayed native login, accepted a fresh synthetic test account, displayed the empty cloud rehydration state, and entered the workspace without requiring a migration pack. Its plan creation page loaded. Selecting the synthetic lesson and submitting plan creation produced `服务暂时无法完成请求`; no plan or material was created. The resource page displayed its empty state and required a plan before adding resources.

Screenshots retained under `E:/Codex/Temp/MF-02-AB-UAT-27-69b7e210/`: `A-login-ui.png`, `A-empty-rehydration.png`, `A-workspace.png`, `A-create-plan.png`, `A-plan-failure.png`. No password/token screenshot is part of this evidence.

Device A: `E:/Codex/TestDevices/MentorFlow/A`.
Device B: `E:/Codex/TestDevices/MentorFlow/B`.
Both were prepared as independently owned fresh profiles; previous owned test roots were retained separately. Filesystem copy A to B: **0**. B was not launched before desktop automation was stopped.

LOGIN_UI and WORKSPACE_UI: PASS for the observed A session. PLAN_UI: rendered, mutation failed. RESOURCE_UI: empty state observed only. PROGRESS_UI, MISSING_LOCAL_RESOURCE_UI, SYNC_FAILURE_UI, RESTART_UI: NOT_VERIFIED. A_TO_SERVER, SERVER_TO_B, B_TO_SERVER, SERVER_TO_A, DEVICE_B_RESTART_REHYDRATION and ACCOUNT_ISOLATION: NOT_VERIFIED. No Frozen A/B product UAT PASS is claimed.

## Defect MF02-UPLOAD-MAXPATH-27

The material upload path appends the full workspace identifier beneath two account hash components. On this Windows host, creating that directory through an ordinary pathlib path fails beyond MAX_PATH with `FileNotFoundError: [WinError 3]`. The upload route does this before parsing/inserting the material; its exception becomes the generic HTTP 500 shown in the GUI.

An isolated HTTP regression reproduced that exact route failure on the real Windows filesystem. A short-path upload against an isolated snapshot of the synthetic database succeeded, and required tables were present. This establishes the long-path source defect consistent with the GUI failure; the running Frozen process did not expose an uncaught stack trace, so a post-fix packaged retest remains necessary.

The repair reuses the existing native Windows path helper at material upload I/O, preserving the account directory and full identifiers. Physical deletion normalizes both the stored locator and allowed root to the same spelling before checking resolved ancestry. This also preserves deletion compatibility with older ordinary-path records. Default deletion remains reversible archive; paths outside the upload root are not deleted. No global Windows policy, credential behavior, sync engine or UI was changed.

## Validation and handoff

Before repair: new long-account-path HTTP regression failed at `target_dir.mkdir` with WinError 3.

After repair: **20 distinct tests passed** on Windows using isolated `E:/Codex/Temp` roots:

- `backend/tests/test_material_long_account_paths.py`: 3 tests, including long paths, legacy ordinary paths and outside-root deletion protection.
- `backend/tests/test_material_pipeline_v1.py` and `backend/tests/test_upload_limits.py`: 17 tests.

The first run covered 19 tests; the subsequently added outside-root regression passed separately. A pre-existing Starlette/httpx deprecation warning remains. These are source/HTTP/filesystem regressions, not packaged GUI or cloud continuity acceptance.

For the next user test, integrate this patch and build a new TEST candidate with a new recorded identity. Repeat the synthetic lesson upload and plan creation first, then resume the A/B product matrix. Do not reuse this report's pre-fix EXE as proof of the fix.

Real user data mutated: NO. Secret exposure count: 0. SMTP lifecycle, VPS deployment, real-user login, Workbench CURRENT mutation and canonical promotion: NOT_RUN. The new disposable test runtime and existing GUI session were not interacted with after the user's desktop stop instruction.
