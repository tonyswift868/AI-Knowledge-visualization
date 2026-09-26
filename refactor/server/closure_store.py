"""PostgreSQL adapter: EXECUTE-only closure functions, no direct deletion grants."""
from sqlalchemy import text
from .closure import ClosureError


class PostgresClosureStore:
    def __init__(self, engine, issuer):
        if engine.dialect.name != "postgresql":
            raise ValueError("Account closure requires PostgreSQL")
        self.engine, self.issuer = engine, issuer

    def _call(self, function, **params):
        # Function names and parameter names are internal constants.
        query = "SELECT account_closure." + function + "(" + ", ".join(":" + n for n in params) + ")"
        try:
            with self.engine.begin() as connection:
                connection.execute(text("SET LOCAL lock_timeout = '3s'"))
                connection.execute(text("SET LOCAL statement_timeout = '10s'"))
                value = connection.execute(text(query), params).scalar_one()
        except Exception:
            raise ClosureError("CLOSURE_STORAGE_UNAVAILABLE") from None
        if not isinstance(value, dict):
            raise ClosureError("CLOSURE_STORAGE_INVALID")
        if value.get("error"):
            code = value["error"]
            raise ClosureError(code, 403 if code == "CLOSURE_CREDENTIAL_INVALID" else
                               409 if code in {"CLOSURE_SCOPE_UNRESOLVED", "CLOSURE_ALREADY_COMMITTED"} else 503)
        return value

    def prepare(self, issuer, subject, account, operation, digest):
        return self._call("prepare", p_issuer=issuer, p_subject=subject, p_account=account,
                          p_operation=operation, p_digest=digest)

    def status(self, operation, digest):
        return self._call("status", p_operation=operation, p_digest=digest)

    def acquire(self, operation, digest, claim):
        return self._call("acquire", p_operation=operation, p_digest=digest, p_claim=claim)

    def clean(self, operation, digest, claim):
        return self._call("clean", p_operation=operation, p_digest=digest, p_claim=claim)

    def provider_done(self, operation, digest, claim):
        return self._call("provider_done", p_operation=operation, p_digest=digest, p_claim=claim)

    def complete(self, operation, digest, claim):
        return self._call("complete", p_operation=operation, p_digest=digest, p_claim=claim)

    def fail(self, operation, digest, claim, code):
        return self._call("fail", p_operation=operation, p_digest=digest, p_claim=claim, p_code=code)

    def ensure_open(self, issuer, subject):
        value = self._call("check_open", p_issuer=issuer, p_subject=subject)
        if value.get("closed") is True:
            raise ClosureError("ACCOUNT_CLOSURE_IN_PROGRESS", 403)

