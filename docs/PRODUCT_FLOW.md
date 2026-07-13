# 产品流程

## 新建学习空间

1. Demo 入口明确创建 demo Workspace；“创建真实学习计划”创建 real Workspace，未验证 ProviderSession 时只允许保存草稿信息，不允许生成。
2. 导入文本、文件、URL 或 preview 后确认的本地文件。
3. 填写目标、成功标准、截止日期、每日分钟、每周学习日和不可用日期。
4. 查看可行性、风险、容量和可选调整。
5. 选择角色，使用当前 verified ProviderSession 生成 draft 计划，查看 generation_meta、里程碑/课程/日历任务后激活。

## 学习闭环

今日任务从 PlanTask 读取；Tutor 根据用户问题检索当前 Workspace 的 chunks，使用同一 verified ProviderSession，返回 grounded/insufficient、引用、generation_meta 和下一步。用户提交答案后按 rubric 评估，写入 MasteryEvent、MistakeRecord、ReviewItem，Progress 从任务与事件计算。

## Replan

用户提交原因和新的截止日期/每日时间/学习日，系统保留已完成任务，重新安排未完成任务，生成 moved/added/removed/capacity/risk diff。新版本保持 draft，旧 active 版本归档，用户激活后才生效。

## Demo 与真实流程

首页 Demo 使用 Fake Provider 生成真实 draft/任务/引用/进度闭环；“创建学习计划”不插入 Demo 资料，必须由用户完成真实导入和目标表单。
