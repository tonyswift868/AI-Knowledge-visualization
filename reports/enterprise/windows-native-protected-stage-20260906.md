# E107 native protected staging primitive — PARTIAL

Dispatch MF-E107-STARTUP-TRUST-BOUNDARY-20260906-01.
Task MENTORFLOW_WINDOWS_FAST_START_VERIFICATION_V1.
Base dedd3037d77fc0b17349b8e035ff3cbd7b50aacc.

Implemented installer/ProtectedStage.cs for the future elevated outer installer.
It uses existing Windows/.NET system facilities only: no Python, PowerShell,
NSIS plugin, third-party dependency or staged executable is needed to create
the boundary. This is a component, NOT a complete installation entry point.

Create requires an elevated token, local NTFS and an existing safe parent chain.
Every ancestor is checked for reparse points, trusted owner and ordinary-user
replacement rights, including DELETE on non-volume ancestors. Volume root ACLs
are only read. A fresh unpredictable stage directory is created atomically with
BA ownership, protected DACL, BA/SYSTEM full control and Users read/execute.
Every child directory/file receives the same explicit creation-time descriptor.
Existing objects are refused, not adopted, overwritten or repaired.

The creation-time descriptor uses the documented Windows
[file security model](https://learn.microsoft.com/en-us/windows/win32/fileio/file-security-and-access-rights).
CREATE_NEW and an exclusive file handle prevent replacing an existing file.
Relative input paths reject rooting, traversal, separators/ADS aliases, reserved
device names, trailing dots/spaces, invalid characters and excessive components.
Expected file length is enforced while streaming; partial files remain protected
after failure. There is no receipt or execution operation in this class.

Extended Win32 paths support deep package entries without changing global OS
long-path policy. This is not an end-to-end long-path qualification: the existing
Python ACL/read APIs rejected the unprefixed >260-character observation path.
The component test explicitly inspected its extended-path output instead; that
integration limitation is retained, not reported as a complete-chain PASS.

Validation: compiled the component and synthetic checks executable using the
already-installed Framework64/v4.0.30319/csc.exe. Three native staging tests plus
the legacy installer refusal test PASS (four tests, 1.192 seconds):

- actual protected directory/file creation, nested and >260-character content,
  independent ACL inspection, ordinary-token write denial and no overwrite;
- traversal/device/path and declared-length rejection inside the test executable;
- ordinary-owned parent refused without creating a stage;
- BA-owned parent permitting ordinary DELETE refused specifically for replacement
  rights, without creating a stage; its test-only ACL restored afterward.

Test executables remain under E:/Codex/Temp/e107-native-stage-* and staged files
under the isolated E:/MentorFlow-TrustTest-20260906 container. No production
shortcut, registry, database, retained release, dependency/lock or old receipt
changed. No final product/helper build, push or server mutation. Earlier 58-case
runtime tests and performance samples were not rerun/reclassified. Historical
four WinError 1314 skips remain unqualified.

Still required: embedded payload/identity verification, extraction orchestration,
visible elevated installation UI, invoking the verified installed gate, actual
shortcut destination/update handling and final integration acceptance. This
component alone does not qualify the outer bootstrap or remove the legacy build
refusal. Startup stability and FORM_INTERACTIVE targets remain incomplete.
