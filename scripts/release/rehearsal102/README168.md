# 168-MF04：登录门控预览排练

以下命令只适用于 `D:\MentorFlow\.local\test-tmp\gate168` 的预览数据。先在 PowerShell 中 dot-source `env168.ps1`；它隔离 `LOCALAPPDATA`、`TEMP`、`TMP`，设置 `MentorFlow.UAT.gate168` 并清除代理环境变量。不要并行运行场景。本机 HTTP 更新源只监听 `127.0.0.1:18763`，HTTPS 账号替身只监听 `127.0.0.1:18764`。

每个新 PowerShell 进程先加载仓库里的环境脚本（不是任务目录里的同名文件）：

```powershell
. 'D:\MentorFlow-office\runtime\repo\scripts\release\rehearsal102\env168.ps1'
```

完成最终源码后，先运行 `prepare168.py` 与 `servers168.py`。首次构建用下方不带 `--reuse-frozen` 的 X 命令，它把当前源码独立冻结到 `freeze-X-final`。已有 X 仅需修正 App 源码时，可用 `refreeze_final168.py` 刷新 App；生成的测试签名身份模块也必须复制。每次冻结后先运行：

```powershell
& 'D:\MentorFlow\.venv\Scripts\python.exe' -B 'D:\MentorFlow-office\runtime\repo\scripts\release\rehearsal102\refreeze103.py' X-final3 --check-only --source-root 'D:\MentorFlow-office\runtime\repo'
```

检查必须确认本机账号地址、hook 的 localhost-only 限制及包内更新模块与当前源码一致，否则不得启动。

生成最终签名源：

```powershell
& 'D:\MentorFlow\.venv\Scripts\python.exe' -B 'D:\MentorFlow-office\runtime\repo\scripts\release\rehearsal102\build168.py' X-final3
& 'D:\MentorFlow\.venv\Scripts\python.exe' -B 'D:\MentorFlow-office\runtime\repo\scripts\release\rehearsal102\build168.py' Y-optional-final3
& 'D:\MentorFlow\.venv\Scripts\python.exe' -B 'D:\MentorFlow-office\runtime\repo\scripts\release\rehearsal102\build168.py' Y-required-final3
```

X-final (`9.168.11`) 是本轮唯一独立冻结的产品代码；两个 Y 变体 (`9.168.12` 可选、`9.168.13` 必须) 共用该冻结 App，只改变版本元数据和签名，用于比较指针提示策略与签名清单判定，**不是跨源码版本兼容测试**。Y 不重新编译 Inno 安装程序；场景用已验签的 UserRuntime 安装核心安装到任务目录，不覆盖用户安装，也不测试 Inno 注册/快捷方式行为。

按顺序在不同目录运行三个真实打包场景：

```powershell
& 'D:\MentorFlow\.venv\Scripts\python.exe' -B 'D:\MentorFlow-office\runtime\repo\scripts\release\rehearsal102\scenario168.py' optional --root 'D:\MentorFlow\.local\test-tmp\gate168\o\MentorFlow'
& 'D:\MentorFlow\.venv\Scripts\python.exe' -B 'D:\MentorFlow-office\runtime\repo\scripts\release\rehearsal102\scenario168.py' required --root 'D:\MentorFlow\.local\test-tmp\gate168\r\MentorFlow' --observe-only
& 'D:\MentorFlow\.venv\Scripts\python.exe' -B 'D:\MentorFlow-office\runtime\repo\scripts\release\rehearsal102\scenario168.py' mismatch --root 'D:\MentorFlow\.local\test-tmp\gate168\m\MentorFlow' --observe-only
```

本机服务只延迟完整 `stable.json`，不延迟 `latest.json` 或 `login-policy.json`。optional 必须观察到 `startup_login_gate=optional_pointer` 且签名清单未返回时登录字段可用；required 必须在签名清单返回前保持登录禁用，随后显示无“稍后”的强制更新框；mismatch 用 CDP 检查首页网页全屏模态框（不可用 UIA），要求提示必须安装且无“稍后”。计时同时记录场景 wall-clock 起点、启动器日志时刻和 UIA 观察上界，不能把 UIA 轮询时间当作门控耗时。发现任何非本机请求立即停止。

使用短场景目录，避免完整载荷暂存超过现有240 UTF-16字符路径预算，不修改产品路径限制。`--observe-only`只检查本任务要求的门控和提示框，结果返回测试PID，随后由调用者核对其映像路径位于任务目录再结束测试进程。去掉此参数才执行额外的安装更新收尾；它不属于本任务三个门控场景的完成条件。

场景预热会先验证 X 的签名缓存；启动后仍以 `optional_pointer` 或等待签名清单的门控日志作为放行证据。`verified_cache_ready` 只代表缓存读取，不代表登录放行。安装的 `current-release.json` 和数据目录不改。

每场景结束先归档必要日志/截图，再清理该场景任务目录，避免多个安装副本同时占盘。清理需逐个校验目标仍位于 gate168 下。若删除被拒绝，记录确切路径与大小，不改用其他方式重试，也不删除父目录。禁止触碰 `pointer168-cleanup.ps1` 或此前已被拒绝的目录。任务目录最终只留报告和截图（被拒绝清理的路径另列）；复用脚本保存在仓库中，同时记录正式安装注册、快捷方式和用户进程均未触碰的证据。
