"""Final paid component replay after a new false negative in the second cohort.

Explicit opt-in, same cumulative budget, no rewriting classroom events.
"""
from contextlib import ExitStack
import json
import os

from .class_eval import ROOT, live_gateway, dump, audit
from refactor.learning.connections.errors import ModelError
from refactor.learning.course import teacher_agent


def run():
    if os.environ.get("MF_CLASS_EVAL")!="1":
        raise RuntimeError("Paid replay requires MF_CLASS_EVAL=1")
    repeats=int(os.environ.get("MF_CLASS_EVAL_REPLAYS","3"))
    if repeats not in (1,2,3):
        raise ValueError("MF_CLASS_EVAL_REPLAYS must be 1, 2 or 3")
    output=ROOT/"targeted"
    output.mkdir(parents=True,exist_ok=True)
    dump(output/"design.json",dict(repeats=repeats,reason="Final targeted replays share the original CNY 10 cap; never reset budget.json"))
    results=[]
    with ExitStack() as stack:
        gateway=live_gateway(stack,output)
        for purpose,identifier,module in (("teacher","call-0622",teacher_agent),
                                          ("teacher","call-0577",teacher_agent)):
            original=json.loads((ROOT/"after/calls"/(identifier+".json")).read_text(encoding="utf-8"))
            system=module.TEACHER_SYSTEM+"\n【固定教学偏好】\n"+original["system"].split("\n【固定教学偏好】\n",1)[1]
            for attempt in range(repeats):
                marker=output/f"{purpose}-{identifier}-{attempt}.json"
                if marker.exists():
                    results.append(json.loads(marker.read_text(encoding="utf-8")))
                    continue
                gateway.case,gateway.operation="final-repair",f"{identifier}-{attempt}"
                try:
                    # Historical teacher-only replay retains its budget cap.
                    cap=min(original["max_tokens"],4096)
                    result=gateway.complete(purpose,original["messages"],system=system,max_tokens=cap)
                    row=dict(before=identifier,after=gateway.calls[-1]["id"],purpose=purpose,text=result.text,max_tokens=cap)
                except ModelError as exc:
                    row=dict(before=identifier,purpose=purpose,error=exc.code,passed=False)
                dump(marker,row)
                results.append(row)
                dump(output/"results.json",results)
        dump(output/"results.json",results)
        from .report_eval import format_results
        calls=[json.loads(path.read_text(encoding="utf-8")) for path in sorted((output/"calls").glob("*.json"))]
        dump(output/"formats.json",format_results(calls,"after"))
        audit(gateway,output)
    return output


if __name__=="__main__":
    run()
