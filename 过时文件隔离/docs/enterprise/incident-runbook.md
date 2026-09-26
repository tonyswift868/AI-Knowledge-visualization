# Enterprise incident runbook

Status: **PARTIAL**; routing/contact integration is `PENDING_OWNER_EVIDENCE`.

1. Declare severity, timestamp, affected tenants/routes, and incident owner; preserve evidence without secrets.
2. Stop promotion. Record exact commit, three candidate image refs, migration heads, contract hash, and release receipt.
3. Contain: revoke exposed credentials, disable affected ingress/job paths, or invoke the retained-image rollback. Never replace a commit tag.
4. Verify rollback with health and k6 smoke; verify foreign-tenant successes remain zero and real provider calls remain zero.
5. For suspected tenant exposure, isolate logs and database evidence, determine tenant/time scope, and follow the approved notification process.
6. For dependency or image findings, quarantine the affected digest, regenerate SBOM/scans under a granted lease, and rebuild from reviewed lockfiles.
7. Recover with a reviewed immutable candidate, migration gate, health, smoke, promotion, and post-release monitoring.
8. Close only after timeline, root cause, impact, evidence links, corrective actions, owners, and deadlines are recorded.

Escalation contacts, pager routing, legal/privacy notification windows, and production access procedures are environment-owned and remain `PENDING_OWNER_EVIDENCE`.
