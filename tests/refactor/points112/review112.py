"""Build the self-contained, offline proposal review. No network or course writes."""
import argparse
import json
from pathlib import Path


PAGE = r'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>知识点重切 · 新旧对照</title>
<style>
:root{color-scheme:light;--bg:#f5f4f0;--paper:#fff;--ink:#25352e;--muted:#64716c;--line:#dbe2dc;--accent:#256b50;--soft:#e9f4ed;--warn:#8e4c17}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.7 "Microsoft YaHei","Segoe UI",sans-serif}
body.dark{color-scheme:dark;--bg:#18221e;--paper:#202e27;--ink:#e6eee8;--muted:#b0c1b5;--line:#405449;--accent:#9bd6b4;--soft:#2b4638;--warn:#f4c289}
header{padding:32px max(24px,calc((100% - 1460px)/2));background:var(--paper);border-bottom:1px solid var(--line)}
h1{font-size:28px;line-height:1.4;margin:6px 0 12px;letter-spacing:.02em}h2{font-size:20px;margin:0 0 10px}h3{font-size:17px;margin:0}p{margin:8px 0}
.eyebrow,.muted{color:var(--muted)}.eyebrow{font-size:12px;letter-spacing:.15em}.notice{padding:12px 16px;border-left:4px solid var(--accent);background:var(--soft);max-width:1050px}
.notice.warning{border-color:var(--warn);color:var(--warn)}button,input{font:inherit;color:inherit}button{cursor:pointer;background:var(--paper);border:1px solid var(--line);border-radius:7px;padding:7px 12px}button:hover{border-color:var(--accent)}button:focus-visible,input:focus-visible{outline:3px solid var(--accent);outline-offset:2px}
.topline,.row{display:flex;gap:12px;align-items:center;justify-content:space-between}.metrics{display:flex;gap:12px;flex-wrap:wrap;margin-top:20px}.metric{min-width:142px;background:var(--bg);padding:10px 16px;border-radius:8px}.metric b{display:block;font-size:24px}.metric span{font-size:12px;color:var(--muted)}
main{max-width:1508px;margin:auto;padding:24px}.tabs{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:18px}.tabs button.active{background:var(--accent);color:var(--paper);border-color:var(--accent)}
.module-head{padding:20px;background:var(--paper);border:1px solid var(--line);border-radius:12px;margin-bottom:20px}.module-stats{color:var(--muted)}
.columns{display:grid;grid-template-columns:minmax(270px, .85fr) minmax(520px,2fr);gap:22px;align-items:start}.old-panel{position:sticky;top:16px;max-height:calc(100vh - 32px);display:flex;flex-direction:column;background:var(--paper);border:1px solid var(--line);border-radius:12px;overflow:hidden}.panel-head{padding:18px;border-bottom:1px solid var(--line)}input{width:100%;padding:8px 10px;border:1px solid var(--line);border-radius:6px;background:var(--bg);margin-top:10px}.old-list{overflow:auto;padding:10px}
.old{padding:13px 12px;border:1px solid var(--line);border-radius:8px;margin:8px 0}.old-title{display:block;text-align:left;width:100%;padding:0;border:0;background:none;font-weight:600}.badge{font-size:12px;display:inline-block;padding:1px 7px;border-radius:4px;background:var(--soft);color:var(--accent);margin:6px 0}.dest{display:inline-block;font-size:12px;padding:3px 7px;margin:2px 4px 2px 0;background:var(--bg)}
.new-panel{min-width:0}.point,.navigation{border:1px solid var(--line);border-radius:12px;background:var(--paper);margin-bottom:16px;scroll-margin-top:20px}.point>summary,.navigation>summary{cursor:pointer;padding:18px 20px;font-size:18px;font-weight:600}.duration{float:right;font-size:13px;font-weight:400;color:var(--muted);margin-left:10px}.body{padding:0 22px 22px}.goal{padding:11px 14px;background:var(--soft);border-radius:7px}.sub{border-top:1px solid var(--line);margin-top:18px;padding-top:15px}.sub ul{margin:8px 0;padding-left:22px}.sub li{margin:10px 0;white-space:pre-wrap;overflow-wrap:anywhere}.cite{font-size:12px;border:0;color:var(--accent);background:var(--soft);padding:1px 6px;margin-left:6px}.nav-entry{padding:14px 0;border-top:1px solid var(--line)}.highlight{box-shadow:0 0 0 3px var(--accent)}.empty{padding:35px;text-align:center;background:var(--paper);border:1px dashed var(--line);border-radius:12px}
dialog{max-width:900px;width:calc(100% - 32px);max-height:85vh;background:var(--paper);color:var(--ink);border:1px solid var(--line);border-radius:12px;padding:24px}dialog::backdrop{background:#0008}.source-body{white-space:pre-wrap;overflow-wrap:anywhere;background:var(--bg);padding:18px;border-radius:8px;font:14px/1.9 "Microsoft YaHei",sans-serif;max-height:55vh;overflow:auto}.source-meta{color:var(--muted);font-size:12px;overflow-wrap:anywhere}
footer{margin:30px 0;color:var(--muted);font-size:12px}@media(max-width:850px){.columns{grid-template-columns:1fr}.old-panel{position:static;max-height:400px}h1{font-size:23px}.duration{float:none;display:block}header{padding:22px}main{padding:16px}}
</style></head><body><header><div class="topline"><span class="eyebrow">MENTORFLOW · 方案审阅</span><button id="theme">切换亮暗</button></div>
<h1>从当前位置开始，按概念重新组织</h1><p class="muted" id="scope"></p>
<div class="notice" id="notice">这是待确认的方案。没有写回用户数据库，没有改变已学进度或实际计划时长。</div>
<div class="metrics" id="metrics"></div></header><main><nav class="tabs" id="tabs" aria-label="模块"></nav>
<section class="module-head"><h2 id="module-title"></h2><div class="module-stats" id="module-stats"></div></section>
<div class="columns"><aside class="old-panel"><div class="panel-head"><h2>原来的知识点</h2><span class="muted">点旧点查看去向；点去向跳到右侧。</span><input id="search" placeholder="搜索旧点标题" aria-label="搜索旧点标题"></div><div class="old-list" id="old-list"></div></aside>
<section class="new-panel" id="new-list" aria-label="新的结构"></section></div>
<footer>导航不讲、不考、不算时长。分钟数为大知识点的学习量估计，尚未实测。覆盖是对旧点出处段落的程序核对，不能代替内容质量审查。原文仅随页面展示，不会联网加载图片或脚本。</footer></main>
<dialog id="source"><div class="row"><h2 id="source-title">原文出处</h2><button id="close-source">关闭</button></div><p class="source-meta" id="source-meta"></p><pre class="source-body" id="source-body"></pre></dialog>
<script type="application/json" id="data">__DATA__</script><script>
const data=JSON.parse(document.getElementById('data').textContent);let current=null;
const el=(tag,cls,text)=>{const n=document.createElement(tag);if(cls)n.className=cls;if(text!==undefined)n.textContent=text;return n};
const $=id=>document.getElementById(id);const totals=data.modules.reduce((a,m)=>{a.old+=m.old_points.length;a.points+=m.points.length;a.nav+=m.navigation.length;a.merged+=m.merged_count;a.missing+=m.uncovered.length;return a},{old:0,points:0,nav:0,merged:0,missing:0});
$('scope').textContent=data.scope_text;
if(!data.complete){$('notice').classList.add('warning');$('notice').textContent='部分方案：'+data.status_text+'。未处理的模块不会显示成已完成。用户数据保持不变。'}
for(const [name,value] of [['旧点 → 大知识点',totals.old+' → '+totals.points],['资料导航',totals.nav],['归并旧点',totals.merged],['未覆盖旧点',totals.missing]]){const c=el('div','metric');c.append(el('b','',value),el('span','',name));$('metrics').append(c)}
function citation(ref){const b=el('button','cite','['+ref+']');b.onclick=()=>{const p=current.paragraphs[ref];$('source-title').textContent='原文段落 ['+ref+']';$('source-meta').textContent=(p.source_title||'原资料')+' · '+(p.section||[]).join(' / ')+' · 原段落 '+p.id;$('source-body').textContent=p.text;$('source').showModal()};return b}
function goto(id){const n=$('entry-'+id);if(!n)return;const detail=n.tagName==='DETAILS'?n:n.closest('details');if(detail)detail.open=true;n.scrollIntoView({behavior:'smooth',block:'start'});n.classList.add('highlight');setTimeout(()=>n.classList.remove('highlight'),2200)}
function renderOld(){const list=$('old-list');list.replaceChildren();if(!current)return;const term=$('search').value.toLowerCase();for(const p of current.old_points){if(!p.title.toLowerCase().includes(term))continue;const map=current.old_to_new[p.id];const card=el('article','old');const title=el('button','old-title',String(p.position+1).padStart(2,'0')+' · '+p.title);card.append(title,el('span','badge',({navigation:'转为导航',merged:'归并',point:'知识内容',uncovered:'未覆盖'})[map.disposition]||'待核对'));const info=el('div');for(const d of map.destinations){const b=el('button','dest',(d.kind==='navigation'?'导航 · ':'新点 · ')+d.title);b.onclick=()=>goto(d.id);info.append(b)}if(map.merged_with.length){const names=map.merged_with.map(k=>current.old_points.find(o=>o.id===k)).filter(Boolean).map(o=>o.position+1);info.append(el('p','muted','与旧第 '+names.join('、')+' 点共用去向'))}if(map.uncovered_refs.length)info.append(el('p','', '未覆盖：'+map.uncovered_refs.join('、')));card.append(info);title.onclick=()=>{if(map.destinations.length)goto(map.destinations[0].id)};list.append(card)}}
function selectModule(m,index){current=m;$('search').value='';[...$('tabs').children].forEach((b,i)=>b.classList.toggle('active',i===index));$('module-title').textContent='第'+(m.module_position+1)+'模块 · '+m.title;$('module-stats').textContent=m.old_points.length+' 个旧点 → '+m.points.length+' 个大知识点 · 导航 '+m.navigation.length+' 条 · 原 '+m.old_minutes+' 分 → 新学习估计 '+m.new_minutes+' 分（导航0分；不含检测）';renderOld();const list=$('new-list');list.replaceChildren();if(m.navigation.length){const nav=el('details','navigation');nav.id='navigation';nav.open=true;nav.append(el('summary','','资料导航 · 不计时'));const body=el('div','body');for(const n of m.navigation){const row=el('div','nav-entry');row.id='entry-'+n.id;row.append(el('h3','',n.title));const names=n.concepts.map(id=>m.concepts.find(c=>c.id===id)?.title).filter(Boolean);row.append(el('p','muted',names.join('；')));for(const ref of n.refs)row.append(citation(ref));body.append(row)}nav.append(body);list.append(nav)}
for(const [i,p] of m.points.entries()){const point=el('details','point');point.id='entry-'+p.id;const summary=el('summary','','新 '+(i+1)+' · '+p.title);summary.append(el('span','duration',p.minutes+' 分钟 · '+p.subpoints.length+' 个小要点'));point.append(summary);const body=el('div','body');body.append(el('p','goal','掌握目标：'+p.goal));if(p.notes_status==='not_generated_after_stop')body.append(el('p','notice warning','质量检查未通过后已停止，这个点的讲义未生成。以下只展示结构草案和依据，不能当成完成结果。'));for(const s of p.subpoints){const sub=el('section','sub');sub.append(el('h3','',s.title));const ul=el('ul');for(const b of s.bullets||[]){const li=el('li','',b.text);li.append(citation(b.ref));ul.append(li)}sub.append(ul);const refs=el('div','muted','本要点依据：');for(const r of s.refs)refs.append(citation(r));sub.append(refs);body.append(sub)}point.append(body);list.append(point)}if(!m.points.length)list.append(el('p','empty','这一范围只整理为资料导航，没有新增教学知识点。'))}
data.modules.forEach((m,i)=>{const b=el('button','','第'+(m.module_position+1)+'模块');b.onclick=()=>selectModule(m,i);$('tabs').append(b)});
for(const m of data.not_run||[]){const b=el('button','','第'+(m.position+1)+'模块 · 未处理');b.disabled=true;$('tabs').append(b)}
if(data.modules.length)selectModule(data.modules[0],0);else{$('module-title').textContent='暂无通过校验的新结构';$('new-list').append(el('div','empty',data.status_text));}
$('search').oninput=renderOld;$('theme').onclick=()=>document.body.classList.toggle('dark');$('close-source').onclick=()=>$('source').close();
</script></body></html>'''


def build(root):
    root=Path(root)
    source=json.loads((root/'input112.json').read_text(encoding='utf-8'))
    from .run112 import select_scope
    scope=select_scope(source)
    modules=[json.loads(p.read_text(encoding='utf-8')) for p in sorted(root.glob('module-*.json'))]
    done={m['module_id'] for m in modules}
    quality=json.loads((root/'stage1-review.json').read_text(encoding='utf-8')) if (root/'stage1-review.json').exists() else {}
    payload=dict(schema='mentorflow.concept-proposal.v1',plan_id=source['plan']['id'],
        complete=len(done)==len(scope) and quality.get('passed') is True,
        status_text=quality.get('reason') or ('第一段质量待人工确认' if len(done)<len(scope) else '全部范围已生成，等待用户逐项审阅'),
        scope_text='第5模块从索引7（第8个旧点）“数据库基础教材与动手实践”起；第5模块前7点及此前已学/已跳过模块原样保留。',
        source_manifest=source['manifest'],quality_review=quality,modules=modules,
        not_run=[dict(id=m['id'],position=m['position'],title=m['title'],old_count=len(ps)) for m,ps in scope if m['id'] not in done])
    (root/'new-structure.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8')
    raw=json.dumps(payload,ensure_ascii=False).replace('<',r'\u003c').replace('>',r'\u003e').replace('&',r'\u0026')
    (root/'comparison.html').write_text(PAGE.replace('__DATA__',raw),encoding='utf-8')
    return payload


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--root',type=Path,default=Path('D:/MentorFlow/.local/test-tmp/points112'))
    print('modules',len(build(parser.parse_args().root)['modules']))
