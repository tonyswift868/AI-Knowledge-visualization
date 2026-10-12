# 预览版真安装与更新排练（UIA + CDP）

仅对任务目录内 `.UAT.` 包操作；不运行正式安装、不连接正式账号/模型、不修改正式凭据。Node 24 使用内置 WebSocket；UIA 使用已有 PowerShell 7（必要时设 MF_POWERSHELL）。不发送系统鼠标或键盘，不调用 UIA SetFocus，不靠抢前台操作。所有命令从本目录执行，Python 用 D:/MentorFlow/.venv/Scripts/python.exe。

先设置 MF_UAT_ROOT=D:/MentorFlow/.local/test-tmp/inapp102c，LOCALAPPDATA 指向其 local，TEMP/TMP/TMPDIR 指向其 temp，PYTHONDONTWRITEBYTECODE=1；创建这些目录。PYTHONPATH 为 D:/MentorFlow/.local/test-tmp/installer/build-tools;D:/MentorFlow-office/runtime/repo。PowerShell 辅助脚本也继承这些变量。另一轮需换任务目录并在 common.py 改独立 APP_ID，不能同时占用相同端口。

1. **构建与本机源**：`python prepare.py` 生成本轮 Ed25519/CA，已有密钥不覆盖；隐藏启动 `python servers.py`。更新源 18763，HTTPS 账号替身 18764，验证码 123456。`python build.py X` 冻结产品并编真实预览 Inno；`python build.py Y` 复制 X、改版本并重新签名。将 release-X/stable.json 放到 webroot/updates/app/stable/stable.json。只用自己的测试密钥；结束删除私钥。旧版 O：git archive 5d7c211e 到任务目录，`python build.py O --source <旧源码>`。故障/计时钩子仅在预览包中；账号层使用 HTTPS 替身，更新链与安装核心保持真实。
2. **安装记录留证与全新安装**：先 `snapshot.ps1 -Out <任务目录>/before.json`，结束再比较快照。`python scenario.py fresh --root <任务目录>/fresh/MentorFlow`：真实向导→启动 Updater→CDP 注册→UIA 登录→CDP 存假 Key→正常关闭→重启核对 Key。Inno 父进程启动 .tmp 子进程，UIA 应操作子进程。所有启动均为桌面快捷方式实际目标 Updater/UserRuntime.exe launch --root；启动器 LOCALAPPDATA 与 App 的 .MentorFlow-runtime 不同。
3. **主界面更新**：`python scenario.py main-update --root <同一安装根>`：CDP 改极光夜→发布 Y→检查更新→网页按钮“立即更新并重启”→观测自退出与新窗口。`check.py <根> --version 9.102.6` 检查 HEALTHY、Updater 与 pending；用 CDP 核对主题、Key、自动登录。CDP 仅在测试进程开启 127.0.0.1:18861。`observe.py` 读真实事务、ACK、OS 进程状态及窗口日志，用 PrintWindow 截更新窗。
4. **覆盖升级**：先 `scenario.py uninstall --root <上一安装根>` 清除本轮预览记录，避免相同 AppId 复用。`scenario.py cover --root <任务目录>/upgrade/MentorFlow`：真实 O 安装→启动并退出→过期 pending/ROLLED_BACK/小垃圾及三份学习文件→真实 X 覆盖→启动两次→检查学习文件 SHA256 不变、垃圾清理、Updater、HEALTHY。`dirty.py` 仅造小测试状态，不复制用户目录。
5. **上一轮场景**：每场景使用新装 X，更新源先放 X。登录入口：`scenario.py login-update`；保存故障：登录主界面后 `scenario.py blocked-update`（预览 hook 阻塞 33 秒），核对中文待安装提示→UIA“关闭软件”→Updater 启动安装 Y；已下载待装 X 用 `scenario.py next-start` 关闭再启动。`launch.py` 可在协调器工作时再运行一次，核对门闩、不产生第二 App。--release/--target/--x-setup/--o-setup 可指定修复后重新构建的包，不能沿用修前签名产物。
6. **低层操作与留证**：`node cdp.mjs inspect '' '' learning` 看当前页；click/input/shot/wait-text/evaluate 操作真实 DOM/CDP。原生 `uia.ps1 -TargetPid ... -Action tree/invoke/value/toggle/shot`。退出要 Invoke 真标题栏“关闭”（登录）或“Close”（主界面），产品拦截 WindowPattern.Close，不能当成退出成功。logs/scenarios.jsonl 不记输入的密码、Key、令牌。timing_hook 记录函数耗时；新运行另存 reconstruction-*.json 细分 IO，观测时间与函数时间须区分。
7. **收尾**：截图、API/HTTP 状态、事务和计时写报告。替身未实现学习同步，因此“同步失败”仅是夹具限制。预览原钩子隔离账号凭据；本工具额外隔离模型凭据服务 Learning.Models.v1，不读正式 Key。用本轮预览卸载器清理本轮注册/快捷方式，正式快照应不变。大文件即删；被拦就列路径，不绕过。只留报告、截图、小脚本；工具不进入 pytest 自动收集。

