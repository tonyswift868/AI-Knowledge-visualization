"""Read-only structural gate for the recovered, migration-owned 0036 ledger.

This contract follows the retained staging migration, never creates or repairs
tables, and must be checked in addition to the exact known Alembic head.
"""
from __future__ import annotations

import re
from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection

from .schema import APPLICATION_DATABASE_ROLE, rls_predicate_for

TABLE = "account_import_ledger"
STRING_COLUMNS = {
    "import_id": 64, "issuer": 512, "subject_id": 128,
    "organization_id": 96, "workspace_id": 96, "idempotency_key": 128,
    "source_digest": 64, "request_digest": 64, "format_version": 80, "state": 16,
}
UNIQUE_COLUMNS = {
    "uq_account_import_request": ["issuer", "subject_id", "workspace_id", "idempotency_key"],
    "uq_account_import_snapshot": ["issuer", "subject_id", "workspace_id", "source_digest"],
}


def _expression(value: str | None) -> str:
    # pg_get_expr adds harmless text casts/parentheses. Keep literal case and
    # operators so a changed scope, OR true, or a different state stays rejected.
    parts = re.split(r"('(?:''|[^'])*')", value or "")
    return "".join(part if index % 2 else re.sub(
        r'[\s()"]', "", re.sub(r"::(?:character varying|text)(?:\[\])?", "", part)
    ) for index, part in enumerate(parts))


def require_account_import_ledger_schema(connection: Connection) -> None:
    """Raise a bounded error on schema, ownership, grants or RLS drift."""
    def require(condition: bool) -> None:
        if not condition:
            raise ValueError("account import ledger schema does not match migration 0036")

    require(connection.dialect.name == "postgresql")
    metadata = inspect(connection)
    require(metadata.has_table(TABLE, schema="public"))
    columns = {c["name"]: c for c in metadata.get_columns(TABLE, schema="public")}
    require(set(columns) == set(STRING_COLUMNS) | {"snapshot_json", "summary_json", "created_at", "updated_at"})
    for name, column in columns.items():
        require(not column["nullable"])
        kind = str(column["type"])
        if name in STRING_COLUMNS:
            require(kind == f"VARCHAR({STRING_COLUMNS[name]})")
        elif name in {"snapshot_json", "summary_json"}:
            require(kind == "JSONB")
        else:
            require(kind == "TIMESTAMP" and bool(column["type"].timezone))
        require(column.get("default") == ("CURRENT_TIMESTAMP" if name in {"created_at", "updated_at"} else None))
    require(metadata.get_pk_constraint(TABLE, schema="public")["constrained_columns"] == ["import_id"])
    unique = {c["name"]: c["column_names"] for c in metadata.get_unique_constraints(TABLE, schema="public")}
    require(unique == UNIQUE_COLUMNS)
    foreign = metadata.get_foreign_keys(TABLE, schema="public")
    require(len(foreign) == 1)
    fk = foreign[0]
    require(fk["constrained_columns"] == ["organization_id", "workspace_id"]
            and fk["referred_table"] == "workspaces"
            and fk["referred_schema"] in (None, "public")
            and fk["referred_columns"] == ["organization_id", "id"]
            and not fk.get("options"))
    checks = metadata.get_check_constraints(TABLE, schema="public")
    require(len(checks) == 1 and checks[0]["name"] == "ck_account_import_state"
            and _expression(checks[0]["sqltext"]) == _expression(
                "state = ANY (ARRAY['PREVIEWED','PLANNED','COMPLETED'])"))
    table = connection.execute(text("""
        SELECT pg_get_userbyid(relowner), relrowsecurity, relforcerowsecurity
        FROM pg_class WHERE oid='public.account_import_ledger'::regclass
    """)).one()
    require(tuple(table) == ("mentorflow_migrator", True, True))
    require(connection.execute(text("""
        SELECT count(*) = 5 AND bool_and(convalidated)
        FROM pg_constraint WHERE conrelid='public.account_import_ledger'::regclass
    """)).scalar_one())
    indexes = connection.execute(text("""
        SELECT count(*) = 3 AND bool_and(indisunique AND indisvalid AND indisready
            AND indpred IS NULL AND indexprs IS NULL)
        FROM pg_index WHERE indrelid='public.account_import_ledger'::regclass
    """)).scalar_one()
    require(indexes)
    policies = connection.execute(text("""
        SELECT policyname, permissive, roles, cmd, qual, with_check FROM pg_policies
        WHERE schemaname='public' AND tablename='account_import_ledger'
    """)).mappings().all()
    require(len(policies) == 1)
    policy = policies[0]
    predicate = _expression(rls_predicate_for(TABLE))
    require(policy["policyname"] == "tenant_isolation_account_import_ledger"
            and policy["permissive"] == "PERMISSIVE" and list(policy["roles"]) == ["public"]
            and policy["cmd"] == "ALL"
            and _expression(policy["qual"]) == predicate
            and _expression(policy["with_check"]) == predicate)
    for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER", "MAINTAIN"):
        allowed = connection.execute(text(
            "SELECT has_table_privilege(:role, 'public.account_import_ledger', :privilege)"
        ), {"role": APPLICATION_DATABASE_ROLE, "privilege": privilege}).scalar_one()
        require(allowed == (privilege in {"SELECT", "INSERT", "UPDATE"}))
    require(not connection.execute(text("""
        SELECT EXISTS (SELECT 1 FROM pg_class c CROSS JOIN LATERAL aclexplode(c.relacl) a
          WHERE c.oid='public.account_import_ledger'::regclass AND
            (a.grantee=0 OR pg_get_userbyid(a.grantee) NOT IN (:role, 'mentorflow_migrator')
             OR (pg_get_userbyid(a.grantee)=:role AND a.is_grantable)))
    """), {"role": APPLICATION_DATABASE_ROLE}).scalar_one())
