"""Shared concept_v1 payload contract; no CourseStore or production writes."""
import hashlib


def apply_module(module, known_chunk_ids):
    paragraphs=module['paragraphs'];known=set(known_chunk_ids)
    def chunk(ref):
        if ref not in paragraphs:raise ValueError('Unknown paragraph alias: '+ref)
        cid=paragraphs[ref].get('parent_chunk_id')
        if cid not in known:raise ValueError('Not an existing source_chunks id: '+str(cid))
        return cid
    def ordered(refs):return list(dict.fromkeys(chunk(ref) for ref in refs))
    def identifier(kind,text):
        return module['module_id']+'-'+kind+'-'+hashlib.sha256(text.encode('utf-8')).hexdigest()[:16]
    points=[];seen=set()
    start=min(p['position'] for p in module['old_points'])
    for index,p in enumerate(module['points']):
        pid=identifier('concept',p['title']+'|'+p['goal']+'|'+','.join(r for s in p['subpoints'] for r in s['refs']))
        if pid in seen:raise ValueError('Duplicate point id')
        seen.add(pid);subs=[]
        for j,s in enumerate(p['subpoints'],1):
            if type(s.get('minutes')) is not int or s['minutes']<=0:raise ValueError('Invalid subpoint minutes')
            if not s.get('bullets'):raise ValueError('Unvalidated/missing notes: '+s['id'])
            cids=ordered(s['refs']);notes=[]
            for b in s['bullets']:
                if b['ref'] not in s['refs']:raise ValueError('Note outside subpoint provenance')
                if not isinstance(b['text'],str) or not b['text'].strip():raise ValueError('Empty note')
                if ''.join(b['text'].split()) not in ''.join(paragraphs[b['ref']]['text'].split()):
                    raise ValueError('Note not in original paragraph')
                notes.append(dict(text=b['text'],chunk_id=chunk(b['ref'])))
            subs.append(dict(id=pid+f'-s{j}',title=s['title'],minutes=s['minutes'],chunk_ids=cids,notes=notes))
        minutes=sum(s['minutes'] for s in subs)
        if minutes!=p['minutes']:raise ValueError('Point minutes must equal subpoint sum')
        points.append(dict(id=pid,module_id=module['module_id'],position=start+index,title=p['title'],
            summary=p['goal'],minutes=minutes,chunk_ids=list(dict.fromkeys(c for s in subs for c in s['chunk_ids'])),
            structure='concept_v1',goal=p['goal'],subpoints=subs))
    nav=[dict(id=identifier('nav',n['title']+'|'+','.join(n['refs'])),title=n['title'],
              chunk_ids=ordered(n['refs']),reason=n['reason']) for n in module['navigation']]
    aliases={p['id']:converted['id'] for p,converted in zip(module['points'],points)}
    aliases.update((n['id'],converted['id']) for n,converted in zip(module['navigation'],nav))
    mapping={old:dict(info,destinations=[dict(d,id=aliases[d['id']]) for d in info['destinations']])
             for old,info in module['old_to_new'].items()}
    return dict(id=module['module_id'],title=module['title'],position=module['module_position'],
        replace_point_ids=[p['id'] for p in module['old_points']],points=points,navigation=nav,old_to_new=mapping)


def apply_payload(payload,known_chunk_ids):
    if not payload.get('complete'):raise ValueError('Proposal is incomplete or quality gate failed')
    return dict(schema='mentorflow.concept-apply.v1',plan_id=payload['plan_id'],complete=True,
        source_manifest=payload['source_manifest'],modules=[apply_module(m,known_chunk_ids) for m in payload['modules']])
