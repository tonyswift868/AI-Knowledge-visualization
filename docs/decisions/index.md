# Decisions index

先看 [CURRENT_STATE](../project/CURRENT_STATE.md) 的适用范围/冲突登记。
新治理权威：[MAIN3 governance ADR](0001-main3-repository-governance.md)。
网页交接的来源绑定与哈希边界：[Web handoff decision](0002-web-handoff-provenance.md)。
旧 ADR 保留原编号和路径；标题中的 Accepted 只约束其当时 scope，不提升今天的产品状态。

| 决策入口 | 范围 / 使用方法 |
| --- | --- |
| [Knowledge map ADR-001](../adr/001-knowledge-map-v1-open-source.md) | 知识地图开源选择；结合当前 registry/锁文件 |
| [Video collection ADR-002](../adr/002-video-collection-v1.md) | 目录、字幕、分层摘要；不是原始视频媒体上传通用许可 |
| [Native classroom ADR-003](../adr/003-native-classroom-theme-system.md) | 原生课堂/主题系统 |
| [Updater ADR-004](../adr/004-windows-incremental-updater.md) | 历史增量方案；当前接线读 E109 integration，不恢复旧安装布局 |
| [Per-user trust](../PER_USER_INSTALL_TRUST.md) | 安装/签名/用户数据 authority 分离 |
| [Native credential auth](../ADR_NATIVE_CREDENTIAL_AUTH_V1.md) | Batch02 正常登录 authority；原生表单、私有 authenticated verifier、Keycloak credential provider、session/bootstrap；supersedes Batch01/03d979 浏览器正常 UX |
| [E109 auth migration](../E109_AUTH_ISSUER_MIGRATION.md) | 精确 issuer 窗口及旧 identity namespace；prepared 不等于 live |
| [Shared contracts](../../backend/app/enterprise/contracts_v1/README.md) | enterprise-context.v1 的类型权威 |

新增 ADR 需要真实已冻结决定与 source/dispatch/receipt 指针，不从“可能以后需要”制造决定。
