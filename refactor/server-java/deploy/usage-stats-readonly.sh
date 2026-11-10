#!/bin/sh
# Stream-only server wrapper for usage-stats.py.  It creates no files.
set -eu

from=${1:?usage: usage-stats-readonly.sh YYYY-MM-DD YYYY-MM-DD [deploy-dir]}
to=${2:?usage: usage-stats-readonly.sh YYYY-MM-DD YYYY-MM-DD [deploy-dir]}
deploy_dir=${3:-/opt/mentorflow/releases/java-1070685b/refactor/server-java/deploy}
java_container=${JAVA_CONTAINER:-mentorflow-java-backend}
postgres_container=${POSTGRES_CONTAINER:-mentorflow-e1-preview-postgres-1}
docker_bin=${DOCKER_BIN:-docker}
python_bin=${PYTHON_BIN:-python3}

case "$from:$to" in
  [0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]:[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]) ;;
  *) echo 'dates must be YYYY-MM-DD' >&2; exit 2 ;;
esac
"$python_bin" -c 'from datetime import date; import sys; start=date.fromisoformat(sys.argv[1]); end=date.fromisoformat(sys.argv[2]); raise SystemExit("start date is after end date") if start > end else None' "$from" "$to"
test -r "$deploy_dir/usage-stats.py"

# The dates are shell-validated before they are inserted into this fixed SQL.
# The only database operation is one SELECT in a read-only transaction.
query="BEGIN READ ONLY;
SELECT (updated_at AT TIME ZONE 'UTC')::date::text AS day,
       COUNT(DISTINCT subject_id)::text AS accounts
  FROM public.account_import_ledger
 WHERE state = 'COMPLETED'
   AND (updated_at AT TIME ZONE 'UTC')::date >= DATE '$from'
   AND (updated_at AT TIME ZONE 'UTC')::date <= DATE '$to'
 GROUP BY 1 ORDER BY 1;
COMMIT;"
active=$($docker_bin exec -e PGOPTIONS='-c default_transaction_read_only=on' \
  "$postgres_container" psql -XqAt -v ON_ERROR_STOP=1 -U postgres -d mentorflow -c "$query")

# Convert the date/count-only rows to JSON; account identifiers never leave
# PostgreSQL.  The access log remains the only stdin of the statistics script.
active_json=$(printf '%s\n' "$active" | "$python_bin" -c \
  'import json,sys; out=[]
for line in sys.stdin:
 p=line.rstrip("\r\n").split("\t")
 if len(p)==2: out.append({"day":p[0],"accounts":int(p[1])})
print(json.dumps(out,separators=(",",":")))')

$docker_bin logs --since "${from}T00:00:00Z" --until "${to}T23:59:59.999999Z" "$java_container" |
  "$python_bin" "$deploy_dir/usage-stats.py" --start "$from" --end "$to" \
    --active-accounts-json "$active_json"
