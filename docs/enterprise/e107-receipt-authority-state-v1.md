# Canonical installed authority and pre-seed state

Dispatch 13 follows `eb1b33ec1a7c0b634835cf777e58d46cda423be2`.

The earlier pre-seed gate called the installed-tree verifier before a BASE
existed. Its correct unknown-control error was mistaken for a corrupt receipt.
The canonical authority remains **`<install root>/installation-receipt.json`**.
There is no JSON scan, glob, alternate path, or receipt-looking-object fallback.

## Namespace and schema roles

| Role | Authority/location | Discriminator |
|---|---|---|
| Installed | Exact canonical file | `record_type=mentorflow-installed-receipt`, `schema_version=1`; exact legacy `binding.schema=mentorflow.admin-installation.v1` adapter |
| Transaction | Protected `.install-staging`, explicit attempt state | `mentorflow-update-transaction`; typed from/to transitions |
| Health | External per-attempt health directory | Existing signed UAT health schema; never a receipt |
| ACL bootstrap | `.uat-control/acl-bootstrap-result.json` | Exact immutable dispatch-10 legacy adapter exposed as `mentorflow-uat-acl-bootstrap`, schema 1 |
| Release | Signed feed envelope | Existing signed release schema; never a receipt |

The bootstrap file is not rewritten to add fields. Its exact historical shape,
root, successful high-token result, nonce/time and sentinel are checked, alongside
fresh OS owner/DACL validation. This proves only ACL readiness, never App health.

`inspect_root` loads the canonical installed file if present and delegates full
inventory/hash verification. Missing canonical authority plus the precisely
identified bootstrap sentinel-only App is `ACL_READY_NO_BASE`. Any unknown/real
App payload without the canonical file fails as inconsistent. Invalid canonical
JSON/type/schema/binding fails closed. Other metadata never becomes installed
authority, regardless of its fields. The state transition graph separately
defines UNINITIALIZED, ACL_READY_NO_BASE, BASE_INSTALLED_HEALTHY, UPDATE_PREPARED,
PENDING_APPLY, PENDING_HEALTH, TARGET_INSTALLED_HEALTHY and ROLLBACK_REQUIRED.
Transaction transitions must be explicitly supplied typed records; the pre-seed
detector does not infer active transactions by enumerating JSON files.

## Receipt compatibility

New receipts retain the existing binding, inventory, file-set identity, critical
hashes, full-verification flag/time and optional control hashes. They add only
an explicit schema/type and HEALTHY/immutable flags. App ID remains bound by the
validated MentorFlow manifest; version/root/volume/FileId are in the binding;
EXE SHA is in `critical_hashes`. The existing exact legacy receipt shape remains
readable, but a wrong explicit type is never accepted through that adapter.
The product GUI receipt checks and original authority path are unchanged.

The App inventory excludes the separately validated protected evidence namespace.
Every entry is still path- and ACL-checked. App file-set/hash validation and
uninstaller control hashing are not relaxed. Metadata cannot substitute for App
files or a canonical receipt. No formal installation is changed in this dispatch.

## Seed ownership and publication

The UAT native launcher pins the exact root FileId/volume, bootstrap hash and
embedded runtime ZIP. After explicit runas it verifies its actual HIGH token,
creates a protected one-shot claim, extracts its own sealed runtime to protected
staging, and runs only that runtime with a fixed `--seed` action and clean
environment. It does not execute a user-writable Python script/interpreter.
The runtime embeds exact source/root/package pins; a request cannot supply a
command, registry path, executable or alternate destination.

The runtime separately claims the one-shot seed, creates protected staging,
validates the independent source file set and copies only hash-accepted bytes
from no-follow, deny-write/delete source handles. It preserves the entire
original sentinel-only App in that protected attempt, renames the completed
App into place, and runs the existing full installation verifier. Only then
does that verifier fsync and atomically rename its receipt into the canonical
path. Failure before receipt publication retains failed owned payload and puts
the original sentinel App back; no recursive deletion is used. Bootstrap
evidence is neither moved nor rewritten.

The native host waits at most 120 seconds for consent and bounds the owned seed
runtime to 840 seconds. It captures result/exit data, does not retry UAC and never
launches the product as administrator. A successfully published BASE receipt is
not a claim of a completed real update or Qt startup; Qt is recorded NOT_RUN by
the seed runtime until a Medium coordinator separately observes it.

Historical bootstrap UAC, this one BASE seed UAC and the later update UAC are
separate counters. The seed harness does not start the update UAC. Its code and
payload are test-only and unsigned; they are not production release machinery.
