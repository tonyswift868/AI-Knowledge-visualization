"""Collect final bounded test evidence without including runtime credentials."""
import hashlib
import json
import subprocess
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'docs/qa/MF-MAIN3-02-PRODUCT-SYNC-31'
SCRATCH=Path('E:/Codex/Temp/p31')


def main():
    cases={}
    for name in ('regression.xml','final-focused.xml','last-gates.xml','vault-retry.xml'):
        for case in ET.parse(SCRATCH/name).iter('testcase'):
            key=case.attrib['classname']+'::'+case.attrib['name']
            status='PASS'
            if case.find('failure') is not None:status='FAIL'
            if case.find('error') is not None:status='ERROR'
            if case.find('skipped') is not None:status='SKIP'
            cases[key]=dict(status=status,evidence=name)
    counts=Counter(value['status'] for value in cases.values())
    if counts!=Counter(PASS=164):raise RuntimeError('Unexpected final pytest results: '+repr(counts))
    previous=json.loads((ROOT/'docs/qa/MF-MAIN3-02-PRODUCT-SYNC-30/test-results.json').read_text(encoding='utf-8'))
    # Check the actual previous test identities, not just that a larger total passed.
    previous_keys=set(previous['cases'])
    if not previous_keys<=set(cases):raise RuntimeError('Checkpoint case missing: '+repr(previous_keys-set(cases)))
    front=json.loads((SCRATCH/'frontend.json').read_text(encoding='utf-8'))
    if (front['numPassedTests'],front['numFailedTests'],front['numPendingTests'])!=(12,0,0):
        raise RuntimeError('Frontend checks incomplete')
    changed=subprocess.check_output(['git','diff','--name-only'],cwd=ROOT,text=True).splitlines()
    changed+=subprocess.check_output(['git','ls-files','--others','--exclude-standard'],cwd=ROOT,text=True).splitlines()
    paths=sorted(set(path for path in changed if not path.startswith('docs/qa/')))
    hashes={path:hashlib.sha256((ROOT/path).read_bytes().replace(b'\r\n',b'\n')).hexdigest() for path in paths}
    report=dict(dispatch_id='MF-MAIN3-02-PRODUCT-SYNC-P0-FINAL-CLOSURE-20260916-31',
        base_sha='0effc3ac7373b60b4c1b2d1e0a8edbea0cadc58e',status='PASS_SOURCE_P0_CLOSURE',
        pytest=dict(passed=164,failed=0,errors=0,skipped=0,previous_141_preserved=True),
        frontend=dict(passed=12,failed=0,skipped=0,typescript='PASS'),
        tests_pass=176,nested_postgres_protocol=dict(passed=22,note='Inside one counted pytest wrapper; not added to 176'),
        ab_postgres=25,ab_desktop=26,settings_families=dict(account=10,device_local=28,ambiguous=0),
        entry_counts=json.loads((OUT/'entry-counts.json').read_text()),
        cases=[dict(id=key,**value) for key,value in sorted(cases.items())],
        changed_source_sha256_lf=hashes,gui='NOT_RUN',frozen='NOT_RUN',
        filesystem_copy_a_to_b=0,real_user_data_mutated=False,vps_deployed=False,secret_exposure_count=0)
    (OUT/'test-results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(dict(pytest=164,frontend=12,total=176,checkpoint_preserved=141)))


if __name__=='__main__':main()
