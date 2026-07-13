# MentorFlow Story Catalog V2 — 接口与运行规则

## 核心原则

10,000条记录**绝不一次性放入模型上下文**。Codex只实现检索接口：

1. SQLite/FTS先筛选到最多100条；
2. 规则排序压缩到20条；
3. 多样性重排后展示3条；
4. 用户选中后只加载该条记录；
5. 若为`candidate_index`且没有Story Pack，返回`PACK_REQUIRED`并创建策展请求；
6. Codex不得联网搜索、补写或仿写原作主线。

## 两层数据

- `curated_dna`：41条V1完整DNA卡，可用于高层匹配，但仍不等于完整可播放剧本。
- `candidate_index`：9959条叙事候选，只含元数据、标签、评价信号和来源；剧情尚未核验。

## 推荐数据库接口

```python
search_catalog(query, filters, limit=20)
match_task(task_profile, user_preferences, recent_fingerprints, limit=3)
get_record(catalog_id)
get_story_pack_status(catalog_id)
request_curation(catalog_id, requested_task_profile)
import_catalog(snapshot_path, expected_version, checksum)
```

## 匹配顺序

1. 内容安全硬过滤；
2. 周期、玩家身份、交互形式和关系偏好过滤；
3. 优先匹配`curated_dna`；
4. 不足时从`candidate_index`返回“待策展候选”；
5. 三个结果必须在关系引擎、玩家位置、时间结构或信息结构中至少三项不同；
6. 任何候选在Story Pack缺失时都不能进入正式战役。

## Prompt预算

- 10k目录：0 token，走数据库；
- 初筛结果：只把最多20条的紧凑元数据交给排序器；
- 最终选择：只加载3条；
- 正式运行：只加载已选Story Pack当前章节与相关角色状态。
