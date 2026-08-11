# Qwen3-TTS wheel license supplement contract

Status: **implemented and tested for the locked 48-package 12Hz-only closure; the overall runtime release remains blocked by unrelated gates**.

The runtime builder prefers dependency licence evidence whose SHA256 is present
in the pinned wheel itself. That remains the default. The current immutable
48-package generation retains every occurrence and validates exactly 103
in-wheel licence/notice files plus one external supplement, for 104 evidence
occurrences in total. Repeated equal hashes are not deduplicated.

The one supplement belongs to `tokenizers==0.22.2`: its wheel has no accepted
licence member. `flatbuffers` is not part of the locked 48-package closure and
is explicitly excluded; the older two-package proposal is obsolete.

The materialized evidence uses schema
`mentorflow.qwen-runtime-license-evidence.v1`. The tokenizers supplement receipt
is separately pinned, binds the release licence bytes to the exact wheel and
METADATA, and is consumed offline. This implementation does not make the full
voice release GO: runner redistribution evidence, production trust roots,
real-model service E2E, and final voice rights/listening QA remain separate
fail-closed gates.

## Minimal accepted form

For a dependency with no licence member in its wheel, the component may declare
exactly one `license_supplement`. It is mutually exclusive with treating a file
as an in-wheel licence and has schema
`mentorflow.python-license-supplement-receipt.v1`.

The builder must verify all of these bindings:

1. `package.name`, `package.version`, canonical PyPI purl, wheel filename,
   wheel SHA256, and wheel `METADATA` SHA256 exactly match the dependency lock
   and inspected wheel.
2. The wheel has no in-wheel licence candidate. If it has one, the supplement
   route is rejected and normal in-wheel evidence is required.
3. `pypi_metadata_binding.repository` occurs verbatim as a Source/Homepage
   `Project-URL` inside that exact hashed wheel `METADATA`; a generic GitHub host
   match is insufficient.
4. `upstream_release.repository` equals that bound repository. The tag is
   explicitly pinned, the resolved commit is exactly 40 lowercase hex
   characters, and the tag-to-commit receipt URL is exactly under
   `https://api.github.com/repos/<bound-owner>/<bound-repo>/commits/<tag>`.
5. The licence source URL is immutable and exactly
   `https://raw.githubusercontent.com/<bound-owner>/<bound-repo>/<40-hex-commit>/<safe-relative-path>`.
   Redirects, alternate hosts, branches, abbreviated commits, query strings,
   fragments, path traversal, and mutable `main`/`master` links are rejected.
6. The saved licence bytes are non-empty and bounded to 1 MiB, and their exact
   byte length and SHA256 match the receipt and build-spec pin.
7. The receipt itself is a separately SHA256-pinned build input. Its accepted
   keys are exact; unknown or missing keys fail. The package/version/tag mapping
   must be explicitly approved in the build spec rather than inferred by
   stripping or rewriting version text.
8. The resolved SPDX expression remains mandatory. The supplement bytes,
   repository, tag, commit, URL, and receipt are copied into the notices index
   and SBOM provenance; they never masquerade as wheel members.

No network access is needed during the final pack build. The fetch/audit phase
creates the immutable inputs; the offline builder rechecks every binding and
hash. A missing, extra, mutable, mismatched, or inapplicable supplement still
fails closed rather than falling back to an inferred licence.
