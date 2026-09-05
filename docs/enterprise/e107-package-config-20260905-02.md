# E1-07 package/config boundary repair

Dispatch: MF-E107-LOGIN-UX-RESUME-20260905-02

Baseline HEAD: dc6b8bbb24a9fbc5cc878841674bffca2a51519a (initial worktree clean).

REJECTED_FILE = account-core-launch.json

REJECTING_FUNCTION = scripts.verify_windows_package.build_manifest -> _find_user_data_files

REJECTION_RULE = application-owned path + PRIVATE_NAME_RE matches account followed by a hyphen

EXPECTED_MANIFEST_STATE = immutable program payload manifest under SELF-CHECK/package-manifest.json, generated after final package layout

ACTUAL_STATE = generation rejects legacy account-core-launch.json before writing a manifest; original raw PyInstaller output also lacks the standard SELF-CHECK support-file layout

This is a file-placement/privacy-policy conflict, not a SHA mismatch. No privacy
allowlist or original package-verifier code is changed. The original release is
preserved, including its sidecar. The supplied path ending MentorFlow\\.exe is a
path typo: the verified baseline is MentorFlow/MentorFlow.exe with the supplied hash.

## Boundary

Use prepare_account_core_package.py as a module to copy the frozen baseline into
a new, previously absent release directory. This is layout preparation, not a
PyInstaller rebuild. Program files and SELF-CHECK support files are immutable and
covered by the unchanged manifest. Runtime/launch.json is outside that boundary.
The release launcher pins the manifest, frozen verifier and preview CA hashes.
Protected program files still undergo the original full integrity verification.

Runtime JSON accepts exactly schema and data_profile, with strict types, unique
keys, bounded length, a fixed schema and a constrained profile slug. Profile roots
are confined to E:/Codex/Data/MentorFlow-E107-PackageUAT, and reparse paths are
rejected. Arbitrary data paths, issuer, TLS options, credentials, environment maps,
organization and workspace keys are rejected, not silently ignored.

The launcher creates a minimal child environment, discarding inherited account,
tenant, proxy, TLS, Python and Chromium overrides. It uses the existing pinned
HTTPS preview endpoint and certificate/SPKI trust configuration, not a mutable
certificate-error bypass. Passwords/tokens are neither retrieved nor persisted.
There are no hardcoded test-account workspace defaults. The existing verified
/auth/me response supplies the effective organization/workspace after login;
403/404 handling is unchanged and remains fail-closed.

The launcher also restricts PowerShell module discovery to its own trusted
PSHOME/Modules and explicitly imports the built-in utility module. This prevents
an inherited development PSModulePath from breaking Get-FileHash, or selecting
a user-supplied module for integrity verification.

The supported entry is Start-MentorFlow.cmd (or its PS1), not direct EXE launch.
The unchanged EXE without the old sidecar depends on the external launcher's
isolated Account Core environment. No direct-EXE safety claim is made.

This dispatch does not start a tunnel, log in online, poll worker 06, create
workspaces or change server/user data. Preview service/tunnel and live workspace
binding will be qualified only under the subsequent architecture dispatch.

## Verified results for this dispatch

- Focused suite: 92 passed, 4 skipped, 1 warning in 115.20 seconds. The four
  existing package-privacy symlink tests require a Windows privilege unavailable
  on this host; they are not reported as passes. The warning is the existing
  Starlette/httpx deprecation, and no dependency was installed.
- Command: existing Python environment, pytest with
  backend/tests/test_account_core_package_launch.py,
  backend/tests/test_windows_package_privacy.py,
  desktop/tests/test_login_gate_bootstrap.py and
  desktop/tests/test_enterprise_oidc.py; -q -rs -p no:cacheprovider, offscreen Qt,
  and E:/Codex/Temp/E107-Config-20260905-02 for test temporary files.
- Full release copy, native launcher and native verifier: 10/10 checks pass.
  Same-size one-byte EXE and Qt6Core.dll mutations produce exact tampered-path
  failures. A changed manifest fails the launcher's trust anchor. Invalid
  issuer/workspace/TLS/token/unknown config keys fail before program launch.
  The restored full copy passes again. Mutations never targeted the release.
- Final Start-MentorFlow.cmd launched the final reused EXE from a clean child
  environment, after native package verification. The only observed app window
  was LoginGate; bundled Qt6Core loaded, no developer Python environment or
  tenant defaults were inherited, and both app and launcher exited 0 after
  closing the gate. An HWND-only screenshot excludes unrelated desktop windows.
- Immutable manifest: 14,033 files, 2,096,207,762 bytes.
  SHA-256: 2400A7EE3ED63F3496A1F70D2F5B646B1D1412FA4E21964C6BA54AAE5C1CEFE7.
- EXE unchanged: 9684CA57C7DA5E91F4B91433E1FFE6200234121DE97102538C6D556D518E5EF8.
- External launcher matches the checked-in template plus the two release trust
  anchors, after normalizing LF/CRLF. Old release EXE and sidecar hashes remain
  unchanged. Existing app/OIDC/bootstrap/UI/package-verifier code has no diff.

Early test findings were fixed, not hidden: a preparation-overwrite unit test
first hit the E:-only output guard under a C: test directory; overwrite refusal
now happens first. Full-copy launcher testing exposed inherited PSModulePath;
the fixed trusted-module initialization is covered by a new regression test.

Final status: CONFIG_FIXED_READY_FOR_LIVE_BIND, not user hands-on qualification.
