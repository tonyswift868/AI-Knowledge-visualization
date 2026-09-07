"""Read-only local/VPS guard. Never calls GitHub, starts jobs or installs tools.

Conservative classification: every workflow is heavy unless an explicit reviewed
lightweight exception exists. Counts are job definitions, NOT expanded matrix runs;
auto counts are workflow/event pairs. workflow_call is an edge, not an entry event.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import subprocess

import yaml


class StrictLoader(yaml.BaseLoader):
    """Preserve YAML 'on' as a string; reject ambiguous duplicate/merge keys."""


def mapping(loader, node):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node)
        if not isinstance(key, str) or key in result or key == '<<':
            raise ValueError('duplicate, complex or merge YAML key')
        result[key] = loader.construct_object(value_node)
    return result


StrictLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, mapping)


def parse(source):
    value = yaml.load(source, Loader=StrictLoader)
    if not isinstance(value, dict):
        raise ValueError('workflow must be a mapping')
    return value


def events(value):
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, (dict, list)) and all(isinstance(k, str) for k in value):
        return list(value)
    raise ValueError('invalid on declaration')


def runner_type(value, job):
    if isinstance(value, list) and 'self-hosted' in value:
        return 'SELF_HOSTED'
    raw = str(value)
    if '${{' in raw:
        raw += str(job.get('strategy', {}).get('matrix', {}))
    if re.search(r'(ubuntu|windows|macos)-', raw, re.I):
        return 'GITHUB_HOSTED'
    return 'UNKNOWN'


def audit(sources, policy):
    errors, records, edges = [], [], {}
    if not sources:
        errors.append('no workflow sources found; wrong root or incomplete checkout')
    counts = dict(all_workflow_count=len(sources), auto_github_compute_trigger_count=0,
                  heavy_auto_trigger_count=0, github_hosted_job_count=0,
                  github_hosted_heavy_job_count=0, self_hosted_job_count=0, unknown_runner_job_count=0)
    if policy.get('default_classification') != 'heavy' or policy.get('allowed_entry_events') != ['workflow_dispatch', 'workflow_call']:
        errors.append('policy must default to heavy and allow only manual/call events')
    approved = policy.get('approved_runner_labels')
    if not isinstance(approved, list) or 'self-hosted' not in approved or len(approved) < 2:
        errors.append('policy requires explicit self-hosted project labels')
    exemptions = policy.get('lightweight_exceptions', {})
    for name in exemptions:
        if name not in sources:
            errors.append(f'{name}: stale lightweight exception')
    for name, source in sorted(sources.items()):
        try:
            data = parse(source)
            trigger = data.get('on')
            names = events(trigger)
            if not names:
                raise ValueError('missing trigger')
            jobs = data.get('jobs')
            if not isinstance(jobs, dict) or not jobs:
                raise ValueError('missing jobs')
            exception = exemptions.get(name)
            heavy = exception is None
            if exception is not None:
                if not isinstance(exception, dict) or not str(exception.get('reason', '')).strip():
                    errors.append(f'{name}: exception needs a reviewed reason')
                # A name alone cannot disguise scans/tests or a reusable heavy caller.
                if re.search(r'pytest|vitest|codeql|semgrep|gitleaks|sbom|sast|npm\s+(test|audit)|docker\s+build|k6|workflow', str(jobs), re.I):
                    errors.append(f'{name}: lightweight exception contains heavy or delegated work')
            auto = [event for event in names if event not in ('workflow_dispatch', 'workflow_call')]
            counts['auto_github_compute_trigger_count'] += len(auto)
            if heavy:
                counts['heavy_auto_trigger_count'] += len(auto)
                if auto:
                    errors.append(f'{name}: automatic heavy entry {auto}')
            runs, heavy_jobs = {}, []
            edges[name] = []
            for job_id, job in jobs.items():
                if not isinstance(job, dict):
                    raise ValueError(f'invalid job {job_id}')
                label = f'{name}:{job_id}'
                if heavy:
                    heavy_jobs.append(job_id)
                if 'uses' in job:
                    target = job['uses']
                    if not isinstance(target, str) or not target.startswith('./.github/workflows/') or target[2:] not in sources:
                        errors.append(f'{label}: external/unresolved reusable workflow forbidden')
                    else:
                        edges[name].append(target[2:])
                        if 'workflow_call' not in events(parse(sources[target[2:]]).get('on')):
                            errors.append(f'{label}: target is not workflow_call-enabled')
                    runs[job_id] = {'uses': target, 'runner_environment': 'REUSABLE_CALL'}
                    continue
                runner = job.get('runs-on')
                kind = runner_type(runner, job)
                counts[{'SELF_HOSTED':'self_hosted_job_count','GITHUB_HOSTED':'github_hosted_job_count','UNKNOWN':'unknown_runner_job_count'}[kind]] += 1
                if heavy and kind == 'GITHUB_HOSTED':
                    counts['github_hosted_heavy_job_count'] += 1
                if heavy and runner != approved:
                    errors.append(f'{label}: must use exact approved self-hosted labels, even when disabled')
                runs[job_id] = dict(name=job.get('name', job_id), runs_on=runner,
                                    runner_environment=kind, condition=job.get('if'))
            records.append(dict(workflow_file=name, workflow_name=data.get('name'), triggers=names,
                runs_on=runs, heavy_jobs=heavy_jobs, uses_codeql='github/codeql-action/' in source,
                uses_github_hosted=any(v['runner_environment']=='GITHUB_HOSTED' for v in runs.values()),
                uses_self_hosted=any(v['runner_environment']=='SELF_HOSTED' for v in runs.values()),
                schedule=trigger.get('schedule') if isinstance(trigger,dict) else None,
                workflow_run_dependency=trigger.get('workflow_run') if isinstance(trigger,dict) else None,
                status='FAIL' if any(e.startswith(name+':') for e in errors) else 'PASS'))
        except (ValueError, TypeError, yaml.YAMLError) as exc:
            errors.append(f'{name}: invalid YAML/contract: {exc}')
    def visit(name, stack):
        if name in stack:
            raise ValueError('cyclic reusable workflow graph')
        for target in edges.get(name, []):
            visit(target, stack | {name})
    try:
        for name in edges:
            visit(name, set())
    except ValueError as exc:
        errors.append(str(exc))
    return dict(status='FAIL' if errors else 'PASS', counts=counts, workflows=records, errors=errors)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument('--ref', help='Read-only historical Git reference for before audit')
    args = parser.parse_args()
    root = args.root.resolve()
    policy = json.loads((root/'.github/actions-compute-policy.json').read_text(encoding='utf-8'))
    if args.ref:
        paths = subprocess.check_output(['git','ls-tree','-r','--name-only',args.ref,'--','.github/workflows'],cwd=root,text=True).splitlines()
        sources = {p:subprocess.check_output(['git','show',f'{args.ref}:{p}'],cwd=root,text=True,encoding='utf-8') for p in paths if Path(p).suffix in ('.yml','.yaml')}
    else:
        sources = {p.relative_to(root).as_posix():p.read_text(encoding='utf-8') for p in (root/'.github/workflows').iterdir() if p.suffix in ('.yml','.yaml')}
    result = audit(sources, policy)
    if not args.ref:
        dependabot = root/'.github/dependabot.yml'
        if dependabot.exists() and parse(dependabot.read_text(encoding='utf-8')).get('updates') != []:
            result['errors'].append('Dependabot scheduled updates must remain disabled; use local/VPS dependency review')
            result['status'] = 'FAIL'
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['status']=='PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
