# GOV-002 — derived web handoff with exact provenance

Status: ACCEPTED_BY_USER_DISPATCH; Date: 2026-09-12.
Authority: `MF-MAIN3-A-KNOWLEDGE-INTEGRATION-WEB-HANDOFF-20260912-02`.

Keep durable contracts, dynamic state and immutable evidence in their existing
authorities. Extend the standard-library compact generator with `--web` for one
committed, <=4096-byte entry; no second context engine or transcript archive.

A tracked file cannot contain its own final Git commit hash. The web handoff
therefore binds the exact latest integration **source snapshot**, then is
published in a commit changing only the derived file. `--check` verifies that
the snapshot is an ancestor and no other tracked path changed. A new source
commit or modified source invalidates the entry. The final publication HEAD is
read from the repository ref and the external immutable delivery receipt.

This makes provenance reproducible without amending hashes in a loop. The
generated timestamp comes from the source commit's committer timestamp (UTC),
not a claimed wall-clock observation of workers or remote services. Detailed
read order, authority priority and legacy paths: [web contract](../handoff/README.md).
