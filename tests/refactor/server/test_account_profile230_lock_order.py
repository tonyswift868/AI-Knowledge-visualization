"""Ensure all Python profile mutations enter closure fencing before profile locks."""

import pytest

from refactor.server.enterprise.account_profiles import AccountProfileService


class _Result:
    def mappings(self):
        return self

    def first(self):
        return {
            "nickname": "before",
            "signature": "",
            "avatar_etag": None,
            "avatar_width": None,
            "avatar_height": None,
            "version": 1,
            "avatar_bytes": None,
        }


class _Connection:
    class _Dialect:
        name = "postgresql"

    dialect = _Dialect()

    def __init__(self):
        self.statements = []

    def execute(self, statement, params=None):
        self.statements.append(str(statement).lower())
        return _Result()


class _Engine:
    def __init__(self, connection):
        self.connection = connection

    class _Transaction:
        def __init__(self, connection):
            self.connection = connection

        def __enter__(self):
            return self.connection

        def __exit__(self, *_):
            return False

    def begin(self):
        return self._Transaction(self.connection)


@pytest.mark.parametrize("operation", ["update", "save_avatar", "delete_avatar"])
def test_python_profile230_write_lock_order(operation):
    connection = _Connection()
    service = AccountProfileService(_Engine(connection))
    issuer, subject = "https://id.example.test/realm", "profile230-user"

    if operation == "update":
        service.update(issuer, subject, "after", "signature", 1)
    elif operation == "save_avatar":
        service.save_avatar(issuer, subject, b"image", "etag", 1, 1, 1)
    else:
        service.delete_avatar(issuer, subject, 1)

    statements = connection.statements
    context = next(i for i, sql in enumerate(statements)
                   if "set_config('mentorflow.actor_issuer'" in sql)
    fence = next(i for i, sql in enumerate(statements)
                 if "account_closure.guard_row" in sql)
    profile_lock = next(i for i, sql in enumerate(statements)
                        if "pg_advisory_xact_lock" in sql)
    profile_row = next(i for i, sql in enumerate(statements)
                       if any(token in sql for token in (
                           "from public.account_profiles", "into public.account_profiles",
                           "update public.account_profiles")))
    assert context < fence < profile_lock < profile_row
