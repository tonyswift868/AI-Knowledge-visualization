"""Module generation reads all material in batches and splits a cut-off batch (MF-04 helpers, 7-MF02)."""
import json
import re
import threading
from datetime import date
from types import SimpleNamespace

import pytest

from refactor.learning.app.plan_flow import PlanFlow
from refactor.learning.connections.errors import MODEL_OUTPUT_TRUNCATED, ModelError
from refactor.learning.course import planner_agents as agents

FORM = {"goal": "学 Python", "final_goal": "能写小工具", "level": "beginner", "level_note": "", "notes": "",
        "start": "2026-10-01", "end": "2026-10-30", "daily_cap": 60, "weekday_weights": [1] * 7, "special_days": []}
MODULE = {"id": "mod1", "key": "m1", "title": "文件读写", "summary": "读写文本和 CSV", "refs": ["s1.1"],
          "prerequisites": [], "minutes": [60, 90]}
OUTLINE = {"stages": [{"title": "基础", "modules": [MODULE]}]}


class Gateway:
    def __init__(self, truncate=0):
        self.truncate = truncate
        self.requests = []
        self.max_tokens = []
        self.lock = threading.Lock()

    def complete(self, purpose, messages, **kwargs):
        user = messages[-1]["content"]
        with self.lock:
            self.requests.append(user)
            self.max_tokens.append(kwargs.get("max_tokens"))
            if self.truncate:
                self.truncate -= 1
                raise ModelError(MODEL_OUTPUT_TRUNCATED, "模型输出达到上限")
        aliases = re.findall(r"\[(c\d+)\]", user)
        low, high = map(int, re.search(r"分钟区间：(\d+)–(\d+)", user).groups())
        # One point per alias (at most 2), sharing the middle of the range.
        picked = aliases[:2]
        total = (low + high) // 2
        minutes = [total // len(picked)] * len(picked)
        minutes[0] += total - sum(minutes)
        if any(not 2 <= m <= 60 for m in minutes):
            picked, minutes = picked[:1], [min(60, max(2, total))]
        points = [{"title": f"点{len(self.requests)}-{i}", "summary": f"第{len(self.requests)}批第{i}点", "chunks": [a],
                   "minutes": m} for i, (a, m) in enumerate(zip(picked, minutes))]
        return SimpleNamespace(text=json.dumps({"points": points}, ensure_ascii=False), search_hits=(), citations=())


def flow(gateway):
    return PlanFlow(SimpleNamespace(gateway=gateway), lambda *a: None, lambda: date(2026, 10, 1))


def chunks(count, size):
    return [{"id": f"k{i:03d}", "text": f"第{i}段" + "字" * size, "section": ["文件"], "source_title": "教程"}
            for i in range(count)]


def draft():
    return {"plan_id": "a" * 32, "form": FORM, "goal": "学会文件读写", "outline": OUTLINE}


def ctx():
    return SimpleNamespace(cancel=threading.Event())


def test_small_module_is_one_request_with_the_module_range():
    gateway = Gateway()
    points = flow(gateway)._generate_module(draft(), MODULE, chunks(3, 500), ctx())
    assert len(gateway.requests) == 1 and "分钟区间：60–90" in gateway.requests[0]
    assert sum(p["minutes"] for p in points) == 75


def test_long_module_reads_every_chunk_in_batches():
    gateway = Gateway()
    material = chunks(12, 5000)                   # 60k characters: nothing is sampled away any more
    points = flow(gateway)._generate_module(draft(), MODULE, material, ctx())
    assert len(gateway.requests) == len(agents.split_module_chunks(material)) > 1
    sent = "".join(gateway.requests)
    assert all(c["text"][:8] in sent for c in material)
    assert all("批资料" in r for r in gateway.requests)
    assert 2 <= len(points) <= 30 and {c for p in points for c in p["chunk_ids"]} <= {c["id"] for c in material}


def test_a_cut_off_answer_first_gets_more_room():
    gateway = Gateway(truncate=1)
    flow(gateway)._generate_module(draft(), MODULE, chunks(4, 2000), ctx())
    assert len(gateway.requests) == 2 and gateway.max_tokens == [8192, 16384]


def test_a_cut_off_answer_is_retried_as_two_halves():
    gateway = Gateway(truncate=2)
    points = flow(gateway)._generate_module(draft(), MODULE, chunks(4, 2000), ctx())
    assert len(gateway.requests) == 4                      # cut off twice (the second with more room), then two halves
    assert "第1/2批" in gateway.requests[2] and "第2/2批" in gateway.requests[3]
    assert len(points) >= 2


def test_a_half_that_is_still_cut_off_fails_the_module():
    gateway = Gateway(truncate=4)
    with pytest.raises(ModelError) as caught:
        flow(gateway)._generate_module(draft(), MODULE, chunks(4, 2000), ctx())
    assert caught.value.code == MODEL_OUTPUT_TRUNCATED
