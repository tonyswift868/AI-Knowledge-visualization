# Worker 02 — long-path and Frozen A/B closeout

Dispatch `MF-MAIN3-02-LONGPATH-AB-FINAL-20260916-35`. Gate #1 **PASS** on the bounded synthetic local UAT authority. This is a checkpoint continuation, not a new release or Workbench CURRENT promotion. Remaining big gates: **7**.

Base control: `d15cdf7b12e28dbeb6f553af0040bfde7ead4c0c`; base worker: `247741ed87ceac075e8c9b2828bf56b7d3165fbc`. All changes are on `worker/main3/02`. The delivery commit is the commit containing this directory; resolve it with `git log -1 --format=%H -- docs/qa/MF-MAIN3-02-LONGPATH-AB-35/receipt.json`. Its tree is `DELIVERY_COMMIT^{tree}`. These publication identities intentionally differ from the frozen build source.

## Exact final candidate

- Source: `a1190ca18753fdedb3e4c155efeb3a111cfbb94f`
- Tree: `aafbd810065b86f81cba400f2a60516960106501`
- Build: `MF-02-FROZEN-35-a1190ca1`
- EXE SHA256: `a31849225ba6a7becee8116b579b4d4bce35be6984673cbf2af97b320313c2de`
- Package manifest SHA256: `3a6784844424e8280353e3f23c701fd63c15b1abc747c3488394141a6fdaf2ab`
- Exact path and verifier outcome: [candidate.json](candidate.json). Package verifier PASS. Source archive/commands recorded; bitwise reproducibility was **not tested**.

## Fixes and their evidence

| Defect | Root cause and minimal change | Commit / regression |
|---|---|---|
| MF02-D34-03-LONG-PATH-UPLOAD | Ordinary source open failed before HTTP with LongPathsEnabled=0. Reuse existing native path helper for stat/open/hash; retain canonical domain identity, reject relative/traversal/device/ADS/reparse/hardlink sources. All three upload paths share one checked stream. | `258ba9f`; upload source tests |
| MF02-D35-01-DELETE-BOUNDARY | Physical purge needed a workspace boundary in addition to account upload root. Enforce exact workspace and reject reparse escapes. | `258ba9f`; material convergence tests |
| MF02-D35-02-STALE-PLAN-PROJECTION | Bound native UI used bootstrap metadata instead of current product plan API. Limit bootstrap-only projection to unbound legacy flow. | `990d1f5`; product plan projection tests |
| MF02-D35-03-LONG-PATH-RESTART | Profile validator used ordinary lstat/iteration on nested valid long-path payload, preventing restart. Use native representation solely at I/O. | `a0fcd37`; full test_devices.py, including hardlink rejection |
| MF02-D35-04-REVIEW-DISCOVERY | Fresh device had restored review records but no UI route to discover their IDs; recovery summary omitted supported counts. Add account-bound list endpoint, existing review tool entry and restore action; consume confirmed review/history counts. | `a1190ca`; backend discovery, rehydration and frontend tests |

The screenshot fallback in the retained UIA harness is limited to a freshly captured, hash-verified frame for the same PID/HWND/artifact and unchanged rectangle. No arbitrary desktop click or OS SendInput is used. It covers Qt row buttons missing from accessibility. Inputs and passwords are not recorded.

