"""Offline second-round view, preserving incomplete/failed evidence explicitly."""
import argparse
import json
from pathlib import Path
from tests.refactor.points112.review112 import PAGE
from tests.refactor.points112.run112 import dump, select_scope


def page(payload):
    html=PAGE.replace('资料导航 · 不计时','资料导航 · 整节用途分类 · 不计时')
    html=html.replace("card.append(info);title.onclick", """for(const s of map.sections||[]){info.append(el('p','muted',
        '章节：'+s.title+' · '+({knowledge:'教知识',navigation:'导航',mixed:'混合（按段分开）'})[s.kind]+'；'+s.reason))}
        card.append(info);title.onclick""")
    html=html.replace("renderOld();const list=$('new-list');", """const counts=m.section_counts||{};
        $('module-stats').textContent+=' · 导航节 '+(counts.navigation||0)+' · 知识节 '+(counts.knowledge||0)+' · 混合节 '+(counts.mixed||0);
        renderOld();const list=$('new-list');""")
    html=html.replace("sub.append(el('h3','',s.title));", """sub.append(el('h3','',s.title+' · '+s.minutes+' 分钟'));
        const est=s.minute_estimate||{};sub.append(el('p','muted','内容依据：'+est.characters+' 字 · '+est.questions+' 道关联题 · '+est.difficulty+'；复用 point minutes v2，不补到固定分钟。'));""")
    html=html.replace('质量检查未通过后已停止，这个点的讲义未生成。','本阶段已停止，这个点的讲义未生成。')
    html=html.replace('分钟数为大知识点的学习量估计，尚未实测。','分钟数为现有内容估时函数的结果（小要点求和），不是人为补足20～40分钟；尚未实测学习时长。')
    raw=json.dumps(payload,ensure_ascii=False).replace('<',r'\u003c').replace('>',r'\u003e').replace('&',r'\u0026')
    return html.replace('__DATA__',raw)


def build(root):
    from refactor.learning.course import planner_agents as a
    root=Path(root);source=json.loads((root/'input112.json').read_text(encoding='utf-8'))
    scope=select_scope(source);modules=[]
    for m,old in scope:
        output=root/f'module-{m["position"]+1}.json'
        merged=root/'work'/f'module-{m["position"]+1}-merged.json'
        if not output.exists() and not merged.exists():continue
        row=json.loads((output if output.exists() else merged).read_text(encoding='utf-8'))
        if not output.exists():
            notes_path=root/'work'/f'module-{m["position"]+1}-notes.json'
            notes={p['id']:p for p in json.loads(notes_path.read_text(encoding='utf-8'))} if notes_path.exists() else {}
            row['points']=[notes.get(p['id'],dict(p,notes_status='not_generated_after_stop')) for p in row['points']]
        row.update(old_points=old,module_position=m['position'],old_minutes=sum(p['minutes'] for p in old),
                   new_minutes=sum(p['minutes'] for p in row['points']))
        row['section_counts']={kind:sum(s['kind']==kind for s in row['sections']) for kind in ('navigation','knowledge','mixed')}
        paragraphs,required=a.concept_materials(old,source['chunks'])
        for p in old:
            row['old_to_new'][p['id']]['sections']=[dict(id=s['id'],title=s['title'],kind=s['kind'],reason=s['reason'])
                for s in row['sections'] if set(required[p['id']]).intersection(s['refs'])]
        for paragraph in row['paragraphs'].values():paragraph.pop('block_fragments',None)
        modules.append(row)
    quality=json.loads((root/'stage1-review.json').read_text(encoding='utf-8')) if (root/'stage1-review.json').exists() else {}
    done={m['module_id'] for m in modules}
    payload=dict(schema='mentorflow.concept-proposal.v2',plan_id=source['plan']['id'],
        complete=len(done)==len(scope) and quality.get('passed') is True,
        status_text=quality.get('reason','第一段待人工核对'),quality_review=quality,source_manifest=source['manifest'],
        scope_text='第5模块第8旧点“数据库基础教材与动手实践”起；此前已学/跳过保留，不改进度。',
        modules=modules,not_run=[dict(id=m['id'],position=m['position'],title=m['title'],old_count=len(ps)) for m,ps in scope if m['id'] not in done])
    calls=root/'audit/calls.json'
    if calls.exists():
        payload['calls']=[{k:v for k,v in c.items() if k in ('id','seconds','peak_cost_cny','time_cost_cny','charged_bound','error','usage','tokens')}
                          for c in json.loads(calls.read_text(encoding='utf-8'))]
    dump(root/'new-structure.json',payload)
    (root/'comparison.html').write_text(page(payload),encoding='utf-8')
    return payload


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,default=Path('D:/MentorFlow/.local/test-tmp/points113'))
    build(p.parse_args().root)
