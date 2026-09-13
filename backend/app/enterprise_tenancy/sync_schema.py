"""Read-only admission of the immutable 0037 sync storage contract."""
from sqlalchemy import inspect, text
from .import_ledger_schema import _expression
from .schema import rls_predicate_for


def require_structured_sync_schema(connection):
    def require(ok):
        if not ok:
            raise ValueError('structured sync schema differs from 0037')
    inspector = inspect(connection)
    scope = dict(issuer='VARCHAR(512)', subject_id='VARCHAR(128)',
                 organization_id='VARCHAR(128)', workspace_id='VARCHAR(128)')
    definitions = {
        'structured_sync_entities': (dict(scope, kind='VARCHAR(24)', entity_id='VARCHAR(160)',
            revision='INTEGER', deleted='BOOLEAN', payload='JSON'), list(scope)+['kind', 'entity_id']),
        'structured_sync_requests': (dict(scope, request_id='VARCHAR(128)', digest='VARCHAR(64)',
            response='JSON'), list(scope)+['request_id'])}
    for table, (types, pk) in definitions.items():
        require(inspector.has_table(table, schema='public'))
        columns = inspector.get_columns(table, schema='public')
        require({c['name']: str(c['type']) for c in columns} == types)
        require(all(not c['nullable'] and c.get('default') is None for c in columns))
        require(inspector.get_pk_constraint(table, schema='public')['constrained_columns'] == pk)
        checks = {c['name']: _expression(c['sqltext']) for c in inspector.get_check_constraints(table, schema='public')}
        require(checks == ({'ck_sync_revision': _expression('revision > 0'),
            'ck_sync_kind': _expression("kind = ANY (ARRAY['plan','progress','resource'])")}
            if table.endswith('entities') else {}))
        info = connection.execute(text('''SELECT pg_get_userbyid(relowner),relrowsecurity,relforcerowsecurity
            FROM pg_class WHERE oid=to_regclass(:table)'''), {'table': 'public.'+table}).one()
        require(tuple(info) == ('mentorflow_migrator', True, True))
        policies = connection.execute(text('''SELECT policyname,permissive,roles,cmd,qual,with_check
            FROM pg_policies WHERE schemaname='public' AND tablename=:table'''), {'table': table}).mappings().all()
        require(len(policies) == 1)
        p = policies[0]
        require(p['policyname'] == 'tenant_isolation_'+table and p['cmd'] == 'ALL'
                and p['permissive'] == 'PERMISSIVE' and list(p['roles']) == ['public']
                and _expression(p['qual']) == _expression(rls_predicate_for(table))
                and _expression(p['with_check']) == _expression(rls_predicate_for(table)))
        for privilege in ('SELECT', 'INSERT', 'UPDATE', 'DELETE', 'TRUNCATE', 'REFERENCES', 'TRIGGER', 'MAINTAIN'):
            allowed = connection.execute(text("SELECT has_table_privilege('mentorflow_app', :table, :privilege)"),
                dict(table='public.'+table, privilege=privilege)).scalar_one()
            require(allowed == (privilege in ('SELECT', 'INSERT', 'UPDATE')))
        require(not connection.execute(text('''SELECT EXISTS (
            SELECT 1 FROM pg_class c CROSS JOIN LATERAL aclexplode(c.relacl) a
            WHERE c.oid=to_regclass(:table) AND (a.grantee=0 OR
            pg_get_userbyid(a.grantee) NOT IN ('mentorflow_app','mentorflow_migrator') OR
            (pg_get_userbyid(a.grantee)='mentorflow_app' AND a.is_grantable)))'''),
            {'table': 'public.'+table}).scalar_one())
