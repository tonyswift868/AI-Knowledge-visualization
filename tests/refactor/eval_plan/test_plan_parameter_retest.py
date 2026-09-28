"""Batch 7 driver: never enable paid work from the default test suite."""
from copy import deepcopy
from datetime import date
from decimal import Decimal
import json
import re
import tempfile
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

import plan_parameter_eval as base
import retest_plan_parameters as retest


@pytest.fixture
def retest_work(monkeypatch):
    base.TEMP_ROOT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="r7-", dir=base.TEMP_ROOT) as folder:
        path = Path(folder)
        monkeypatch.setattr(base, "TEMP_ROOT", path)
        for name in ("BASELINE", "LIMIT", "STOP_AT", "FORMAL", "COMPARISONS"):
            monkeypatch.setattr(base, name, getattr(base, name))
        retest.configure_retest()
        yield path


def test_parameter_retest_default_skip_cannot_construct_real_model(monkeypatch):
    monkeypatch.delenv("MF_LIVE_EVAL", raising=False)
    monkeypatch.setattr(retest, "prepare_retest", lambda *a: pytest.fail("No real services allowed"))
    assert retest.main([]) == 0
    assert retest.main(["--root", str(base.TEMP_ROOT / "unused"), "--stage", "formal"]) == 0


def test_parameter_retest_preserves_baseline_forms_and_hashes_in_new_root(retest_work):
    previous, root = retest_work / "old", retest_work / "new"
    configs = base.configurations(date(2026, 9, 28))
    manifest = dict(configurations=configs, today="2026-09-28", run_order=base.run_order(configs),
                    sources=[], model=None)
    base.write_json(previous / "manifest.json", manifest)
    base.write_json(previous / "material-snapshot/test.json", dict(sha256="fixture"))
    original = (previous / "manifest.json").read_bytes()
    exp = retest.prepare_retest(root, previous)
    assert base.LIMIT == Decimal("8") and base.STOP_AT == Decimal("7.50")
    assert len(exp.manifest["run_order"]) == 8 and set(exp.manifest["configurations"]) == retest.NAMES
    assert all(form == configs[name] for name, form in exp.manifest["configurations"].items())
    assert (previous / "manifest.json").read_bytes() == original
    assert (root / "material-snapshot/test.json").is_file()
    with pytest.raises(RuntimeError, match="snapshot"):
        exp.fetch("https://github.com/not-in-snapshot", None)
    assert retest.prepare_retest(root, previous).manifest == exp.manifest
    exp.manifest["prompt_sha"] = "changed"
    base.write_json(root / "manifest.json", exp.manifest)
    with pytest.raises(ValueError, match="Changed prompts"):
        retest.prepare_retest(root, previous)
    with pytest.raises(ValueError, match="overwrite"):
        retest.prepare_retest(previous / "nested", previous)


def test_parameter_retest_offline_demo_is_explicit_fake_and_uses_real_utilities(retest_work, monkeypatch):
    module = dict(id="m1", title="故障模块", summary="验证接口", minutes=[30, 60])
    draft = dict(form=base.configurations(date(2026, 9, 28))["base"], goal="验证接口",
                 outline=dict(stages=[dict(title="阶段", modules=[module])]))
    chunks = [dict(id="a", text="测试资料。" * 4500, section=["标题"])]
    original = deepcopy(chunks)
    monkeypatch.setattr(retest, "failed_module_input", lambda _: (dict(draft=draft), module, chunks))
    record = retest.offline_batch_demo(retest_work / "new", retest_work / "old")
    assert record["network_calls"] == 0 and "fake" in record["kind"]
    assert len(record["batches"]) == 2
    assert sum(b["chars"] for b in record["batches"]) == len(chunks[0]["text"])
    assert max(b["chars"] for b in record["batches"]) <= 12000
    assert all(p["chunk_ids"] == ["a"] for p in record["points"])
    assert 23 <= record["minutes"] <= 75
    assert chunks == original


