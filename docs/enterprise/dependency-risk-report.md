# Dependency and license risk report

Status: **OFFLINE_VERIFIED_INVENTORY / STAGING_EXECUTION_PENDING**. This is not a legal audit and `LICENSE_COMPLIANT` is deliberately `UNKNOWN`.

The deterministic machine report at `reports/enterprise/machine/dependency-risk-report.json` is generated from the reviewed CycloneDX lockfile SBOM. It contains 439 Python/Node components, dependency scope, source lockfile, declared license values where present, maintenance status, and risk flags.

Current offline inventory summary:

| Signal | Count | Meaning |
|---|---:|---|
| Critical runtime dependencies | 48 | direct production dependencies requiring heightened review |
| Unknown license | 111 | no license was present in the lock evidence; nothing was guessed |
| Copyleft review candidates | 12 | string-based review candidates, not a compliance failure or legal conclusion |
| Deprecated declaration in Node lock | 0 | no package-lock `deprecated` marker observed; unsupported status for other packages remains unknown |
| Known vulnerabilities | unknown | OSV/npm/container scanners have not run locally |

CI uses mature scanners: dependency review on PR, OSV for Python/Node, npm audit for critical Node findings, Trivy for three images, and Dependabot update proposals. Any unwaived mature-scanner finding fails the gate. A waiver must identify one exact finding and include reason, owner, scope, and a non-expired date; wildcard/permanent waivers are rejected.

Machine vulnerability results remain `STAGING_EXECUTION_PENDING`; this report must be regenerated and linked from the `EnterpriseReleaseManifest` after CI scanning.
