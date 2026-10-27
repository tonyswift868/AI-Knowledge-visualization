from refactor.learning.course import material_questions as questions


def test_answer_groups214_sibling_list_items_are_separate_and_intro_is_metadata():
    reference = (
        "分片键应该具备以下特点：\n\n"
        "- 具有共性，覆盖大多数查询；\n"
        "- 具有离散性，避免数据倾斜；\n"
        "- 具有稳定性，避免频繁迁移；\n"
        "- 具有扩展性，支持动态增减。"
    )
    groups = questions.answer_groups(reference)
    assert [group["text"] for group in groups] == [
        "- 具有共性，覆盖大多数查询；",
        "- 具有离散性，避免数据倾斜；",
        "- 具有稳定性，避免频繁迁移；",
        "- 具有扩展性，支持动态增减。",
    ]
    assert all(group["heading"] == "分片键应该具备以下特点：" for group in groups)
    assert questions.answer_units(reference) == [group["text"] for group in groups]
    assert all(group["text"] in reference for group in groups)


def test_answer_groups214_short_title_list_item_is_heading_not_checkbox():
    reference = (
        "- 分片键的特点\n"
        "- 高基数，能够区分大量记录。\n"
        "- 分布均匀，避免数据倾斜。\n"
        "- 保持稳定，避免频繁迁移。\n"
        "- 支持扩展，适应数据增长。"
    )
    groups = questions.answer_groups(reference)
    assert len(groups) == 4
    assert all(group["heading"] == "分片键的特点" for group in groups)
    assert all("分片键的特点" not in group["text"] for group in groups)
    metadata = questions._answer_group_metadata(reference)
    title_spans = [span for group in metadata for span in group['heading_spans']]
    assert any(raw == "- 分片键的特点\n" for _, _, raw in title_spans)
    assert all(reference[group['text_span'][0]:group['text_span'][1]].strip() == group['text']
               for group in metadata)


def test_answer_groups214_lead_in_and_nested_explanation_stay_with_parent_item():
    reference = (
        "路由键应兼顾查询效率：\n\n"
        "- 用户维度：查看自己的订单。\n"
        "  - 常用条件是 buyer_id 和 create_time。\n"
        "  还要处理状态筛选。\n"
        "- 商家维度：后台筛选和导出。\n"
        "  低频查询可限制时间范围。"
    )
    groups = questions.answer_groups(reference)
    assert [group["heading"] for group in groups] == ["路由键应兼顾查询效率："] * 2
    assert len(groups) == 2
    assert "buyer_id" in groups[0]["text"] and "状态筛选" in groups[0]["text"]
    assert "时间范围" in groups[1]["text"]


def test_answer_groups214_table_data_rows_are_distinct_with_header_context():
    reference = (
        "| 场景 | 分片键 | 注意事项 |\n"
        "| --- | --- | --- |\n"
        "| 订单 | 用户 ID | 看订单查询路径 |\n"
        "| IM | 会话 ID | 留意热点 |\n"
        "| 多租户 | 租户 ID | 大租户单独评估 |"
    )
    groups = questions.answer_groups(reference)
    assert len(groups) == 3
    assert [group["text"] for group in groups] == [
        "| 订单 | 用户 ID | 看订单查询路径 |",
        "| IM | 会话 ID | 留意热点 |",
        "| 多租户 | 租户 ID | 大租户单独评估 |",
    ]
    assert all(group["heading"] == "| 场景 | 分片键 | 注意事项 |" for group in groups)


def test_answer_groups214_paragraph_remains_unsplit_and_code_keeps_context():
    paragraph = "分片键决定数据分布，选择时要看查询路径并兼顾数据均匀和扩容成本。"
    assert questions.answer_groups(paragraph) == [{"text": paragraph, "heading": None}]

    reference = "路由先算槽位：\n\n```sql\nSELECT * FROM orders;\n```\n\n再按槽位定位分片。"
    groups = questions.answer_groups(reference)
    assert len(groups) == 2
    assert groups[0]["heading"] == "路由先算槽位："
    assert "```sql" in groups[0]["text"]
    assert groups[1]["text"] == "再按槽位定位分片。"


def test_answer_groups214_count_is_unbounded_and_legacy_helpers_remain_available():
    reference = "\n".join(f"- 独立要点{i}，各有自己的说明。" for i in range(8))
    assert len(questions.answer_groups(reference)) == 8
    assert questions.previous_answer_units("完整原文。") == ["完整原文。"]
    assert questions.legacy_answer_units("完整原文。") == ["完整原文。"]