复用现有 PyInstaller（GPL 打包例外）、Inno Setup（免费许可）、Python/Node 标准库、Windows UI Automation（系统组件），未新增依赖或更新机制。

103 轮：APP_ID 与模型凭据服务独立为 class103；MF_UAT_ROOT 指到 class103。
用 Python tarfile 解包 git archive 10e625f4（Windows tar 对中文资源名可能失败），
`build.py O --source <解包目录>` 构建 2.0.13，`build.py X --source <最终工位>`
独立冻结 9.103.14（不要用复制 X 改版本的 Y）。若最后只改网页，`python final103.py`
重新打网页并给完整最终包签名为 N。两包齐全后 `python scenario103.py`：
真实 O 安装/登录/保存测试 Key → 主界面入口更新 X → 核对 → 本轮卸载 → 正式快照比较。
输入通过 DOM 原生 setter 和 input/change 事件，不发送 CDP Input 键盘操作。
复制旧资源后必须重新写入并核验本机 public-runtime.json 与 CA；`refreeze103.py`
会检查包内地址，并重新冻结测试用 HTTP 本机限制。不要用整目录覆盖已准备的本机配置。
若真实更新已自行打开新版而检查失败，`scenario103.py --resume --verify-current`
只核对当前进程、保留真实失败结果并卸载，不再更新或重启来掩盖故障。
103 实测：App HEALTHY、pending 清除、设置/Key/新网页均正确；Updater 刷新因
更新锁忙而 DEFERRED，仍为旧版。该项未通过，不能把排练脚本退出成功当作发布通过。

104 轮：MF_UAT_ROOT 指向 D:/MentorFlow/.local/test-tmp/inapp104，APP_ID 与模型凭据
服务独立为 inapp104。`prepare.py` 后运行 `build104.py`：用 10e625f4 的 O 与当前
工位 X 独立冻结为 2.0.13 / 9.104.14，并逐包调用 `refreeze103.py --check-only`
核验本机账号配置及两个 exe 内的 localhost 网络限制。然后 `scenario104.py`
真实安装 O、存测试 Key、从主界面更新 X，等待第一次启动的 Updater UPDATED，
再从快捷方式目标正常启动一次，核对 UP_TO_DATE 和 cleanup_complete，最后用本轮
卸载器撤销预览注册和快捷方式、比对正式快照。日志 result104.json、first-startup.jsonl、
final-startup.jsonl 与 scenario-observations.json 保留首次更新证据，不用重启冒充刷新成功。
`metrics104.py` 从真实回执/事务/窗口记录汇总分段耗时；`clear104.py` 只清理本轮
本机测试账号在 inapp104 服务中的凭据和两份测试私钥，完成截图留证后执行。
若 Inno 首次 UIA provider 尚未就绪，先读取本轮向导状态，仅在文件夹选择框已打开时
用 `scenario104.py --installer-pid <本轮向导 PID>` 接续；不重复安装、不模拟输入。

105 轮：MF_UAT_ROOT= D:/MentorFlow/.local/test-tmp/fast105；common.APP_ID 和模型凭据
服务为 MentorFlow.UAT.fast105。先 prepare.py，再分别运行 build105.py O、P、X：
O 用 10e625f4（2.0.13）；P/X 用最终工位代码分别独立冻结为 9.105.1/9.105.2，
不复制改版本。每次冻结自动执行 refreeze103.py <O|P|X> --check-only；先通过才启动。
scenario105.py A / B / NEXT 各跑一次：A 兼容旧版主界面更新；B 等后台就绪后点击；
NEXT 等后台就绪后正常关闭，从真实图标目标启动器安装。每次用真 Inno 新装、
本机 HTTPS 登录、存假 Key/主题，核对新 App、首次 Updater UPDATED、第二次启动
UP_TO_DATE/清理、卸载及正式注册/快捷方式快照。B 的后台准备期间反复 CDP 读取
真实页面，记录响应耗时；所有点击为真实 DOM 或 UIA Invoke，不抢鼠标键盘。
结果 logs/result105-*.json、各路线 observations/first-startup、stages/reconstruction
记录用于汇总。用 clear105.py 删除该通道凭据及测试私钥；保留报告、截图和小脚本。
改动产品源码后用 refinal105.py P / X 从各自 spec 执行 --clean 独立重冻，不能直接
复用旧 PYZ。严格核对命令：refreeze103.py P --check-only --source-root <工位仓库>。
metrics105.py 汇总三条路线；驱动修复后需新安装目录可用 scenario105.py A --run-id A-final。
