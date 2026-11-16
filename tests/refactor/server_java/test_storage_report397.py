"""Verify the storage report against isolated embedded PostgreSQL data."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import subprocess
import uuid

import psycopg
import pytest

import contract_support145 as support145
from test_question_index391 import _EmbeddedPostgres391


ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "refactor/server-java/deploy/storage-report.sh"
TEST_ROOT = Path(os.environ.get("STORAGE397_TMP", r"D:\MentorFlow\.local\test-tmp\397-MF02")) / ("retest-" + uuid.uuid4().hex)
ACCOUNT_ISSUER = "issuer397"
ACCOUNT_SUBJECT = "subject397-private@example.invalid"
ACCOUNT_SCOPE = hashlib.sha256((ACCOUNT_ISSUER + "\0" + ACCOUNT_SUBJECT).encode()).hexdigest()
SECRET = "private-payload-397-do-not-print@example.invalid"


@pytest.fixture(scope="module")
def storage_report397_db():
    TEST_ROOT.mkdir(parents=True, exist_ok=True)
    build = TEST_ROOT / "build"
    build.mkdir(parents=True, exist_ok=True)
    classpath_file = Path(os.environ.get(
        "JAVA156_CLASSPATH_FILE",
        r"D:\MentorFlow\.local\test-tmp\java-cache\test-classpath.txt"))
    if not classpath_file.is_file():
        pytest.fail("shared Java/PostgreSQL test classpath is unavailable")
    classpath = classpath_file.read_text(encoding="utf-8").strip()
    helper_classes = build / "db-test-classes"
    helper_classes.mkdir(parents=True, exist_ok=True)
    helper_source = Path(__file__).parent / "java" / "question391" / "EmbeddedPostgres391.java"
    subprocess.run(
        [str(support145.JAVA_HOME / "bin" / "javac.exe"), "-encoding", "UTF-8",
         "-cp", classpath, "-d", str(helper_classes), str(helper_source)],
        check=True, cwd=ROOT, timeout=180, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    postgres = _EmbeddedPostgres391(os.pathsep.join((str(helper_classes), classpath)), TEST_ROOT)
    try:
        with psycopg.connect(postgres.url("postgres", "postgres", "postgres"), autocommit=True) as conn:
            conn.execute("CREATE DATABASE storage397")
        yield postgres.url("storage397", "postgres", "postgres")
    finally:
        postgres.stop()


def _report397(db_url: str, test_bin: Path, root: Path, backups: Path, *, fail_sql: bool = False,
               stream_script: bool = False):
    env = os.environ.copy()
    bash_tmp = TEST_ROOT / "bash-tmp"
    bash_tmp.mkdir(exist_ok=True)
    env.update({
        "PATH": str(test_bin) + os.pathsep + env.get("PATH", ""),
        "PYTHON_BIN": support145.PYTHON.as_posix(),
        "STORAGE397_DATABASE_URL": db_url,
        "MENTORFLOW_ROOT": str(root),
        "BACKUP_DIR": str(backups),
        "POSTGRES_CONTAINER": "embedded-test",
        "TMPDIR": "/" + bash_tmp.drive[0].lower() + bash_tmp.as_posix()[2:],
    })
    if fail_sql:
        env["STORAGE397_FAIL_SQL"] = "sql detail must stay private"
    streamed = "".join(SCRIPT.open(encoding="utf-8")) + "\nprintf 'STORAGE397_STREAM_COMPLETE\\n'\n"
    completed = subprocess.run(
        [r"C:\Program Files\Git\bin\bash.exe", "-s" if stream_script else str(SCRIPT)], cwd=ROOT, env=env,
        input=streamed if stream_script else None,
        check=False, capture_output=True, text=True, timeout=120)
    if fail_sql:
        return completed
    assert completed.returncode == 0
    assert completed.stderr == ""
    return completed.stdout


def test_storage_report397_masks_accounts_counts_json_and_stays_read_only(storage_report397_db):
    db_url = storage_report397_db
    with psycopg.connect(db_url) as conn:
        conn.execute("CREATE TABLE structured_sync_requests (issuer text, subject_id text, response jsonb)")
        conn.execute("CREATE TABLE rag_rows (account_scope text, payload text)")
        conn.execute("CREATE TABLE profile_rows (account_scope text, email text)")
        conn.execute("CREATE TABLE account_daily_activity (account_scope text, day date)")
        conn.execute("CREATE TABLE sample_records (issuer text, subject_id text, deleted boolean, payload text)")
        conn.execute("CREATE TABLE subject_rows (subject text, payload text)")
        conn.execute("CREATE TABLE owner_rows (owner_subject_id text, payload text)")
        conn.execute("CREATE TABLE null_scope_rows (account_scope text, payload text)")
        conn.execute("CREATE TABLE non_boolean_deleted (deleted timestamptz)")
        conn.execute("INSERT INTO structured_sync_requests VALUES (%s, %s, %s::jsonb), (%s, %s, %s::jsonb)",
                     (ACCOUNT_ISSUER, ACCOUNT_SUBJECT, '{"message":"' + SECRET + '"}',
                      ACCOUNT_ISSUER, ACCOUNT_SUBJECT, '{"count":2}'))
        conn.execute("INSERT INTO rag_rows VALUES (%s, %s), (%s, %s), (NULL, %s)",
                     (ACCOUNT_SCOPE, SECRET, ACCOUNT_SCOPE, "second rag row", "null scope row"))
        conn.execute("INSERT INTO profile_rows VALUES (%s, %s)", (ACCOUNT_SCOPE, "profile-email@example.invalid"))
        conn.execute("INSERT INTO account_daily_activity VALUES (%s, DATE '2026-11-16')", (ACCOUNT_SCOPE,))
        conn.execute("INSERT INTO sample_records VALUES (%s, %s, true, %s)",
                     (ACCOUNT_ISSUER, ACCOUNT_SUBJECT, SECRET))
        conn.execute("INSERT INTO subject_rows VALUES (%s, %s)", ("native-subject-secret", SECRET))
        conn.execute("INSERT INTO owner_rows VALUES (%s, %s)", ("owner-subject-secret", SECRET))
        conn.execute("INSERT INTO null_scope_rows VALUES (NULL, %s)", (SECRET,))
        conn.execute("INSERT INTO non_boolean_deleted VALUES (now())")
        expected_response_bytes = conn.execute(
            "SELECT round(avg(octet_length(response::text))) FROM structured_sync_requests").fetchone()[0]
        expected_rows, expected_bytes = conn.execute(
            "SELECT count(*), sum(bytes) FROM ("
            "SELECT pg_column_size(t) AS bytes FROM structured_sync_requests t WHERE issuer=%s AND subject_id=%s "
            "UNION ALL SELECT pg_column_size(t) FROM rag_rows t WHERE account_scope=%s "
            "UNION ALL SELECT pg_column_size(t) FROM profile_rows t WHERE account_scope=%s "
            "UNION ALL SELECT pg_column_size(t) FROM account_daily_activity t WHERE account_scope=%s "
            "UNION ALL SELECT pg_column_size(t) FROM sample_records t WHERE issuer=%s AND subject_id=%s"
            ") account_rows", (ACCOUNT_ISSUER, ACCOUNT_SUBJECT, ACCOUNT_SCOPE, ACCOUNT_SCOPE,
                               ACCOUNT_SCOPE, ACCOUNT_ISSUER, ACCOUNT_SUBJECT)).fetchone()
        before_rows = conn.execute("SELECT count(*) FROM sample_records").fetchone()[0]
        conn.commit()

    test_bin = TEST_ROOT / "bin"
    test_bin.mkdir(parents=True, exist_ok=True)
    psql_script = test_bin / "psql397.py"
    psql_script.write_text(
        "import os,sys,psycopg\n"
        "if os.environ.get('STORAGE397_FAIL_SQL'): raise RuntimeError(os.environ['STORAGE397_FAIL_SQL'])\n"
        "args=sys.argv[1:]\n"
        "query=args[args.index('-c')+1]\n"
        "assert query.startswith('BEGIN READ ONLY; ') and query.endswith(' COMMIT;')\n"
        "options=os.environ.get('PGOPTIONS','')\n"
        "assert 'default_transaction_read_only=on' in options and 'statement_timeout=' in options\n"
        "query=query[len('BEGIN READ ONLY; '):-len(' COMMIT;')]\n"
        "with psycopg.connect(os.environ['STORAGE397_DATABASE_URL']) as c:\n"
        " c.execute('SET TRANSACTION READ ONLY')\n"
        " with c.cursor() as cur:\n"
        "  cur.execute(query)\n"
        "  if cur.description:\n"
        "   for row in cur.fetchall(): print('|'.join('' if v is None else str(v) for v in row))\n",
        encoding="utf-8", newline="\n")
    (test_bin / "psql").write_text(
        f"#!/usr/bin/env bash\nexec \"{support145.PYTHON.as_posix()}\" \"{psql_script.as_posix()}\" \"$@\"\n",
        encoding="utf-8", newline="\n")
    (test_bin / "docker").write_text(
        "#!/usr/bin/env bash\n"
        "if [[ $1 == exec ]]; then\n"
        " shift\n"
        " while [[ $1 == -i || $1 == -e ]]; do\n"
        "  if [[ $1 == -i ]]; then cat >/dev/null; fi\n"
        "  if [[ $1 == -e ]]; then shift; export \"$1\"; fi\n"
        "  shift\n"
        " done\n"
        " shift\n"
        " exec \"$@\"\n"
        "fi\n"
        "if [[ $1 == system && $2 == df ]]; then printf 'TYPE|TOTAL|ACTIVE|SIZE|RECLAIMABLE\\nImages|0|0|0B|0B\\nVolumes|0|0|0B|0B\\n'; exit 0; fi\n"
        "exit 2\n", encoding="utf-8", newline="\n")
    root = TEST_ROOT / "mentorflow-root"
    for name in ("releases", "state", "logs"):
        (root / name).mkdir(parents=True, exist_ok=True)
        (root / name / "placeholder").write_text("local fixture", encoding="utf-8")
    backups = TEST_ROOT / "backups"
    backups.mkdir(exist_ok=True)
    (backups / "backup-a.dump").write_bytes(b"backup fixture")

    output = _report397(db_url, test_bin, root, backups)
    assert f"structured_sync_requests|2|{expected_response_bytes}" in output
    assert f"account|{ACCOUNT_SCOPE[:8]}|{expected_rows}|{expected_bytes}" in output
    assert re.search(r"^unknown-subject\|[0-9a-f]{8}\|1\|\d+$", output, re.MULTILINE)
    assert re.search(r"^unknown-owner\|[0-9a-f]{8}\|1\|\d+$", output, re.MULTILINE)
    assert "public.sample_records|1" in output
    assert "non_boolean_deleted" not in output.split("Tombstone rows by table", 1)[1].split("Largest account data", 1)[0]
    assert ACCOUNT_SUBJECT not in output
    assert "profile-email@example.invalid" not in output
    assert SECRET not in output
    assert "backup-a.dump" not in output
    assert "1|14|" in output
    assert "Images|0|0|0B|0B" in output and "Volumes|0|0|0B|0B" in output
    streamed_output = _report397(db_url, test_bin, root, backups, stream_script=True)
    assert "Backup directory (file count | total bytes | oldest file date)" in streamed_output
    assert "STORAGE397_STREAM_COMPLETE" in streamed_output
    with psycopg.connect(db_url) as conn:
        assert conn.execute("SELECT count(*) FROM sample_records").fetchone()[0] == before_rows

    failure = _report397(db_url, test_bin, root, backups, fail_sql=True)
    assert failure.returncode != 0
    assert "storage report: read-only PostgreSQL query failed" in failure.stderr
    assert "sql detail must stay private" not in failure.stdout + failure.stderr

    with psycopg.connect(db_url) as conn:
        conn.execute("DELETE FROM structured_sync_requests")
        conn.commit()
    empty_receipt_output = _report397(db_url, test_bin, root, backups)
    assert "structured_sync_requests|0|0" in empty_receipt_output

    with psycopg.connect(db_url) as conn:
        conn.execute("DROP TABLE structured_sync_requests")
        conn.commit()
    missing_receipt_output = _report397(db_url, test_bin, root, backups)
    assert "structured_sync_requests|absent|absent" in missing_receipt_output
    assert SECRET not in missing_receipt_output


def test_storage_report397_script_is_lf():
    assert b"\r\n" not in SCRIPT.read_bytes()
