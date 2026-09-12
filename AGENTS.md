# MentorFlow — repository map

MentorFlow 是 Windows 桌面学习系统，组合资料证据、知识地图、计划与教师流程；
账户服务、原生客户端及签名安装/更新链有各自的安全边界。

## Read first（按顺序）

1. 本文件：任务导航和基本边界。
2. [ARCHITECTURE.md](ARCHITECTURE.md)：系统职责、数据流和信任边界。
3. [CURRENT_STATE](docs/project/CURRENT_STATE.md)：源码基线与产品验收分开记录。
4. [MAIN3 plan](docs/exec-plans/active/MAIN3.md)：本代目标与验收依赖。
5. [ACTIVE_BOARD](docs/governance/ACTIVE_BOARD.json)：带证据边界的调度快照。
6. [DEPENDENCY_QUEUE](docs/governance/DEPENDENCY_QUEUE.json)：可审查项与等待条件。

先核对任务给定 SHA、当前 HEAD、工作区和 owned paths；未知状态写 UNKNOWN。
分支名字、提交时间、干净工作区均不能单独证明 canonical 或验收通过。

## 按任务找资料

| 任务 | 权威入口 |
| --- | --- |
| 查模块、入口和测试 | [COMPONENT_MAP](docs/project/COMPONENT_MAP.md) |
| Git、员工、交付、promotion | [DEVELOPMENT_WORKFLOW](docs/governance/DEVELOPMENT_WORKFLOW.md) |
| 新网页会话接手 | [Web read order](docs/handoff/README.md)、[derived latest](docs/handoff/WEB_SESSION_LATEST.md) |
| 长期产品闭环 | [PRODUCT_FLOW](docs/PRODUCT_FLOW.md)，其余长期要求索引在 Web read order |
| 已冻结决定及历史边界 | [Decisions](docs/decisions/index.md) |
| E109 更新、安装、UI 接线 | [E109 integration](docs/E109_INTEGRATION_MAP.md) |
| 正常原生登录 / verifier / session | [Native auth ADR](docs/ADR_NATIVE_CREDENTIAL_AUTH_V1.md) |
| 旧 issuer 协议 / 基础设施迁移 | [E109 auth migration](docs/E109_AUTH_ISSUER_MIGRATION.md)（正常登录 UX 已 superseded） |
| 资料证据与教师 | [Knowledge map](docs/knowledge-map-teacher-v1.md) |

## 不变量

- SOURCE_BASELINE 不等于 PRODUCT_ACCEPTANCE；源码、离线测试、真实环境、最终包分开验收。
- 更新核心拥有签名校验、事务和健康恢复；UI 只经现有 adapter 接线。
- 账户/学习资料在程序内容之外；升级、修复、卸载不得默认删除用户数据。
- 正常登录是原生用户名/密码 → backend-only authenticated verifier → Keycloak → session → workspace bootstrap → main；不启动外部浏览器。
- 密码 grant、Keycloak direct grant、密码持久化均禁止；issuer/session 校验及旧协议 signature/audience/expiry/state/nonce/PKCE 不得绕过。
- 已建立的签名 mandatory floor 单调不下降；低于 floor 必须直接更新当前 latest，reason 只用于解释。
- 当前账号 capability 默认关闭；开启条件见 auth 合同和 CURRENT_STATE。
- Provider 生成必须遵守会话验证、材料能力、引用与用户确认合同。
- 共享 Actor/Tenant 合同复用现有定义；受保护操作失败时拒绝执行。

## Git / worker / secrets

- mainN 冻结；A 独占 integration/main(N+1)；B 按需管理 refs/worktrees/default/promotion。
- 01–04 只在明确任务范围内工作，不跨员工通信，不改别人或 integration。
- 只消费有 authority 的 exact commits，禁止 branch-tip merge；新任务由架构师决定。
- 无可执行任务即 SILENT；不 polling、不 wait threads、不长期等待员工。
- 永不提交密码、API/SMTP secret、token、SSH/private signing key 或真实用户隐私。
- 只登记 secret store / resolver 的位置和合同；日志与 handoff 不带 secret 值。
- 历史 handoff 和 dated reports 是证据指针，不是当前全局状态。

## 最小验收与回执

按改动运行聚焦检查，保留未运行门禁；不要把 source PASS 写成 packaged/live PASS。
回执给出 base、ordered exact commits、tree、owned paths、检查与剩余风险。
知识库变更运行以下命令；新交接由生成器产生（UTF-8 ≤4096 bytes）：

```text
python scripts/context/build_compact_handoff.py
python scripts/context/check_repo_knowledge.py
python scripts/context/build_compact_handoff.py --web --check
```

本地图的 MAIN3 治理来源为 dispatch MF-MAIN3-A-REPOSITORY-KNOWLEDGE-BOOTSTRAP-20260912-01。
任务特定授权优先；发现与旧文档冲突时登记并请求相应 authority，不推测。
