# E107 native visibility state regression — PARTIAL

Dispatch MF-E107-STARTUP-TRUST-BOUNDARY-20260906-01.
Task MENTORFLOW_WINDOWS_FAST_START_VERIFICATION_V1.
Base e3988c35ed5251f5c672d7bdd91ae928d4e56a0b.

Extracted the observer's real Win32 visibility predicate into login_window_state.
The existing actual synthetic-window regression now drives visible -> minimized
-> hidden -> restored -> undersized 150x150 -> original dimensions. It calls the
same IsWindowVisible/IsIconic/GetClientRect predicate as the product observer and
waits boundedly for each expected state. Only the exact fixture HWND is changed;
normal dimensions restored before the existing render/focus tests. No production
window or user settings touched. Three focused tests PASS in 2.671 seconds.

This fills the dedicated native minimized/hidden/small-client regression gap.
The observer also clears its candidate HWND before each enumeration (prior
change), preventing reuse when no current window qualifies. A visible normal-size
window still does NOT imply a rendered or interactive identity-provider form.

No actual product measurement repeated. Native gate results from the latest
frozen helper remain 2.974892/2.286175/2.432933s, P50 2.432933, MAX 2.974892, all
normal-token exit 0. This establishes only that three-run window, not universal
long-tail performance. Historical slow samples retained. Form readiness remains
unverified: UI Automation is unqualified for this Qt page; PrintWindow produced
only frame/blank-client output; occluded BitBlt was rejected and removed; TLS
discovery can succeed but does not prove actual form input readiness.

Further acceptance needs reliable direct foreground observation with user
assistance (no credential submission) or an independently qualified observation
mechanism that respects the no-DOM/no-secret boundary. Do not keep relabeling
shell visibility as FORM_INTERACTIVE, or launch another unchanged observation
solely to manufacture progress. Goal not complete; request assisted measurement.

No runtime policy/product redesign, final package, dependency change, push,
server mutation, formal data write or account test suite. Historical four
WinError 1314 skips remain skipped. PARTIAL_RETURNED.
