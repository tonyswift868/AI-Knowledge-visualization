# W4 Sol High 融合前置说明

## 本轮交付边界

本轮只建立三个可独立验证的算法边界，不进行最终融合：

1. `memory_scheduler.py`：知识节点与复习 item 的稳定映射、Again / Hard / Good / Easy、due 和 retrievability。
2. `skill_models.py`：BKT V1 与现有确定性 Rule V1 的同证据对比。
3. `persona_replay.py`：六类学习者画像的固定回放。

FSRS 只回答“这个记忆项目何时应复习、此刻可能记得多少”。BKT 和 Rule V1
只回答“某项技能在当前证据下可能掌握多少”。本轮没有把两者压成单一分数，也没有
引入 DKT。

## Py-FSRS 运行策略

适配器遵循 Py-FSRS 6 的公开接口：`Scheduler`、`Card`、`Rating`、
`Scheduler.review_card` 和 `Scheduler.get_card_retrievability`。当前项目环境没有
安装 `fsrs`，所以实际运行 provider 是 `compat-fallback-v1`。

兼容层不是 FSRS 实现，不能以 FSRS 精度对外宣传。它的用途仅是：

- 缺少可选依赖时不阻断学习流程；
- 保留四档评分、到期时间和可提取率契约；
- 让状态明确记录 provider，后续可无歧义迁移到 `py-fsrs`；
- 原生序列化载荷损坏或跨版本不兼容时安全降级。

接入原生依赖前应固定并验证 Py-FSRS 版本，完成历史 card JSON 迁移测试，再把依赖
加入正式环境锁文件。不能悄悄把 fallback 历史状态当作原生 Card 反序列化。

## Sol High 最终融合前必须决定

### 1. 身份与持久化

- `knowledge_node_id` 是技能身份还是知识内容身份；
- 一个知识节点是否允许多个练习/复习 item；
- 节点合并、拆分和知识地图换版时，旧 memory card 与 BKT state 如何迁移；
- review log、BKT observation 和 Evaluator 证据的幂等键及审计保留期。

### 2. 证据语义

- Again / Hard / Good / Easy 由用户选择、Evaluator 派生还是教师建议；
- “正确”阈值是否按任务类型、难度和 rubric 调整；
- 提示、猜测、非独立完成、迁移题分别怎样影响 BKT 与 Rule V1；
- 同一次提交是否能同时产生 memory review 和 skill observation，如何防止重复记账。

### 3. 决策层而非分数层融合

建议保留三个独立输出：

- memory：`due`、`retrievability`、`stability`、`difficulty`；
- BKT：`probability_known` 及 prior / learn / guess / slip 解释；
- Rule V1：mastery、独立通过、提示次数等产品规则证据。

Tutor Decision Engine 可基于多信号选择动作，但不应先求一个不可解释的加权总分。
例如：BKT 高但 retrievability 低时安排短复习；retrievability 高但独立证据不足时
安排变式测试；两者都低时先补救概念再重新练习。

### 4. 参数校准与验收

- 默认 BKT 参数仅是最小可运行先验，不是经过本项目数据校准的结论；
- 不得用六类合成 persona 宣称真实教学效果；
- 原生 FSRS 验收需覆盖 UTC、跨时区、同日多次复习、序列化、版本升级和长期区间；
- BKT 验收需用带标签历史数据检查校准度，而不只是测试单调性；
- 最终融合至少做离线回放、影子运行、教师解释审查和小流量对照。

## 六类画像的用途与限制

当前画像为 struggling novice、steady beginner、hint dependent、
overconfident guesser、returning learner、transfer ready。它们用于防止明显的算法
退化，并证明记忆调度与技能估计可以产生不同结论。画像是固定合成事件，不代替真实
用户测试，不应成为硬编码教学分支。

## 推荐接入顺序

1. 固定 Py-FSRS 版本并为可选/必选依赖做部署决策。
2. 新增独立持久化模型及迁移，保持旧 Progress 数据可读。
3. 从 Evaluator 事件建立幂等 observation adapter。
4. 影子计算 FSRS、BKT、Rule V1，不影响现有教师决策。
5. 用历史日志校准并审核解释，再让 Tutor Decision Engine 消费多信号。
6. 最后才评估更复杂的知识追踪；没有充分序列数据前不引入 DKT。
