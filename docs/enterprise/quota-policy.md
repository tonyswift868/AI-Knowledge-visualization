# Enterprise quota policy

Quota governance defines `OrganizationBudget`, `WorkspaceBudget`, and
`UserBudget`. Each may contain daily and monthly limits, warning and hard-stop
thresholds, a model allowlist, and per-feature quotas.

## Evaluation order

Applicable policies are evaluated broad-to-narrow. A narrower workspace or
user allowance cannot override an organization hard stop. Any hard stop,
unknown required spend, currency mismatch, disallowed model, or exhausted
feature quota fails closed. Warnings do not block execution.

The service adapts every monetary limit to the existing `ExecutionBudgetGate`.
It does not calculate Provider cost and does not replace the Cost Engine.

## Cost views

- Learner: own cost only.
- Instructor: aggregate for an assigned cohort.
- Admin: current workspace.
- Organization Owner: organization aggregate.
- Auditor: read-only tenant aggregate.

Unknown cost remains unknown rather than being represented as zero. The
`OrganizationCostReadModel` is a read-only application seam derived from an
already-authorized organization view; it performs no independent calculation.
