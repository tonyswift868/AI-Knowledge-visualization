#!/usr/bin/env bash
# Read-only storage inventory for the MentorFlow server.
set -euo pipefail

root=${MENTORFLOW_ROOT:-/opt/mentorflow}
backup_dir=${BACKUP_DIR:-/opt/mentorflow/backups}
postgres_container=${POSTGRES_CONTAINER:-mentorflow-e1-preview-postgres-1}
docker_bin=${DOCKER_BIN:-docker}
psql_user=${PSQL_USER:-postgres}
psql_database=${PSQL_DATABASE:-mentorflow}
python_bin=${PYTHON_BIN:-python3}

psql_readonly() {
  local result
  if result=$("$docker_bin" exec -i -e 'PGOPTIONS=-c default_transaction_read_only=on -c statement_timeout=60000' \
    "$postgres_container" psql -XqAt -v ON_ERROR_STOP=1 \
    -U "$psql_user" -d "$psql_database" -c "BEGIN READ ONLY; $1 COMMIT;" 2>/dev/null); then
    printf '%s' "$result"
  else
    printf '%s\n' 'storage report: read-only PostgreSQL query failed' >&2
    return 1
  fi
}

printf '%s\n' 'PostgreSQL table storage (bytes | estimated rows | dead rows | last autovacuum UTC)'
psql_readonly "SELECT format('%I.%I|%s|%s|%s|%s', schemaname, relname,
  pg_total_relation_size(format('%I.%I', schemaname, relname)::regclass),
  n_live_tup, n_dead_tup,
  COALESCE(to_char(last_autovacuum AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS') || ' UTC', 'never'))
FROM pg_stat_user_tables ORDER BY schemaname, relname;"
printf '\n'

printf '%s\n' 'Sync receipt rows and mean response bytes'
if receipt_table=$(psql_readonly "SELECT COALESCE(to_regclass('public.structured_sync_requests')::text, '')"); then
  if [ -n "$receipt_table" ]; then
    psql_readonly "SELECT format('structured_sync_requests|%s|%s', count(*),
      COALESCE(round(avg(octet_length(response::text)))::text, '0')) FROM public.structured_sync_requests;"
  else
    printf '%s\n' 'structured_sync_requests|absent|absent'
  fi
else
  exit 1
fi
printf '\n'

printf '%s\n' 'Tombstone rows by table'
tombstone_sql=$(psql_readonly "SELECT COALESCE(string_agg(format(
  'SELECT %L, count(*) FROM %I.%I WHERE deleted IS TRUE', schemaname||'.'||tablename,
  schemaname, tablename), ' UNION ALL '), 'SELECT ''none'', 0')
FROM pg_tables t WHERE schemaname NOT IN ('pg_catalog','information_schema')
AND EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema=t.schemaname
  AND c.table_name=t.tablename AND c.column_name='deleted' AND c.data_type='boolean');")
psql_readonly "$tombstone_sql ORDER BY 1;"
printf '\n'

