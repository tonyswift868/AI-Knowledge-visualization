ARG MF_MIGRATION_BASE=mentorflow-slim-backend:269-python-fallback
FROM ${MF_MIGRATION_BASE}

# The application image intentionally excludes maintenance-only Alembic.
# Reuse its pinned runtime dependencies and add the already documented
# migration tool only to this disposable, locally tagged helper image.
RUN python -m pip install --no-cache-dir alembic==1.18.5
