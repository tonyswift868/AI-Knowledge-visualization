"""Trusted, fully local fixture for the Review Product demo."""

from __future__ import annotations

from dataclasses import dataclass

from ..teaching.evaluator_v2 import (
    EvaluationEvidence,
    EvaluationPath,
    EvaluationRubric,
    MisconceptionAnswer,
)
from ..teaching.review_v1 import ReviewItem


@dataclass(frozen=True)
class DemoConcept:
    concept_id: str
    label: str
    critical: bool = False


@dataclass(frozen=True)
class DemoQuestion:
    item_id: str
    concept_id: str
    prompt: str
    accepted_answers: tuple[str, ...]
    partial_answers: tuple[str, ...]
    misconception_answer: str
    hint: str
    explanation: str

    @property
    def canonical_answer(self) -> str:
        return self.accepted_answers[0]


DEMO_CONCEPTS: tuple[DemoConcept, ...] = (
    DemoConcept("concept-active-recall", "主动回忆", True),
    DemoConcept("concept-spacing", "间隔重复"),
    DemoConcept("concept-interleaving", "交错练习"),
    DemoConcept("concept-desirable-difficulty", "适宜难度", True),
    DemoConcept("concept-generation", "生成效应"),
    DemoConcept("concept-feedback", "反馈时机"),
    DemoConcept("concept-elaboration", "精细加工"),
    DemoConcept("concept-calibration", "元认知校准", True),
    DemoConcept("concept-transfer", "迁移练习"),
    DemoConcept("concept-retrieval-strength", "提取强度"),
)


DEMO_QUESTIONS: tuple[DemoQuestion, ...] = (
    DemoQuestion(
        "review-q01", "concept-active-recall",
        "主动回忆开始时，学习者应先做什么，再核对资料？",
        ("从记忆中提取答案", "先从记忆中提取答案"),
        ("先想一想",), "先重读资料",
        "先把资料合上，动作发生在记忆内部。",
        "主动回忆的关键是先尝试从记忆中提取，再用资料核对。",
    ),
    DemoQuestion(
        "review-q02", "concept-active-recall",
        "哪一种动作比反复重读更能直接检验是否记住？",
        ("不看资料作答", "闭卷作答"),
        ("做题",), "继续重读",
        "答案需要描述一种不看资料的输出行为。",
        "不看资料作答会暴露真实的提取能力，而重读容易制造熟悉感。",
    ),
    DemoQuestion(
        "review-q03", "concept-spacing",
        "间隔重复为什么要把复习分散到不同时间？",
        ("在遗忘后重新提取", "让学习者在遗忘后重新提取"),
        ("防止忘记",), "一次学完更快",
        "考虑每次有一点遗忘时，重新想起来会发生什么。",
        "分散复习让每次提取都需要重新建立通路，而不是依赖短时熟悉。",
    ),
    DemoQuestion(
        "review-q04", "concept-spacing",
        "固定三轮活动中，FSRS 可以调整什么，而不能调整什么？",
        ("可以调整题目优先级，不能调整三轮", "调整题目优先级但不改变三轮"),
        ("调整优先级",), "可以增加轮数",
        "区分微观题目排序与宏观活动承诺。",
        "FSRS 信号只影响题目到期与优先级，产品承诺的固定轮数保持不变。",
    ),
    DemoQuestion(
        "review-q05", "concept-interleaving",
        "交错练习把不同类型的问题混合起来，主要训练什么？",
        ("辨别应使用哪种方法", "辨别该使用哪种方法"),
        ("练习更多题型",), "背诵同一种步骤",
        "真正困难的不是执行步骤，而是先选择步骤。",
        "交错练习迫使学习者识别问题类型并选择方法。",
    ),
    DemoQuestion(
        "review-q06", "concept-interleaving",
        "同类题连续刷完后加入另一类题，最先增加的判断是什么？",
        ("判断题目类型", "判断当前题属于哪一类"),
        ("判断难度",), "判断做题速度",
        "需要先判断眼前问题属于哪一类。",
        "题型切换打破了步骤惯性，学习者必须先判断题目类型。",
    ),
    DemoQuestion(
        "review-q07", "concept-desirable-difficulty",
        "适宜难度的边界是什么？",
        ("有挑战但仍可成功", "有挑战且仍然能够成功"),
        ("越难越好",), "必须让学习者失败",
        "难度需要同时满足挑战性和可完成性。",
        "适宜难度不是越难越好，而是需要努力、仍有合理成功机会。",
    ),
    DemoQuestion(
        "review-q08", "concept-desirable-difficulty",
        "如果任务难到只能猜，应该先做什么？",
        ("降低难度或提供支架", "提供支架并降低难度"),
        ("给提示",), "继续增加难度",
        "先恢复可学习的成功概率。",
        "超出可成功边界时应降低难度或增加支架，再逐步撤除。",
    ),
    DemoQuestion(
        "review-q09", "concept-generation",
        "生成效应要求学习者在看到标准答案前先做什么？",
        ("先生成自己的答案", "尝试生成自己的答案"),
        ("先猜",), "先抄标准答案",
        "答案强调先产生自己的输出。",
        "先生成再比较会留下更强的差异信号，并促进编码。",
    ),
    DemoQuestion(
        "review-q10", "concept-generation",
        "先尝试解题再看解法，比直接看解法多了哪个步骤？",
        ("生成尝试", "自己的生成尝试"),
        ("尝试",), "重复阅读",
        "这个步骤发生在标准解法出现之前。",
        "学习者先生成一次尝试，之后才能把自己的模型与标准解法比较。",
    ),
    DemoQuestion(
        "review-q11", "concept-feedback",
        "主动回忆中，反馈最重要的作用是什么？",
        ("纠正提取错误", "及时纠正提取错误"),
        ("告诉分数",), "替代学习者作答",
        "反馈应改变错误的知识表征，而不只是给数字。",
        "反馈用于纠正提取错误、补齐缺口，并指出下一次如何改进。",
    ),
    DemoQuestion(
        "review-q12", "concept-feedback",
        "为什么不能在提交前直接显示标准答案？",
        ("会污染主动回忆证据", "会污染独立回忆证据"),
        ("会降低难度",), "会浪费时间",
        "一旦看过答案，就无法证明本次是独立提取。",
        "提交前看答案会把识别或短时复述误当成独立回忆。",
    ),
    DemoQuestion(
        "review-q13", "concept-elaboration",
        "精细加工最常用的提问词是什么？",
        ("为什么", "为什么会这样"),
        ("是什么",), "记住了吗",
        "这个词会推动因果或关系说明。",
        "追问为什么、如何关联会把孤立事实接入已有知识网络。",
    ),
    DemoQuestion(
        "review-q14", "concept-elaboration",
        "把新概念连接到已有经验，主要增加了什么？",
        ("可提取的关联线索", "更多可提取线索"),
        ("记忆数量",), "学习时长",
        "想一想未来能通过多少条路径找到它。",
        "与已有知识建立关系，会增加未来可用的提取线索。",
    ),
    DemoQuestion(
        "review-q15", "concept-calibration",
        "元认知校准要比较主观把握与什么？",
        ("实际作答表现", "真实作答表现"),
        ("学习时间",), "他人的感觉",
        "需要一个可观察的行为结果。",
        "校准来自把自我判断与闭卷作答等真实表现相比较。",
    ),
    DemoQuestion(
        "review-q16", "concept-calibration",
        "觉得熟悉却答不出来，说明哪种感觉不等于掌握？",
        ("熟悉感", "材料熟悉感"),
        ("困难感",), "疲劳感",
        "重读时最容易产生这种流畅的感觉。",
        "熟悉感可能来自最近看过材料，不能替代可验证的提取表现。",
    ),
    DemoQuestion(
        "review-q17", "concept-transfer",
        "迁移练习为什么要更换情境？",
        ("检验知识能否用于新情境", "检验能否应用到新情境"),
        ("增加题量",), "避免重复",
        "目标是检验知识是否超越原例题。",
        "更换情境可以检验学习者是否掌握可迁移的原则，而非记住表面模式。",
    ),
    DemoQuestion(
        "review-q18", "concept-transfer",
        "只会原题不会变式，最可能缺少什么？",
        ("迁移能力", "把原则迁移到新情境的能力"),
        ("记忆能力",), "阅读速度",
        "问题出现在把同一原则用于新表面形式时。",
        "能复现原题不代表能迁移；变式要求抽取并应用底层原则。",
    ),
    DemoQuestion(
        "review-q19", "concept-retrieval-strength",
        "一次成功回忆最直接增强什么？",
        ("未来提取该记忆的能力", "未来的提取能力"),
        ("阅读速度",), "材料长度",
        "关注下一次从记忆中找到它的概率。",
        "成功提取会提高未来再次访问该记忆的强度。",
    ),
    DemoQuestion(
        "review-q20", "concept-retrieval-strength",
        "复习成效为什么不能只看见过多少题？",
        ("见过不代表能够独立提取", "看过题目不等于独立提取"),
        ("题目数量不重要",), "题目会重复",
        "区分曝光次数与可验证的回忆结果。",
        "通过必须依据提取结果、关键概念与弱项，而不是题目曝光计数。",
    ),
)


