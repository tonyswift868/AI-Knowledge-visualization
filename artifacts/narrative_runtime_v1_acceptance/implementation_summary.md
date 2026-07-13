# MentorFlow Narrative Runtime V1

## 实际完成内容

- 新增只读、版本化 Story Pack 加载器：`backend/app/story_pack.py`。
- 新增开发夹具 `story_packs/dev_fixture_minimal/`，包含匿名角色、5 个测试场景、学习门禁、结局与 postgame；所有内容显式标记为 `DEV FIXTURE — NOT PRODUCTION STORY ASSET`。
- 新增 SQLAlchemy 模型：Campaign、Narrative Event、Voice Cache；新增幂等 Alembic 迁移 `0005_narrative_runtime_v1`。
- 新增 Narrative Runtime：状态持久化、固定主线锚点、revision 并发保护、事件日志、幂等学习事件、公开状态脱敏和门禁推进。
- 新增三类对话路由：story dialogue、tutor dialogue、system/planning dialogue。Tutor 路由复用现有 Tutor、Retrieval 和 Grounding；planning 路由返回现有 Plan/Replan 工作流入口。
- 新增 Persona Renderer。V1 不做生成式改写；任何候选文本与 Tutor 验证文本不一致时直接回退原文。
- 新增 FakeVoiceProvider 基础设施和句级缓存；失败时明确退化为文字，不阻塞学习或剧情。
- React 中新增学习剧场入口、Narrative Store、固定场景渲染、逐字显示、跳过已读、自动播放开关、固定选项、自由输入、故事日志、学习门禁和同页进入今日学习。
- 既有导航索引保持不变，新入口放在末尾，避免破坏原有 E2E。

## 边界

Tutor / Evaluator / Plan / Replan / Retrieval / Grounding / ProviderSession 仍是学习事实和计划的权威来源。Narrative Runtime 只消费结构化学习事件，不凭角色反馈判定掌握，也不生成正式故事或真实资产。

真实 Story Pack 后续只需放入 `story_packs/<id>/`，通过 `/api/story-packs` 验证后创建 Campaign；战役保存 pack ID 和版本，不会静默跟随文件更新。

语音只提供接口、FakeVoiceProvider、缓存键和文字回退；真实 voice ID、音频和资产必须由上游 Story Pack 提供。
