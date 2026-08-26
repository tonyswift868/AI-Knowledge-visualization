# Enterprise authorization matrix

MentorFlow Enterprise authorization uses the frozen `enterprise-context.v1`
`ActorContext`, `TenantContext`, `ResourceScope`, and `AccessDecision` contracts.
Governance-specific role and relationship fields are adapters; they are not a
second shared identity or tenancy contract.

## Default policy

The default is **DENY**. A request is allowed only after tenant, workspace,
role, relationship, resource classification, and optional break-glass checks
all succeed. Every returned decision must first be appended to the audit log;
an audit append failure fails closed.

## Vocabulary

The machine-readable `ResourceType` vocabulary covers Organization,
Membership, Workspace, Document, KnowledgeMap, QuestionBank, Plan,
TeacherSession, ReviewCampaign, StudentProgress, ProviderConfig, Cost, Audit,
and Export. Actions are READ, READ_METADATA, CREATE, UPDATE, DELETE, MANAGE,
EXPORT, and EXECUTE.

## Role summary

| Role | Primary scope | Explicit restrictions |
| --- | --- | --- |
| ORGANIZATION_OWNER | Organization-wide administration, content, cost, audit, and provider governance | Still subject to tenant/resource scope and audit checks |
| ADMIN | Membership/workspace administration and governed operations | Private Student Model access requires an explicit permission |
| CONTENT_MANAGER | Document, KnowledgeMap, QuestionBank, Plan, and ReviewCampaign management | No private learning-history access by default |
| INSTRUCTOR | Assigned learner or cohort teaching, review, progress, and cohort cost | A target learner must belong to the assigned cohort or be assigned directly |
| LEARNER | Own sessions, reviews, progress, export, and cost | Other learners' resources are denied |
| AUDITOR | Audit and cost read-only, tenant metadata | No mutation; membership/workspace content is metadata-only |

`ROLE_MATRIX` is immutable and machine tested. UI navigation derived from it is
only a presentation hint; backend authorization remains authoritative.

The Admin UI projection always returns the same eight categories—Organization
Admin, Workspace Admin, Content, Instructor, Learner, Audit, Cost, and Provider
Settings—with `VISIBLE`, `DISABLED`, or `HIDDEN`. These states are candidate
presentation data. They do not authorize an API call or bypass relationship
checks.

## Enforcement runtime

`GovernanceEnforcementService` is the application write gate. It resolves a
canonical actor and tenant plus a domain adapter operation, appends the
authorization audit decision, and only then invokes the mutation. A denied
operation returns an `EnforcementReceipt` with `mutation_performed=false`; the
business callback is never called.

`ResourceVisibilityFilter` applies the same policy to list, search, detail, and
export candidates after repository retrieval. It is an application-layer
overfetch defense and does not replace PostgreSQL RLS. Homogeneous batches are
evaluated through one bulk policy pass and one aggregate audit append while
preserving a decision for every resource. Offline fixtures cover 100, 1,000,
and 10,000 candidates.

## Relationship and IDOR rules

- Actor, tenant, and resource organization/workspace identifiers must agree.
- Instructor access to a learner through a cohort requires both an assigned
  cohort and evidence that the learner belongs to it.
- Organization and workspace mutations use the same pre-mutation enforcement
  gate. Workspace role bindings are stored per workspace, so one member may be
  an Instructor in one workspace and a Content Manager in another.
- Personal Provider configuration is owner-only unless a specific permission is
  present.
- Resource identifiers are authorized before data is returned; guessing an ID
  never widens scope.

## Break glass

Break glass is disabled by default. The application workflow is request,
separate-person approval, requester activation, then expiry or revocation. A
grant has a short TTL and exact actions, resource types, resource identifiers,
and one canonical `ResourceScope`. Self-approval is denied and audited.
Revocation is registered in the authorization service, so a previously issued
grant cannot be replayed. Every lifecycle transition and use is audited. Break
glass is not a universal administrator bypass.
