"""Explicit offline stress runner; not collected by pytest. No new packages."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

REPO=Path(__file__).resolve().parents[3]
CASES={
    'loading86':['tests/refactor/loading86'],
    'status88':['tests/refactor/status88'],
    'self_test81':['tests/refactor/self_test81/test_self_test81_browser.py'],
    'integration_gateway':[
        'tests/refactor/integration38',
        'tests/refactor/flaky115/test_worker115_quiescence.py',
        'tests/refactor/gateway/test_gateway_cost_controls.py::test_purpose_caps_raise_only_and_background_timeout',
        'tests/refactor/gateway/test_gateway_cost_controls.py::test_timeout_keeps_larger_preset_and_foreground_unchanged'],
}

def main():
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--repeat',type=int,default=20)
    p.add_argument('--case',choices=CASES,action='append',help='Rerun only a changed bucket after a failure')
    args=p.parse_args();args.root.mkdir(parents=True,exist_ok=True)
    env=dict(os.environ,QT_QPA_PLATFORM='offscreen',QTWEBENGINE_CHROMIUM_FLAGS='--disable-gpu',PYTHONDONTWRITEBYTECODE='1')
    def bucket(name):
        results=[]
        for index in range(args.repeat):
            target=args.root/name/f'{index+1:02d}';target.mkdir(parents=True)
            local=target/'local';temp=target/'temp';local.mkdir();temp.mkdir()
            current=dict(env,LOCALAPPDATA=str(local),TEMP=str(temp),TMP=str(temp),TMPDIR=str(temp),MENTORFLOW_TEST_ROOT=str(target))
            command=[sys.executable,'-B','-m','pytest',*CASES[name],'-q','-p','no:cacheprovider',
                     '--basetemp',str(target/'pytest'),'--junitxml',str(target/'result.xml')]
            with (target/'run.log').open('w',encoding='utf8') as log:
                run=subprocess.run(command,cwd=REPO,env=current,stdout=log,stderr=log,timeout=180)
            suite=ET.parse(target/'result.xml').getroot()[0]
            results.append(dict(iteration=index+1,code=run.returncode,tests=int(suite.attrib['tests']),
                failures=int(suite.attrib['failures']),errors=int(suite.attrib['errors']),seconds=float(suite.attrib['time'])))
            (args.root/f'{name}.json').write_text(json.dumps(results,indent=2),encoding='utf8')
            if run.returncode:break
        return name,results
    with ThreadPoolExecutor(max_workers=4) as pool:
        summary=dict(pool.map(bucket,args.case or CASES))
    (args.root/'summary.json').write_text(json.dumps(summary,indent=2),encoding='utf8')
    print(json.dumps({k:dict(runs=len(v),passed=sum(r['tests']-r['failures']-r['errors'] for r in v),failures=sum(r['failures']+r['errors'] for r in v)) for k,v in summary.items()}))
    assert all(len(v)==args.repeat and all(r['code']==0 for r in v) for v in summary.values()),summary

if __name__=='__main__':main()
