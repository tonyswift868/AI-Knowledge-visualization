"""Read-only structural gate for the 0036 ledger with exact 0041 closure access.

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


def _closure_policy_matches(policy, command: str) -> bool:
    return (policy["cmd"] == command and policy["permissive"] == "PERMISSIVE"
            and list(policy["roles"]) == ["mentorflow_closure"]
            and _expression(policy["qual"]) == "true" and policy["with_check"] is None)


def _closure_access_matches(connection: Connection, table: str) -> bool:
    """0041's NOLOGIN owner has SELECT/DELETE only, without delegation."""
    role_safe = connection.execute(text("""
        SELECT NOT rolcanlogin AND NOT rolsuper AND NOT rolbypassrls
          AND NOT rolcreaterole AND NOT rolcreatedb AND NOT rolreplication AND NOT rolinherit
        FROM pg_roles WHERE rolname='mentorflow_closure'
    """)).scalar_one_or_none()
    if role_safe is not True or connection.execute(text(
        "SELECT pg_has_role('mentorflow_app','mentorflow_closure','MEMBER')"
    )).scalar_one():
        return False
    for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER", "MAINTAIN"):
        allowed = connection.execute(text(
            "SELECT has_table_privilege('mentorflow_closure', :table, :privilege)"
        ), {"table": "public." + table, "privilege": privilege}).scalar_one()
        if allowed != (privilege in {"SELECT", "DELETE"}):
            return False
    # Column grants can add UPDATE/INSERT without has_table_privilege noticing.
    # No canonical migration grants column privileges on these four tables.
    return not connection.execute(text("""
        SELECT EXISTS (SELECT 1 FROM pg_attribute a CROSS JOIN LATERAL aclexplode(a.attacl) p
          WHERE a.attrelid=to_regclass(:table) AND a.attnum>0 AND NOT a.attisdropped)
    """), {"table": "public." + table}).scalar_one()


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
    by_name = {p["policyname"]: p for p in policies}
    require(len(policies) == 3 and set(by_name) == {
        "tenant_isolation_account_import_ledger", "account_closure_read", "account_closure_delete"})
    for name, command in (("account_closure_read", "SELECT"), ("account_closure_delete", "DELETE")):
        require(_closure_policy_matches(by_name[name], command))
    policy = by_name["tenant_isolation_account_import_ledger"]
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
    require(_closure_access_matches(connection, TABLE))
    require(not connection.execute(text("""
        SELECT EXISTS (SELECT 1 FROM pg_class c CROSS JOIN LATERAL aclexplode(c.relacl) a
          WHERE c.oid='public.account_import_ledger'::regclass AND
            (a.grantee=0 OR pg_get_userbyid(a.grantee) NOT IN (:role, 'mentorflow_migrator', 'mentorflow_closure')
             OR (pg_get_userbyid(a.grantee) IN (:role, 'mentorflow_closure') AND a.is_grantable)))
    """), {"role": APPLICATION_DATABASE_ROLE}).scalar_one())
