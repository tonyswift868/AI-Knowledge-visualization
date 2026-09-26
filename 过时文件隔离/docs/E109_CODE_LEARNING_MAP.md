# E109 代码阅读地图

按下表顺序阅读。路径均相对仓库根目录。

| 顺序 | 文件 | 职责 | 谁调用谁 |
| --- | --- | --- | --- |
| 1 | `desktop/main.py` | 应用主流程、登录壳和账号动作入口 | 创建登录壳；调用更新入口接线与应用内 OIDC |
| 2 | `desktop/login_shell.py` | 登录、注册、找回密码按钮及登录阻止状态 | 发出操作信号；接受 controller 策略和 capability |
| 3 | `desktop/gui_update_runtime.py` | 登录前与关于页共用更新 controller；验证后绑定账号能力 | 调用 `application_updater`、`UpdatePanel`、`desktop.version` |
| 4 | `desktop/update_panel.py`、`desktop/update_controller.py` | 更新呈现、操作顺序、进度和并发控制 | Panel → Controller → `CoreUpdatePort` |
| 5 | `desktop/update_lifecycle.py` | UI 到更新核心的单一适配层；会话、窗口和取消保护 | `CoreUpdatePort` → `get_startup_update_state` / `repair_installation` / 下载与应用接口 |
| 6 | `scripts/per_user_update_core.py` | 启动分类、检查更新、修复与原生协调器移交 | 调用现有更新服务、签名验证及安装事务 |
| 7 | `scripts/per_user_install.py`、`scripts/per_user_launcher.py` | 稳定运行时命令、安装/恢复/卸载、启动和健康确认 | `UserRuntime` → install/launch/repair-update → 更新核心与 App |
| 8 | `desktop/in_app_oidc.py`、`desktop/enterprise_oidc.py` | 应用内身份页面、OIDC 请求及回调验证 | `desktop/main.py` → 应用内登录流程 → Keycloak；保留 PKCE/state/nonce 校验 |
| 9 | `release/account-capabilities.json`、`desktop/version.py` | 构建能力开关、运行时版本与能力读取 | 构建器 → 包内版本 JSON → version → 登录壳；缺失或错误类型默认关闭 |
| 10 | `deploy/enterprise/auth-email-security/realm-policy.json`、`nginx.edge.conf`、`test_policy_contract.py` | 05 的身份安全配置与离线合同检查 | 服务端配置由独立部署授权使用；测试只读取策略，不执行部署 |
| 11 | `installer/inno/MentorFlow.iss`、`per-user-core.iss` | 唯一 Inno 外壳，接入已签名安装核心 | Inno → `UserRuntime` preflight/install/uninstall；快捷入口 → launch/repair-update |
| 12 | `installer/inno/branded-pages.iss`、`finish-options.iss`、`directory-policy.iss` | 安装交互、默认关闭的可选设置和目录预检查 | Inno 事件 → 界面/目录提示；完成按钮 → 可选设置；核心仍做最终准入 |
| 13 | `scripts/build_e108_user_candidate.py`、`scripts/build_inno_installer.py` | 沿用原构建入口，生成 1.5.0/1.5.1 包并编译单一安装器 | 候选构建器 → 版本/签名输入 → Inno 编译器；现阶段未执行最终构建 |
| 14 | `scripts/e108_release_input.py`、`scripts/build_release.py` | 离线发布输入、签名发布对象与上传白名单；文件名保留历史命名 | 候选构建器 → finalize；其输出交 04，A 不自行部署 |
| 15 | `desktop/tests/test_e109_integration_binding.py`、`desktop/tests/test_e109_capability_config.py`、`scripts/test_e108_final_feed_inputs.py` | 聚焦接线、能力开关、版本和发布输入回归 | pytest → 真实模块接口与隔离测试替身；不代替最终包黑盒验收 |
| 16 | `release/FINAL_E109_CANDIDATE_QA.json` | 四项 07 最终候选验收清单，保留 PENDING_QA | A 登记；最终唯一候选交 08 验证，不触发独立 07 重测 |
