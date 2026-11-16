"""396 synthetic sizing and old/new committed repository write benchmark."""
import json
import os
from pathlib import Path
import subprocess
import time
import psycopg
from sqlalchemy import create_engine
import contract_support145 as support
from test_question_index391 import _EmbeddedPostgres391, _migration as upgrade_database
from test_sync_receipt_payload396 import _migration

def main():
    root = Path(os.environ['SHIP156_TMP'])
    build = root / 'build'
    build.mkdir(parents=True, exist_ok=True)
    deps = Path(os.environ['JAVA156_CLASSPATH_FILE']).read_text(encoding='utf8').strip()
    app = Path(os.environ['JAVA156_BUILD']) / 'classes'
    cp = os.pathsep.join((str(build), str(app), deps))
    java = support.JAVA_HOME / 'bin/java.exe'
    javac = support.JAVA_HOME / 'bin/javac.exe'
    sources = Path(__file__).parent / 'java'
    subprocess.run([str(javac), '-cp', cp, '-d', str(build), str(sources/'question391/EmbeddedPostgres391.java'), str(sources/'receipt396/ReceiptBenchmark396.java')], check=True)
    old = build / 'old-classes'
    old.mkdir(exist_ok=True)
    subprocess.run([str(javac), '-encoding', 'UTF-8', '-cp', cp, '-d', str(old), str(root.parent/'old-src/SyncRepository.java')], check=True)
    server = _EmbeddedPostgres391(cp, root)
    try:
        with psycopg.connect(server.url('postgres','postgres','postgres'), autocommit=True) as c:
            c.execute('ALTER ROLE mentorflow_closure NOINHERIT NOREPLICATION')
        upgrade_database(server.url('sync145_java','mentorflow_migrator',support.MIGRATION_PASSWORD,sqlalchemy=True),'upgrade',build/'migration.log')
        url = server.url('sync145_java','postgres','postgres')
        engine = create_engine(server.url('sync145_java','mentorflow_migrator',support.MIGRATION_PASSWORD,sqlalchemy=True))
        with psycopg.connect(url,autocommit=True) as conn:
            for count in (10_000,100_000,1_000_000):
                conn.execute('TRUNCATE public.structured_sync_requests,public.structured_sync_entities')
                conn.execute("""INSERT INTO structured_sync_requests (issuer,subject_id,organization_id,workspace_id,request_id,digest,response)
                    SELECT 'bench396','synthetic','bench','bench',i::text,md5(i::text)||md5(i::text),
                    json_build_object('kind','learning_run','entity_id',i::text,'revision',1,'deleted',false,
                    'payload',json_build_object('text',repeat('x',128))) FROM generate_series(1,%s) i""",(count,))
                writes = {}
                for label,classpath in [('old',os.pathsep.join((str(old),cp))),('new',cp)]:
                    result = subprocess.run([str(java),'-cp',classpath,'receipt396.ReceiptBenchmark396',url],check=True,capture_output=True,text=True)
                    writes[label] = [line for line in result.stdout.splitlines() if line.startswith(('MEDIAN ','SAMPLES '))]
                size = lambda: conn.execute("SELECT pg_total_relation_size('public.structured_sync_requests')").fetchone()[0]
                before = size()
                started = time.perf_counter()
                with engine.begin() as c:
                    _migration(c)
                elapsed = (time.perf_counter()-started)*1000
                middle = size()
                conn.execute('VACUUM public.structured_sync_requests')
                after = size()
                assert conn.execute("SELECT count(*) FROM structured_sync_requests WHERE response::jsonb ? 'payload'").fetchone()[0] == 0
                print(json.dumps(dict(rows=count,migration_ms=elapsed,bytes_before=before,bytes_after=middle,bytes_after_vacuum=after,writes=writes)),flush=True)
        engine.dispose()
    finally:
        server.stop()

if __name__ == '__main__':
    main()
