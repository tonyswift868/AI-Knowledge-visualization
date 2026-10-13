"""Latest read-only backup -> staged concept proposal. Never writes user data."""
import argparse
import json
from decimal import Decimal
from pathlib import Path
from refactor.learning.course import planner_agents as a
from tests.refactor.points112.run112 import PaidCalls, PLAN, dump, select_scope, snapshot

ROOT=Path('D:/MentorFlow/.local/test-tmp/points114')
ACCOUNT='mfa1_17fc829187d21932c9e27bfcf8a9ecac0bc698565aebf16c90c0f27428aac932'


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=ROOT)
    parser.add_argument('--snapshot',action='store_true')
    parser.add_argument('--live',action='store_true')
    parser.add_argument('--rest',action='store_true')
    parser.add_argument('--account-id',default=ACCOUNT)
    parser.add_argument('--preset-root',type=Path,default=Path('D:/MentorFlowData/account-shell'))
    parser.add_argument('--budget',default='12')
    args=parser.parse_args(argv);root=args.root.resolve()
    if not root.is_relative_to(ROOT.parent.resolve()):parser.error('仅允许D盘测试目录')
    if args.snapshot:snapshot(root,args.account_id)
    source=root/'input112.json'
    if not source.exists():print('SKIP: requires --snapshot, no model call');return 0
    data=json.loads(source.read_text(encoding='utf-8'))
    if 'questions' not in data:
        from refactor.learning.course.store import CourseStore
        with CourseStore(args.account_id,root/'snapshot') as store:data['questions']=store.records('question_bank',PLAN)
        dump(source,data)
    scope=select_scope(data)
    state=data['position']['state']
    print('saved position',state['module_id'],state['index'],data['position']['saved_at'],flush=True)
    for module,points in scope:print(module['position']+1,module['title'],len(points),flush=True)
    if not args.live:print('SKIP: explicit --live required');return 0
    if args.rest and json.loads((root/'stage1-review.json').read_text(encoding='utf-8')).get('passed') is not True:
        parser.error('第一段六项未人工全过，禁止第二段模型调用')
    if Decimal(args.budget)>12:parser.error('本批预算上限为12元')
    caller=PaidCalls(root,args.account_id,args.preset_root,Decimal(args.budget));failures=[]
    try:
        for module,points in (scope[1:] if args.rest else scope[:1]):
            output=root/f'module-{module["position"]+1}.json'
            if output.exists():continue
            def checkpoint(stage,value):dump(root/'work'/f'module-{module["position"]+1}-{stage}.json',value)
            try:
                result=a.rebuild_classified_concept_module(module,points,data['chunks'],data['questions'],caller,
                    checkpoint=checkpoint,semantic_check=True,allow_short_examples=True)
            except Exception as exc:
                failures.append(dict(module_id=module['id'],position=module['position'],code=getattr(exc,'code',type(exc).__name__),message=str(exc)))
                dump(root/'generation-errors.json',failures)
                print('MODULE FAILED',module['position']+1,type(exc).__name__,str(exc),flush=True)
                if not args.rest:raise
                if caller.gateway.halted.is_set():break
                continue
            result.update(old_points=points,module_position=module['position'],old_minutes=sum(p['minutes'] for p in points),
                new_minutes=sum(p['minutes'] for p in result['points']))
            dump(output,result)
            print('MODULE COMPLETE',module['position']+1,len(points),'->',len(result['points']),result['new_minutes'],flush=True)
    finally:caller.close()
    return 1 if failures else 0


if __name__=='__main__':raise SystemExit(main())
