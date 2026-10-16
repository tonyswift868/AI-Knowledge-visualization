#!/bin/sh
# Local/disposable database verification only; this script does not deploy or create an extension.
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/../../.." && pwd)
: "${RAG_VERIFY_DISPOSABLE_DATABASE:?Set this to the name of a disposable test database}"
: "${MENTORFLOW_DATABASE_URL:?Set the mentorflow_app PostgreSQL URL for the disposable database}"
: "${RAG_VERIFY_MIGRATION_URL:?Set the migrator PostgreSQL URL for that same disposable database}"
: "${RAG_VERIFY_TMP:?Set a writable temporary directory outside the source checkout}"
PYTHON_BIN=${PYTHON_BIN:-python}
MVN=${MVN:-mvn}
JAVA_BIN=${JAVA_BIN:-java}
mkdir -p "$RAG_VERIFY_TMP/java-home"
export DJL_CACHE_DIR="$RAG_VERIFY_TMP/native-cache" ENGINE_CACHE_DIR="$RAG_VERIFY_TMP/native-cache"
export HF_HOME="$RAG_VERIFY_TMP/huggingface" HF_HUB_CACHE="$RAG_VERIFY_TMP/huggingface/hub"
export TMPDIR="$RAG_VERIFY_TMP" PYTHONDONTWRITEBYTECODE=1
# Check both configured URLs point to the named test database, not two unrelated databases.
"$PYTHON_BIN" - <<'PY'
import os
from urllib.parse import urlsplit
name=os.environ['RAG_VERIFY_DISPOSABLE_DATABASE']
for key in ('MENTORFLOW_DATABASE_URL','RAG_VERIFY_MIGRATION_URL'):
    url=os.environ[key].removeprefix('jdbc:').replace('postgresql+psycopg:', 'postgresql:')
    if urlsplit(url).path.strip('/') != name:
        raise SystemExit(key+' does not name the disposable database')
print('Disposable database names verified; extension must already exist.')
PY
migrate() {
  MENTORFLOW_MIGRATION_DATABASE_URL="$RAG_VERIFY_MIGRATION_URL" "$PYTHON_BIN" -m alembic \
    -c "$ROOT/refactor/server/alembic.ini" "$@"
}
cd "$ROOT"
migrate upgrade head
"$MVN" -B -f refactor/server-java/pom.xml \
  "-Dmaven.repo.local=$RAG_VERIFY_TMP/m2" "-Djava142.build.directory=$RAG_VERIFY_TMP/build" \
  -DskipTests package dependency:build-classpath "-Dmdep.outputFile=$RAG_VERIFY_TMP/classpath.txt"
CP="$RAG_VERIFY_TMP/build/classes:$RAG_VERIFY_TMP/build/test-classes:$(cat "$RAG_VERIFY_TMP/classpath.txt")"
"$JAVA_BIN" "-Djava.io.tmpdir=$RAG_VERIFY_TMP" "-Duser.home=$RAG_VERIFY_TMP/java-home" -cp "$CP" rag154.RagPgvectorVerify154
# Only disposable test data is removed by this RAG migration rollback.
migrate downgrade 0043_native_sessions
migrate upgrade head
"$JAVA_BIN" "-Djava.io.tmpdir=$RAG_VERIFY_TMP" "-Duser.home=$RAG_VERIFY_TMP/java-home" -cp "$CP" rag154.RagPgvectorVerify154
printf '%s\n' 'RAG_PGVECTOR154_MIGRATION_ROUNDTRIP_OK'
