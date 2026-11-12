"""Account daily activity storage contract for 361-MF06."""
from __future__ import annotations

import hashlib

import psycopg

from support_rag154 import rag154_backends


def _scope(issuer: str, subject: str) -> str:
    return hashlib.sha256((issuer + "\0" + subject).encode("utf-8")).hexdigest()


def _set_actor(connection, issuer: str, subject: str) -> None:
    connection.execute(
        "SELECT set_config('mentorflow.actor_issuer', %s, true), "
        "set_config('mentorflow.actor_subject', %s, true)",
        (issuer, subject),
    )


def test_daily_activity361_deduplicates_days_and_enforces_rls_and_closure(rag154_backends):
    backends = rag154_backends
    app_url = backends["app_url"].replace("postgresql+psycopg://", "postgresql://")
    admin_url = backends["admin_url"].replace("postgresql+psycopg://", "postgresql://")
    issuer = backends["issuer"]
    subject_a, subject_b = backends["subject_a"], backends["subject_b"]
    scope_a, scope_b = _scope(issuer, subject_a), _scope(issuer, subject_b)

    with psycopg.connect(app_url) as connection:
        _set_actor(connection, issuer, subject_a)
        connection.execute(
            "INSERT INTO public.account_daily_activity(account_scope,day) VALUES (%s,%s),(%s,%s) "
            "ON CONFLICT DO NOTHING",
            (scope_a, "2026-11-09", scope_a, "2026-11-10"),
        )
        connection.execute(
            "INSERT INTO public.account_daily_activity(account_scope,day) VALUES (%s,%s) "
            "ON CONFLICT DO NOTHING",
            (scope_a, "2026-11-09"),
        )
        assert connection.execute(
            "SELECT count(*) FROM public.account_daily_activity"
        ).fetchone()[0] == 2
        assert connection.execute(
            "SELECT count(*) FROM public.account_daily_activity WHERE account_scope=%s", (scope_b,)
        ).fetchone()[0] == 0

    with psycopg.connect(app_url) as connection:
        _set_actor(connection, issuer, subject_b)
        connection.execute(
            "INSERT INTO public.account_daily_activity(account_scope,day) VALUES (%s,%s)",
            (scope_b, "2026-11-09"),
        )
        assert connection.execute(
            "SELECT count(*) FROM public.account_daily_activity"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT count(*) FROM public.account_daily_activity WHERE account_scope=%s", (scope_a,)
        ).fetchone()[0] == 0

    with psycopg.connect(admin_url) as connection:
        connection.execute("SET ROLE mentorflow_closure")
        connection.execute(
            "DELETE FROM public.account_daily_activity WHERE account_scope=%s", (scope_a,)
        )
        connection.execute("RESET ROLE")
        assert connection.execute(
            "SELECT count(*) FROM public.account_daily_activity WHERE account_scope=%s", (scope_a,)
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT count(*) FROM public.account_daily_activity WHERE account_scope=%s", (scope_b,)
        ).fetchone()[0] == 1
