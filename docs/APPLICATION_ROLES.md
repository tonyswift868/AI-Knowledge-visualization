# MentorFlow application roles

Backend owns the product role returned in `/api/enterprise/auth/me.role`.
Every account defaults to `LEARNER`, including first-use and newly registered
accounts. Desktop may display this value but cannot grant privileges.

An operator can provision at most 32 canonical account IDs in the Backend-only
`MENTORFLOW_APPLICATION_SUPER_ADMIN_ACCOUNT_IDS` environment variable (a JSON
array; default `[]`). Those human accounts receive `SUPER_ADMIN`. Obtain the
canonical ID from the authenticated account identity contract; never match a
username, email, client role assertion, or Keycloak role claim. Invalid policy
configuration prevents startup. Configuration changes require a Backend restart.

`GET /api/enterprise/auth/admin/check` is the minimal administrator-only endpoint.
It freshly revalidates the session and active organization/workspace membership,
then checks the product role. An authenticated learner receives 403; a valid
administrator receives 200 with `authorized: true`. Explicit client product-role
headers/query parameters are rejected, including for administrators. Ordinary
tenant roles and RLS membership rules remain unchanged.

This is a minimal provisioned role model, not an administration UI or a role
editing API. It never grants Keycloak realm/server administration or PostgreSQL
SUPERUSER/BYPASSRLS. Backend continues to connect as the restricted application
database role. A preprovisioned account marked email-verified does not establish
that the ordinary registration/email verification flow has passed acceptance.
