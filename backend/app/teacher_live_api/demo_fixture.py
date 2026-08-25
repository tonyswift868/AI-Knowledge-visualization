"""Completely offline five-turn Teacher Live classroom fixture."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class DemoClassroomTurn(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    learner_message: str
    teacher_reply: str


DEMO_EVIDENCE_ID = "demo-hashmap-threshold"
DEMO_EVIDENCE_CONTENT = (
    "HashMap 的 threshold 等于 capacity 乘以 load factor；"
    "当元素数量超过 threshold 时会触发扩容。"
)

DEMO_CLASSROOM_TURNS = (
    DemoClassroomTurn(
        learner_message="我没懂",
        teacher_reply=(
            "没关系，我们只抓住一件事：capacity 是桶的数量，threshold 是开始扩容的警戒线。"
        ),
    ),
    DemoClassroomTurn(
        learner_message="为什么 threshold 不是 capacity？",
        teacher_reply=(
            "因为 HashMap 不会等桶完全占满才扩容。threshold = capacity × load factor，"
            "它会提前留出空间来减少冲突。"
        ),
    ),
    DemoClassroomTurn(
        learner_message="换个例子",
        teacher_reply=(
            "把 capacity 想成有 16 个座位的教室，load factor 是 0.75。"
            "坐到第 12 人就是 threshold，下一位来之前就该扩容了。"
        ),
    ),
    DemoClassroomTurn(
        learner_message="那 ConcurrentHashMap 也一样吗？",
        teacher_reply=(
            "这是相关问题，但实现细节不同。先保留共同点：两者都需要控制装载程度；"
            "当前课堂先把 HashMap 的 threshold 讲牢。"
        ),
    ),
    DemoClassroomTurn(
        learner_message="我已经掌握了",
        teacher_reply=(
            "收到，这是你的自述掌握信号。我们不会把它当成独立测验证据；"
            "下一步可以用一道小题确认。"
        ),
    ),
)


def demo_reply(message: str) -> str:
    normalized = "".join(str(message).casefold().split())
    for turn in DEMO_CLASSROOM_TURNS:
        candidate = "".join(turn.learner_message.casefold().split())
        if candidate in normalized or normalized in candidate:
            return turn.teacher_reply
    if "例" in message:
        return DEMO_CLASSROOM_TURNS[2].teacher_reply
    if "为什么" in message or "why" in message.casefold():
        return DEMO_CLASSROOM_TURNS[1].teacher_reply
    if "懂" in message or "困惑" in message:
        return DEMO_CLASSROOM_TURNS[0].teacher_reply
    return "这是离线 DEMO。我们先围绕当前概念继续：threshold 是触发扩容的警戒线。"


__all__ = [
    "DEMO_CLASSROOM_TURNS",
    "DEMO_EVIDENCE_CONTENT",
    "DEMO_EVIDENCE_ID",
    "DemoClassroomTurn",
    "demo_reply",
]
