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

132 总验收：沿用 107 驱动，设 `MF_UAT_ROOT=D:/MentorFlow/.local/test-tmp/gate132`、
`MF_UAT_APP_ID=MentorFlow.UAT.gate132`，所有子进程 LOCALAPPDATA/TEMP/TMP 指到该目录。
依次 `prepare.py`、`build132.py O`、`build132.py P`、`build132.py Q`：O 从 64fdf076
独立冻结为 2.0.14，P/Q 从当前工位独立冻结为 9.132.15/9.132.16；O/P 编真实预览安装程序。
每包自动做 refreeze103 --check-only（P/Q 对照最终源码），通过后才启动。
`scenario132.py` 走全新安装/注册/登录/测试 Key/新建计划页；旧版用过并退出后直接覆盖 P，
用 BUG-86 重切夹具检查取代旧点不显示、进入第一句前的课堂；另一份 O→P 软件内更新，
再等 P 后台就绪→Q。所有界面动作仍为 UIA/CDP，未改更新链。
测试 Key 通过真实设置页保存，其模型接口也设为 localhost，避免课堂自动预取访问正式模型。
使用 fixture 的已准备模块，不注入老师回复、不点开始讲解；注册、更新与课堂页面是实际产品。
BUG-86 夹具沿用原测试的数据关系，但计划编号改为桥接口要求的 32 位十六进制；
原单元测试的 `p` 只适用于 Store，不能直接用于真界面。若修正驱动后要接续已通过的
全新安装，可 `scenario132.py --remaining --cover-folder <新的任务内目录名>`，保留失败记录，
重新走覆盖和更新，不能把失败步骤标成通过。
若真实覆盖已通过、只在后续 UI 等待上修正驱动，用 `--verify-covered`：保留原覆盖
证据，在新的任务目录安装同一 P 包，复制本轮保留的 Data 后接续 UI/更新验证；报告
注明接续边界。这不是再次覆盖测试，也不能把它冒充原来失败的覆盖安装。
课堂的知识点默认收起，先用实际“展开知识点”按钮核对，不等待隐藏列表里的文字。
驱动修正后若需把覆盖安装→详情→课堂→第二次启动连贯重走，更新已通过时可用
`--remaining --cover-only --cover-folder <新的任务内目录名>`，不重复已通过的两次更新。
已通过覆盖 UI 后可用 `--updates-only --update-folder <新的任务内目录名>` 仅接续更新。
更新等待以真实“立即更新并重启”按钮可点为准，最多 180 秒；后台重建不受 CDP 通用
30 秒文字等待限制，超时仍明确失败，不跳过下载或准备。
VCL 按钮在 .NET UIA 代理拿不到 InvokePattern 时，uia.ps1 改用系统原生 COM UIA 的
同一 InvokePattern（native_uia.py）；仍核对 HWND 的 PID 和任务目录所有权，不模拟输入。
result132.json、各更新 observations/startup、截图留证；最后按既有卸载器撤销预览注册和
快捷方式，对照正式版全目录/注册/快捷方式快照。`clear132.py` 仅清本轮测试凭据和私钥。

107 总验收：先设 `MF_UAT_ROOT=D:/MentorFlow/.local/test-tmp/gate107`、
`MF_UAT_APP_ID=MentorFlow.UAT.gate107`，LOCALAPPDATA/TEMP/TMP 同样指到任务目录。
common 和预览 hook 共用此 AppId 的模型凭据命名空间，默认仍兼容 fast105。
依次运行 `prepare.py`、`build107.py O`、`build107.py P`、`build107.py Q`。
O 从 10e625f4 解包；P/Q 从当前工位独立冻结为 9.107.14/9.107.15；
所有安装程序使用当前工位的 Inno 脚本。每包自动执行 refreeze103 --check-only，
P/Q 额外核对冻结更新模块与工位源码一致；不允许跳过检查就启动。
`scenario107.py` 串行执行真实全新安装/注册/登录/存 Key/打开新建计划、卸载、
真实 O 安装→主界面更新 P→二次启动→P 原位置覆盖（读取 UIA 位置框）→后台
就绪→主界面更新 Q→二次启动→卸载。版本、HEALTHY、pending、Updater UPDATED、
课程库 schema_version=10、设置/Key 和覆盖前后数据哈希都用真实运行结果核对。
复用 installer105 的只读全目录/两种注册表视图/快捷方式快照，检查正式版和默认
安装位置前后不变。结果及分段日志在 logs/result107.json、*-observations.json、
*-startup.jsonl；失败保留到具体步骤，不用离线替身替代。完成后 clear107.py 只删
本轮测试凭据和私钥，再清理任务内构建等中间产物。正式签名、服务器和用户数据不动。

