# MentorFlow 原生桌面版

## 现在是什么

MentorFlow 的桌面入口使用 PySide6（Qt 6）构建。Qt 创建真正的 Windows
窗口、输入框、按钮、任务表和导航；用户不需要手动启动 FastAPI，也不需要打开
浏览器。软件进程在后台线程中启动仅监听 `127.0.0.1` 随机端口的业务服务，退出
软件时同一进程负责优雅关闭它。

当前采用渐进式原生化：主窗口、概览、合并任务表、资料导入、知识地图
画布、节点/关系证据、候选审核、学习条件、计划预览/激活、结构化课堂、附件、录音
转写、模型连接、账户诊断和备份均为 Qt 原生控件。尚未迁移的 Live2D、远征等旧工具
通过 Qt WebEngine 兼容页继续提供。
兼容页不是最终架构，而是避免重写期间丢失 React Flow、Live2D、Markdown、摄像头、
音频和资料上传等已实现能力的过渡边界。

## 当前本地数据模式

发布版默认使用纯本地文件模式，不显示注册、登录、账号或找回入口。软件直接读取
`E:\MentorFlowData`：已有目录就继续使用其中的任务、资料、知识地图、计划和进度；
没有数据时创建新的学习空间。换设备时在“数据与备份”点击“导出迁移压缩包”，新设备
选择压缩包和一个新目录导入。导入只允许新目录或空目录，不会覆盖正在使用的数据。

迁移包包含文件 SHA-256 清单，并使用 SQLite 在线备份快照数据库。Windows Credential
Locker 中的 Provider API Key 不会进入压缩包，换设备后需要重新输入并保存模型预设。

当前源码不提供账号认证入口。历史数据库可能仍保留认证相关表用于兼容旧数据，
但它们不是当前运行时身份边界，也不代表未来必须恢复远程账号功能。

“模型连接”页把新输入的 Provider Key 直接写入 Windows Credential Locker，随后
清空输入框。Qt 的 `QSettings` 只保存 Base URL、模型名和 `mfcred_...` 不透明引用；
软件重启后使用该引用创建新的进程内 Provider 会话并重新验证连接。原始 Key 不会
进入应用 HTTP API、SQLite、浏览器存储或日志。

## 怎么启动和调试

首次准备环境：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\bootstrap.ps1
```

生成开发版软件入口：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\build_native_desktop.ps1
```

- `release\native\MentorFlow.exe`：真正的 Windows GUI 启动器，监督 Qt 子进程且不显示控制台。
- `release\native\MentorFlow.lnk`：指向上述 EXE 的日常快捷方式。
- `release\native\MentorFlow-Debug.lnk`：显示 Python/Qt 后端输出，适合一起调试。
- `scripts\desktop.ps1`：从 PowerShell 启动日常版本。

开发版 EXE 链接当前源码和当前数据，修改后重启即可验证。可分发版使用 PyInstaller
的 `onedir` 模式冻结 Python、Qt、后端和材料转换依赖：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\build_distributable.ps1
```

生成的 `release\distributable\MentorFlow\MentorFlow.exe` 不依赖项目 `.venv`。它不包含
开发者的数据库、资料或密钥；默认把每个 Windows 用户的数据写到 `E:\MentorFlowData`。
复制整个发布目录给其他人时，对方得到全新的空白本地空间；已有迁移包的用户可以在
“数据与备份”导入自己的数据。

### Windows 安装包完整性与更新

版本唯一来源是仓库根目录的 `mentorflow-version.json`，构建时会复制到安装目录并生成
`SELF-CHECK\package-manifest.json`。发布目录的 `SELF-CHECK\mentorflow-check.bat`
可直接从 `cmd.exe` 运行（即使安装路径含空格）。它优先调用发布包根目录的
`SELF-CHECK\MentorFlowSelfCheck.exe` 独立控制台 helper，因此普通用户无需安装 Python；仅源码树中的
BAT 在 helper 不存在时才回退到 Python。它校验必需文件和 SHA-256，拒绝安装包
内出现 `app.db`、`mentorflow_v2.db`、上传资料或密钥，并只读检查所选数据根的 SQLite
完整性。启动冒烟把 `MENTORFLOW_USER_DATA_ROOT` 指到一次性 E: 目录，并等待应用在
`MENTORFLOW_DESKTOP_SMOKE_SECONDS` 健康检查后干净退出（退出码 0）；仅进程仍存活不算
成功，因而不会迁移、覆盖或锁定正式用户数据。

`SELF-CHECK\mentorflow-update.bat` 是显式的检查/暂存操作，不是静默更新器。它按照
GitHub Releases API 的 owner/repo 配置寻找版本和 release asset，缺少 `sha256:` digest
或下载摘要不匹配就拒绝；通过校验的压缩包存到 `E:\MentorFlowUpdates`，并写出
`update-staging.json` 与 release 页面链接。工具不替换当前 EXE，也不改动任何用户数据。
用户应审阅发布页和摘要，退出 MentorFlow 后按发布说明手工安装；若仓库未配置或 API 不可用，
保持当前版本并使用 release 页面手工安装。

## 已完成和下一阶段原生化顺序

1. 已用 `QGraphicsScene` 实现通用 DAG 节点、关系、缩放、平移、证据和候选审核。
2. 已用 Qt Model/View 实现资料导入、转换状态、条件回执和计划预览/激活。
3. 已用原生课堂消息模型实现结构化教学、回答、纠错、附件和录音文件转写；实时麦克风
   采集和更完整的代码编辑器仍待后续增强。
4. 最后处理 Live2D/WebGL；它可能长期保留为隔离的渲染表面，因为这类 GPU 内容
   用 WebGL 实现并不等同于把整个产品做成网页。

## 许可证边界

PySide6 是 Qt 官方 Python 绑定，采用 LGPLv3/GPLv3/商业三选一许可。本项目目前按
动态链接、保留 Qt 许可证文本且不修改 Qt 库的 LGPL 使用路径设计；正式对外分发
独立安装包前必须再次完成许可证清单和可替换库验证。

面向没有数据库经验的使用说明见 `docs/native-desktop-user-guide.md`。
当前磁盘的验收结果和未完成边界见 `docs/native-desktop-acceptance.md`。
