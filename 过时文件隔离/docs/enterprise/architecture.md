# Enterprise architecture evidence

Status: **PARTIAL** (`STAGING_EXECUTION_PENDING` for integrated runtime evidence).

MentorFlow Enterprise is packaged as three independently tagged OCI images: a Python API backend, an Nginx static frontend, and an offline Docling document worker. The staging topology uses a one-shot migration service before the backend, then exposes only the frontend reverse proxy. Every E1-07 runtime declaration fixes `MENTORFLOW_PROVIDER_MODE=fake`; real provider calls are zero.

The enterprise identity and tenant boundary is the frozen upstream `enterprise-context.v1` bundle (`03e4f66505b6b98202a07a70a39925293876ecc7b7c72f617dbe3b76dcb8b628`, final commit `fa488048d2856d687854b50a23137a45bb020d7d`). E1-07 owns no Actor/Tenant contract path and does not redefine those types. E1-01 through E1-06 must be integrated before tenant, authorization, RLS, observability, backup, AI, and cost claims can be accepted.

Static release assets and offline tests are locally verifiable. Linux/Windows CI, Docker builds, scanners, k6, PostgreSQL RLS, staging deployment, and promotion are execution gates whose results remain `STAGING_EXECUTION_PENDING` until their leases and runners are available. The current machine truth is `LOCAL_WINDOWS_DOCKER = NOT_RUN`, `LOCAL_WINDOWS_COMPOSE = NOT_RUN`, `REAL_POSTGRES_RLS = NOT_RUN`, `REAL_K6 = NOT_RUN`, `REAL_CONTAINER = NOT_RUN`, and `REAL_LINUX_STAGING = NOT_RUN`.
