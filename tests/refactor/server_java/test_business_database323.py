"""Automatic gates for the shared business pool, using the existing Java build/PG fixtures."""
from collections import deque
import os
import subprocess

from java_build156 import BUILD, CLASSPATH_FILE, JAVA_HOME


def _run_main(class_name, marker, tmp_path, *, database_url=None):
    classpath = os.pathsep.join((str(BUILD / "classes"), str(BUILD / "test-classes"),
                                CLASSPATH_FILE.read_text(encoding="utf-8").strip()))
    environment = dict(os.environ)
    if database_url is not None:
        environment["MENTORFLOW_DATABASE_URL"] = database_url
    log_path = tmp_path / "business-pool323.log"
    with log_path.open("wb") as log:
        result = subprocess.run(
            [str(JAVA_HOME / "bin/java.exe"), "-Djava.io.tmpdir=" + str(tmp_path),
             "-cp", classpath, "win.clannad.mentorflow.persistence." + class_name],
            env=environment, stdout=log, stderr=subprocess.STDOUT, timeout=60,
        )
    found = False
    tail = deque(maxlen=60)
    with log_path.open(encoding="utf-8", errors="replace") as log:
        for line in log:
            found |= marker in line
            tail.append(line)
    assert result.returncode == 0, "".join(tail)
    assert found, "".join(tail)


def test_business_database323_offline_pool_lifecycle(tmp_path):
    _run_main("BusinessDatabase323Main", "BUSINESS_DATABASE_323_OFFLINE_OK", tmp_path)


def test_business_database323_production_wiring(tmp_path):
    _run_main("BusinessDatabaseWiring323Main", "BUSINESS_DATABASE_WIRING_323_OK", tmp_path)


def test_business_database323_real_postgres_transactions(native_http144, tmp_path):
    # Reuse conftest's session-owned migrated local-test DB; do not start another PG fixture.
    database_url = native_http144["java"]["env"]["MENTORFLOW_DATABASE_URL"]
    _run_main("BusinessDatabasePostgres323Main", "BUSINESS_DATABASE_POSTGRES_323_OK", tmp_path,
              database_url=database_url)
