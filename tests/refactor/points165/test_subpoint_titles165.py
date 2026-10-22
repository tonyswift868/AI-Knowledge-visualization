from pathlib import Path

import pytest

from refactor.learning.course import extract, planner_agents


TITLE_CASES = [
    (
        "Spring Boot 的自动配置会根据类路径中的依赖和配置条件，决定是否创建相应组件。后续文字不属于标题。",
        "Spring Boot 的自动配置会根据类路径中的依赖和配置条件，决定是否创建相应组件。",
    ),
    (
        "| 属性 | 说明 |\n| --- | --- |\n| key | 唯一标识 |\n这个表格说明了记录之间如何关联。",
        "这个表格说明了记录之间如何关联。",
    ),
    (
        "```sql\nSELECT id FROM users;\n```\n数据库会根据查询条件返回匹配的记录。",
        "数据库会根据查询条件返回匹配的记录。",
    ),
    (
        "    redis-cli -h localhost\n    GET user:1\n使用 GET 命令可以读取指定键对应的值。",
        "使用 GET 命令可以读取指定键对应的值。",
    ),
    (
        "print('hello')\nclient.connect()\n客户端调用 connect 方法后会建立与服务端的连接。",
        "客户端调用 connect 方法后会建立与服务端的连接。",
    ),
    (
        "x+=1\n// comment\n配置项会控制客户端连接池的最大连接数。",
        "配置项会控制客户端连接池的最大连接数。",
    ),
    (
        "1. 初始化连接池\n2. 配置最大连接数\n连接池会复用已经建立的连接，减少重复创建的开销。",
        "连接池会复用已经建立的连接，减少重复创建的开销。",
    ),
    (
        "1）初始化连接池\n2）配置最大连接数\n连接池会复用已经建立的连接，减少重复创建的开销。",
        "连接池会复用已经建立的连接，减少重复创建的开销。",
    ),
    (
        "（1）初始化连接池\n（2）配置最大连接数\n连接池会复用已经建立的连接，减少重复创建的开销。",
        "连接池会复用已经建立的连接，减少重复创建的开销。",
    ),
    (
        "**事务隔离级别**用于说明并发事务之间可见数据的范围。",
        "事务隔离级别用于说明并发事务之间可见数据的范围。",
    ),
    (
        "C# 的 using 指令可以引入命名空间，user_id 字段保存用户标识。",
        "C# 的 using 指令可以引入命名空间，user_id 字段保存用户标识。",
    ),
]


@pytest.mark.parametrize("source,expected", TITLE_CASES)
def test_165_title_uses_complete_clean_prose_not_markup_or_structural_lines(source, expected):
    title = planner_agents._directory_subpoint_title(source, "原文小节")

    assert title == expected
    assert len(title) <= 64
    assert not any(mark in title for mark in ("**", "`", "|", "```"))
    assert not title.startswith("#")


def test_165_long_phrase_stops_at_word_boundary_and_balances_parentheses():
    source = (
        "**Spring Boot** 的自动配置（auto-configuration）会根据类路径中的依赖、"
        "条件注解以及用户提供的参数，逐项决定是否创建相应的组件和默认配置。"
    )

    title = planner_agents._directory_subpoint_title(source, "默认标题")

    assert 1 <= len(title) <= 64
    assert not title.endswith(("(", "（", "*", "`", "-", "/"))
    assert title.count("(") == title.count(")")
    assert "**" not in title


def test_165_truncation_drops_an_english_word_that_would_be_split_at_the_limit():
    source = "请求会交给 SpringBootApplication " + "X" * 55 + "负责处理。"

    title = planner_agents._directory_subpoint_title(source, "默认标题")

    assert 1 <= len(title) <= 64
    assert "SpringBootApplication" in title
    assert not title.endswith(("X", "SpringBootApplicationX"))


def test_165_structural_only_text_uses_original_heading_fallback():
    for source in (
        "| 字段 | 含义 |\n|---|---|\n| id | 主键 |",
        "```python\nprint('hello')\n```",
        "1. 第一项\n2. 第二项",
        "    SELECT 1;",
    ):
        assert planner_agents._directory_subpoint_title(source, "原文小节标题") == "原文小节标题"


def test_165_original_markdown_subheading_takes_priority_over_body_sentence():
    assert planner_agents._directory_subpoint_title(
        "## Zuul 的请求路由\nZuul 是 Netflix 开发的网关组件，负责转发客户端请求。",
        "默认标题",
    ) == "Zuul 的请求路由"


def test_165_directory_cut_skips_structural_first_reference_and_uses_next_prose():
    paragraphs = {
        "c1": dict(text="| 字段 | 含义 |\n|---|---|", purpose="knowledge", parent_chunk_id="chunk1",
                   source_id="source", section=["正文"]),
        "c2": dict(text="配置项决定了连接池可以同时保持的最大连接数量。", purpose="knowledge",
                   parent_chunk_id="chunk1", source_id="source", section=["正文"]),
        "c3": dict(text="程序会按配置创建并复用数据库连接。", purpose="knowledge", parent_chunk_id="chunk1",
                   source_id="source", section=["正文"]),
    }
    original_texts = {ref: paragraph["text"] for ref, paragraph in paragraphs.items()}
    old_points = [dict(id="old1", difficulty="中", chunk_ids=["chunk1"])]
    required = {"old1": ["c1", "c2", "c3"]}

    points = planner_agents.directory_structure(paragraphs, old_points, required, [])

    assert len(points) == 1
    assert points[0]["subpoints"][0]["title"] == "配置项决定了连接池可以同时保持的最大连接数量。"
    assert {ref: paragraph["text"] for ref, paragraph in paragraphs.items()} == original_texts


