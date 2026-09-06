# E107 owned-window rendering and visibility guard — PARTIAL

Dispatch MF-E107-STARTUP-TRUST-BOUNDARY-20260906-01.
Task MENTORFLOW_WINDOWS_FAST_START_VERIFICATION_V1.
Base fec261c6fb1388b4db523db7422afdaeb0efd447.

Added diagnostic OwnedWindowRender: exact HWND/PID check, bounded bitmap size,
cleared private bitmap and PrintWindow (no desktop/window-DC BitBlt). Exclusive
PNG creation prevents overwrite. Controller runs it in a separate process with
a ten-second bound because PrintWindow is synchronous. No DOM or input values
read. API: https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-printwindow
Synthetic actual-window test confirms wrong PID rejects before file creation and
correct PID renders a nonuniform image. This does not qualify Qt rendering.

Actual old-product attempts using native-deferred-utilities helper:

1. Full activation 12.776959s/hash 9.859988s; old visibility predicate timestamp
   8.359263s. After five-second dwell, rendering returned only a minimized title
   bar. Discovery passed. Close 0.719657s, exit 0, no owned children remained.
   Image owned-window-render-19748.png in the existing isolated observation root.
2. Added guards excluding IsIconic and clients smaller than 200x200. Full
   activation 5.557082s/hash 3.321588s; guarded visibility 6.888615s. No restore
   request was needed/recorded. Rendering returned the target's full frame but
   blank client area. Discovery curl 28, issuer readiness false. Close 0.665359s,
   exit 0, no children remained. Observation correctly failed readiness.
   Evidence observation-46772.json and owned-window-render-46772.png.

Both renderings are INVALID as form evidence. Qt may not support this rendering
path; a blank rendered client is not proof of a blank live form. No other-window
contents appear in these images. They remain isolated diagnostic artifacts, not
product screenshots. Earlier BitBlt capture remains deleted and rejected.

The observer now requires non-minimized, adequately sized client geometry and
clears its saved HWND before each enumeration, so a window minimized/hidden later
cannot reuse a stale qualifying handle. Optional --restore-login-window records
any explicit restore request and targets only the identified owned login window.
Older visibility reports lacked these guards; retain that methodological limit
rather than treating every older sample as robust visible-form evidence.

Three focused diagnostic tests PASS in 3.447s after final guard edit; the exact
new minimized/stale-handle path has code inspection but no dedicated native
regression yet. No new product/gate binary, dependency install, push, server or
formal data mutation. Renderer is a small diagnostic executable only. Native
three-run <=3s result retains its scope; <=8s interactive-form remains unverified.