CONCEPT_BY_ID = {item.concept_id: item for item in DEMO_CONCEPTS}
QUESTION_BY_ID = {item.item_id: item for item in DEMO_QUESTIONS}


def build_review_items() -> tuple[ReviewItem, ...]:
    items: list[ReviewItem] = []
    for question in DEMO_QUESTIONS:
        evidence_id = f"evidence-{question.item_id}"
        items.append(
            ReviewItem(
                item_id=question.item_id,
                concept_id=question.concept_id,
                prompt=question.prompt,
                rubric=EvaluationRubric(
                    rubric_id=f"rubric-{question.item_id}",
                    path=EvaluationPath.OBJECTIVE,
                    accepted_answers=question.accepted_answers,
                    partial_answers=question.partial_answers,
                    misconception_answers=(
                        MisconceptionAnswer(
                            answer=question.misconception_answer,
                            misconception="demo-grounded-misconception",
                        ),
                    ),
                    required_evidence_ids=(evidence_id,),
                ),
                evidence=(
                    EvaluationEvidence(
                        evidence_id=evidence_id,
                        content=question.explanation,
                        source_reference="mentorflow-review-demo-v1",
                    ),
                ),
                source_id="mentorflow-review-demo-v1",
                critical=CONCEPT_BY_ID[question.concept_id].critical,
            )
        )
    return tuple(items)


def validate_demo_fixture() -> None:
    assert len(DEMO_CONCEPTS) == 10
    assert len(DEMO_QUESTIONS) == 20
    assert len({item.concept_id for item in DEMO_CONCEPTS}) == 10
    assert len({item.item_id for item in DEMO_QUESTIONS}) == 20
    counts = {
        concept.concept_id: sum(
            question.concept_id == concept.concept_id
            for question in DEMO_QUESTIONS
        )
        for concept in DEMO_CONCEPTS
    }
    assert set(counts.values()) == {2}
    build_review_items()


validate_demo_fixture()


__all__ = [
    "CONCEPT_BY_ID",
    "DEMO_CONCEPTS",
    "DEMO_QUESTIONS",
    "QUESTION_BY_ID",
    "DemoConcept",
    "DemoQuestion",
    "build_review_items",
    "validate_demo_fixture",
]
