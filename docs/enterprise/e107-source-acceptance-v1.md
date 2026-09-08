# Accepted-current identity and authoritative target verification

Dispatch: MF-E107-UPDATER-SOURCE-ACCEPTANCE-CLOSE-20260907-08.
Parent: bc1f37bd0e6bf382ca6c1d509cfe7822a9f1e701.

The original constructor was profiled before changing its checks. On a fresh,
independently copied 2,113,954,984-byte App, the first observation took 121,491 ms.
A second fresh-copy native-instrumented observation took 132,676 ms: native
CreateFileW opens accounted for 117,306 ms, while file stream open/read/hash
accounted for 7,159 ms. The warm repeated original constructor took 8,743 ms.
Thus cold serial read-data-handle acquisition is the primary measured cost;
duplicate content hashing is secondary. This does not identify which filesystem
filter causes the native latency and is not evidence that Defender was disabled.
No Defender settings or exclusions were changed.

After the bounded native-open change, a third independently copied App's first
successful acceptance took 13,581 ms (12,483 ms metadata/handle acquisition,
736 ms unique-directory security, 327 ms lexical paths). Only the 2,251,418-byte
manifest was hashed. One earlier admission attempt while that copy was still
incomplete correctly failed at missing-directory validation, before payload
handles or hashes; no payload was prewarmed by that rejected attempt.

The source manifest hash remains pinned. Production acceptance reads the existing
admin-owned installation-receipt.json, validates its accepted schema/version,
manifest pin, expected normalized root and root/App object identities. FileIdInfo
returns the full volume serial and 128-bit FileId used by Python 3.13 receipts;
BY_HANDLE_FILE_INFORMATION's DWORD volume serial must not be compared to that
64-bit receipt value. The in-memory lease adds the fixed MentorFlow app identity
and immutable/protected contract; it creates no alternative persistent receipt.

Manifest paths are lexically validated once, unique directories are pinned, and
every source file receives a no-follow native metadata/final-path check. Bounded
16-worker read-data-handle acquisition retains the exact deny-write semantics of
the serial implementation. All worker results are drained, and all acquired
handles closed on failure. This is local I/O concurrency, not delegated agents.
Per-object production ACL checks remain fresh; trusting only a parent ACL would
miss an explicitly writable child. This round does not establish protected ACLs
in UAT or measure an elevated/protected installation.

Source acceptance hashes only the pinned manifest, not the 2 GB payload. The
reconstructed target still receives a complete file-set, size and SHA-256 check.
A corrupted same-size unchanged source can be accepted for reconstruction but
must fail final target SHA verification after hardlinking. It cannot become READY
or commit. The service retains the source lease until close; read-data handles
prevent in-place payload writes while shared files are verified. No source ACL,
attribute, timestamp, or content is modified by this optimization. Protected
current App is not writable by the normal app; writable UAT roots are explicitly
not production permission qualification. Normal production preparation uses the
protected factory, never a freely supplied fixture receipt.

The same-volume NTFS decision is operation-wide and independent of manifest size.
Cross-volume reconstruction selects independent copy, creates its copying/*.part
under target staging, verifies it, and renames on that same target volume. The
packaged cross-volume test uses representative independently copied EXE/Python DLL
and version/manifest bytes, not a claim of full 2 GB cross-volume qualification.
The separate full same-volume run uses the real complete packaged payload.

All dispatch-08 measurements, frozen build logs and final decision receipt live
under D:\MFU8. The private UAT signing key remains scratch-only, never bundled or
committed. UAT entry points are selected by the dedicated UAT build; they cannot
enable production bypass through runtime configuration. No UAC, apply, installer
execution, account/server data, USN, signing, push, or product acceptance is part
of this round. Previous dispatch receipts remain immutable.
