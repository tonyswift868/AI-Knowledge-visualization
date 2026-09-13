# 产品流程

## 长期产品合同

Authority: `MF-MAIN3-A-KNOWLEDGE-INTEGRATION-WEB-HANDOFF-20260912-02`。
MentorFlow 的交付目标是有证据的学习系统，不能以摘要器或 Chat 壳代替以下闭环：

Materials → Evidence → Knowledge Inventory / Anchor-Support → Knowledge Map
→ Plan → Teacher → Assessment → Student Model → Active Recall / Spaced Review
→ Progress / Replan。

资料经解析后保留来源、修订和引用定位；Inventory 区分核心知识 Anchor 与解释/例证
Support，再供知识地图、计划和教学消费。评估产生可追溯事件，Student Model 区分
自述与独立验证的掌握；主动回忆和间隔复习反馈进度与重排。具体边界复用
[知识地图合同](knowledge-map-teacher-v1.md) 和 [grounding](SAFETY_AND_GROUNDING.md)。
Free Chat 是独立工具：不得偷偷写 mastery、plan 或 score；任何学习状态变化必须
通过有 authority 的学习/评估流程。业务链到代码和测试见
[COMPONENT_MAP](project/COMPONENT_MAP.md)，选择与取舍见 [decisions](decisions/index.md)。

本节是持续有效的要求，不是当前全链路 PASS 声明；当前缺口和最终用户验收看
[CURRENT_STATE](project/CURRENT_STATE.md)。短期测试结果不能静默删减长期要求。

## 跨设备连续性

Authority: `MAIN3_BATCH03_AND_CROSS_DEVICE_CONTINUITY_AUTHORITY`。
CROSS_DEVICE_DATA_CONTINUITY = REQUIRED；完整合同见 [三层数据与 Portable Pack V2](decisions/CROSS_DEVICE_DATA_CONTINUITY_V2.md)。
服务端持久保存结构化学习／账号状态；新设备登录即恢复计划、进度与支持的历史，无需旧设备在线。
本机缺失的大资源单独标记，用户导入经账号／空间／完整性校验的迁移包后按稳定 ID 重连。
QUICK/FULL 与可选模型、大 payload、非覆盖幂等性和保密都需验收；现有 V1 不等于 V2。
产品先智能分类推荐，仅真实歧义需要选择。真实旧用户迁移仍未执行，稳定身份与持久目录为硬门禁。

## 启动、登录与更新

Batch02 authority：`MF-MAIN3-A-BATCH02-INTEGRATION-20260913-01`。
Launch MentorFlow → native login gate → username/password → secure backend
verification → MentorFlow session → workspace bootstrap → main window。
正常用户无需外部浏览器；密码只用于本次认证，不持久化。身份、安全和生产启用门
复用 [native auth ADR](ADR_NATIVE_CREDENTIAL_AUTH_V1.md)。

OPTIONAL update 允许继续登录/使用、稍后、更新并重启或退出时更新。
MANDATORY update 同时阻止登录与主窗口，只能更新至当前 latest 或退出。
历史签名 floor 不下降，断网或取消不能绕过；例如 current 1.0、历史 mandatory 1.2、
latest optional 1.4，结果仍为强制更新至 1.4。原因仅作解释，详见
[更新 authority](windows-update-release-contract-v1.md)。本节不声明真实安装或生产登录已验收。

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
