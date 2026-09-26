# Enterprise deployment topology

Status: **CI_CONFIGURED**; container execution is `STAGING_EXECUTION_PENDING` and `REAL_CONTAINER = NOT_RUN`.

| Service/image | Docker target | Network exposure | Persistent state | Runtime boundary |
|---|---|---|---|---|
| Migration | `backend-image` | none | `mentorflow-data` | one-shot schema upgrade, read-only root |
| Backend | `backend-image` | internal `8000` | `mentorflow-data` | UID/GID 10001, dropped capabilities, provider=fake |
| Frontend | `frontend-image` | loopback `8080` | none | UID 101, read-only root, `/api/` proxy only |
| Document worker | `document-worker-image` | none | data + read-only staged config | opt-in Compose profile, offline worker guards |

The three candidates use the exact Git commit as the tag. Three earlier immutable images are retained before deployment. TLS termination, registry policy, Kubernetes/orchestrator hardening, document-worker config delivery, real database topology, and runtime network policy are staging responsibilities and are not verified here.
