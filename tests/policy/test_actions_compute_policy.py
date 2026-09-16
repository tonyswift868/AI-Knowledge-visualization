"""Offline tests: no network, providers, workflow execution, databases or installs."""
import importlib.util
import json
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('actions_policy', ROOT/'scripts/enterprise/verify_actions_compute_policy.py')
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)
POLICY = json.loads((ROOT/'.github/actions-compute-policy.json').read_text())
LABELS = '[self-hosted, Linux, X64, mentorflow-e1, turkey-ci]'
NAME = '.github/workflows/example.yaml'


def source(trigger='workflow_dispatch', runner=LABELS):
    return f'name: Tests\non: {trigger}\njobs:\n  test:\n    runs-on: {runner}\n    steps:\n      - run: python -m pytest\n'


@pytest.mark.parametrize('event', ['push','pull_request','pull_request_target','schedule','workflow_run','repository_dispatch','release','issues'])
@pytest.mark.parametrize('shape', ['scalar','array','mapping'])
def test_auto_events_fail_for_all_yaml_on_forms(event,shape):
    trigger = event if shape=='scalar' else f'[{event}, workflow_dispatch]' if shape=='array' else f'\n  {event}:\n  workflow_dispatch:'
    result=guard.audit({NAME:source(trigger)},POLICY)
    assert result['status']=='FAIL' and result['counts']['heavy_auto_trigger_count']==1


@pytest.mark.parametrize('runner', ['ubuntu-latest','windows-latest','macos-latest','ubuntu-24.04','windows-2025','macos-15',
                                  '${{ matrix.os }}','${{ inputs.runner }}','[self-hosted]','{group: hosted}', ''])
def test_unapproved_hosted_dynamic_or_unknown_runner_fails(runner):
    assert guard.audit({NAME:source(runner=runner)},POLICY)['status']=='FAIL'


def test_approved_manual_runner_passes_and_guard_is_not_an_action():
    assert guard.audit({},POLICY)['status']=='FAIL'
    assert guard.audit({NAME:source()},POLICY)['status']=='PASS'
    workflows={p.relative_to(ROOT).as_posix():p.read_text() for p in (ROOT/'.github/workflows').iterdir() if p.suffix in ('.yaml','.yml')}
    result=guard.audit(workflows,POLICY)
    assert result['status']=='PASS',result['errors']
    assert result['counts']['heavy_auto_trigger_count']==0
    assert result['counts']['github_hosted_heavy_job_count']==0
    assert result['counts']['self_hosted_job_count']==34
    assert not any('verify_actions_compute_policy.py' in value for value in workflows.values())


@pytest.mark.parametrize('text', ['on: push\non: workflow_dispatch\njobs: {}', 'on: [', '[]',
                                'on: workflow_dispatch\njobs:\n  test:\n    runs-on: ubuntu-latest\n    runs-on: '+LABELS,
                                'on: workflow_dispatch\njobs:\n  test:\n    <<: {runs-on: ubuntu-latest}'])
def test_ambiguous_or_malformed_yaml_fails(text):
    assert guard.audit({NAME:text},POLICY)['status']=='FAIL'


def test_reusable_caller_cannot_restore_auto_execution():
    callee='.github/workflows/reusable.yml'
    caller='on: push\njobs:\n  invoke:\n    uses: ./'+callee+'\n'
    result=guard.audit({NAME:caller,callee:source('workflow_call')},POLICY)
    assert result['status']=='FAIL' and result['counts']['heavy_auto_trigger_count']==1
    caller=caller.replace('on: push','on: workflow_dispatch')
    assert guard.audit({NAME:caller,callee:source('workflow_call')},POLICY)['status']=='PASS'
    assert guard.audit({NAME:caller,callee:source()},POLICY)['status']=='FAIL'


def test_external_reusable_and_cycles_fail_closed():
    external='on: workflow_dispatch\njobs:\n  invoke:\n    uses: someone/repo/.github/workflows/test.yml@main\n'
    assert guard.audit({NAME:external},POLICY)['status']=='FAIL'
    recursive='on: workflow_call\njobs:\n  invoke:\n    uses: ./'+NAME+'\n'
    assert guard.audit({NAME:recursive},POLICY)['status']=='FAIL'


def test_missing_or_fake_lightweight_exception_is_not_a_bypass():
    for entry in ({},{'reason':'status only'}):
        policy={**POLICY,'lightweight_exceptions':{NAME:entry}}
        assert guard.audit({NAME:source('push')},policy)['status']=='FAIL'


def test_historical_heavy_steps_preserved_and_windows_not_misrepresented():
    baseline='89315a1f66a4af4a1522f19e3a93008f37a144ed'
    for path in (ROOT/'.github/workflows').glob('*.yml'):
        relative=path.relative_to(ROOT).as_posix()
        before=guard.parse(subprocess.check_output(['git','show',baseline+':'+relative],cwd=ROOT,text=True,encoding='utf-8'))
        after=guard.parse(path.read_text())
        assert before['jobs'].keys()==after['jobs'].keys()
        for job_id,job in before['jobs'].items():
            assert job.get('steps')==after['jobs'][job_id].get('steps'),(relative,job_id)
            assert job.get('services')==after['jobs'][job_id].get('services')
    ci=guard.parse((ROOT/'.github/workflows/enterprise-ci.yml').read_text())
    for name in ('backend-full-windows','backend-desktop','shadow-repair-contract'):
        assert ci['jobs'][name]['if']=='${{ false }}'
    security=guard.parse((ROOT/'.github/workflows/enterprise-security.yml').read_text())
    assert security['jobs']['codeql']['needs']=='scanner-lease'
    assert security['jobs']['codeql']['if']=="needs.scanner-lease.outputs.granted == 'true'"
    assert guard.parse((ROOT/'.github/dependabot.yml').read_text())['updates']==[]
