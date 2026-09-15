"""Narrow server-side aggregate for the SUPER_ADMIN overview."""
from alembic import op
import sqlalchemy as sa

revision = '0040_admin_overview_aggregate'
down_revision = '0039_generic_sync_kinds'
branch_labels = depends_on = None

FUNCTION = """
CREATE OR REPLACE FUNCTION public.mentorflow_admin_account_counts(p_issuer text, p_human_subjects text[])
RETURNS jsonb
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $fn$
WITH canonical AS (
  SELECT DISTINCT subject_id FROM public.rehydration_manifests
  WHERE issuer = p_issuer AND initialized = true
), requested AS (
  SELECT DISTINCT value AS subject_id FROM unnest(COALESCE(p_human_subjects, ARRAY[]::text[])) AS value
), mismatch AS (
  SELECT subject_id FROM canonical EXCEPT SELECT subject_id FROM requested
  UNION
  SELECT subject_id FROM requested EXCEPT SELECT subject_id FROM canonical
), members AS (
  SELECT DISTINCT wm.subject_id FROM public.workspace_memberships wm
  JOIN canonical c ON c.subject_id = wm.subject_id
  WHERE wm.status = 'active'
)
SELECT jsonb_build_object(
  'backend_account_count', (SELECT count(*) FROM canonical),
  'workspace_member_count', (SELECT count(*) FROM members),
  'identity_account_mismatch_count', (SELECT count(*) FROM mismatch)
)
$fn$;
"""


def upgrade():
    bind = op.get_bind()
    if bind.dialect.name != 'postgresql':
        return
    if bind.execute(sa.text("SELECT current_user")).scalar_one() != 'mentorflow_migrator':
        raise RuntimeError('Admin overview migration requires mentorflow_migrator')
    # The existing migration role owns the function and is never exposed to the
    # application. The application role receives EXECUTE, never table SELECT.
    op.execute(sa.text("CREATE POLICY admin_overview_read ON public.rehydration_manifests FOR SELECT TO mentorflow_migrator USING (true)"))
    op.execute(sa.text("CREATE POLICY admin_overview_read ON public.workspace_memberships FOR SELECT TO mentorflow_migrator USING (true)"))
    op.execute(sa.text(FUNCTION))
    op.execute(sa.text("REVOKE ALL ON FUNCTION public.mentorflow_admin_account_counts(text, text[]) FROM PUBLIC"))
    op.execute(sa.text("GRANT EXECUTE ON FUNCTION public.mentorflow_admin_account_counts(text, text[]) TO mentorflow_app"))


def downgrade():
    raise RuntimeError('Forward only: retain administrator aggregate contract')
