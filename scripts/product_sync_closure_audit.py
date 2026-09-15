"""Bounded inventory of dispatch 30's existing domains, plus compatibility entries.

This inventories named business entries, not every backend route. Unrelated
providers, campaigns, games, mail and operational endpoints are outside scope.
It does not infer acceptance from a route prefix or a test count.
"""
import ast
import csv
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
DEST=ROOT/'docs/qa/MF-MAIN3-02-PRODUCT-SYNC-31'

LEGACY_NAMES=set('''create_workspace patch_workspace patch_workspace_persona delete_workspace
add_text_source add_local_source upload_source preview_archive_source import_archive_source cancel_archive_preview
add_url_source delete_source set_goal patch_goal feasibility_analyze confirm_feasibility
generate_plan activate_plan replan export_plan start_task_v2 start_task complete_task_v2
learning_run_create learning_run_message learning_run_quick_quiz learning_run_submit learning_run_hint
complete_task create_session message_session evaluate_lesson_v2 evaluate_lesson'''.split())
ACTIVE_NAMES=set('''upload_material _set_material_disposition delete_material generate_plan _refresh_plan_calendar
_complete_execution_task _v1_self_attested_completion _capture_knowledge_progress _materialize_review_occurrence
start_review submit_review save_receipt _sync_receipt_learner_preference_profile _record_message
update_learning_workspace'''.split())
LOCAL={'preview_archive_source','cancel_archive_preview','export_plan'}


def functions(path):
    tree=ast.parse((ROOT/path).read_text(encoding='utf-8-sig'))
    for node in tree.body:
        if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef)):
            routes=[]
            for decorator in node.decorator_list:
                if isinstance(decorator,ast.Call) and isinstance(decorator.func,ast.Attribute) and decorator.func.attr in {'post','put','patch','delete'}:
                    routes.append(decorator.func.attr.upper()+' '+ast.literal_eval(decorator.args[0]))
            yield node.name,node.lineno,'; '.join(routes)


def main():
    DEST.mkdir(parents=True,exist_ok=True)
    previous=list(csv.DictReader((ROOT/'docs/qa/MF-MAIN3-02-PRODUCT-SYNC-30/domain-write-matrix.csv').open(encoding='utf-8')))
    frozen={status:[row['DOMAIN'] for row in previous if row['CURRENT_STATUS']==status] for status in ('MISSING','PARTIAL')}
    (DEST/'previous-gaps.json').write_text(json.dumps(frozen,indent=2)+'\n',encoding='utf-8')
    entries=[]
    def add(path,name,line,route,status,note):
        entries.append(dict(file=path,entry=name,line=line,route=route,status=status,contract=note))
    for path,names,contract in (
        ('backend/app/main.py',LEGACY_NAMES,'legacy compatibility + existing ORM producer'),
        ('backend/app/active_teaching.py',ACTIVE_NAMES,'checkpoint 30 WIRED domain entry; existing table producer preserved'),
    ):
        found=set()
        for name,line,route in functions(path):
            if name not in names:continue
            found.add(name)
            add(path,name,line,route,'LOCAL_ONLY_BY_CONTRACT' if name in LOCAL else 'WIRED',
                'device preview/export; no server business state' if name in LOCAL else contract)
        if found!=names:raise RuntimeError('Named business entry disappeared: '+repr(names-found))
    for path in ('backend/app/plan_product_api.py','backend/app/review_product_v1/api.py',
                 'backend/app/product_settings_api.py','backend/app/product_resources_api.py'):
        for name,line,route in functions(path):
            if route:add(path,name,line,route,'WIRED','typed durable state + command receipt + existing producer/ACK')
    # These are the normal desktop preference and Vault entry points, not helper APIs.
    for path,names,contract in (
        ('desktop/main_window.py',{'_open_news_preferences','_set_task_color'},'explicit account setting mapping'),
        ('desktop/account_vault.py',{'import_managed','import_linked'},'same Vault transaction metadata intent; local API producer'),
    ):
        tree=ast.parse((ROOT/path).read_text(encoding='utf-8-sig'))
        found=set()
        for node in ast.walk(tree):
            if isinstance(node,ast.FunctionDef) and node.name in names:
                found.add(node.name)
                add(path,node.name,node.lineno,'native method','WIRED',contract)
        if found!=names:raise RuntimeError('Named desktop entry disappeared: '+repr(names-found))
    path='desktop/account_vault.py'
    node=next(n for n in ast.walk(ast.parse((ROOT/path).read_text(encoding='utf-8'))) if isinstance(n,ast.FunctionDef) and n.name=='install_shared_model')
    add(path,node.name,node.lineno,'local installation','LOCAL_ONLY_BY_CONTRACT','shared device binary cache; not an account history writer')
    # Physical payload writes belong to the existing ingestion entries above.
    # Their bytes are explicitly unsupported by structured sync, not missing metadata.
    path='desktop/local_asset_lifecycle.py'
    for node in ast.walk(ast.parse((ROOT/path).read_text(encoding='utf-8'))):
        if isinstance(node,ast.FunctionDef) and node.name in {'redownload','restore_from_package','relink','relink_resource','bind_resource','bind_resource_to_asset'}:
            add(path,node.name,node.lineno,'resource recovery','LOCAL_ONLY_BY_CONTRACT','local bindings/bytes; canonical resource identity unchanged')
    counts={status:sum(row['status']==status for row in entries) for status in ('WIRED','LOCAL_ONLY_BY_CONTRACT','NOT_SUPPORTED_BY_CONTRACT','MISSING','PARTIAL')}
    with (DEST/'domain-write-entries.csv').open('w',encoding='utf-8',newline='') as output:
        writer=csv.DictWriter(output,fieldnames=list(entries[0]));writer.writeheader();writer.writerows(entries)
    (DEST/'entry-counts.json').write_text(json.dumps(dict(total=len(entries),**counts),indent=2)+'\n',encoding='utf-8')
    print(json.dumps(dict(total=len(entries),**counts)))


if __name__=='__main__':main()
