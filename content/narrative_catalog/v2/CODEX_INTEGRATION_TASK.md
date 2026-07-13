# Codex任务：接入10,000条分层剧情目录，但禁止把目录塞入提示词

读取本包：

- `narrative_catalog_v2.sqlite`
- `unified_story_catalog_10000.jsonl`
- `story_catalog_api_v2.openapi.yaml`
- `source_connector_specs_v2.json`

实施要求：

1. 在现有MentorFlow中新增只读StoryCatalogRepository；
2. 默认使用SQLite/FTS本地检索，禁止逐条写入Prompt；
3. 实现search、match、get、story-pack-status、curation-request、versioned import；
4. 搜索最多返回100条，进入LLM重排最多20条，用户最终看到默认3条；
5. `curated_dna`与`candidate_index`严格分开；
6. `candidate_index`不得被描述为“已分析剧情”；
7. 候选没有Story Pack时必须返回`PACK_REQUIRED`；
8. 不允许Codex自行联网找剧本、生成原作主线或下载IP资产；
9. 策展请求只记录catalog_id、任务画像、优先级和原因，由上游助手后续研究；
10. import必须校验版本、记录数、SHA256和重复ID；
11. 新增测试：记录数10000、curated=41、candidate=9959、FTS、过滤、多样性、PACK_REQUIRED、无网络调用；
12. 保持Tutor/Evaluator/Plan/Replan为学习权威。

完成后输出实际变更文件、测试结果和接口示例。不要重做现有学习系统。
