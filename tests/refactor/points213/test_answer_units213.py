from refactor.learning.course import material_questions as questions


def test_answer_units213_heading_and_sibling_list_form_one_meaning_group():
    reference = (
        "分片键是影响数据分布的关键字段。\n\n"
        "一般来说，分片键应该具备以下特点：\n\n"
        "- 具有共性，覆盖大多数查询；\n"
        "- 具有离散性，避免数据倾斜；\n"
        "- 具有稳定性，避免频繁迁移；\n"
        "- 具有扩展性，支持动态增减。\n\n"
        "项目中还需要综合权衡。"
    )

    units = questions.answer_units(reference)

    assert units == [
        "分片键是影响数据分布的关键字段。",
        "一般来说，分片键应该具备以下特点：\n\n"
        "- 具有共性，覆盖大多数查询；\n"
        "- 具有离散性，避免数据倾斜；\n"
        "- 具有稳定性，避免频繁迁移；\n"
        "- 具有扩展性，支持动态增减。",
        "项目中还需要综合权衡。",
    ]
    assert all(unit in reference for unit in units)


def test_answer_units213_plain_title_before_list_is_context_not_an_answer_point():
    reference = "分片键的特点\n\n- 共性覆盖常见查询。\n- 离散避免倾斜。"

    assert questions.answer_units(reference) == [reference]


def test_answer_units213_heading_only_reference_has_a_preserving_fallback():
    reference = "# 什么是哈希分片？"

    assert questions.answer_units(reference) == [reference]


def test_answer_units213_title_attaches_and_table_is_a_single_comparison():
    reference = (
        "常见业务的分片键选择可以参考：\n\n"
        "| 业务场景 | 候选键 | 注意事项 |\n"
        "| --- | --- | --- |\n"
        "| 订单 | 用户、商家、订单 ID | 先看主要查询路径 |\n"
        "| IM | 会话 ID、用户 ID | 留意热点 |\n"
        "| 多租户 | 租户 ID | 大租户单独评估 |\n\n"
        "查询路径冲突时要做取舍。"
    )

    assert questions.answer_units(reference) == [
        "常见业务的分片键选择可以参考：\n\n"
        "| 业务场景 | 候选键 | 注意事项 |\n"
        "| --- | --- | --- |\n"
        "| 订单 | 用户、商家、订单 ID | 先看主要查询路径 |\n"
        "| IM | 会话 ID、用户 ID | 留意热点 |\n"
        "| 多租户 | 租户 ID | 大租户单独评估 |",
        "查询路径冲突时要做取舍。",
    ]


def test_answer_units213_list_keeps_explanatory_lines_nested_children_and_parent():
    reference = (
        "选择路由时要考虑这些方面：\n\n"
        "- 用户维度：查看自己的订单。\n"
        "  - 常用条件是 buyer_id 和 create_time。\n"
        "  还要处理状态筛选。\n"
        "- 商家维度：后台筛选和导出。\n"
        "  低频查询可限制时间范围。"
    )

    units = questions.answer_units(reference)

    assert units == [reference]


def test_answer_units213_keeps_short_sentences_together_and_uses_paragraph_boundaries():
    reference = "同一段有几个短句。第二句补充条件。第三句给出结论。\n\n另一个完整段落。"

    assert questions.answer_units(reference) == [
        "同一段有几个短句。第二句补充条件。第三句给出结论。",
        "另一个完整段落。",
    ]


def test_answer_units213_fenced_code_attaches_to_its_explanation_and_code_only_is_preserved():
    reference = "路由先算槽位：\n\n```sql\nSELECT * FROM orders;\n```\n\n再按槽位定位分片。"

    units = questions.answer_units(reference)

    assert units == [
        "路由先算槽位：\n\n```sql\nSELECT * FROM orders;\n```",
        "再按槽位定位分片。",
    ]
    code_only = "```sql\nSELECT 1;\n```"
    assert questions.answer_units(code_only) == [code_only]


def test_answer_units213_one_paragraph_answer_remains_one_full_literal_point():
    reference = "分片键决定数据分布，选择时要看查询路径并兼顾数据均匀和扩容成本。"

    assert questions.answer_units(reference) == [reference]
    assert questions.selected_units(reference, None) == [reference]


def test_answer_units213_selection_has_no_fixed_three_to_five_count():
    reference = "第一组完整意思。\n\n第二组完整意思。\n\n第三组完整意思。\n\n第四组完整意思。\n\n第五组完整意思。\n\n第六组完整意思。"
    units = questions.answer_units(reference)

    assert questions.selected_units(reference, [1, 2, 3, 4, 5, 6]) == units
    assert questions.selected_units(reference, [2]) == [units[1]]


def test_answer_units213_identical_paragraph_does_not_make_question_invalid():
    reference = '重复出现的完整解释。\n\n重复出现的完整解释。'
    assert questions.answer_units(reference) == ['重复出现的完整解释。']
    assert reference.count(questions.answer_units(reference)[0]) == 2
