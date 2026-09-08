# E107 protected-installation implementation — PARTIAL

Dispatch: MF-E107-STARTUP-TRUST-BOUNDARY-20260906-01.
Task: MENTORFLOW_WINDOWS_FAST_START_VERIFICATION_V1.
Base: 3575e062711ac610312d294bb65dd46ba4236759.

Architecture decision applied: installation/update is the full-verification
boundary. The old same-user DPAPI provenance requirement is superseded. Existing
legacy verification paths remain unchanged until the new installer is integrated.

## Implemented and exercised

`windows_install_trust.py` provides elevated install/protect/activate operations,
read-only normal `gate`, and native `launch` dispatch. Installation uses a fresh
dedicated version root, refuses existing targets, and creates every directory,
file and receipt with an admin-owned protected DACL from the outset. Partial
copy or failed full verification cannot issue a valid receipt. Receipt publication
is atomic. Existing-target repair can re-protect and perform full activation;
missing/corrupt bytes require a fresh installation from the verified payload.

The Windows ACL contract is enforced through security-descriptor APIs, not a
DPAPI signature or an editable user-directory flag. SYSTEM and Administrators
receive full control; Users receive read/execute only. Root, descendants and
receipt ownership/DACL are checked, including parent-directory replacement
permissions and reparse/hardlink rejection. E: is NTFS. No E: volume-root ACL
was targeted for modification. API references:
[SetNamedSecurityInfoW](https://learn.microsoft.com/en-us/windows/win32/api/aclapi/nf-aclapi-setnamedsecurityinfow),
[security descriptor operations](https://learn.microsoft.com/en-us/windows/win32/secauthz/security-descriptor-operations).

Isolated installation: `E:/MentorFlow-TrustTest-20260906/large/App`.
Receipt: `E:/MentorFlow-TrustTest-20260906/large/installation-receipt.json`.
Its owner is BUILTIN/Administrators. Effective DACL:
`D:P(A;;FA;;;SY)(A;;FA;;;BA)(A;;0x1200a9;;;BU)`.
Receipt binds manifest SHA, exact source/tree, version, absolute root and NTFS
identity, full inventory identity, complete verification time/status, and critical
hashes. The retained old payload's provenance is 1531c65116063ec606ed1c623acd29fb68e9e8b7,
tree c441dbfd48fa4bedcac1ee7212866728bc8ac03c, as recorded in its TEST-ACCESS.md.
This isolated receipt is NOT a new product release qualification.

Normal verification checks one complete inventory and ACLs, the pinned manifest,
receipt identities, and all critical hashes. Static resource metadata includes
NTFS ChangeTime, not just restored mtime. Invalid trust returns exit 3 / FULL
VERIFY OR REPAIR REQUIRED before application execution; normal startup never
silently issues a new receipt or elevates. Elevated activation performs full
verification. New-copy parent-directory notifications can invalidate an activation;
the driver discards that attempt and retries the entire full check at most twice,
never suppressing a notification or publishing a receipt from a failed attempt.

Classification is contextual and fail-closed. Only known Qt designer `images/*.png`
paths are currently exempt from repeated content hashing (377 of 14,038 payloads).
The remaining 13,661 are critical, including DLL/PYD/Python/QML/scripts/configs,
plugin metadata, and unreviewed story/prompt assets. Their hashes are never skipped.

## Native startup source and remaining integration

The installed launcher uses a windowless native helper and the existing pinned
preview transport directly, without daily PowerShell. It retains strict external
profile JSON, clean child environment, TLS certificate/SPKI/issuer pins, no
workspace hints, and no application execution before the gate. No server mutation
or online account test was performed.

The helper is **onedir**, not one-file: its runtime must stay inside the protected
installation, not be extracted to user-writable TEMP. MentorFlow.spec includes
the new GUI/asInvoker helper and its dependencies. The small standalone helper
was built separately for native-gate testing; the final product package was NOT
built. Initial one-file experiments are superseded and are not acceptance evidence.

`create_protected_install_shortcut.ps1` is installer-stage publication source:
after trusted protected activation it checks the integrated gate and publishes
a shortcut directly to `MentorFlowInstallGate.exe launch`. It must be invoked
by the trusted elevated installer for its just-activated root, not as a general
untrusted-path bootstrap. The existing NSIS installer is NOT yet wired to the
new install/activation/shortcut sequence. No current desktop shortcut was changed.
Visible installer progress and the complete new shortcut-to-LoginGate flow remain
unverified. The final integrated payload must include the pinned certificate in
`App/Config/edge-tls-ca.crt`; the old retained payload is not a drop-in launch
candidate for this new layout. Do not promote this receipt to SOURCE_READY.

## Tests and measurements

Standalone unittest avoids backend conftest and all application/database imports.
Final focused result: 16 PASS, 0 SKIP, 0 XFAIL. Cases exercise new atomic install,
full-to-fast transition, actual same-user non-elevated write/create/delete denial,
receipt mutation/deletion, root and child ACL drift, real new-version/manifest
activation, unknown code/config/plugin files, restored-mtime DLL tamper, elevated
static-resource tamper plus full rejection, profile schema and clean environment.
Historical four WinError 1314 symlink skips were not rerun or relabeled PASS.
Earlier fixture failures (identification-only token, insufficient token handle
access, a missing keyword argument, final directory notifications) were diagnosed
and corrected; they are not hidden behind skips or xfails.

Latest exact-source full verification: 9.368 s, hash 5.510 s, all 14,038 payloads.
Three final onedir native gate processes, using a verified same-user normal token:

| Run | Seconds | Exit |
| --- | ---: | ---: |
| 1 | 10.790 | 0 |
| 2 | 9.502 | 0 |
| 3 | 9.470 | 0 |

P50 9.502 s; MAX 10.790 s. These include native gate process start-to-exit, not
launch-click-to-form. Evidence: E:/Codex/Temp/E107-TrustBoundary/native-gate-results.json.
No fastest-run selection; security <=3s is NOT MET. Login window and interactive
form for this new installed chain are NOT_RUN, so <=8s is NOT QUALIFIED.
Most payload remains critical; root protection alone is not being substituted
for the required critical SHA-256 checks to manufacture a performance pass.

Preserved release EXE still hashes to
4235AB853C4EB45F848AD92F8F87943C064B0B3BE280EA2556700C6E9D98C496.
No dependency/lock changes, push, server deployment, formal database/UAT/snapshot
access or final unified package build. Earlier worktree-database test-isolation
incident remains disclosed in the preceding report; this dispatch's standalone
tests did not use that test harness or open application databases.

## Acceptance gaps

- Wire and validate the real elevated installer UI and shortcut publication.
- Finish protected-layout certificate/launcher packaging integration.
- Improve the measured critical verification path to <=3s without loosening policy.
- Run the new installed launch chain and prove LoginGate first, QtCore loading,
  main window hidden before authentication, clean close, and form timing.

No further design authorization is requested. This is implementation progress,
not completion of the supplied architecture decision or permission to build the
final unified product. Owner A must retain these gaps when integrating Import.
