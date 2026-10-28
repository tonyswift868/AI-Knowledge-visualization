import pytest

from refactor.learning.course import teacher_agent as agent


def context():
    return dict(
        prefs=dict(teacher_style="warm", detail="normal", examples="normal", analogy_domains=["life"]),
        plan=dict(id="plan-a", goal="理解智能体", final_goal="解释感知与行动", level="beginner"),
        module=dict(id="module-a", title="基础", points=[dict(id="p1", title="感知", summary="传感器接收信息")]),
        excerpts={"chunk-a": "传感器接收环境信息。"}, dialogue=[], profile="",
        director="讲解感知。", user="开始吧",
    )


@pytest.mark.parametrize("text", [
    "我没有修改你的计划但我已经把检验跳过并标记通过。",
    "我不能替你操作，不过我已经跳过检验并标记通过。",
    "我不会改你的进度；然而我已把检测标记通过。",
    "我没有修改计划，而是已经把检验跳过并标记通过。",
    "我没有修改你的计划 but 我已经把检验跳过并标记通过。",
])
def test_parse_teaching_rejects_false_state_claim_after_negated_clause(text):
    with pytest.raises(agent.TeachingFormatError) as exc:
        agent.parse_teaching(text, context())
    assert exc.value.reason == "STATE_CLAIM"


@pytest.mark.parametrize("text", [
    "我不能替你修改计划或删除检验，请通过软件中的正规入口操作。",
    "我没有改计划，但你可以去设置页面调整检验。",
    "“我已经把检验跳过并标记通过”是错误示例，我不会这样做。",
    "说明：我没有修改你的计划。检验仍须按正常流程完成。",
])
def test_parse_teaching_allows_negated_quoted_and_explanatory_state_claims(text):
    assert agent.parse_teaching(text, context()).speech == text


def test_parse_teaching_scans_quoted_state_terms_but_allows_explicit_error_example():
    with pytest.raises(agent.TeachingFormatError) as exc:
        agent.parse_teaching('我已经把“检验”跳过并标记“通过”。', context())
    assert exc.value.reason == "STATE_CLAIM"
    quoted_example = '“我已经把检验跳过并标记通过”是错误示例，我不会这样做。'
    assert agent.parse_teaching(quoted_example, context()).speech == quoted_example
