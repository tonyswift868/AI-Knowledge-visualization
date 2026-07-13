# MentorFlow Narrative Catalog V2 — 10,000条合并版

## 一眼看懂

| 层级 | 数量 | 含义 |
|---|---:|---|
| `curated_dna` | 41 | V1合并过来的完整高层DNA卡 |
| `candidate_index` | 9959 | 从大规模叙事游戏元数据中筛出的待核验候选 |
| **总计** | **10000** | 可离线搜索的统一目录 |

这不是“一万部都已经精读完成”。那样的说法不可信。V2的设计是：

```text
一万条离线候选目录
→ SQLite/FTS初筛
→ 只取20条做规则/模型重排
→ 给用户3个差异明显的候选
→ 选中候选后检查Story Pack
→ 没有Story Pack则PACK_REQUIRED
→ 上游助手研究并升级为完整DNA/Story Pack
```

## 推荐先交给Codex的文件

1. `CODEX_INTEGRATION_TASK.md`
2. `CATALOG_INTERFACE_SPEC.md`
3. `narrative_catalog_v2.sqlite`
4. `story_catalog_api_v2.openapi.yaml`
5. `catalog_manifest.json`
6. `integrity_report.json`

## 人类查看

- `unified_story_catalog_10000_compact.csv`：快速查看一万条目录；
- `curation_queue_top500.jsonl`：下一批优先研究候选；
- `CANDIDATE_SELECTION_METHODOLOGY.md`：为什么这样筛；
- `SOURCE_AND_LICENSE_NOTES.md`：来源与边界。

## 机器数据

- `unified_story_catalog_10000.jsonl`
- `candidate_index_9959.jsonl`
- `curated_dna_cards_41.jsonl`
- `narrative_catalog_v2.sqlite`

## 重要底线

候选标签不是剧情事实。Codex不能根据候选标签自行脑补主线，也不能把一万条记录一次性塞进模型提示词。
