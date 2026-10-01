from .gateway_cost_cache_audit import audit,proposed,info_signature

def row(i,question):
    return dict(id=str(i),purpose='judge',at=f'2026-10-01T00:00:0{i}+00:00',status='received',system='固定系统指令'*100,
        messages=[dict(role='user',content='【知识点清单】\n[{"id":"p1"}]\n【本次判定】\n{"mode":"test","reply":"'+question+'"}')],
        usage=dict(input_tokens=1000,cached_input_tokens=500,raw={}))

def test_gateway_cache_proposal_preserves_every_field_and_text():
    r=row(1,'答案');assert info_signature(r)==info_signature(proposed(r))
    assert proposed(r)['messages'][0]['content'].startswith('【本次判定】')
    assert r['messages'][0]['content'].startswith('【知识点清单】')

def test_gateway_cache_audit_uses_adjacent_and_preserved_endpoints():
    rows=[row(1,'甲'),row(2,'乙'),row(3,'丙')]
    value=audit(rows)
    assert value['requests'][1]['previous']=='1'
    assert value['requests'][2]['previous']=='2'
    assert value['requests'][1]['endpoint_est']==0
    assert value['requests'][2]['endpoint_est']>0
    assert value['warm']['cached_tokens_rate']==.5

def test_gateway_cache_unknown_input_not_counted_as_zero():
    a,b=row(1,'甲'),row(2,'乙');b['usage']=None
    result=audit([a,b]);assert result['total']['missing']==1
    assert result['requests'][1]['input_tokens'] is None
