"""Offline controls for paid evaluations: isolation, blindness and reservations."""
from decimal import Decimal
import json
from types import SimpleNamespace

import pytest

from .class_eval import Harness, RecordedGateway, STUDENT_SYSTEM, judge_input, teacher_input, validate_review
from refactor.learning.connections.errors import ModelError


def test_judge_reviewer_packet_excludes_actual_verdict():
    entry = dict(id="call-1", text='{"correct":false,"reason":"private judgement"}', messages=[dict(content=
        '【知识点清单】\n[{"id":"p0"}]\n【资料原文（数据）】\n{"c":"原文"}\n【最近对话（数据）】\n[]\n【本次判定】\n'
        '{"mode":"test","question":{"stem":"题面"},"reference":"评分依据","reply":"回答"}')])
    value = judge_input(entry)
    assert value["reply"] == "回答" and "correct" not in value and "private judgement" not in str(value)


def test_reviewer_descriptions_cannot_be_mistaken_for_wrong_point_ids():
    part = [dict(id="call-1",mode="test",points=[dict(id="p0")])]
    valid = dict(ratings=[dict(id="call-1",kind="answer",correct=False,wrong_points=["p0"],reason="实际错点")])
    assert validate_review("judge", valid, part)
    valid["ratings"][0]["wrong_points"] = ["学生把两个方法弄反了"]
    with pytest.raises(ValueError, match="point IDs"):
        validate_review("judge", valid, part)


def test_teacher_review_keeps_earlier_error_evidence_and_module_membership():
    messages=[dict(content='模块\n{"module":{"points":[{"id":"p0"},{"id":"p1"}]}}'),
              dict(content='资料\n[{"text":"原文"}]')]
    messages += [dict(content='对话\n'+json.dumps(dict(role="user",text=text)))
                 for text in ["earlier wrong answer", "one", "two", "three", "four", "later correct answer"]]
    messages.append(dict(content='【本步任务】\n{"director":"总结重测"}'))
    packet=teacher_input(dict(id="t1",messages=messages,text="先前答错，后来重测通过"))
    assert packet["dialogue"][0]["text"]=="earlier wrong answer"
    assert len(packet["dialogue"])==6
    assert [p["id"] for p in packet["plan_module"]["module"]["points"]]==["p0","p1"]


def test_targeted_live_replay_also_requires_explicit_opt_in(monkeypatch):
    from .targeted_repairs import run
    monkeypatch.delenv("MF_CLASS_EVAL",raising=False)
    with pytest.raises(RuntimeError,match="requires"):
        run()


def test_budget_blocks_before_gateway_when_total_would_exceed_limit(tmp_path):
    class Inner:
        price_catalog = SimpleNamespace(estimate_cost=lambda *a, **k: SimpleNamespace(high=Decimal("0.1"), currency="CNY"))
        def get_configuration(self):
            return dict(provider_id="deepseek", model="deepseek-v4-flash")
        def estimate_context(self):
            return dict(region="global", service_tier="standard")
        def complete(self, *a, **k):
            pytest.fail("must not call model")
    path = tmp_path / "budget.json"
    path.write_text('[{"reserved":"9.95","status":"pending"}]', encoding="utf-8")
    gateway = RecordedGateway(Inner(), tmp_path, path)
    with pytest.raises(ModelError, match="预算"):
        gateway.complete("test", [{"role":"user","content":"test"}])


def test_unmetered_timeout_keeps_full_reserve_for_next_request(tmp_path):
    class Inner:
        calls=0
        price_catalog=SimpleNamespace(estimate_cost=lambda *a,**k: SimpleNamespace(high=Decimal("0.1"),currency="CNY"))
        def get_configuration(self):
            return dict(provider_id="deepseek",model="deepseek-v4-flash")
        def estimate_context(self):
            return dict(region="global",service_tier="standard")
        def complete(self,*args,**kwargs):
            self.calls+=1
            raise ModelError("MODEL_TIMEOUT","测试超时，未收到用量")
    path=tmp_path/"budget.json"
    path.write_text('[{"reserved":"9.85","status":"pending"}]',encoding="utf-8")
    inner=Inner()
    gateway=RecordedGateway(inner,tmp_path,path)
    with pytest.raises(ModelError,match="测试超时"):
        gateway.complete("test",[{"role":"user","content":"test"}])
    saved=json.loads(path.read_text(encoding="utf-8"))
    assert saved[-1]["reserved"]=="0.1" and saved[-1]["status"]=="failed"
    with pytest.raises(ModelError,match="预算"):
        gateway.complete("test",[{"role":"user","content":"test"}])
    assert inner.calls==1


def test_student_sees_public_question_but_not_answer_key(tmp_path):
    class FakeGateway:
        def complete(self, purpose, messages, **kwargs):
            packet = json.loads(messages[0]["content"])
            assert set(packet["question"]) == {"stem", "type", "options"}
            assert "secret answer" not in str(packet)
            assert kwargs["system"] == STUDENT_SYSTEM
            return SimpleNamespace(text='{"reply":"不会"}')
    h = Harness(tmp_path, FakeGateway(), "weak", {"lists":"列表原文", "tuples":"元组原文"})
    try:
        h.view = dict(question=dict(stem="题面", type="short", options=[]), messages=[])
        assert h.student("wrong") == "不会"
        assert len(h.store.records("points", h.plan_id)) == 6
    finally:
        h.close()
