from __future__ import annotations

import importlib.util
from pathlib import Path
import re
from types import ModuleType

import pytest


BACKEND_ROOT = Path(__file__).resolve().parents[1]
MIGRATION_PATH = (
    BACKEND_ROOT / "alembic" / "versions" / "0034_enterprise_asset_manifests.py"
)


class _RoleResult:
    def __init__(self, role: tuple[bool, ...] | None) -> None:
        self._role = role

    def one_or_none(self) -> tuple[bool, ...] | None:
        return self._role


class _RoleBind:
    def __init__(self, role: tuple[bool, ...] | None) -> None:
        self._role = role
        self.statement = ""
        self.parameters: dict[str, str] = {}

    def execute(self, statement, parameters):
        self.statement = str(statement)
        self.parameters = dict(parameters)
        return _RoleResult(self._role)


def _migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "enterprise_asset_manifest_migration_0034",
        MIGRATION_PATH,
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_safe_application_role_contract_passes_read_only_check() -> None:
    migration = _migration()
    bind = _RoleBind((False, False, False, False, False))

    migration._assert_application_role_security(bind)

    assert bind.parameters == {"role": "mentorflow_app"}
    assert all(
        attribute in bind.statement
        for attribute in (
            "rolsuper",
            "rolcreatedb",
            "rolcreaterole",
            "rolreplication",
            "rolbypassrls",
        )
    )
    assert "pg_roles" in bind.statement


@pytest.mark.parametrize(
    ("role", "unsafe_attribute"),
    (
        ((True, False, False, False, False), "rolsuper"),
        ((False, True, False, False, False), "rolcreatedb"),
        ((False, False, True, False, False), "rolcreaterole"),
        ((False, False, False, True, False), "rolreplication"),
        ((False, False, False, False, True), "rolbypassrls"),
    ),
)
def test_unsafe_application_role_contract_fails_closed(
    role: tuple[bool, ...],
    unsafe_attribute: str,
) -> None:
    migration = _migration()

    with pytest.raises(
        RuntimeError,
        match=(
            "ENTERPRISE_APP_ROLE_SECURITY_CONTRACT_VIOLATION.*"
            f"{unsafe_attribute}"
        ),
    ):
        migration._assert_application_role_security(_RoleBind(role))


def test_missing_application_role_contract_fails_closed() -> None:
    migration = _migration()

    with pytest.raises(
        RuntimeError,
        match=(
            "ENTERPRISE_APP_ROLE_SECURITY_CONTRACT_VIOLATION.*"
            "mentorflow_app does not exist"
        ),
    ):
        migration._assert_application_role_security(_RoleBind(None))


def test_migration_never_mutates_cluster_role_attributes() -> None:
    source = MIGRATION_PATH.read_text(encoding="utf-8")

    assert re.search(r"\bALTER\s+ROLE\b", source, flags=re.IGNORECASE) is None
    assert "_assert_application_role_security(bind)" in source


def test_manifest_delete_reaches_rls_without_overgranting_revisions() -> None:
    source = MIGRATION_PATH.read_text(encoding="utf-8")

    assert (
        '"SELECT, INSERT, UPDATE, DELETE"\n'
        '            if table == "asset_manifests"'
    ) in source
    assert 'else "SELECT, INSERT"' in source
