# 启动、更新与修复（一个核心）

入口 `MentorFlow.exe` → `resolve_launch_root` → 已签名安装身份及 GUI 最小加载闭包
→ `get_startup_update_state` → 登录壳或独立修复壳 → 已有 downloader / transact / health。

## 根因与三个边界

人工安装的 Inno 平铺包把 EXE 放在 `MentorFlow/`；旧入口无条件取 EXE 的
`parent.parent`，得到 `E:/Downloads`，由 `existing_parent` 因目录名非 MentorFlow
抛 ValueError。失败先于 package-manifest 检查，并非卸载器哈希错误。
安装器 02 的平铺分支与 A 已集成的 `App + Updater + Maintenance` 分支并不等价。

- **APP_PAYLOAD_AUTHORITY**：已有 Ed25519 signed release、SHA256 和 GUI closure；不信 INI 哈希。
- **INSTALLER_OWNED_METADATA**：A 的 `Maintenance/` 在 App 外；旧 Inno v1 的
  `.mentorflow-install.ini` 必须匹配签名 payload 的 Product/Files/Directories，
  `unins000.exe/.dat` 是不由 App 加载的卸载记录。未知 DLL/文件仍拒绝。
- **MUTABLE_RUNTIME_STATE**：已有 current/highest/transaction/pending 记录留在安装根，
  仅作为状态或签名封装；不提供路径、密钥或执行权限。账号数据始终在程序树外。

## 唯一启动分类器

`scripts/per_user_update_core.get_startup_update_state` 先检查独立修复 runtime，再检查
payload，最后使用 `update_protocol.startup_version_policy` 解释**已验证**的 release。

| 状态 | 启动行为 |
|---|---|
| HEALTHY | 正常登录；feed 不可用也不推断过期或锁死 |
| OPTIONAL_UPDATE | 可登录，显示新版及更新按钮 |
| MANDATORY_UPDATE | 当前低于 minimum 或 signed mandatory=true；登录壳保留，禁止完成登录，提供更新 |
| REPAIR_REQUIRED | runtime 完好但 App 损坏；修复与更新，不要求登录 |
| FATAL_UNRECOVERABLE | 连独立 runtime 都无法验证；仅显示人话安装修复提示 |

相等版本为 HEALTHY；withdrawn 不允许应用。没有第二套版本解析、签名验证或修复引擎。

## 稳定用户入口与集成

`MentorFlow.exe --repair-update`；独立等价入口：
`Updater/UserRuntime.exe repair-update --root <安装根>`。
02 的开始菜单名称为“MentorFlow 修复与更新”。独立入口不导入 App/Qt/账号代码，
调用同一 `get_startup_update_state / check_latest / update_to_latest / repair_installation`。
它保留可用的原生修复窗口，网络与事务在工作线程运行，不把内部工程名暴露给用户。

正确的新 Inno 继续通过已有核心安装生成根部 signed `current-release.json` 和 STATE；
不得再发布只复制平铺 payload、遗漏 signed binding 的旧分支。旧平铺布局如需兼容，
必须随安装传入 signed current-release sidecar；缺失时只从编译期
`RECOVERY_RELEASE_URL` 取固定签名描述，验证独立 runtime 后保存。INI 绝不替代签名。
修复使用已有 full reconstruction + transact；平铺原文件和卸载记录原地保留，
新 App 在规范 App 子目录提交，previous 由签名文件集合构建，不删除用户邻居。

`repair_installation` 明确允许同版本、同签名 manifest 的完整重建；拒绝降级、同版本
换内容及撤回版本。下载后、提交前再次刷新签名 metadata，并走原有关闭/交换/health。
禁止在 UI 线程下载或在 shutdown/logoff 时开始破坏性应用。公开更新 feed 无需登录 token。

程序完整性失败不会被 catch 后放行。无法安全加载 Qt 时只运行已验证的独立修复入口，
二者均无法验证才显示最终人话错误。真实验收必须使用隔离桌面发布包，而非源码成功。
