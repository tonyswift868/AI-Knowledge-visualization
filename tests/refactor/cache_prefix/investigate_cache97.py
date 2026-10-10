"""Read-only cache investigation. No network unless --live is explicitly set.

Run with --root pointing to a private artifact directory containing the SQLite
backup manifest produced by snapshot(). Not collected by pytest. Credentials and
original account databases are never exported by this module.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict
import html

TZ = timezone(timedelta(hours=8))  # product catalogue/billing calendar
METERS = ('input_tokens', 'cached_input_tokens', 'uncached_input_tokens', 'output_tokens')


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def snapshot(root, sources):
    manifest = []
    for label, base in sources.items():
        for source in sorted((Path(base) / 'learning/accounts').glob('*/*.sqlite3')):
            if source.name not in ('course.sqlite3', 'model-usage.sqlite3'):
                continue
            target = root / 'copies' / label / source.parent.name / source.name
            target.parent.mkdir(parents=True, exist_ok=True)
            with sqlite3.connect(source.as_uri() + '?mode=ro', uri=True) as src:
                with sqlite3.connect(target) as dst:
                    src.backup(dst)
            manifest.append(dict(source=str(source), copy=str(target), bytes=target.stat().st_size,
                                 sha256=hashlib.sha256(target.read_bytes()).hexdigest()))
    write(root / 'snapshot-manifest.json', manifest)


def read_calls(manifest):
    unique, conflicts, coverage, duplicates = {}, [], [], 0
    for item in manifest:
        if Path(item['copy']).name != 'model-usage.sqlite3':
            continue
        with sqlite3.connect(Path(item['copy']).as_uri() + '?mode=ro', uri=True) as db:
            db.row_factory = sqlite3.Row
            rows = [dict(r) for r in db.execute('SELECT * FROM calls ORDER BY at_utc, request_id')]
        coverage.append(dict(source=item['source'], calls=len(rows),
                             first=rows[0]['at_utc'] if rows else None,
                             last=rows[-1]['at_utc'] if rows else None))
        for row in rows:
            key = (Path(item['source']).parent.name, row['request_id'])
            if key in unique:
                if unique[key] != row:
                    conflicts.append(dict(account=key[0], request_id=key[1]))
                duplicates += 1
            else:
                unique[key] = row
    return sorted(unique.values(), key=lambda r: (r['at_utc'], r['request_id'])), dict(
        coverage=coverage, duplicates=duplicates, conflicts=conflicts)


def aggregate(rows, catalog=None):
    from refactor.learning.connections.providers import Usage
    from refactor.learning.connections.pricing import timestamp
    meters = Counter()
    known_cost = Decimal(0)
    recalculated_cost = Decimal(0)
    known_cost_calls = recalculated_calls = unknown_cost_calls = measured_calls = 0
    for row in rows:
        usage = json.loads(row['usage_json']) if row['usage_json'] else None
        if usage and not set(usage.get('raw', {}).get('_mentorflow', {}).get('unreported_fields', ())).intersection(METERS):
            assert usage['input_tokens'] == usage['cached_input_tokens'] + usage['uncached_input_tokens']
            measured_calls += 1
            meters.update({k: usage[k] for k in METERS})
        cost = json.loads(row['cost_json']) if row['cost_json'] else None
        if cost and cost.get('currency') == 'CNY':
            known_cost += Decimal(str(cost['amount']))
            known_cost_calls += 1
        elif catalog is not None and usage:
            quote = catalog.cost(row['provider_id'], row['model'], Usage(**usage), at=timestamp(row['at_utc']))
            if quote and quote.currency == 'CNY':
                recalculated_cost += quote.amount
                recalculated_calls += 1
            else:
                unknown_cost_calls += 1
        else:
            unknown_cost_calls += 1
    return dict(calls=len(rows), measured_calls=measured_calls, states=dict(Counter(r['state'] for r in rows)),
                **{k: meters[k] for k in METERS},
                hit_rate=meters['cached_input_tokens']/meters['input_tokens'] if meters['input_tokens'] else None,
                recorded_cost_cny=str(known_cost), recorded_cost_calls=known_cost_calls,
                recalculated_cost_cny=str(recalculated_cost), recalculated_calls=recalculated_calls,
                unknown_cost_calls=unknown_cost_calls)


def statistics(root, since, until):
    from refactor.learning.connections.pricing import PriceCatalog
    catalog = PriceCatalog(Path(__file__).resolve().parents[3] / 'refactor/learning/course/data/model-prices.json')
    calls, audit = read_calls(json.loads((root / 'snapshot-manifest.json').read_text(encoding='utf-8')))
    start, end = datetime.fromisoformat(since).replace(tzinfo=TZ), datetime.fromisoformat(until).replace(tzinfo=TZ)
    recent = [r for r in calls if start <= datetime.fromisoformat(r['at_utc']).astimezone(TZ) < end]
    groups = defaultdict(list)
    daily = defaultdict(list)
    for row in recent:
        groups[row['purpose']].append(row)
        daily[datetime.fromisoformat(row['at_utc']).astimezone(TZ).date().isoformat()].append(row)
    groups.setdefault('profile', [])
    for index in range((end-start).days):
        daily.setdefault((start+timedelta(days=index)).date().isoformat(),[])
    without_first = sorted([r for group in groups.values() for r in group[1:]],key=lambda r:(r['at_utc'],r['request_id']))
    result = dict(window=dict(since=since, until_exclusive=until, timezone='UTC+08:00'), audit=audit,
                  total=aggregate(recent, catalog),
                  without_first_per_purpose=aggregate(without_first,catalog),
                  purposes={p: dict(all=aggregate(v, catalog), without_first=aggregate(v[1:], catalog),
                                    models=dict(Counter(r['model'] for r in v))) for p,v in sorted(groups.items())},
                  daily={d:aggregate(v,catalog) for d,v in sorted(daily.items())},
                  historical=aggregate(calls, catalog))
    write(root / 'statistics.json', result)
    components = defaultdict(lambda:defaultdict(Decimal))
    for row in recent:
        cost = json.loads(row['cost_json']) if row['cost_json'] else None
        if cost and cost.get('currency') == 'CNY':
            for key,value in cost['components'].items(): components[row['purpose']][key] += Decimal(str(value))
    write(root/'cost-components-summary.json',{p:{k:str(v) for k,v in row.items()} for p,row in components.items()})
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result


def canonical(row):
    return '<system>\n' + row['system'] + '\n</system>' + ''.join(
        '\n<' + m['role'] + '>\n' + m['content'] + '\n</' + m['role'] + '>' for m in row['messages'])


def first_difference(a, b):
    return next((i for i,(x,y) in enumerate(zip(a,b)) if x != y), min(len(a),len(b)))


class AdmissionStopped(Exception):
    pass


class ReadOnlyCourse:
    """Adapter for request capture only; admission is deliberately impossible."""
    def __init__(self, path):
        self.path = path
        self.capture_preparation = False

    def records(self, entity, plan_id):
        assert entity in ('plans','stages','modules','points','sources','source_chunks','messages','teacher_profile')
        with sqlite3.connect(self.path.as_uri()+'?mode=ro', uri=True) as db:
            if entity == 'plans':
                rows = db.execute('SELECT payload FROM plans WHERE id=?',(plan_id,))
            else:
                rows = db.execute('SELECT payload FROM '+entity+' WHERE plan_key=? ORDER BY rowid',(plan_id,))
            values = [json.loads(r[0]) for r in rows]
            values.sort(key=lambda p:(p.get('position',0),p['id']))
            if self.capture_preparation and entity == 'modules':
                for value in values: value.pop('question_extraction',None)
            return values

    def require_material_plan(self, plan_id):
        assert self.records('plans',plan_id)[0].get('material_version') == 2

    @contextmanager
    def transaction(self):
        raise AdmissionStopped('analysis never writes course data')
        yield


def reconstruct(root, *, teacher_point=False):
    """Replay current assembly on stored inputs, NOT reconstructed historical wire.

    Usage journals store no prompts. Original intake form, historical preferences
    and exact director state are absent; derived values are recorded explicitly.
    """
    from types import SimpleNamespace
    from refactor.learning.course import planner_agents as agents, point_materials
    from refactor.learning.course.teacher_agent import teacher_request
    manifest = json.loads((root/'snapshot-manifest.json').read_text(encoding='utf-8'))
    entry = next(e for e in manifest if '/source/' in e['copy'].replace('\\','/') and e['copy'].endswith('course.sqlite3'))
    store = ReadOnlyCourse(Path(entry['copy']))
    with sqlite3.connect(store.path.as_uri()+'?mode=ro',uri=True) as db:
        plan = next(json.loads(r[0]) for r in db.execute('SELECT payload FROM plans') if json.loads(r[0]).get('material_version')==2)
    pid = plan['id']
    modules = sorted(store.records('modules',pid),key=lambda m:m['position'])
    points, chunks = store.records('points',pid),store.records('source_chunks',pid)
    by_chunk = {c['id']:c for c in chunks}
    sources = {s['id']:s for s in store.records('sources',pid)}
    for chunk in chunks:
        chunk['source_title'] = sources[chunk['source_id']]['title']
    window = plan['initial_window']
    form = dict(goal=plan['goal'],final_goal=plan['final_goal'],level=plan['level'],
                start=window['start'],end=window['end'],daily_cap=window['daily_cap_minutes'],
                weekday_weights=window['weekday_weights'],notes=plan.get('notes',''))
    stages = sorted(store.records('stages',pid),key=lambda s:s['position'])
    outline = dict(stages=[dict(title=s['title'],modules=[m for m in modules if m['stage_id']==s['id']]) for s in stages])
    rows = []
    for module in modules:
        batches = agents.split_module_chunks(agents.material_paragraphs([by_chunk[cid] for cid in module['chunk_ids']]), max_chars=agents.DEFAULT_BATCH_CHARS)
        for index,batch in enumerate(batches,1):
            system,messages,_ = agents.generate_request(form,plan['goal'],outline,module,batch,
                material_map=True,batch_index=index,batch_count=len(batches),max_points=30)
            rows.append(dict(purpose='generate',module=module['title'],system=system,messages=messages))
    for module in modules:
        messages,_ = agents.point_links_request(module,points)
        rows.append(dict(purpose='point_links',module=module['title'],system=agents.POINT_LINKS_SYSTEM,messages=messages))
    class Capture:
        def complete(self,purpose,messages,*,system,**kwargs):
            rows.append(dict(purpose=purpose,module=current['title'],system=system,messages=messages))
            return SimpleNamespace(text='{"questions":[]}')
    store.capture_preparation = True  # replay initial preparation, not cached done markers
    for current in modules:
        try:
            point_materials.prepare_module(store,pid,current['id'],dict(gateway=Capture()),lambda *a:None)
        except AdmissionStopped:
            pass
    store.capture_preparation = False
    # Persisted dialogue replay, choosing the actual module with recent turns.
    module = modules[0]
    history = sorted([m for m in store.records('messages',pid) if m.get('module_id')==module['id']],key=lambda m:m['at'])
    module_points = [p for p in points if p['module_id']==module['id']]
    ctx = dict(plan=plan,module=dict(module,points=module_points),
               excerpts={cid:by_chunk[cid]['text'] for cid in module['chunk_ids']},
               director='依据当前模块原文回应学生，接着刚才的讲解。',prefs={},profile='')
    if teacher_point:
        from refactor.learning.app.classroom import _Course, Classroom
        course = _Course(store,plan)
        focus = [course.points_of(module['id'])[0]['id']]
        brief, module_brief = Classroom._skeleton(None,course,dict(module_id=module['id']),focus)
        ctx.update(plan=brief,module=module_brief,excerpts=course.excerpts(focus),
                   current_points=[dict(id=pid,title=course.point(pid)['title']) for pid in focus])
    for index,message in enumerate(history):
        if message['role'] != 'user': continue
        ctx.update(dialogue=[dict(role=m['role'],text=m['content']) for m in history[:index]],user=message['content'])
        system,messages = teacher_request(ctx)
        rows.append(dict(purpose='teacher_reopen',module=module['title'],system=system,messages=messages))
    write(root/'reconstructed.json',dict(plan_id=pid,baseline='aa1ee1db',
        teacher_scope='actual host first-point excerpts' if teacher_point else 'module-wide document prototype (larger than normal point focus)',
        limitations='Current-code replay, not historical wire. Form derived from initial_window; deleted scope/director/preferences snapshots unavailable. To replay initial preparation, adapter ignores already-done markers. Teacher uses recorded dialogue with a controlled fixed focus, not recovered historical director state. Capture gateway never calls model; course adapter rejects writes.',
        rows=rows))
    print('reconstructed',dict(Counter(r['purpose'] for r in rows)))
    return rows


def diagnose(root, tool_path):
    import sys
    sys.path.insert(0,str(tool_path))
    from prefixcash import CallRecord, build_heatmap, to_metrics
    rows = json.loads((root/'reconstructed.json').read_text(encoding='utf-8'))['rows']
    groups = defaultdict(list)
    for row in rows: groups[row['purpose']].append(row)
    result = {}
    svg = ['<svg xmlns="http://www.w3.org/2000/svg" width="1100" height="1100"><rect width="100%" height="100%" fill="white"/>',
           '<text x="15" y="24">prefixcash 0.2.1 word stability / character prefix boundaries (NOT billed tokens)</text>']
    y = 52
    for purpose,group in groups.items():
        prompts = [canonical(r) for r in group]
        # Zero counters deliberately mean no measurement in this offline plot.
        metrics = to_metrics('deepseek','deepseek-flash',{'prompt_tokens':0,'completion_tokens':0})
        heat = build_heatmap(purpose,[CallRecord(metrics,prompt) for prompt in prompts])
        differences = []
        for previous,current in zip(prompts,prompts[1:]):
            pos = first_difference(previous,current)
            differences.append(dict(character=pos,previous=previous[max(0,pos-45):pos+90],
                                    current=current[max(0,pos-45):pos+90],current_chars=len(current),
                                    prefix_character_ratio=pos/len(current)))
        result[purpose] = dict(requests=len(group), heatmap=asdict(heat),differences=differences)
        write(root/'prefixcash'/f'{purpose}.json',result[purpose])
        svg.append(f'<text x="15" y="{y}">{html.escape(purpose)}: {len(group)} requests; word positions, not tokens</text>')
        for index,cell in enumerate(heat.cells):
            colour={'hot':'#46a46b','warm':'#deb34b','cold':'#cf5757'}[cell.kind]
            svg.append(f'<rect x="{15+index*1040/max(1,len(heat.cells)):.2f}" y="{y+8}" width="{max(.1,1040/max(1,len(heat.cells))):.2f}" height="20" fill="{colour}"/>')
        y += 47
        for diff in differences[:4]:
            width = 1040*diff['prefix_character_ratio']
            svg.append(f'<rect x="15" y="{y}" width="1040" height="15" fill="#cf5757"/><rect x="15" y="{y}" width="{width:.1f}" height="15" fill="#46a46b"/><text x="15" y="{y+29}">prefix {diff["character"]} / {diff["current_chars"]} characters</text>')
            y += 42
        y += 12
    svg.append('</svg>')
    (root/'prefixcash.svg').write_text('\n'.join(svg),encoding='utf-8')
    write(root/'breakpoints.json',{p:{'requests':r['requests'],'differences':r['differences']} for p,r in result.items()})
    print('prefixcash diagrams written; offline, no API cache usage implied')


def documents_first(messages):
    """Experimental pure permutation; same messages, roles and complete content."""
    stable = [m for m in messages[1:] if m['content'].startswith(('【当前模块原文（数据）】','【计划其他已采用资料'))]
    return messages[:1] + stable + [m for m in messages[1:] if m not in stable]


def live_comparison(root, budget):
    """Explicit paid experiment in an isolated gateway copy, never original data.

    Both arms use 8192 output tokens and one attempt (cost bound); this deliberately
    differs from production's 384000 upper bound, equally in both arms. Only layout
    differs between arms. No model response is used to score its own quality.
    """
    import importlib.util
    import shutil
    import sys
    import time
    import uuid
    from dataclasses import asdict
    from refactor.learning.connections import gateway as production
    from refactor.learning.connections.pricing import PriceCatalog
    rows = [r for r in json.loads((root/'reconstructed.json').read_text(encoding='utf-8'))['rows'] if r['purpose']=='teacher_reopen'][:4]
    assert len(rows) == 4
    manifest = json.loads((root/'snapshot-manifest.json').read_text(encoding='utf-8'))
    source = next(Path(m['source']).parent for m in manifest if '/source/' in m['copy'].replace('\\','/'))
    account = source.name
    data = root/'live-data'
    target = data/'learning/accounts'/account
    target.mkdir(parents=True,exist_ok=True)
    # These two files contain preset metadata/credential references, never API keys.
    for name in ('model-gateway.json','connections.json'):
        if (source/name).exists(): shutil.copyfile(source/name,target/name)
    path = root/'gateway_experiment.py'
    code = Path(production.__file__).read_text(encoding='utf-8')
    code = code.replace("MODEL_MAX_OUTPUT = {'deepseek-v4-flash': 384000, 'deepseek-v4-pro': 384000}",
                        "MODEL_MAX_OUTPUT = {'deepseek-v4-flash': 8192, 'deepseek-v4-pro': 8192, 'deepseek-flash': 8192}")
    assert '384000' not in code
    assert 'for retry in range(3):' in code
    code = code.replace('for retry in range(3):','for retry in range(1):')
    path.write_text(code,encoding='utf-8')
    name = 'refactor.learning.connections.cache97_gateway_experiment'
    spec = importlib.util.spec_from_file_location(name,path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    catalog = PriceCatalog(Path(__file__).resolve().parents[3]/'refactor/learning/course/data/model-prices.json')
    gateway = module.ModelGateway(account,data,price_catalog=catalog)
    presets = gateway.list_presets()
    chosen = next(p for p in presets if p['provider_id']=='deepseek' and p['model'] in ('deepseek-flash','deepseek-v4-flash'))
    gateway.select_preset(chosen['id'])
    print('PAID EXPERIMENT:',chosen['display_name'],chosen['model'],'max output 8192; 8 calls maximum')
    results = []
    file = root/'live-results.json'
    if file.exists():
        raise RuntimeError('Live results already exist; refusing accidental paid rerun')
    task_root = Path(manifest[0]['copy']).parents[3]
    previous_peak = sum((Decimal(json.loads(p.read_text(encoding='utf-8'))['spent_peak_cny'])
                         for p in task_root.rglob('live-results.json')), Decimal(0))
    nonce = uuid.uuid4().hex
    charged = Decimal(0)
    try:
        for index,row in enumerate(rows):
            for arm in (('A','B') if index%2==0 else ('B','A')):
                system = '实验标识（不影响任务）：cache97-'+nonce+'-'+arm+'\n'+row['system']
                messages = row['messages'] if arm=='A' else documents_first(row['messages'])
                # UTF-8 bytes bound plus 4096 framing tokens: stricter than the
                # heuristic CJK midpoint and sufficient for these text requests.
                token_upper = len((system+''.join(m['content'] for m in messages)).encode('utf-8'))+4096
                reservation = (Decimal(token_upper)*2+Decimal(8192)*8)/Decimal(1000000)
                if charged+reservation > budget or previous_peak+charged+reservation > Decimal('2'):
                    raise RuntimeError('Budget bound would be exceeded; no request sent')
                forecast = dict(arm=arm,index=index,expectation='B should preserve module documents before growing dialogue; A reopens with documents after dialogue. Measure provider usage; no promised result.',
                                reserved_peak_cny=str(reservation),spent_peak_cny=str(charged),
                                prior_experiments_peak_cny=str(previous_peak))
                with (root/'paid-forecast.jsonl').open('a',encoding='utf-8') as stream:
                    stream.write(json.dumps(forecast,ensure_ascii=False)+'\n')
                with (root/'report.md').open('a',encoding='utf-8') as stream:
                    stream.write('\n调用前预期与上限：'+json.dumps(forecast,ensure_ascii=False)+'\n')
                request = dict(arm=arm,index=index,system=system,messages=messages,purpose='teacher',output_limit=8192)
                write(root/'live-requests'/f'{arm}-{index}.json',request)
                started = time.monotonic()
                try:
                    response = gateway.complete('teacher',messages,system=system,enable_search=False,plan_id='cache97-experiment',max_tokens=8192)
                except Exception as exc:
                    # An error may be billable; reserve the full bound and stop.
                    charged += reservation
                    results.append(dict(arm=arm,index=index,error=type(exc).__name__,code=getattr(exc,'code',''),reserved_peak_cny=str(reservation)))
                    write(file,dict(results=results,spent_peak_cny=str(charged),error='stopped after error; cost upper bound'))
                    raise
                usage = asdict(response.usage)
                peak = (Decimal(usage['cached_input_tokens'])*Decimal('.04')+
                        Decimal(usage['uncached_input_tokens'])*2+Decimal(usage['output_tokens'])*8)/Decimal(1000000)
                charged += peak
                result = dict(arm=arm,index=index,seconds=time.monotonic()-started,usage=usage,
                              text=response.text,finish_reason=response.finish_reason,peak_cost_cny=str(peak))
                results.append(result)
                write(file,dict(results=results,spent_peak_cny=str(charged),nonce=nonce))
                print(arm,index,'hit',usage['cached_input_tokens'],'/',usage['input_tokens'],'peak CNY',peak,flush=True)
                time.sleep(5)  # cache construction is asynchronous, both arms equal
    finally:
        gateway.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path)
    parser.add_argument('--since', default='2026-10-04')
    parser.add_argument('--until', default='2026-10-11')
    parser.add_argument('--reconstruct', action='store_true')
    parser.add_argument('--teacher-point', action='store_true', help='Use actual host point excerpt/skeleton assembly')
    parser.add_argument('--tool-path', type=Path)
    parser.add_argument('--live', action='store_true', help='Explicit opt-in to paid eight-call comparison')
    parser.add_argument('--budget-cny', type=Decimal, default=Decimal('2'))
    args = parser.parse_args()
    if args.root is None:
        print('SKIP: specify --root; SQLite snapshots required; no model calls by default')
        return
    statistics(args.root.resolve(), args.since, args.until)
    if args.reconstruct:
        reconstruct(args.root.resolve(), teacher_point=args.teacher_point)
    if args.tool_path:
        diagnose(args.root.resolve(), args.tool_path)
    if args.live:
        if not Decimal('0') < args.budget_cny <= Decimal('2'):
            raise ValueError('Task budget must be >0 and <= CNY 2')
        live_comparison(args.root.resolve(), args.budget_cny)


if __name__ == '__main__':
    main()
