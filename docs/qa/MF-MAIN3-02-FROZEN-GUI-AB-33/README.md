# Dispatch33 Frozen GUI closeout — BLOCKED_GUI_INPUT_ISOLATION

Exact dispatch32 EXE and its worker02 copy matched the supplied SHA256. Running the copy with the required worker02 D profile failed before login: `TEST_EPHEMERAL_UNASSIGNED_ROOT`.

Fix `76b9b946bac8b15084e7e591c2f85a5abfd05fa1` adds only the explicitly assigned dispatch33 root and its NEGATIVE profile. Environment selection still cannot authorize arbitrary D paths or cross into another assignment; ownership, reparse and reset fences remain. Focused tests: 5 PASS; full test_devices.py: 27 PASS.

The rebuilt exact QA candidate passed package verification, synthetic native login, empty-account recovery and main-window first paint. Only the three reviewed synthetic screenshots below are retained. A screenshot returned for an occluded target showed another app; it was not saved as evidence.

- `login-A.png`: new frozen login gate, no credential entered.
- `A-empty-rehydration.png`: synthetic account's initial empty recovery view.
- `A-workspace.png`: synthetic account main window.

The later target click was rejected because another application covered MentorFlow. One target activation/refresh recovery still returned non-target pixels. GUI input stopped; the owned A process was stopped by verified PID/path cleanup. This is an automation blocker, not evidence of a sync regression. B, negative-account GUI, resource upload, business-write convergence, deletion and restart gates remain NOT_RUN.

An independent disposable UAT (head0040, exact issuer/JWKS, no-context RLS denial, backend health and SMTP AUTH/NOOP without MAIL/RCPT/DATA) was prepared on D. Existing demo fixture setup was used to avoid Provider generation. Fixture writes are explicitly excluded from GUI acceptance. No profile or payload was copied A-to-B. All scratch and private credentials remain in the dispatch's D root, outside Git. UAT is retained with its recorded lease; no production or other worker state was changed.

`receipt.json` contains every requested gate and storage field. Its containing commit/tree is the final delivery identity; candidate.json binds the separate exact build source/tree/artifact. FULL_FROZEN_GUI_AB and FULL_ACCOUNT_REHYDRATION are not PASS. No bitwise-reproducible rebuild claim.
