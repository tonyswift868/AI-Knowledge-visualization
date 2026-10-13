"""Explicit --live only; no product writes, login or synchronization."""
import argparse
import json
from decimal import Decimal
from pathlib import Path
from refactor.learning.course import planner_agents as a
from tests.refactor.points112.run112 import PaidCalls, PLAN, dump, select_scope, snapshot

ROOT = Path('D:/MentorFlow/.local/test-tmp/points113')
ACCOUNT = 'mfa1_17fc829187d21932c9e27bfcf8a9ecac0bc698565aebf16c90c0f27428aac932'


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=ROOT)
    parser.add_argument('--snapshot',action='store_true')
    parser.add_argument('--live',action='store_true')
    parser.add_argument('--rest',action='store_true')
    parser.add_argument('--account-id',default=ACCOUNT)
    parser.add_argument('--preset-root',type=Path,default=Path('D:/MentorFlowData/account-shell'))
    parser.add_argument('--budget',default='5')
    args=parser.parse_args(argv); root=args.root.resolve()
    if not root.is_relative_to(ROOT.parent.resolve()):parser.error('仅在D盘任务目录运行')
    if args.snapshot:snapshot(root,args.account_id)
    if not (root/'input112.json').exists():print('SKIP: requires snapshot; no live call');return 0
    data=json.loads((root/'input112.json').read_text(encoding='utf-8'))
    # Only our backup is opened writable by CourseStore, never the source.
    if 'questions' not in data:
        from refactor.learning.course.store import CourseStore
        with CourseStore(args.account_id,root/'snapshot') as store:data['questions']=store.records('question_bank',PLAN)
        dump(root/'input112.json',data)
    scope=select_scope(data)
    for m,ps in scope:
        paragraphs,required=a.concept_materials(ps,data['chunks'])
        sections=a.concept_sections(paragraphs)
        print(m['title'],len(ps),'sections',len(sections),'structural navigation',sum(s['decision']=='structure' for s in sections),flush=True)
    if not args.live:print('SKIP live: --live required');return 0
    if args.rest and json.loads((root/'stage1-review.json').read_text(encoding='utf-8')).get('passed') is not True:
        parser.error('第一段未人工通过，禁止后续模型调用')
    caller=PaidCalls(root,args.account_id,args.preset_root,Decimal(args.budget))
    try:
        for m,ps in (scope[1:] if args.rest else scope[:1]):
            output=root/f'module-{m["position"]+1}.json'
            if output.exists():continue
            def checkpoint(stage,value):dump(root/'work'/f'module-{m["position"]+1}-{stage}.json',value)
            result=a.rebuild_classified_concept_module(m,ps,data['chunks'],data['questions'],caller,checkpoint=checkpoint)
            result.update(old_points=ps,old_minutes=sum(p['minutes'] for p in ps),module_position=m['position'],
                          new_minutes=sum(p['minutes'] for p in result['points']))
            dump(output,result)
            print('MODULE COMPLETE',len(ps),'->',len(result['points']),result['new_minutes'],result['section_counts'],flush=True)
    finally:caller.close()
    return 0


if __name__=='__main__':raise SystemExit(main())
