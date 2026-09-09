# E109 当前集成接线

本记录是源代码集成状态，不是最终安装包验收。07 保留 `PENDING_QA`；真实
SMTP 未就绪，最终安装器未构建。没有重跑服务器配置、发送邮件或发布更新源。

## 阅读顺序

1. `scripts/per_user_update_core.py`：07 的 `get_startup_update_state` 和
   `repair_installation`。签名、版本、恢复事务由核心负责；A 未另写一套核心。
2. `desktop/update_lifecycle.py`：03 的单一 `CoreUpdatePort` 适配层。按用户安装
   接到上述真实 API，保留会话、取消、窗口与安装准入保护；修复移交后不二次 apply。
3. `desktop/update_controller.py`、`desktop/update_panel.py`：共享操作和呈现。
   `desktop/gui_update_runtime.py` 将登录前入口、关于页接到同一 controller；
   `desktop/login_shell.py` 根据核心策略阻止不允许的登录/账号动作。
4. `installer/inno/MentorFlow.iss` 与 `per-user-core.iss`：Inno 外壳通过稳定
   `Updater/UserRuntime.exe` 调用签名核心。普通入口为 `launch --root`，修复入口
   为 `repair-update --root`，不是旧版平铺目录中的可执行文件。
5. `installer/inno/finish-options.iss`：默认不勾选桌面快捷方式和开机启动。
   元数据在 `Maintenance/finish-options.ini`，不进入签名 App；快捷方式和 Run
   值使用现有构建命名空间。仅删除仍匹配的自有项，且先要求核心卸载成功。
6. `deploy/enterprise/auth-email-security/`：05 原有认证/邮件安全配置与历史 UAT
   证据。集成仅收源码与配置，禁止把 Mailpit 测试等同真实外部邮件投递。

## 运行时 capability 与后续启用

`release/account-capabilities.json` 是唯一最终构建输入，当前：

```json
{"register": false, "forgot_password": false}
```

现有 `scripts/build_e108_user_candidate.py`（保留历史文件名）严格检查两个布尔
字段，将其写入 `mentorflow-version.json` 并在构建输入记录配置哈希。此元数据
随 App 纳入既有签名包链。`desktop/version.py` 读取它；缺失或错误类型默认关闭。
打包启动只有现有 SECURITY_GATE 通过后才向登录壳绑定 capability。

05 的真实邮件回执满足验收后，A 只需在该 JSON 中改两个值并进行最终构建，
不必再改 03 UI。它只控制入口能力展示，不代替服务端授权。SMTP 密钥不进入
该文件、版本信息或客户端。当前构建脚本仍保留历史 1.4.x 默认版本和隔离身份，
不得把本次源代码集成当作已配置完成的 1.5.x 最终发布构建。

## 验证边界

- 聚焦检查覆盖启动 API 接线、一次移交、保护条件、界面行为、配置开关以及
  安装器源码/输出关闭编译；不声称已执行最终安装器。
- 四项 07 最终包验证统一登记在 `release/FINAL_E109_CANDIDATE_QA.json`。
- 已知 canonical identity recovery 缺陷证据保留，并未因集成而变成 PASS。
- 后续真实 SMTP 回执由用户转交；当前不主动等待或轮询。
