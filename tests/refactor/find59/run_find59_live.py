"""Batch 59 live CLI wrapper: the existing simulation handles every action.

Use the simulation's --live/--pause-material/--material-url flags. No live work
runs during pytest. Only the requested first-ten-point stop is injected here.
"""
import json
from pathlib import Path
import sys
from threading import Lock
from unittest.mock import patch
from refactor.learning.course import point_materials as pm
from tests.refactor.simulation.run import main
from tests.refactor.simulation.driver import Driver


def run59(argv):
    operator_material=None
    if '--operator-material' in argv:
        index=argv.index('--operator-material')
        operator_material=argv[index+1]
        argv=argv[:index]+argv[index+2:]
    directory=Path(argv[argv.index('--root')+1])
    audit=directory/'preparation59.json'
    rows=json.loads(audit.read_text(encoding='utf-8')) if audit.exists() else []
    seen={r['point_id'] for r in rows}
    lock=Lock()
    original=pm.prepare_point
    def prepare(store,plan_id,point,ctx,**kw):
        error=None
        try:return original(store,plan_id,point,ctx,**kw)
        except Exception as exc:
            error=dict(code=getattr(exc,'code',type(exc).__name__),message=str(exc));raise
        finally:
            with lock:
                bank=[q for q in store.records('question_bank',plan_id) if point['id'] in q['point_ids']
                    and q.get('role','bank')=='bank' and pm.checked_question(q)]
                if point['id'] not in seen:
                    rows.append(dict(point_id=point['id'],title=point['title'],module_id=point['module_id'],
                        count=len(bank),material=sum(q.get('question_origin')=='material' for q in bank),
                        web=sum(q.get('question_origin')=='web' for q in bank),error=error))
                    seen.add(point['id'])
                directory.mkdir(parents=True,exist_ok=True)
                audit.write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8')
                if len(rows)>=10 and sum(r['count']==0 for r in rows[:10])>=6:
                    (directory/'first-ten-stop59.json').write_text(json.dumps(rows[:10],ensure_ascii=False,indent=2),encoding='utf-8')
                    if ctx.get('cancel'):ctx['cancel'].set()
                    if ctx.get('gateway'):ctx['gateway'].halt()
    planning=Driver.planning
    def planning_with_material(driver,*args,**kw):
        nonlocal operator_material
        if operator_material:
            text=operator_material;operator_material=None
            driver.sim.evidence.event('operator_selected_recommended_materials',urls=text.split())
            driver.request('plan.say',text=text)
        return planning(driver,*args,**kw)
    with patch.object(pm,'prepare_point',prepare),patch.object(Driver,'planning',planning_with_material):
        return main(argv)


if __name__=='__main__':raise SystemExit(run59(sys.argv[1:]))
