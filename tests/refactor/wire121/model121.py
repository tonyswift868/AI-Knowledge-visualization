"""Synthetic section/notes replies for existing offline gateway fixtures."""
import json
from types import SimpleNamespace


def directory_reply(purpose,messages,system):
    try:packet=json.loads(messages[0]['content'])
    except (ValueError,KeyError,IndexError):return None
    if purpose=='generate' and isinstance(packet,dict) and 'sections' in packet:
        value=[dict(id=s['id'],reason='原文知识章节',knowledge=[p['id'] for p in s['paragraphs']],navigation=[])
               for s in packet['sections']]
    elif purpose=='point_materials' and isinstance(packet,dict) and 'paragraphs' in packet and 'points' in packet:
        value=dict(points=[dict(id=p['id'],subpoints=[dict(id=s['id'],bullets=[
            dict(ref=s['refs'][0],text=t+'。') for t in packet['paragraphs'][s['refs'][0]].split('。')[:2]])
            for s in p['subpoints']]) for p in packet['points']])
    elif purpose=='point_materials' and isinstance(packet,dict) and 'subpoints' in packet:
        value=dict(subpoints=[dict(id=s['id'],cloze=[]) for s in packet['subpoints']])
    else:return None
    return SimpleNamespace(text=json.dumps(value,ensure_ascii=False),search_hits=(),citations=())
