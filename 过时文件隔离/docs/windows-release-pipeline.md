# Windows release publisher

`scripts/build_release.ps1` turns an already packaged MentorFlow directory into
the existing signed file-level updater feed. It does not build the application,
launch it, upload anything, call GitHub, create a GitHub Release, deploy a server,
change a VPS, or alter the supplied package. No new binary-diff algorithm is used.

## One command

Use an existing Python environment containing the project's `cryptography`
dependency. The wrapper does not install dependencies. Run from the repository:

```powershell
.\scripts\build_release.ps1 -Version 1.1.1 -Channel stable `
  -TargetDirectory E:\Packages\MentorFlow-1.1.1 `
  -ReleaseNotes E:\Packages\1.1.1-notes.txt `
  -OutputDirectory E:\Releases\MentorFlow\release `
  -Origin https://updates.example.com `
  -SourceSha <exact-40-character-packaged-source-commit> `
  -TreeSha <exact-40-character-packaged-source-tree> `
  -SigningKey E:\ReleaseSecrets\mentorflow-ed25519.pem -KeyId release-2026
```

Replace the example origin and provenance placeholders with the actual packaged
build values, not the publisher checkout's current branch tip. The package's
`mentorflow-version.json` must already declare the requested version. The
publisher refuses a mismatch rather than relabeling old binaries. It regenerates
the package manifest from bytes without rewriting the input package. File access
rejects symlinks/junctions, invalid Windows paths, case collisions, user-data
namespaces, and obvious private-key/source/cache material.

No JSON editing is needed. `-Channel beta` supports SemVer prereleases;
stable rejects them. Notes are UTF-8 plain text, maximum 64 KiB. Optional switches:

- `-BaseDirectory <packaged-old-version>` explicitly seeds the first incremental
  release. Otherwise the last verified signed channel release supplies its base
  manifest automatically. With no previous release/base, the first publication
  offers the full file set; subsequent publications use file-level increments.
- `-MinSupportedVersion` and `-MinUpdaterVersion` default to `1.0.0`.
- `-MetadataVersion` can explicitly seed a migrated feed; otherwise the next
  per-channel epoch is automatic. It must exceed the saved high-water mark.
- `-ExpiryDays` defaults to 7 (range 1–31); publish before metadata expires.
- `-Mandatory -MandatoryReason security|protocol|data_compatibility` declares a
  genuine mandatory update. Ordinary feature pressure is not an accepted reason.
- `-RolloutPercentage` defaults to 100; `-RolloutSeed` is stable by default.
- `-PythonPath` selects an already-provisioned interpreter.

`MENTORFLOW_RELEASE_SIGNING_KEY` and `MENTORFLOW_RELEASE_KEY_ID` can supply the
key path and ID to the PowerShell wrapper. For encrypted PKCS8 PEM, pass
`-KeyPasswordEnv <name>`; only the name is a command argument, never the password.
The key must remain outside the repository, package, and output. The builder
does not generate or publish production private keys. Key rotation needs a
separately reviewed trust transition; an unknown prior signing key fails closed.

## Static output

```text
release/
  stable.json                       signed channel index (published last)
  beta.json                         present after a beta publication
  objects/<sha256>                   immutable raw file content, deduplicated
  <version>/<channel>/
    release.json                    immutable signed metadata envelope
    file-manifest.json              canonical exact target inventory
    full-assets.json                complete signed-in-envelope asset inventory
    incremental-assets.json         changed/new files and deleted-path report
    SHA256SUMS                      metadata, notes, receipt, and object checksums
    release-notes.txt
    build-receipt.json              operational provenance/signing status
    build-receipt.sig               publisher receipt signature for safe retry
  .release-state/<channel>.json      signed publisher high-water witness