def test_165_real_directory_cut_keeps_original_child_headings():
    paragraphs = {}
    old_points = []
    required = {}
    for index, heading in enumerate(("建立连接", "执行查询", "关闭连接"), 1):
        ref = f"c{index}"
        chunk_id = f"chunk{index}"
        text = f"{heading}时，程序按照原文描述完成对应操作。"
        paragraphs[ref] = dict(
            text=text,
            purpose="knowledge",
            parent_chunk_id=chunk_id,
            source_id="source",
            section=["数据库操作", heading],
        )
        old_points.append(dict(id=f"old{index}", difficulty="中"))
        required[f"old{index}"] = [ref]

    points = planner_agents.directory_structure(paragraphs, old_points, required, [])

    assert [sub["title"] for point in points for sub in point["subpoints"]] == [
        "建立连接", "执行查询", "关闭连接"
    ]


@pytest.mark.parametrize("heading_count", (1, 2))
def test_165_directory_cut_does_not_replace_one_or_two_real_headings_with_body_prefix(heading_count):
    paragraphs = {}
    old_points = []
    required = {}
    headings = ("会话令牌轮换", "刷新凭据校验")[:heading_count]
    for index, heading in enumerate(headings, 1):
        ref = f"c{index}"
        chunk_id = f"chunk{index}"
        paragraphs[ref] = dict(
            text=f"正文开头与标题不同。用户提交凭据后，服务会校验会话状态并继续处理。{'说明材料' * 120}",
            purpose="knowledge",
            parent_chunk_id=chunk_id,
            source_id="source",
            section=["账号流程", heading],
        )
        old_points.append(dict(id=f"old{index}", difficulty="中"))
        required[f"old{index}"] = [ref]

    points = planner_agents.directory_structure(paragraphs, old_points, required, [])

    assert [sub["title"] for point in points for sub in point["subpoints"]] == list(headings)


def test_165_code_and_table_kind_paragraphs_cannot_supply_subpoint_names():
    paragraphs = {
        "c1": dict(text="MagicController routes the user's request.", kind="code", purpose="knowledge",
                   parent_chunk_id="chunk1", source_id="source", section=["正文"]),
        "c2": dict(text="Session state | 令牌状态 | refresh", kind="table", purpose="knowledge",
                   parent_chunk_id="chunk1", source_id="source", section=["正文"]),
        "c3": dict(text="刷新令牌成功后，旧凭据会失效并由新凭据接替。", kind="paragraph",
                   purpose="knowledge", parent_chunk_id="chunk1", source_id="source", section=["正文"]),
    }
    old_points = [dict(id="old1", difficulty="中")]
    required = {"old1": ["c1", "c2", "c3"]}

    points = planner_agents.directory_structure(paragraphs, old_points, required, [])

    assert points[0]["subpoints"][0]["title"] == "刷新令牌成功后，旧凭据会失效并由新凭据接替。"


@pytest.mark.parametrize(
    "filename",
    ("00-project-story.md", "06-authentication-and-authorization.md", "13-incident-and-contribution.md"),
)
def test_165_current_learning_markdown_does_not_generate_fragment_titles(filename):
    path = Path(__file__).resolve().parents[3] / "docs" / "learn" / filename
    document = extract.extract_file(path)

    assert document.blocks
    for block in document.blocks:
        if block.kind == "heading":
            continue
        title = planner_agents._directory_subpoint_title(block.text, "原文小节")
        assert 1 <= len(title) <= 64
        assert title not in {"1、旧标题", "**加粗残留**", "| 特性", "Zuul 是 Netfl", "标题…"}
        assert not title.startswith(("|", "#", "**", "`", "1.", "1、"))


@pytest.mark.parametrize(
    "source,expected",
    (
        ("1、旧标题\nZuul 是 Netflix 的网关组件，负责转发客户端请求。", "Zuul 是 Netflix 的网关组件，负责转发客户端请求。"),
        ("**Zuul 网关**用于转发客户端请求。", "Zuul 网关用于转发客户端请求。"),
        ("| 特性 | 说明 |\n| --- | --- |\nZuul 网关负责转发客户端请求。", "Zuul 网关负责转发客户端请求。"),
        ("Zuul 是 Netflix 的网关组件，负责转发客户端请求。", "Zuul 是 Netflix 的网关组件，负责转发客户端请求。"),
    ),
)
def test_165_bug104_fragment_examples_are_replaced_by_complete_source_phrases(source, expected):
    title = planner_agents._directory_subpoint_title(source, "回退到原文标题")

    assert title == expected
    assert title not in {"1、旧标题", "**加粗残留**", "| 特性", "Zuul 是 Netfl"}
