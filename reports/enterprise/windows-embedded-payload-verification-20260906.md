# E107 authenticated embedded payload component — PARTIAL

Dispatch MF-E107-STARTUP-TRUST-BOUNDARY-20260906-01.
Task MENTORFLOW_WINDOWS_FAST_START_VERIFICATION_V1.
Base 5997a29d1794dbb8da21dec965369e20ee1ab77d.

VerifiedPayload.cs consumes only a resource embedded in its own executing assembly,
not a mutable external archive or caller-supplied stream. It first hashes the
entire resource against the supplied fixed identity, then validates ZIP metadata,
then creates a ProtectedStage and streams entries into protected CREATE_NEW files.
The future production bootstrap must compile its expected hash into its assembly;
the synthetic test host accepts a test pin on argv and is NOT a release entry.

Preflight rejects invalid/duplicate case-insensitive names, traversal/aliases,
file-as-parent conflicts, link/special-file attributes, nonempty directory entries,
more than 50,000 entries and total uncompressed size over 8 GiB. The archive itself
is also bounded at 8 GiB. Streaming enforces each declared uncompressed length.
Explicit empty directories are preserved. No extracted code executes and no
installation receipt is issued by this component.

Uses existing system .NET
[ZipArchiveEntry APIs](https://learn.microsoft.com/en-us/dotnet/api/system.io.compression.ziparchiveentry?view=netframework-4.8.1)
and SHA-256; no dependency installation or lock change. ProtectedStage's canonical
path validation is shared with archive preflight, not reimplemented.

Six focused tests PASS in 2.096 seconds: three native staging tests and three
embedded-payload tests. The latter compile small resource-bearing executables
with the existing Framework64 compiler, exercising seven archive scenarios:

- valid synthetic EXE/DLL-named data plus empty directory, with independent
  post-extraction ACL and content inspection;
- incorrect archive SHA-256 rejected before staging;
- traversal, case-colliding entries, file/directory conflict, Unix symlink and
  nonempty directory rejected before staging, each with the correct fixture hash.

All payload bytes are non-executable fixtures. The only executables run are the
small diagnostic hosts; no staged fixture is launched. Test resources/executables
are under E:/Codex/Temp/e107-embedded-payload-* and new protected test parents under
E:/MentorFlow-TrustTest-20260906. No production files/data/shortcuts changed.

Remaining: build-time release identity embedding, visible elevated installer UI,
invocation of the verified native install command, final destination/shortcut and
update handling, complete-chain long-path checks and integration acceptance.
Existing legacy installer refusal remains in place. This component does not
qualify the complete outer installer, startup <=3s stability or form <=8s target.
No new startup timing, final package build, push, server mutation or formal data
write. Earlier runtime tests and historical four WinError 1314 skips retain their
original status; none were relabeled as this component's acceptance.
