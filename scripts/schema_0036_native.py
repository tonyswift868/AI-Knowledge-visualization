"""Run the 0036 matrix on a new, loopback-only native PostgreSQL cluster.

Accepts binary/scratch paths, never a DSN. Owns and removes only its new cluster.
No Docker, SSH, Keycloak, SMTP, deployment, or external database access.
"""
from __future__ import annotations

import argparse
from contextlib import redirect_stderr, redirect_stdout
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import sys

import psycopg
from psycopg import sql
from sqlalchemy.engine import URL

ROOT = Path(__file__).resolve().parents[1]
HIDDEN = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


class NativePostgres:
    def __init__(self, binary: Path, scratch: Path):
        self.binary = binary.resolve()
        self.scratch = scratch.resolve()
        self.scratch.mkdir(parents=True, exist_ok=True)
        self.identity = "mf-schema-0036-" + secrets.token_hex(8)
        self.home = self.scratch / self.identity
        self.home.mkdir(exist_ok=False)
        self.data = self.home / "pgdata"
        self.password = secrets.token_urlsafe(36)
        self.started = False
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            self.port = listener.getsockname()[1]
        self.manifest = self.home / "owner.json"
        self.manifest.write_text(json.dumps({"identity": self.identity, "root": str(self.home)}))

    def command(self, program, *args):
        suffix = ".exe" if os.name == "nt" else ""
        with (self.home / "private-process.log").open("ab") as log:
            result = subprocess.run([str(self.binary / (program + suffix)), *map(str, args)],
                                    stdout=log, stderr=log, timeout=55, creationflags=HIDDEN)
        if result.returncode:
            raise RuntimeError(f"owned fixture {program} failed")

    def start(self):
        password_file = self.home / "initdb-password"
        password_file.write_text(self.password, encoding="utf-8")
        self.command("initdb", "-D", self.data, "-U", "postgres", "--auth=scram-sha-256",
                     "--pwfile", password_file, "--encoding=UTF8", "--no-locale")
        password_file.unlink()
        self.command("pg_ctl", "-D", self.data, "-o",
                     f"-h 127.0.0.1 -p {self.port} -c shared_buffers=64MB -c max_connections=30 "
                     "-c statement_timeout=15000 -c idle_in_transaction_session_timeout=30000",
                     "-l", self.home / "postgres.log", "-w", "start")
        self.started = True
        with self.admin() as connection:
            self.version = connection.execute("SHOW server_version").fetchone()[0]
            for role in ("mentorflow_migrator", "mentorflow_app"):
                connection.execute(sql.SQL(
                    "CREATE ROLE {} LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOREPLICATION PASSWORD {}"
                ).format(sql.Identifier(role), sql.Literal(self.password)))

    def admin(self):
        return psycopg.connect(host="127.0.0.1", port=self.port, user="postgres",
                               password=self.password, dbname="postgres", autocommit=True)

    def url(self, role, database):
        return URL.create("postgresql+psycopg", username=role, password=self.password,
                          host="127.0.0.1", port=self.port, database=database)

    def close(self):
        if self.started or (self.data / "postmaster.pid").is_file():
            self.command("pg_ctl", "-D", self.data, "-m", "fast", "-w", "stop")
        resolved = self.home.resolve()
        assert resolved.parent == self.scratch and resolved.name == self.identity
        assert json.loads(self.manifest.read_text()) == {"identity": self.identity, "root": str(self.home)}
        with socket.socket() as listener:
            assert listener.connect_ex(("127.0.0.1", self.port)) != 0, "fixture port still open"
        shutil.rmtree(resolved)
        self.password = ""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pg-bin", type=Path, required=True)
    parser.add_argument("--scratch", type=Path, required=True)
    args = parser.parse_args()
    lab = NativePostgres(args.pg_bin, args.scratch)
    report = dict(fixture=lab.identity, fixture_type="NATIVE_POSTGRESQL_ONLY",
                  source_head="0036_account_import_ledger", staging_db_mutation=0,
                  smtp_send=0, keycloak_mutation=0, secret_values_emitted=0, cases=[], cleaned_up=False)
    report["source_sha256"] = {str(path.relative_to(ROOT)).replace("\\", "/"):
        hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest() for path in (
            Path(__file__).resolve(), ROOT / "backend/tests/test_account_import_ledger_schema.py",
            ROOT / "backend/alembic/versions/0036_account_import_ledger.py",
            ROOT / "backend/app/enterprise_tenancy/import_ledger_schema.py")}
    report_path = args.scratch / (lab.identity + "-receipt.json")
    code = 1
    try:
        lab.start()
        report["postgres_version"] = lab.version
        os.environ.update(MENTORFLOW_DEPLOYMENT_MODE="PERSONAL_LOCAL",
                          MENTORFLOW_ORM_DB=str(lab.home / "collection.sqlite3"),
                          PYTHONDONTWRITEBYTECODE="1")
        sys.path[:0] = [str(ROOT / "backend"), str(ROOT / "backend/tests")]
        import pytest

        class Evidence:
            def pytest_configure(self, config):
                config._schema_0036_lab = lab

            def pytest_runtest_logreport(self, report):
                if report.when == "call" or report.failed:
                    entry = {"case": report.nodeid, "phase": report.when, "outcome": report.outcome}
                    if report.failed:
                        entry["failure"] = str(report.longrepr).replace(lab.password, "[REDACTED]")
                    evidence["cases"].append(entry)

        evidence = report
        with (lab.home / "private-tests.log").open("w", encoding="utf-8") as log:
            with redirect_stdout(log), redirect_stderr(log):
                code = int(pytest.main([str(ROOT / "backend/tests/test_account_import_ledger_schema.py"),
                                       "-q", "--basetemp", str(lab.home / "pytest")], plugins=[Evidence()]))
        report["pytest_exit_code"] = code
    except Exception as exc:
        report["failure_type"] = type(exc).__name__
    finally:
        try:
            lab.close()
            report["cleaned_up"] = True
        except Exception as exc:
            report["cleanup_failure_type"] = type(exc).__name__
            code = 1
        report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({"pytest_exit_code": code, "cleaned_up": report["cleaned_up"],
                      "cases": len(report["cases"]), "receipt": str(report_path)}))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
