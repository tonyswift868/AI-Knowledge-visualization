"""Static compatibility projections; no file data or executable recovery log."""

LEGACY_RECORDS = {
    'legacy_workspaces': ('setting', 'workspaces',
        'id title description status character_id allow_external_knowledge persona_prompt execution_mode created_at updated_at'),
    'legacy_sources': ('resource', 'sources',
        'id workspace_id title source_type original_filename trust_level content_hash status page_count created_at updated_at language'),
    'legacy_goals': ('learner_profile', 'goals', 'id workspace_id payload confirmed created_at updated_at'),
    'legacy_assessments': ('learner_profile', 'assessments', 'id workspace_id payload user_confirmed created_at'),
    'legacy_plans': ('plan', 'plans', 'id workspace_id version payload status created_at activated_at'),
    'legacy_days': ('calendar', 'study_days', 'id plan_id date payload'),
    'legacy_tasks': ('task', 'tasks', 'id study_day_id lesson_id payload'),
    'legacy_sessions': ('history', 'sessions', 'id workspace_id lesson_id payload created_at updated_at'),
}
LEGACY_RECORDS = {name: dict(kind=kind, table=table, columns=tuple(columns.split()),
                           keys=('id',), local_defaults={}, store='legacy')
                  for name, (kind, table, columns) in LEGACY_RECORDS.items()}
# These values describe device execution, not the synchronized workspace.
LEGACY_RECORDS['legacy_workspaces']['local_defaults'] = {
    'provider_name': '', 'provider_model': ''}
LEGACY_RECORDS['legacy_sources']['local_defaults'] = {
    'original_url': '', 'local_path': '', 'text': '', 'parse_error': '',
    'archive_preview_id': '', 'archive_relative_path': '', 'parent_archive_name': ''}

# Durable snapshots of existing feature runtimes and account preferences live
# in the existing ORM SQLite, captured by the same transaction triggers.
AGGREGATE_KINDS = {'product_plan_state': 'plan', 'product_review_state': 'review',
                   'product_account_settings': 'setting'}
AGGREGATE_RECORDS = {
    table: dict(kind=kind, columns=('id', 'state'), keys=('id',), local_defaults={})
    for table, kind in AGGREGATE_KINDS.items()
}
AGGREGATE_RECORDS['product_asset_metadata'] = dict(kind='resource',
    columns=('id','original_filename','media_type','sha256','size_bytes','source_kind','classification'),
    keys=('id',),local_defaults={})

# Transition mirrors retain their existing readers. The legacy session payload
# is the primary history item; these relationship rows are not counted twice.
HISTORY_MIRRORS = {
    'orm_sessions':'id workspace_id lesson_id status created_at updated_at',
    'orm_messages':'id session_id role content grounding created_at',
    'orm_citations':'id message_id source_id chunk_id page_start page_end quote',
}
AGGREGATE_RECORDS.update({table:dict(kind='history',columns=tuple(columns.split()),
    keys=('id',),local_defaults={},auxiliary=True) for table,columns in HISTORY_MIRRORS.items()})
for table,kind,columns,auxiliary in (
    ('orm_goals','learner_profile','id workspace_id goal_statement motivation deadline minutes_per_day days_per_week preferred_weekdays current_level expected_output unavailable_dates success_criteria confirmed',True),
    ('orm_feasibility','learner_profile','id workspace_id payload confirmed created_at',True),
    ('orm_mistakes','review','id workspace_id lesson_id criterion occurrences status',False),
):
    AGGREGATE_RECORDS[table]=dict(kind=kind,columns=tuple(columns.split()),keys=('id',),local_defaults={},auxiliary=auxiliary)

def install_aggregate_tables(connection):
    for table in AGGREGATE_KINDS:
        connection.execute(f'CREATE TABLE IF NOT EXISTS "{table}" (id TEXT PRIMARY KEY, state TEXT NOT NULL)')
    connection.execute('''CREATE TABLE IF NOT EXISTS product_asset_metadata (
        id TEXT PRIMARY KEY, original_filename TEXT NOT NULL, media_type TEXT NOT NULL,
        sha256 TEXT NOT NULL, size_bytes INTEGER NOT NULL, source_kind TEXT NOT NULL,
        classification TEXT NOT NULL)''')


# Existing canonical parsed-text stores: text sources have no original file.
# Keep their real rows/IDs so rehydrated source and teaching readers work.
AGGREGATE_RECORDS['orm_chunks'] = dict(kind='task',
    columns=tuple('id source_id ordinal page_start page_end heading text normalized_text content_hash'.split()),
    keys=('id',),local_defaults={},auxiliary=True)
AGGREGATE_RECORDS['active_chunks'] = dict(kind='task',
    columns=tuple('id workspace_id material_id ordinal page_start page_end paragraph_start paragraph_end char_start char_end heading content_hash text'.split()),
    keys=('id',),local_defaults={},auxiliary=True)
AGGREGATE_RECORDS['active_knowledge_memories'] = dict(kind='review',
    columns=('workspace_id','knowledge_point_id','detail_memory','updated_at'),
    keys=('workspace_id','knowledge_point_id'),local_defaults={},auxiliary=True)
