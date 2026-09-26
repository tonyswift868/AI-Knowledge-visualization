# Immutable update payload and runtime path budget

Committed App version payload is immutable. The updater never overwrites,
appends, truncates, patches, chmods, changes ACLs/attributes/timestamps, or writes
logs into a shared payload file. Changed/new files always use independent
temporary objects, verified bytes, close, and atomic materialization. A changed
package manifest is a changed file, not an unchanged hardlink candidate.

`Config`, runtime state, receipts, pending files, logs, cache, temp, user data,
and UAT configuration are excluded from hardlink reuse. These names are not
permission to include user data in a release: signed manifest validation still
rejects user/control namespaces. Control files and the full release envelope
are outside the target App tree.

`AcceptedApp` is a live content lease, not a boolean or an mtime cache. It checks
the accepted manifest pin and all source content, retains deny-data-write file
handles, and verifies the source file object identity before each link. The
production factory additionally requires the existing protected receipt and
protected source entries; it runs lazily in the download worker, not during Qt
startup. Unsupported/cross-volume/denied hardlink operations use independent
copy. Reparse/identity/collision/unknown failures fail closed.

Directory object handles retain identity. Each named parent is reopened with
no-follow semantics and checked against its retained object and final path.
This is a constant number of native calls, not repeated Python full ancestor
walks/resolves per file. Leaf type/reparse/link checks remain fresh. The final
target file set and every target SHA-256 are always checked, including linked
files. Recognized links must bind to the accepted source and have two names.
Unrecognized links/lineage are rejected, never guessed from a filename.

Runtime staging is `<install-root>/.u/<12-hex>/App`. The complete signed envelope
binds the shortened identity; collision or altered control metadata is refused.
`runtime_path_budget.py` admits at most 240 UTF-16 code units for the longest
runtime payload path including the staging suffix. The actual 145-unit payload
therefore permits a 74-unit install root. Installer directory/preflight and
updater admission reject deeper paths with a user-readable Chinese message.
The limit is recomputed from each release; longPathAware is defense-in-depth,
not permission to depend on LongPathsEnabled or alter machine policy.

This round does not qualify protected .u provisioning, cross-process pending
link-lineage recovery, UAC apply, ACL changes on linked files, restart, or health
rollback. The elevated apply bridge must not mutate shared objects. Unknown
or expired source leases fail closed until reacquired through a qualified
ownership/recovery flow. No production feed or signing authority is activated.