160-MF04（BUG-109 登录更新门闩）：必须先显式设置 `MF_UAT_ROOT` 为本轮 `gate160`，
`MF_UAT_APP_ID=MentorFlow.UAT.gate160`，并让 `LOCALAPPDATA`、`TEMP`、`TMP`、`TMPDIR`
及 `PYTHONIOENCODING=UTF-8` 指向任务目录。`build160.py`、`publish160.py`、`scenario160.py`
在导入公共配置前都会拒绝缺少 `MF_UAT_ROOT` 或使用其他 AppId，防止静默落到旧任务目录。
冻结子进程还需 `PYTHONPATH=D:/MentorFlow/.local/test-tmp/installer/build-tools;<repo>`，以使用
已有的 PyInstaller/Pillow/cryptography 工具；不安装新依赖。

本轮最终包使用 `build160.py X-release160b`，从最终工位独立冻结 X（9.160.1），编真实预览
安装程序、本机签名清单和 objects；随后 `build160.py Y-required-release160` 与
`Y-optional-release160` 从该冻结 payload 派生策略清单。每个产品包启动前运行
`refreeze103.py X-release160b --check-only --source-root <最终工位>`，确认签名公钥/本机账号
地址、两个 exe 的 localhost-only hook，以及冻结的更新服务、安装/启动、登录与 handler 模块
和源码一致。冻结包只在本地测试源 `127.0.0.1:18763`，账号替身只在 `localhost:18764`；发现
非本机请求立即停测。

每个策略场景使用独立 task-owned 安装根和该根自己的签名缓存；同一 `MentorFlow.UAT.gate160`
不可并行安装或启动多个 App（共享 CDP 端口 18861），场景间需确认上一个 App PID 已退出。
`scenario160.py core-install --root <任务根>/MentorFlow` 用冻结包内同一 UserRuntime 安装核心，
写入并校验实际 HEALTHY 安装；后续命令接着这个根运行真实冻结 App/Updater，不是桥接替身。
策略操作为 `no-update-timing`、`offline-cache`、`mandatory-login`、`classroom-mandatory`、
`optional-login`。推荐顺序：先在 fresh X 根跑 `no-update-timing` 与 `offline-cache`；
另一个 fresh X 根发布 required Y、设 `logs/metadata-delay.json` 为 `{"seconds": 3}`，立即
提交登录，验证 15 秒门闩内的 mandatory 结果；课堂另用 fresh X 根，发布 required Y 后设
3 秒逐请求延迟（低于现有网络期限），从实际设置页触发检查、回到课堂输入未发送草稿，再验证先持久保存/弹强制框并
在更新重启后从后端恢复草稿；optional 场景也用 fresh X 根和 optional Y，核实“稍后”能登录。
延迟文件删除即恢复无延迟，创建 `logs/update-feed-offline` 只让本机更新源返回 503，账号替身
保持在线。超过网络期限的人工延迟只能证明网络失败，不能冒充更新结果或课堂保存通过。

基线普通 Python 测量不能冒充真包。早期 X-persist 候选真包的一次延迟排练检查约 12.344 秒，
其中指针 5 秒、完整清单 5.25 秒；它已含本轮门控实现，不是未修改的基线。不能仅凭清单
source_sha 认定冻结源码（未提交构建仍可写旧 HEAD）。该延迟场景不等价于最终包缓存未变场景，不据此推断用户公网 9 秒的根因。最终包
必须记录真实 localhost 指针及签名清单分段耗时。UIA 仅用 Invoke/ValuePattern，不用系统输入；
CDP 只连接本机测试窗口。日志、截图、缓存与副本均留在任务目录，最终清理预览注册、快捷方式、
凭据和测试私钥；正式安装与用户数据不动。

239-MF04 最终真包：固定任务根 `D:/MentorFlow/.local/test-tmp/fix239u` 和 AppId
`MentorFlow.UAT.fix239u`，先 dot-source `env239.ps1`，确认 localhost-only 服务在
`127.0.0.1:18763/18764`；复用 task 内测试证书/私钥和现有锁定 build-tools。
`D:/MentorFlow/.venv/Scripts/python.exe build239.py X --source D:/MentorFlow/.local/test-tmp/fix239u/source-final239` 冻结
`X239-lock-final`（9.239.1）、签名并生成本机安装包；`D:/MentorFlow/.venv/Scripts/python.exe build239.py Y-optional` 和
`D:/MentorFlow/.venv/Scripts/python.exe build239.py Y-required` 分别生成 `Y-optional239-lock-final` 与 `Y-required239-lock-final`
（9.239.2 可选/强制策略包）。每包启动前都运行
`refreeze239.py <X|Y-optional|Y-required> --check-only --source-root D:/MentorFlow/.local/test-tmp/fix239u/source-final239`；检查失败时禁止运行。
最后依次 `scenario239.py a --run-id 0`（可选更新已准备但无授权，关闭重开仍为旧版，再显式点击更新）、
`scenario239.py b --run-id 0`（登录入口强制门闩）和 `scenario239.py c --run-id 0`（阻止一次退出后下次启动完成更新）。
所有 HTTP 与账号替身仅指向本机；CDP 只连 `127.0.0.1:18861`，UIA 仅调用真实控件的
Invoke/ValuePattern，不模拟鼠标/键盘。环境边界检查、pending 授权、最终版本、安装分段/前台提示
和截图都写进 task-local logs/screenshots；不得用正式密钥、正式账号或公网请求。