```

The channel index is the release envelope itself, not an unsigned pointer.
Its Ed25519 signature binds app ID, channel, SemVer, publication/issue/expiry,
metadata epoch, minimum versions, mandatory policy, notes, source/tree identities,
manifest, all hashes, incremental paths, and the full fallback inventory. Detached
inventory/checksum/receipt files are for operations; clients trust only the
verified envelope and their build-provisioned public keys.

Unchanged files are reused through the **existing** updater. Changed/new files
download by hash; deleted files are absent from the reconstructed target. Missing
or unsupported base selects the full file set, not Setup.exe. Both paths use the
same existing transaction and content verification. Identical object hashes may
make actual full-download bytes smaller than the logical full payload size.

Keep the entire object store and immutable version directories when publishing a
new version. Copy objects and version directories first, and replace channel
indexes last. Static production hosting must use the configured HTTPS origin,
correct content lengths, identity encoding, and no arbitrary-origin redirects.
Support Range/206 when possible; the existing client also accepts correct full
200 responses. Serve channel indexes with `Cache-Control: no-cache,
must-revalidate`; version paths/objects may be immutable-cached. Deny public
access to dot-directories and lock/build scratch paths. This task supplies files
and instructions only; it does not perform deployment or server configuration.

## Signing boundaries

There are two separate signatures:

1. **Feed authenticity:** external Ed25519 signing key; its public key must
   already be compiled/provisioned into the desktop and privileged updater.
   A public key downloaded with a feed is never a trust root. This publisher does
   not change client trust configuration; the current source's production helper
   trust map is unprovisioned. Product builds must provision it independently.
2. **Windows Authenticode:** optional `-CertificateThumbprint <thumbprint>
   -TimestampServer <https-url>` invokes `scripts/sign_release.ps1` on a private
   staging copy **before** hashing. The hook requires an existing code-signing
   certificate with its private key in CurrentUser/My or LocalMachine/My, a valid
   system trust chain, SHA-256 signatures, and verified timestamps. It signs all
   `.exe`, `.dll`, and `.pyd` files in that copy and records per-file evidence.

Without that real certificate hook, the receipt says exactly
`PRODUCTION_SIGNING = NOT_CONFIGURED`. A metadata signature is not a claim of
trusted Windows signing. An explicitly requested but unavailable/invalid
certificate aborts publication. No self-signed certificate is generated or
accepted as a substitute. A real-certificate success path remains an operator
qualification when credentials are provisioned; current automated tests prove
the missing-certificate path fails closed, not production trust.

## Repeatability, interruption, and freshness

With the same persistent external key and inputs, repeating a
build verifies the existing objects and returns `REUSED` without changing the
signature, metadata epoch, or index bytes. Different content/policy under an
existing version is refused; publish a new version. The separately authenticated
build receipt binds the original package and signing options. An Authenticode
retry reuses the verified signed output without running the hook or generating
another timestamp. The source package itself is always untouched.

The publisher writes objects and the immutable version before the signed
high-water witness and atomic channel replacement. A retry can finish a failure
between those last steps. `.release-build.lock` prevents cooperating concurrent
writers; a crash can leave the lock. Confirm the exact owning PID is stopped
before manually removing that single lock. Never delete a live writer's lock.
Keep `.release-state` with publisher backups and do not roll the whole publisher
state backward: network anti-replay cannot protect a fully rolled-back publisher
or client filesystem. Lower version/epoch and signed same-epoch equivocation are
refused. Expired metadata is never made valid by retry; publish a newer version.

## Local qualification (no production credentials)

`-DevelopmentSigning` explicitly creates a memory-only ephemeral Ed25519 key and
labels the receipt `METADATA_SIGNING=DEVELOPMENT_ONLY`. It permits HTTP only on
literal loopback and writes an explicitly untrusted development **public** key
file. It never persists the private key or provisions production trust. Use a
fresh output tree per standalone development invocation; its ephemeral key
cannot sign later releases or repeat a prior process's publication.

Create a small, visibly synthetic demo and publish it with the real one-command
entry point (requires pytest only because it reuses the test package generator):

```powershell
python -B -m scripts.make_release_demo --root E:\Codex\Temp\MF-release-demo
python -m http.server 8766 --bind 127.0.0.1 --directory E:\Codex\Temp\MF-release-demo\release
```

That directory is directly usable by a static HTTP server, without an asset-map
API or access back to the original package. The demo includes source fixtures and
an explicit untrusted public key. The existing updater's constructor-only
`HTTPTransport(..., fixture_http=True)` and injected public key are for this local
qualification, not a production user setting. Stop the local server when done.

Run focused verification:

```powershell
python -B -m pytest -q scripts/test_build_release.py -p no:cacheprovider `
  --basetemp=E:\Codex\Temp\MF-release-pipeline-unique-test-run
```

Tests build the actual output tree, use real loopback HTTP and the existing
`MatureUpdateService`, and cover BASE→TARGET incremental/full reconstruction and
the existing apply transaction, corruption, signature tampering, expiry,
downgrade, stale-cache replay after restart, beta, automatic bases/epochs,
immutable retry, failed publication recovery, the PowerShell command, and
unavailable production signing. Apply tests use synthetic files and isolated
ACL/registry/shortcut adapters: they are not a real product launch, production
certificate, or UAC qualification.
