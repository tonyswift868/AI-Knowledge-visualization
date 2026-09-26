"""Ordered account/workspace changes without altering the snapshot contract.

The sequence is global, CACHE 1, and intentionally survives downgrade. The
trigger serializes allocation through commit within each scope. Consumers
advance to returned rows only, never to the global sequence high-water mark.
This prevents a later commit from appearing below an already-issued cursor.
"""
from alembic import op
import sqlalchemy as sa

revision = '0042_structured_sync_changes'
down_revision = '0041_account_closure'
branch_labels = depends_on = None

TABLE = 'structured_sync_entities'
SEQUENCE = 'structured_sync_change_seq'
INDEX = 'ix_structured_sync_changes_scope'
UNIQUE = 'ux_structured_sync_change_seq'


def upgrade():
    bind = op.get_bind()
    if bind.dialect.name == 'postgresql':
        # A volatile ADD COLUMN default backfills every row under the DDL lock,
        # including rows hidden from the migrator by FORCE RLS. It does not run
        # business UPDATE/closure triggers or temporarily disable any policy.
        op.execute("CREATE SEQUENCE IF NOT EXISTS public.structured_sync_change_seq AS bigint CACHE 1")
        op.execute("ALTER SEQUENCE public.structured_sync_change_seq CACHE 1")
        op.execute("REVOKE ALL ON SEQUENCE public.structured_sync_change_seq FROM PUBLIC")
        op.execute("GRANT USAGE ON SEQUENCE public.structured_sync_change_seq TO mentorflow_app")
        op.execute("ALTER TABLE public.structured_sync_entities ADD COLUMN change_seq bigint NOT NULL DEFAULT nextval('public.structured_sync_change_seq')")
        op.execute("ALTER TABLE public.structured_sync_entities ALTER COLUMN change_seq DROP DEFAULT")
        op.execute("ALTER TABLE public.structured_sync_entities ADD CONSTRAINT ck_sync_change_seq CHECK (change_seq > 0)")
        op.execute("""
            CREATE FUNCTION public.stamp_structured_sync_change() RETURNS trigger
            LANGUAGE plpgsql SET search_path = pg_catalog AS $$
            BEGIN
                PERFORM pg_advisory_xact_lock(hashtextextended(
                    'mentorflow.sync.changes.v1:' || array_to_json(ARRAY[
                        NEW.issuer, NEW.subject_id, NEW.organization_id, NEW.workspace_id])::text, 0));
                NEW.change_seq := nextval('public.structured_sync_change_seq'::regclass);
                RETURN NEW;
            END $$
        """)
        op.execute("REVOKE ALL ON FUNCTION public.stamp_structured_sync_change() FROM PUBLIC")
        op.execute("GRANT EXECUTE ON FUNCTION public.stamp_structured_sync_change() TO mentorflow_app")
        op.execute("CREATE TRIGGER structured_sync_change_stamp BEFORE INSERT OR UPDATE ON public.structured_sync_entities FOR EACH ROW EXECUTE FUNCTION public.stamp_structured_sync_change()")
    else:
        # SQLite is only the local regression runtime; its single writer gives
        # the same ordering without PostgreSQL advisory locks or RLS claims.
        op.execute("CREATE TABLE IF NOT EXISTS structured_sync_change_clock (id INTEGER PRIMARY KEY CHECK(id=1), value BIGINT NOT NULL)")
        op.execute("INSERT OR IGNORE INTO structured_sync_change_clock VALUES (1,0)")
        op.add_column(TABLE, sa.Column('change_seq', sa.BigInteger(), nullable=False, server_default='0'))
        op.execute("UPDATE structured_sync_entities SET change_seq=(SELECT value FROM structured_sync_change_clock WHERE id=1)+rowid")
        op.execute("UPDATE structured_sync_change_clock SET value=COALESCE((SELECT MAX(change_seq) FROM structured_sync_entities),value) WHERE id=1")
        for name, event in (('insert', 'INSERT'), ('update', 'UPDATE OF revision,deleted,payload')):
            op.execute(f"""CREATE TRIGGER structured_sync_change_{name} AFTER {event} ON {TABLE}
                BEGIN
                    UPDATE structured_sync_change_clock SET value=value+1 WHERE id=1;
                    UPDATE {TABLE} SET change_seq=(SELECT value FROM structured_sync_change_clock WHERE id=1) WHERE rowid=NEW.rowid;
                END""")
    op.create_index(UNIQUE, TABLE, ['change_seq'], unique=True)
    op.create_index(INDEX, TABLE, ['issuer', 'subject_id', 'organization_id', 'workspace_id', 'kind', 'change_seq'])


def downgrade():
    # Preserve all payloads, revisions, tombstones and idempotency receipts.
    # Keep the sequence/clock high-water mark: re-upgrade must not reuse client
    # cursors, even after old clients wrote additional records on schema 0041.
    if op.get_bind().dialect.name == 'postgresql':
        op.execute("DROP TRIGGER structured_sync_change_stamp ON public.structured_sync_entities")
        op.execute("DROP FUNCTION public.stamp_structured_sync_change()")
        op.drop_constraint('ck_sync_change_seq', TABLE, type_='check')
    else:
        op.execute("DROP TRIGGER structured_sync_change_insert")
        op.execute("DROP TRIGGER structured_sync_change_update")
    op.drop_index(INDEX, table_name=TABLE)
    op.drop_index(UNIQUE, table_name=TABLE)
    op.drop_column(TABLE, 'change_seq')