printf '%s\n' 'Largest account data (type | first 8 hash characters | rows | estimated bytes)'
printf '%s\n' 'Account byte totals are row-size estimates only (exclude indexes/external TOAST); unknown-issuer owners remain separate.'
account_sql=$(psql_readonly "SELECT COALESCE(string_agg(query, ' UNION ALL '),
  'SELECT '''', '''', '''', 0, 0 WHERE false') FROM (
  SELECT format('SELECT %L, encode(convert_to(%I::text,''UTF8''),''hex''), %L, count(*), sum(pg_column_size(t)) FROM %I.%I t WHERE %I IS NOT NULL GROUP BY %I',
    'scope', 'account_scope', '', schemaname, tablename, 'account_scope', 'account_scope') AS query
  FROM pg_tables t WHERE schemaname NOT IN ('pg_catalog','information_schema')
    AND EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema=t.schemaname
      AND c.table_name=t.tablename AND c.column_name='account_scope')
  UNION ALL
  SELECT format('SELECT %L, encode(convert_to(%I::text,''UTF8''),''hex''), encode(convert_to(%I::text,''UTF8''),''hex''), count(*), sum(pg_column_size(t)) FROM %I.%I t WHERE %I IS NOT NULL AND %I IS NOT NULL GROUP BY %I, %I',
    'pair', 'issuer', candidate.subject_column, t.schemaname, t.tablename, 'issuer', candidate.subject_column, 'issuer', candidate.subject_column)
  FROM pg_tables t CROSS JOIN LATERAL (
    SELECT CASE WHEN EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema=t.schemaname AND c.table_name=t.tablename AND c.column_name='subject') THEN 'subject' WHEN EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema=t.schemaname AND c.table_name=t.tablename AND c.column_name='subject_id') THEN 'subject_id' ELSE 'owner_subject_id' END AS subject_column
  ) candidate
  WHERE t.schemaname NOT IN ('pg_catalog','information_schema')
    AND EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema=t.schemaname AND c.table_name=t.tablename AND c.column_name='issuer')
    AND (EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema=t.schemaname AND c.table_name=t.tablename AND c.column_name='subject')
      OR EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema=t.schemaname AND c.table_name=t.tablename AND c.column_name='subject_id')
      OR EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema=t.schemaname AND c.table_name=t.tablename AND c.column_name='owner_subject_id'))
    AND NOT EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema=t.schemaname AND c.table_name=t.tablename AND c.column_name='account_scope')
  UNION ALL
  SELECT format('SELECT %L, encode(convert_to(%I::text,''UTF8''),''hex''), %L, count(*), sum(pg_column_size(t)) FROM %I.%I t WHERE %I IS NOT NULL GROUP BY %I',
    CASE WHEN c.column_name='owner_subject_id' THEN 'unknown-owner' ELSE 'unknown-subject' END,
    c.column_name, '', c.table_schema, c.table_name, c.column_name, c.column_name)
  FROM information_schema.columns c JOIN pg_tables t ON t.schemaname=c.table_schema AND t.tablename=c.table_name
  WHERE c.table_schema NOT IN ('pg_catalog','information_schema') AND c.column_name IN ('subject','subject_id','owner_subject_id')
    AND NOT EXISTS (SELECT 1 FROM information_schema.columns x WHERE x.table_schema=c.table_schema AND x.table_name=c.table_name AND x.column_name='issuer')
    AND NOT EXISTS (SELECT 1 FROM information_schema.columns x WHERE x.table_schema=c.table_schema AND x.table_name=c.table_name AND x.column_name='account_scope')
) account_queries;")
account_rows=$(psql_readonly "$account_sql;")
printf '%s\n' "$account_rows" | "$python_bin" -c '
import hashlib, sys
accounts = {}
for line in sys.stdin:
    fields = line.rstrip("\n").split("|")
    if len(fields) != 5:
        continue
    kind, first, second, rows, size = fields
    if not first:
        continue
    if kind == "scope":
        key = bytes.fromhex(first).decode("utf-8")
        label = "account"
    elif kind == "pair":
        issuer, subject = bytes.fromhex(first), bytes.fromhex(second)
        key = hashlib.sha256(issuer + b"\0" + subject).hexdigest()
        label = "account"
    else:
        subject = bytes.fromhex(first)
        key = kind + ":" + hashlib.sha256(kind.encode() + b"\0" + subject).hexdigest()
        label = kind
    previous = accounts.get(key, (label, 0, 0))
    accounts[key] = (label, previous[1] + int(rows), previous[2] + int(size))
for key, (label, rows, size) in sorted(accounts.items(), key=lambda item: item[1][2], reverse=True)[:10]:
    suffix = key.rsplit(":", 1)[-1]
    print(f"{label}|{suffix[:8]}|{rows}|{size}")
'
printf '\n'
printf '%s\n' 'MentorFlow directory sizes (bytes | directory)'
if [ -d "$root" ]; then
  find "$root" -mindepth 1 -maxdepth 1 -type d -print0 | while IFS= read -r -d '' directory; do
    size=$(du -sb -- "$directory" | cut -f1)
    printf '%s|%s\n' "$size" "${directory##*/}"
  done
else
  printf '%s\n' 'root|absent'
fi

printf '%s\n' 'Docker aggregate usage'
if ! "$docker_bin" system df 2>/dev/null; then
  printf '%s\n' 'storage report: Docker usage query failed' >&2
  exit 1
fi

printf '%s\n' 'Backup directory (file count | total bytes | oldest file date)'
if [ -d "$backup_dir" ]; then
  find "$backup_dir" -type f -printf '%T@ %s %TY-%Tm-%Td\n' | awk '
    { count++; bytes += $2; if (count == 1 || $1 < oldest) { oldest=$1; date=$3 } }
    END { printf "%d|%d|%s\n", count, bytes, (count ? date : "none") }
  '
else
  printf '%s\n' '0|0|directory-absent'
fi
