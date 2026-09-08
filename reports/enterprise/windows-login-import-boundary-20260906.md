# E107 login-only import boundary — PARTIAL

Dispatch MF-E107-STARTUP-TRUST-BOUNDARY-20260906-01.
Task MENTORFLOW_WINDOWS_FAST_START_VERIFICATION_V1.
Base 9478b8bb6d489b2746b3452eaf8157af83205e17.

## Change

MentorFlowApplication no longer imports DesktopApi or LocalService during
construction. LocalService loads where Personal Local starts its service or
after account bootstrap starts its service. DesktopApi loads on service-ready.
The existing MainWindow import remains inside authenticated _show_main.
Remove an unused top-level runtime import from startup_flow. Update the existing
stale-service-signal test's injection point to desktop.runtime.LocalService.
No UI redesign, account contract, network protocol or service behavior changed.

## Fresh-process proof

New standalone test starts an independent Python process and installs an import
fence BEFORE importing desktop.main. It rejects desktop.main_window, desktop.api,
desktop.runtime, backend.app.main, backend.app.orm, torch, docling, transformers,
sentence_transformers and uvicorn, including their submodules. A Python audit
hook rejects sqlite3.connect and socket.connect. It checks a visible source Qt
LoginGate, no main/API/service instance and no forbidden module in sys.modules.

Settings use a dedicated temporary INI, all data env paths are bound to new E:
scratch before import, and the interactive-auth entry is replaced with a recording
stub. This deliberately proves only the offline pre-auth import boundary, not
an online form, auth exchange, rendered production package or performance target.
Credential-store definitions needed for login are allowed; actual ORM/main server
imports and database connections are prohibited. No .db was created in the test.

## Regression

- 38 standalone unittest cases PASS, exit 0, 0 skips.
- 3 selected pytest cases PASS: business-window import ownership, first-screen
  embedded auth-pane flow, and old-account service signal rejection.
- First pytest attempt failed three fixture setups because the chosen new
  basetemp parent did not exist. Create the isolated parent before collection;
  corrected rerun 3 PASS in 0.53 s. No product change hid that setup error.
- All data-root env vars set before pytest collection; only the three named
  desktop cases were collected, not the 169-case account suite.
- Historical four WinError 1314 skips not rerun or promoted.

No final/native build or fresh startup timing this turn. Earlier native gate
P50 4.142239 s/MAX 4.842929 s belongs to the prior helper. Earlier retained-EXE
LoginGate observations do not verify these source changes in a final package.
No server mutation, formal database write, dependency/lock change or push.
Installer wiring, <=3 s gate and <=8 s real form remain incomplete. Return
PARTIAL; do not treat this as integrated package or hands-on test readiness.
