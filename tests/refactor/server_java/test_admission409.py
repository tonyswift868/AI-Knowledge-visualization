"""Exact Python startup admission for the 0052 administrator read policy."""
import json

import pytest
from sqlalchemy import create_engine, text


TABLES = (
    "account_import_ledger", "structured_sync_entities", "structured_sync_requests",
    "rehydration_manifests", "account_daily_activity", "rag_documents", "rag_chunks",
    "native_session_families",
)


def test_admission409_records_migration_policy_rows_and_accepts_schema(closure147_backends):
    from refactor.server.enterprise_tenancy.sync_schema import require_structured_sync_schema

    engine = create_engine(closure147_backends["python_admin_url"])
    try:
        with engine.connect() as connection:
            rows = connection.execute(text("""
                SELECT tablename, policyname, permissive, roles, cmd, qual, with_check
                FROM pg_policies
                WHERE schemaname='public' AND tablename = ANY(:tables)
                ORDER BY tablename, policyname
            """), {"tables": list(TABLES)}).mappings().all()
            evidence = [dict(row) for row in rows]
            print("409 pg_policies evidence:", json.dumps(evidence, default=str, sort_keys=True))
            require_structured_sync_schema(connection)
            connection.exec_driver_sql(
                "CREATE POLICY unexpected_extra_policy409 ON public.structured_sync_entities "
                "FOR SELECT TO mentorflow_migrator USING (true)"
            )
            with pytest.raises(ValueError, match="structured sync schema differs"):
                require_structured_sync_schema(connection)
            # The connection context rolls this synthetic policy back.
    finally:
        engine.dispose()


@pytest.mark.parametrize("replacement", [
    "CREATE POLICY admin_account_rows_read ON public.structured_sync_entities FOR UPDATE TO mentorflow_migrator USING (true)",
    "CREATE POLICY admin_account_rows_read ON public.structured_sync_entities FOR SELECT TO public USING (true)",
    "CREATE POLICY admin_account_rows_read ON public.structured_sync_entities FOR SELECT TO mentorflow_migrator USING (false)",
    "CREATE POLICY admin_account_rows_read ON public.structured_sync_entities AS RESTRICTIVE FOR SELECT TO mentorflow_migrator USING (true)",
    "CREATE POLICY admin_account_rows_read ON public.structured_sync_entities FOR ALL TO mentorflow_migrator USING (true) WITH CHECK (false)",
    "CREATE POLICY unexpected_admin_rows_policy ON public.structured_sync_entities FOR SELECT TO mentorflow_migrator USING (true)",
    None,
])
def test_admission409_rejects_noncanonical_or_extra_policy(closure147_backends, replacement):
    from refactor.server.enterprise_tenancy.sync_schema import require_structured_sync_schema

    engine = create_engine(closure147_backends["python_admin_url"])
    try:
        with engine.connect() as connection:
            transaction = connection.begin()
            try:
                connection.exec_driver_sql(
                    "DROP POLICY admin_account_rows_read ON public.structured_sync_entities"
                )
                if replacement:
                    connection.exec_driver_sql(replacement)
                with pytest.raises(ValueError, match="structured sync schema differs"):
                    require_structured_sync_schema(connection)
            finally:
                transaction.rollback()
    finally:
        engine.dispose()
