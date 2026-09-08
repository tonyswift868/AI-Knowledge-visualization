# E107 native install-to-shortcut step — PARTIAL

Dispatch MF-E107-STARTUP-TRUST-BOUNDARY-20260906-01.
Task MENTORFLOW_WINDOWS_FAST_START_VERIFICATION_V1.
Base d4549545e6aa3861e93ab63e041cd9ce0e141bff.

## Implemented source

The existing native `install` command accepts optional `--shortcut-path`.
It first completes protected installation/full verification/receipt publication,
then checks the protected installation again and publishes a new link. Other
commands reject the mutation option. The visible installation status remains
open through link publication. Existing entries are refused, not overwritten.

`windows_install_shortcut.py` uses system IShellLinkW/IPersistFile through ctypes,
following Microsoft's [Shell Links interfaces](https://learn.microsoft.com/en-us/windows/win32/shell/links).
No PowerShell, additional COM dependency or child executable is used to create
the link. Target is App/MentorFlowInstallGate.exe with `launch` and exact root,
manifest/source/tree bindings; never the product directly. Product startup is
not performed by the elevated install step, avoiding inherited elevation.

Require elevation, an external .lnk destination, existing NTFS parent chain
without reparse/ordinary-user replacement permissions, and a verified installed
gate. Stage an admin-owned protected link in the same destination directory,
publish with atomic no-replace hardlink creation, then remove only the temporary
name. A concurrently created destination is preserved; no unknown entry is
deleted. Installation can remain valid if link publication fails, but the command
returns failure and does not claim a shortcut exists.

## Checks

46 standalone tests PASS, 0 skips. Seven new checks cover:

- real COM .lnk creation and independent Shell readback of target and pins;
- invalid package rejection before link creation;
- preserving an existing entry byte-for-byte;
- actual synthetic `install` CLI activation then link publication;
- rejection under the same user's normal token;
- concurrently created entry preservation and temporary-name cleanup;
- rejection of shortcut mutation from non-install commands.

Synthetic targets/links live only under E:/MentorFlow-TrustTest-20260906. No
production desktop/Start Menu entry was touched or fixture executable launched.
The first module-level unittest run rediscovered imported base tests; the final
46-case run explicitly selects NativeShortcutTests to avoid double counting.
Historical four WinError 1314 skips not rerun/promoted.

## Still incomplete

NSIS 3.12 compile-only probe rejects `StrCpy $TEMP ...` (read-only built-in
constant). No probe installer was run. Secure outer extraction/bootstrap and
user-facing NSIS integration remain unfinished; this change does not pretend
to complete that boundary or an update/replacement UX. Caller must choose a new
shortcut path; existing shortcut replacement is intentionally not implemented.

No final/native helper build or new startup timing this turn. Prior source gate
P50 4.485898 s/MAX 4.523641 s and prior native P50 4.142239 s/MAX 4.842929 s are
historical, not measurements of a newly built installer/product. <=3 s security
gate and <=8 s interactive form remain unqualified. No server mutation, formal
data write, dependency/lock change, retained-release change or push.
