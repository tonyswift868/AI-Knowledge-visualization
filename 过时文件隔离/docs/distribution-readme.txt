MentorFlow Windows 桌面版

版本来源是安装目录内的 `mentorflow-version.json`；请不要手工修改 EXE 的版本号。

1. 这是纯本地文件版：数据只保存在当前本地目录，不需要额外的身份配置。请保留整个 `MentorFlow` 目录，不要只复制 `MentorFlow.exe`。
2. 双击 `MentorFlow.exe`；软件会自行启动和关闭本地后端，无需安装 Python。已有数据会自动识别，没有数据会初始化全新的学习空间。
3. 软件安装包和用户数据包必须分开。安装包可以发给别人或安装到任意位置；用户数据默认保存在 `E:\MentorFlowData`（数据库、上传资料、缓存、备份和配置），不放进安装目录。
4. 换设备时：在旧设备“数据与备份”点击“导出迁移压缩包”并选择保存位置；在新设备安装软件；首次打开后选择压缩包和一个空目录导入。压缩包包含当前任务、资料、知识地图、计划和进度，不覆盖正在使用的数据，也不会把别人的内容混入当前目录。
5. Provider API Key 需由每位用户在“模型连接”中自行配置，并保存在 Windows Credential Locker。迁移包不会携带 API Key，换设备后请重新输入并点击“保存预设”。
6. 视频不属于 v1 材料范围；不支持的文件会明确拒绝，不会伪造知识地图。

安装包与迁移包是两种不同的东西：安装包只含冻结程序、材料、许可证和 `SELF-CHECK`，不含 `app.db`、`mentorflow_v2.db`、上传资料或 Provider API Key；迁移压缩包才包含某位用户主动导出的数据。不要把迁移压缩包解压到安装目录。

完整性检查：在 `SELF-CHECK` 目录双击 `mentorflow-check.bat`，或在命令行运行
`SELF-CHECK\mentorflow-check.bat --no-smoke`。发布包内的 `SELF-CHECK\MentorFlowSelfCheck.exe`
是独立的控制台自检/更新 helper，不需要安装 Python；BAT 会优先调用它。只有从源码树
运行 `scripts\mentorflow-check.bat` 或 `scripts\mentorflow-update.bat` 时，才会在 helper
不可用时回退到本机 Python。检查会校验版本、必需文件和 SHA-256，并以只读方式运行
`E:\MentorFlowData` 下 SQLite 的 `PRAGMA integrity_check`；启动冒烟会等待应用在
`MENTORFLOW_DESKTOP_SMOKE_SECONDS` 健康检查后以退出码 0 结束，单纯保持运行不算成功。
冒烟使用临时 E: 数据目录，不会写入正式数据。路径含空格时请保留命令中的双引号。

更新检查：`SELF-CHECK\mentorflow-update.bat --owner <owner> --repo <repo>` 只读取
官方 GitHub Releases API。只有 release asset 提供 `sha256:` digest 且下载后校验通过时，
才会把压缩包暂存到 `E:\MentorFlowUpdates\<version>`；工具绝不会替换当前 EXE、删除
用户数据或静默安装。检查 `update-staging.json` 中的 release 页面后，关闭软件并按发布页
说明手工安装；若 API 不可用，请直接打开 release 页面手工下载并核对 SHA-256。

评审副本：发布目录内的“启动UI评审.cmd”只使用 `E:\MentorFlowData\ui-review` 标记副本，供本地功能和界面评审使用；它不会访问或修改正式的 `E:\MentorFlowData` 数据。

完整第三方组件与许可证说明位于 _internal\docs。