DEEP_PAGINATION_REFERENCE214 = "查询偏移量过大的场景我们称为深度分页，这会导致查询性能较低，例如：\n\n# MySQL 在无法利用索引的情况下跳过1000000条记录后，再获取10条记录\nSELECT * FROM t_order ORDER BY id LIMIT 1000000, 10\n\n当查询偏移量过大时，MySQL 的查询优化器可能会选择全表扫描而不是利用索引来优化查询。\n\n**深度分页变慢的根本原因**在于 MySQL 的执行机制：对于 `LIMIT offset, N`，MySQL 并非直接跳到 `offset` 处，而是必须从头扫描 `offset + N` 条记录。如果查询依赖二级索引且不满足覆盖索引，这意味着 MySQL 需要对前 `offset` 条记录执行毫无意义的**回表查询（产生海量的随机 I/O）**，最后再将这些辛苦查出的数据丢弃。即便优化器最终因代价过高退化为全表扫描，顺序扫描百万行的成本依然巨大。\n\n![深度分页问题](https://oss.javaguide.cn/github/javaguide/mysql/deep-pagination-phenomenon.png)\n\n不同机器上这个查询偏移量过大的临界点可能不同，取决于多个因素，包括硬件配置（如 CPU 性能、磁盘速度）、表的大小、索引的类型和统计信息等。\n\n![转全表扫描的临界点](https://oss.javaguide.cn/github/javaguide/mysql/deep-pagination-phenomenon-critical-point.png)\n\nMySQL 的查询优化器采用基于成本的策略来选择最优的查询执行计划。它会根据 CPU 和 I/O 的成本来决定是否使用索引扫描或全表扫描。如果优化器认为全表扫描的成本更低，它就会放弃使用索引。不过，即使偏移量很大，如果查询中使用了覆盖索引（covering index），MySQL 仍然可能会使用索引，避免回表操作。\n\n范围查询（游标分页）\n\n通过记录上一页最后一条记录的 ID，使用 `WHERE id > last_id LIMIT n` 获取下一页数据：\n\n# 通过记录上次查询结果的最后一条记录的 ID 进行下一页的查询\nSELECT * FROM t_order WHERE id > 100000 ORDER BY id LIMIT 10\n\n**游标分页的核心优势**：**不依赖 ID 的连续性**。MySQL 只需要在 B+ 树上定位到 `last_id` 的位置，然后顺序向后读取 `n` 条记录即可，中间是否有断层（如 ID 被删除）完全不影响结果的准确性和性能。\n\n这种方式的限制：\n\n1. **不支持跳页**：无法直接跳转到第 N 页，只能逐页向后（或向前）翻页。\n2. **排序字段受限**：如果查询需要按照其他字段（如创建时间）排序而非 ID 排序，需使用联合游标 `(sort_field, id)` 保证唯一性和顺序。\n3. **并发场景**：当分页查询期间有新数据插入或删除时，可能出现：\n   - **数据遗漏**：查询第二页时，有新数据插入到第一页范围内，导致该数据被“挤”到第二页，但第二页查询已基于旧的最后 ID 跳过它。\n   - **数据重复**：查询第二页时，第一页末尾有数据被删除，原第二页的第一条数据“升”到第一页末尾，导致第二页查询再次返回它。"


def test_answer_groups214_full_deep_pagination_keeps_definition_and_unfenced_sql_together():
    groups = questions.answer_groups(DEEP_PAGINATION_REFERENCE214)
    assert "查询偏移量过大的场景我们称为深度分页" in groups[0]["text"]
    assert "SELECT * FROM t_order ORDER BY id LIMIT 1000000, 10" in groups[0]["text"]
    assert groups[0]["heading"] is None
    assert groups[1]["text"].startswith("当查询偏移量过大时")


def test_answer_groups214_unindented_explanation_attaches_to_bare_numbered_label():
    reference = (
        "1. 延迟处理\n\n"
        "有些操作没必要同步完成，可以先放到 MQ 里慢慢处理。\n\n"
        "主流程先返回成功，后面的事情交给异步任务处理即可。\n\n"
        "2. 页面片段降级\n\n"
        "页面主体保留，把非核心区块关掉。"
    )
    groups = questions.answer_groups(reference)
    assert len(groups) == 2
    assert groups[0]["text"].startswith("1. 延迟处理")
    assert "放到 MQ 里慢慢处理" in groups[0]["text"]
    assert "主流程先返回成功" in groups[0]["text"]
    assert groups[1]["text"] == "2. 页面片段降级\n\n页面主体保留，把非核心区块关掉。"


def test_answer_groups214_diagram_visual_style_is_not_display_heading():
    reference = 'style maintain fill:#E4C189,stroke:#333\n\nDBMS 通常提供四大核心功能：\n\n- 数据定义，描述数据库结构。\n- 数据操纵，读写数据。'
    groups = questions.answer_groups(reference)
    assert len(groups) == 2
    assert all(group['heading'] == 'DBMS 通常提供四大核心功能：' for group in groups)
    assert 'style maintain' in reference  # The complete original is not rewritten.
