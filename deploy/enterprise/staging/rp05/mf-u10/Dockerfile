FROM mentorflow-mf-u04-backend:MF-U04
COPY --chown=10001:10001 overlay/backend/app/enterprise/identity/native.py /app/backend/app/enterprise/identity/native.py
COPY --chown=10001:10001 overlay/backend/app/enterprise_tenancy/structured_sync.py /app/backend/app/enterprise_tenancy/structured_sync.py
COPY --chown=10001:10001 overlay/backend/app/enterprise/catalog_api.py /app/backend/app/enterprise/catalog_api.py
COPY --chown=10001:10001 overlay/backend/app/account_core_mvp.py /app/backend/app/account_core_mvp.py
COPY --chown=10001:10001 overlay/backend/app/enterprise/structured_sync_api.py /app/backend/app/enterprise/structured_sync_api.py
COPY --chown=10001:10001 overlay/backend/app/enterprise_tenancy/schema.py /app/backend/app/enterprise_tenancy/schema.py
COPY --chown=10001:10001 overlay/backend/app/enterprise_tenancy/sync_schema.py /app/backend/app/enterprise_tenancy/sync_schema.py
COPY --chown=10001:10001 overlay/backend/alembic/versions/0042_structured_sync_changes.py /app/backend/alembic/versions/0042_structured_sync_changes.py