def test_parameter_retest_report_preserves_previous_batch_and_replaces_only_its_section(retest_work):
    from summarize_plan_retest import report
    old, root = retest_work / "old", retest_work / "new"
    base.write_json(root / "manifest.json", dict(previous=str(old), baseline=retest.BASELINE, prompt_sha="fixture"))
    base.write_json(root / "cost-latest.json", dict(calls=0, known_amounts={}, unpriced_calls=0, liability="0", groups=[]))
    base.write_json(root / "offline-batch-demo.json", dict(module="测试", source_chars=100, batch_cap=12000,
                    batches=[{}], points=[{}, {}], minutes=30, original_minutes=[20, 40]))
    output = retest_work / "report.md"
    output.write_text("# 第六批\n\n前一批原始结论不得覆盖。\n", encoding="utf-8")
    report(root, output)
    first = output.read_text(encoding="utf-8")
    report(root, output)
    assert output.read_text(encoding="utf-8") == first
    assert first.startswith("# 第六批\n\n前一批原始结论不得覆盖。\n")
    assert first.count("\n## 第七批复测\n") == 1
    assert "0/8轮廓" in first and "0/8正式计划" in first


def test_parameter_retest_minute_audit_checks_only_finished_modules():
    from summarize_plan_retest import minute_audit
    modules = [dict(id="done", title="完成", minutes=[100, 200]), dict(id="pending", title="未完成", minutes=[50, 60]),
               dict(key="m3", title="尚未开始生成", minutes=[30, 50])]
    run = dict(draft=dict(outline=dict(stages=[dict(modules=modules)]), generation=dict(done="done", pending="failed")),
               metrics=dict(points=[dict(module_id="done", minutes=20), dict(module_id="done", minutes=20),
                                    dict(module_id="pending", minutes=50)]))
    audit = minute_audit({"base-r1": run})
    assert len(audit) == 1 and audit[0]["module"] == "完成" and not audit[0]["valid"]
    assert audit[0]["total"] == 40 and "25%" in audit[0]["error"]


def test_parameter_retest_component_check_preserves_success_and_resumes_only_failed_batch(retest_work, monkeypatch):
    module = dict(id="m1", title="故障模块", summary="验证接口", minutes=[30, 60])
    draft = dict(form=base.configurations(date(2026, 9, 28))["base"], goal="验证接口",
                 outline=dict(stages=[dict(title="阶段", modules=[module])]))
    chunks = [dict(id="a", text="测试资料。" * 4500, section=["标题"])]
    monkeypatch.setattr(retest, "failed_module_input", lambda _: (dict(draft=draft), module, chunks))
    called = []

    def complete(purpose, messages, **kwargs):
        called.append(kwargs["run_id"])
        if len(called) == 2:
            from refactor.learning.connections.errors import ModelError
            raise ModelError("MODEL_RESPONSE_INVALID", "用途明确的失败替身")
        prompt = messages[0]["content"]
        lo, hi = map(int, re.search(r"分钟区间：(\d+)–(\d+)", prompt).groups())
        index = re.search(r"这是本模块第(\d+)/", prompt)[1]
        return SimpleNamespace(text=json.dumps(dict(points=[dict(title=f"协议检查{index}", summary=f"仅用于第{index}批接口检查",
                        minutes=(lo + hi) // 2, chunks=["c1"])]), ensure_ascii=False))

    exp = SimpleNamespace(root=retest_work / "new", retry_failed=False, cancel=threading.Event(),
                          budget=SimpleNamespace(complete=complete))
    first = retest.live_batch_demo(exp, retest_work / "old")
    assert first["completed_batches"] == 1 and not first["success"]
    assert not retest.live_batch_demo(exp, retest_work / "old")["success"]
    assert len(called) == 2
    exp.retry_failed = True
    complete = retest.live_batch_demo(exp, retest_work / "old")
    assert complete["success"] and complete["completed_batches"] == 2 and len(called) == 3
    assert all(p["chunk_ids"] == ["a"] for p in complete["points"])
    assert len(list((exp.root / "live-batch-demo/failures").glob("*.json"))) == 1
