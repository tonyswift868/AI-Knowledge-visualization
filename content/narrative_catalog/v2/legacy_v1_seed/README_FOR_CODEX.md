# README FOR CODEX — Narrative DNA Library V1

本包包含 41 条作品/语料卡与 18 个DNA家族。

## 必须实现

1. 按`narrative_dna_schema_v1.json`验证并导入JSONL。
2. 以`(library_version, entry_id)`做不可变版本键，重复导入幂等。
3. 建立families、duration、task types、coupling、risk、scores索引。
4. 使用`matcher_rules_v1.json`，默认返回三个差异明显候选。
5. 用户选定候选后解析Story Pack；缺失则返回`PACK_REQUIRED`。
6. 禁止根据DNA生成正式原作主线、逐句对白、立绘、音乐或声音。
7. 不得用DNA评分替代学习评分。Tutor/Evaluator/Plan/Replan保持权威。
8. 匹配时不得联网。

## 推荐API

- `POST /api/narrative-dna/import`
- `GET /api/narrative-dna`
- `POST /api/narrative-dna/match`
- `GET /api/narrative-dna/{entry_id}`
- `GET /api/narrative-dna/{entry_id}/story-pack-status`

## 验收

- 非法JSONL返回行号；未知family拒绝。
- 重复版本导入幂等。
- 周期、内容限制和风险过滤有效。
- 三候选满足多样性规则。
- Story Pack缺失返回`PACK_REQUIRED`。
- 匹配过程无网络请求、无故事生成。
- 现有Tutor/Evaluator/Plan/Replan测试继续通过。
