# Consumer install/update trust boundary

Default mode is `per_user`; a real, writable existing parent is accepted and
only its last `MentorFlow` child is created. No elevation or parent ownership
requirement is used. The preexisting protected transaction remains available
only as the explicitly selected per-machine build/mode.

The current Windows user can change their own binaries. The consumer updater
does not claim protection against that user. It does protect release-source and
download/cache authenticity using updater-code-provisioned Ed25519 keys, app ID,
version, channel, expiry, monotonic metadata epochs, target manifest and hashes.
No key or command is admitted from an installed receipt or update intent.

The existing `MatureUpdateService` / `ObjectCache` remains the only default update
check/download/reconstruction path. HTTP Range, resume, signed metadata, and
verified copy/hardlink infrastructure are retained. Consumer mode uses verified
independent copies; it does not acquire a protected-parent source lease.

`per_user_install` implements a fixed-path transaction:

1. Validate source signature and signed contents; lock this installation.
2. Copy signed files to an owned staging tree; verify the complete target again.
3. Close the current app and verify staged bytes again immediately before swap.
4. Retain the former App and its signed release under `previous`; activate target.
5. Start the real ordinary-token GUI and require matching release/nonce/PID and
   a visible responsive window via bounded local IPC.
6. On failed target health, stop only the owned child and restore/relaunch prior.

Journals/receipts are recovery state, not cryptographic roots. Historical signed
metadata may identify an already installed version after expiry, but can never
admit a new download. Apply rechecks current-time signature/freshness and complete
content. The signed high-water witness protects against network replay, not a
malicious local Windows profile rollback. Older versions remain for later cleanup.

No user data belongs in App/current/previous version trees. Per-machine behavior
continues to use the separate protected receipt, ACL and elevated helper model.

Build the isolated packaged proof with `scripts/build_per_user_delivery.py`; it
reuses an existing dependency Analysis and generates a distinct test-only signing
key and namespace. It does not install software or deploy a server. Never ship
the generated test private key. Local qualification feeds are loopback-only.

The normal `build_windows_installer.ps1` entry routes a signed `UserRuntime`
payload to the consumer builder by default (`-SignedRelease` required). It reads
literal public authority from frozen updater code, not from mutable launch
configuration. `-ProtectedInstall` remains an explicit per-machine selection.
