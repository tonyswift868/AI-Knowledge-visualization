"""Shared production-app fixture for the 144 HTTP contract modules."""
import psycopg
import pytest
from contract_support144 import (LOCAL, OLD_ISSUER, FakeKeycloak144, make_tls144,
    prepare_databases144, seed_tenant144, start_java144, start_postgres144,
    start_python144, stop_postgres144, stop_server144)


@pytest.fixture(scope="session")
def native_http144(tmp_path_factory):
    if not (LOCAL / "test-classpath.txt").is_file():
        pytest.skip("144 needs the prepared Java/Zonky test classpath")
    run = tmp_path_factory.mktemp("native144")
    tls = make_tls144(LOCAL / "tls")
    fake = FakeKeycloak144(tls)
    pg = start_postgres144(run)
    fixture = dict(tls=tls, fake=fake, pg=pg, run=run, traces={}, states={}, servers=[])
    try:
        with psycopg.connect(f"host=127.0.0.1 port={pg['port']} user=postgres dbname=postgres") as conn:
            fixture["postgres_version"] = conn.execute("SELECT version()").fetchone()[0]
        databases = prepare_databases144(pg["port"], ("py144", "java144", "shared144"))
        for database in databases:
            admin = f"postgresql+psycopg://postgres@127.0.0.1:{pg['port']}/{database}"
            for user in ("alice", "bob"):
                seed_tenant144(admin, OLD_ISSUER, "subject144-" + user)
        for key, database, launch in (("python", "py144", start_python144),
                ("java", "java144", start_java144),
                ("shared_python", "shared144", start_python144),
                ("shared_java", "shared144", start_java144)):
            fixture[key] = launch(databases[database], fake, tls, run / key)
            fixture["servers"].append(fixture[key])
        yield fixture
    finally:
        for server in reversed(fixture["servers"]):
            stop_server144(server)
        fake.close()
        stop_postgres144(pg)
