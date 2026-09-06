# E107 recent-history source repair

Dispatch: MF-E107-RECENT-HISTORY-ISOLATION-20260906-01
Task: MENTORFLOW_ACCOUNT_RECENT_HISTORY_ISOLATION_REPAIR_V1
Base: a9a75ec6871a2291dca78747653a5afd2d6026f2
Base tree: 31dd2b5569f29694b0c25949afcb764bec3698d3

## Original evidence

The single baseline node was run once with `--runxfail`:
`desktop/tests/test_owner0_nonempty_isolation.py::test_recent_history_must_not_cross_accounts`.
Expected: `[]` after switching the preference selection to B.
Actual: one record with summary `PRIVATE-A-RECENT`, concept_key `concept-a`,
and the remaining normalized fields empty. Result: ordinary assertion failure.
This reproduces synthetic desktop data mixing; it is NOT evidence of an actual
user-data disclosure. The original node is now an ordinary mandatory test, with
an authenticated synthetic B runtime context and the legacy-record preservation
assertion added. No XFAIL exemption remains.

## Root cause and complete direct path

MainWindow used global `QSettings("MentorFlow", "MentorFlow")` for all
`analogy/*` keys. `_remember_life_example` wrote latest_card/recent_cards;
`_load_recent_analogy_cards` restored the history; `_schedule_analogy_prefetch`
sent it to `_generate_life_analogy_resilient` and DesktopApi.generate_life_analogy.
The local API `create_life_analogy` assembles recent_history/forbidden_concepts
from that request for the model. The API does not retrieve this QSettings history
itself, so no server or local API implementation change was necessary.

Latest card, prefetch queue, card library, variation counter, generation policy,
keywords and the legacy news/interests fallback shared this path. Consumers are
the overview card, history avoidance context, cached-card browser, asynchronous
prefetch callbacks and the portable preference snapshot. Previous context keys
did not include account ownership. The worker also looked up `self.api` at
execution time, and account exit removed global cache keys instead of retaining
account-owned records.

## Source repair

`desktop/analogy_history.py` is a narrow view over the existing QSettings store,
not a new database or identity protocol. It captures the window's existing
AccountProfile issuer/sub hashes and obtains authenticated organization/workspace
only from runtime.enterprise_account_server_state. Storage prefix:

`account-history/v1/<issuer_hash>/<subject_hash>/<sha256(JSON[organization,workspace])>/`

Each access revalidates the captured scope. Missing/expired context, changed
workspace, changed profile or a sealed window returns safe empty state and makes
writes inert. Preferences, username, display name and arbitrary `user`/tenant
hints cannot choose the namespace. Explicit Personal Local keeps its independent
legacy namespace, but cannot serve as fallback when Enterprise configuration is
active. Unmarked old global records are neither assigned nor deleted nor uploaded.
Obsolete Personal Local quality-policy caches remain on disk but are not revived;
the policy advances only when a new valid card is accepted.

All direct analogy reads/writes use the same scope. Context keys include that
scope. Request assembly reloads scoped history instead of trusting an old memory
list. Scheduling captures the originating API object, scope and generation.
Queued work checks that ticket before dispatch; already-running A work cannot
publish results after revocation. Polls, feedback timers and modal preference
completion are fenced. Exit seals history before the existing executor/API drain;
API/Vault shutdown ordering remains unchanged. Switching does not erase A history.

SystemPage's directly related preference snapshot reads this same captured scope,
on the Qt thread before starting export work. Without history authority it omits
analogy fields rather than exporting globals. Unrelated UI settings remain shared.
No export, import or migration operation was performed in this dispatch.

## Focused acceptance evidence

Synthetic markers: A_ONLY_RECENT and B_ONLY_RECENT.

- Real QSettings persistence, production history loaders, Qt label rendering,
  latest/queue/library reads, A→B and B→A request assembly and A→B→A retention.
- Independent Python processes write A, read/write B, reopen A and reopen B
  against the same INI file using the production read/write methods. This is
  real storage/process evidence, not a comparison of directory names.
- A real worker thread is held after request assembly, B becomes active, then A
  completes. B's entire settings snapshot, UI/context/cache and pending Future
  remain unchanged. Late/unstarted tasks fail closed; captured A API cannot be
  replaced by B's API. No external model calls occur.
- Same subject under another issuer/workspace remains separate. Missing identity,
  expired runtime session and Enterprise-to-Personal-Local fallback fail closed.
- The actual local API message assembler is executed and intercepted immediately
  before provider invocation: recent_history and forbidden_concepts contain only
  the owning account's marker. No model response is fabricated as successful QA.
- The production return-to-login lifecycle is checked for history seal before API
  drain, handle closure and Vault unmount. Nonempty Plan/Progress/resource-binding
  and stale-bootstrap neighbors remain covered by the existing bounded tests.

Final commands use the existing Python environment, QT_QPA_PLATFORM=offscreen and
explicit small E:\Codex\Temp\h07-* roots, never formal data:

1. `pytest desktop/tests/test_analogy_history_isolation.py desktop/tests/test_owner0_nonempty_isolation.py desktop/tests/test_login_smooth.py backend/tests/test_life_analogy_prefetch_buffer.py backend/tests/test_analogy_history_context.py -q`
2. `pytest backend/tests/test_native_desktop_knowledge_map.py -k "life_analogy or next_fact" -q`

Only the adjacent analogy/native tests are selected; the other 41 native tests
are intentionally deselected, not skipped mandatory checks. Legacy lightweight
fixtures now explicitly choose isolated Personal Local mode; their assertions
were not removed or weakened.

## Delivery boundary

Final results: command 1 = 45 PASS; command 2 = 10 PASS / 41 intentionally
deselected. Total focused results = 55 PASS, mandatory XFAIL = 0, SKIP = 0.
An existing Starlette/httpx deprecation warning was observed; no dependencies
were installed or changed. Scope wiring was also inspected: _accept_login_result
records the verified identity/server context before bootstrap and _show_main
passes that same bootstrap profile to MainWindow.

Source-only READY_FOR_INTEGRATION is qualified by these results. Final
integrated-package real-account qualification is
NOT_RUN. No launch optimization, shortcut change, packaging, push, dependency
change, server mutation, formal DB scan/write, historical import or resource
recovery was performed. Prior launch/shortcut delivery and old receipts remain.