Reference: [Microsoft Maximum Path Length Limitation](https://learn.microsoft.com/en-us/windows/win32/fileio/maximum-file-path-limitation). Canonical identity stays normal; only actual Windows I/O receives extended local/UNC spelling. No registry, global PATH or account/workspace ID shortening. Resource sources are not copied to a short path.

## Continuation matrix

| Stage | Actual evidence |
|---|---|
| R1, source `258ba9f` | 352-character original specimen uploaded through actual Frozen file picker. SHA256 `224bc98b41d5c693ba10b1d2ac6f4c851860ec422dc4d5bc157bee9eb3332a7c`; server material `material_6bb5710b887b45c2a7d5`, revision 1. Metadata only; no local locator or physical bytes in sync. |
| R2, source `990d1f5` | Startup exposed profile-validator long-path failure. Superseded by R3; no PASS claimed for that launch. |
| R3, source `a0fcd37` | A GUI saved learner preferences/settings and completed task 1; B restored records, completed task 2 and changed account setting. A restored B values. Task revision 2→4, setting 1→2. Short-resource GUI deletion produced server tombstone and disappeared after B restart. |
| R4, source `a1190ca` | A GUI created review, started round and requested hint. Fresh review discovery on B restored the same ID/round/hint; B GUI paused it; A full restart restored paused state and continued. Review revisions 3→4→5. Both final restarts restored 27/27 public product records and exact revisions. |
| R4 deletion | GUI deleted Chinese resource, revision 1→2. Only its record and associated source revision tombstoned. Unrelated server records, sibling workspace and physical sentinel hash unchanged; B row absent. |
| R4 interrupted action | One reversible SQLite trigger aborted this synthetic campaign's pause receipt before commit. UI showed a service error. Trigger removed; same logical action retry showed explicit interrupted/no-auto-replay text. Exactly one new RUNNING command; no server change (revision stayed 5), including after restart. |
| R4 second account | Real negative-account login, empty recovery counts, empty review discovery. All supported product tables empty; 0 foreign IDs and 0 sync revisions. No leakage. |

Short/Chinese upload and initial login/workspace/resource surface PASS are carried from dispatch34 as explicitly required. R1 upload implementation is byte-identical in R4; [unchanged-upload-lineage.json](unchanged-upload-lineage.json) records this check. The long-path upload is not falsely labeled a newly repeated R4 upload. Final R4 A and B used the same verified EXE and independent durable profiles, credentials, settings and caches. Filesystem copies A→B and B→A: **0**.

## Fixtures and limits

- A legal synthetic plan fixture supplied deterministic tasks. A retained empty synthetic session fixture was removed before real GUI start generated normal session state. Fixture writes are not counted as GUI writes.
- A sibling-workspace sentinel fixture provided a deletion boundary witness. No real data, server account lifecycle, SMTP, VPS or external provider was used.
- The retained negative fixture's requested UUID differed from Keycloak's assigned UUID. Read-only issuer lookup verified the actual username/subject; admission used that verified identity. No credential or account mutation.
- Review write coverage is create/start/hint/pause/resume. An attempted UIA answer fill did not drive React state; no submitted-answer or grading PASS is claimed. The review web view is functionally visible with plain styling; this is not visual-polish acceptance.
- Missing-resource UX is the actual metadata-only contract. No payload download/object-storage or migration implementation is claimed. It did not block entry into the workspace.
- Retained scripts are audit companions from D scratch, not a new application engine; execute only in a separately admitted synthetic test root. The interruption helper must never run against a user profile.

## Validation and retained evidence

[tests.json](tests.json): **82 passed, 0 failed, 0 skipped** (35 resource/storage + 3 plan projection + 28 complete device harness + 8 backend/rehydration + 8 frontend). Frontend production build, package verifier and git diff checks passed.

[ui-observations.json](ui-observations.json) records actual PID/HWND text and controls. [gui-input-audit.json](gui-input-audit.json) records targeted actions, OS SendInput=0. [server-revision-ledger.json](server-revision-ledger.json) contains IDs, revisions and public-value hashes without credentials. The compare files prove exact public values and revisions, using the product's existing allowlist normalization. Tombstones without payload are checked separately by the deletion proofs rather than counted as live rows.

[r4-proof.json](r4-proof.json), [ab-core-proof.json](ab-core-proof.json), [negative-r4-isolation.json](negative-r4-isolation.json), [long-path-proof.json](long-path-proof.json) and [closeout.json](closeout.json) are bounded assertions. All owned Frozen processes were closed; other workers' processes were left untouched.

[storage.json](storage.json): all build/test/runtime/profile churn on D; E contains only source and final evidence. Scratch retained for independent inspection. The closeout byte count is measured; peak was not continuously sampled. Global Codex storage was neither modified nor audited. No real user data mutation, real SMTP, VPS deployment, secret exposure, protected-ref move or Workbench CURRENT update.
