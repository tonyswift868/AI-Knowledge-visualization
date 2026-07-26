"""Build an auditable MentorFlow material from LeetCode's public study-plan API.

Only plan metadata is stored: topic, title, difficulty and the canonical
problem URL. Problem statements and editorials are deliberately not copied.
"""

from __future__ import annotations

import json
from pathlib import Path
from urllib.request import Request, urlopen


GRAPHQL_URL = "https://leetcode.cn/graphql/"
PLAN_SLUG = "top-interview-150"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUTPUT = PROJECT_ROOT / "data" / "demo_materials" / "leetcode_top_interview_150_20260726.md"

QUERY = """
query studyPlanV2Detail($slug: String!) {
  studyPlanV2Detail(planSlug: $slug) {
    name
    planSubGroups {
      name
      questions {
        title
        titleSlug
        questionFrontendId
        difficulty
      }
    }
  }
}
"""

DIFFICULTY = {"EASY": "简单", "MEDIUM": "中等", "HARD": "困难"}
MINUTES = {"EASY": 20, "MEDIUM": 30, "HARD": 45}


def main() -> None:
    body = json.dumps(
        {"query": QUERY, "variables": {"slug": PLAN_SLUG}},
        ensure_ascii=False,
    ).encode("utf-8")
    request = Request(
        GRAPHQL_URL,
        data=body,
        headers={"Content-Type": "application/json", "User-Agent": "MentorFlow/1.0"},
        method="POST",
    )
    with urlopen(request, timeout=30) as response:
        payload = json.load(response)
    if payload.get("errors"):
        raise RuntimeError(payload["errors"])
    plan = payload["data"]["studyPlanV2Detail"]
    questions = [
        (group["name"], question)
        for group in plan["planSubGroups"]
        for question in group["questions"]
    ]
    if len(questions) != 150:
        raise RuntimeError(f"Expected 150 questions, received {len(questions)}")

    lines: list[str] = []
    for index, (topic, question) in enumerate(questions, start=1):
        difficulty = str(question["difficulty"]).upper()
        title = str(question["title"]).strip()
        slug = str(question["titleSlug"]).strip()
        problem_id = str(question["questionFrontendId"]).strip()
        lines.extend(
            [
                f"## {index:03d}. {title}",
                *(
                    [
                        f"题单：{plan['name']}",
                        "来源：https://leetcode.cn/studyplan/top-interview-150/",
                        "生成规则：仅保存公开题单元数据，不复制题目正文或题解。",
                    ]
                    if index == 1
                    else []
                ),
                f"题号：{problem_id}",
                f"主题：{topic}",
                f"知识点：识别并应用“{topic}”中的 {title} 解题模式。",
                f"难度：{DIFFICULTY.get(difficulty, difficulty)}",
                f"预计任务分钟：{MINUTES.get(difficulty, 30)}",
                (
                    "验收：能独立说明输入输出、核心不变量、时间与空间复杂度，"
                    "写出通过边界测试的实现，并在延迟复习中无提示复现。"
                ),
                f"官方题目：https://leetcode.cn/problems/{slug}/",
                "",
            ]
        )
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({"output": str(OUTPUT), "questions": len(questions)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
