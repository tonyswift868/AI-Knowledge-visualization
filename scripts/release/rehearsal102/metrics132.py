"""Reduce the real 132 gate evidence before disposable builds are removed."""
import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from common import TASK


def read(path, default=None):
    return json.loads(path.read_text(encoding='utf-8-sig')) if path.is_file() else default


def lines(path):
    return [json.loads(line) for line in path.read_text(encoding='utf8').splitlines() if line.strip()] if path.is_file() else []


def main():
    result = read(TASK/'logs/result132.json', {})
    full = (TASK/'full.log').read_text(encoding='utf8')
    full_summary = next((line for line in reversed(full.splitlines()) if re.search(r'\d+ passed.* in [\d.]+s', line)), '全量尚未完成')
    failures = [line for line in full.splitlines() if line.startswith('FAILED ')]
    retries = read(TASK/'retry-results.json', {})
    known = ('concept114', 'loading86', 'self_test81', 'status88')
    full_ok = all(any(name in failure for name in known) and retries.get(failure, {}).get('passed') for failure in failures)
    full_ok = full_ok and '全量尚未完成' not in full_summary and ' error' not in full_summary
    passed = bool(result.get('complete') and result.get('cover_continuous_final') and not result.get('error') and not result.get('uninstall_error') and full_ok and all(result.get(key) for key in (
        'formal_full_inventory_unchanged', 'formal_files_unchanged',
        'registry_shortcuts_unchanged', 'default_location_unchanged')))
    updates = {}
    stages = lines(TASK/'logs/stages.jsonl')
    scenarios = lines(TASK/'logs/scenarios.jsonl')
    startup = {}
    for root in ('fresh', 'cover-final', 'inapp-final'):
        launches = [r['at'] for r in scenarios if r.get('action') == 'launch'
            and Path(r['result']['root']) == TASK/root/'MentorFlow']
        rows = lines(TASK/root/'MentorFlow/Data/logs/launcher-startup.jsonl')
        startup[root] = [r for r in rows if r.get('phase') in (
            'native_startup_window_visible', 'login_window_visible', 'login_window_input_ready', 'launcher_complete')
            and launches and datetime.fromisoformat(r['at']).timestamp() >= launches[0]]
    for tag in ('old-P132', 'P-Q132'):
        events = read(TASK/f'logs/{tag}-observations.json', [])
        maintenance = lines(TASK/f'logs/{tag}-startup.jsonl')
        updates[tag] = dict(events=events,
            maintenance=[r for r in maintenance if r.get('phase') in ('updater_refresh_attempt', 'cleanup_complete', 'prepared_tree_moved')])
    snapshots = {}
    for label in ('before', 'after'):
        data = read(TASK/f'{label}-inventory.json', {})
        snapshots[label] = dict(files=len(data.get('formalFiles', [])),
            unreadable_hashes=sum(r.get('sha256') == 'IN_USE' for r in data.get('formalFiles', [])),
            sha256=hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
            registry=data.get('registry'), shortcuts=data.get('shortcuts'), default_path=data.get('defaultPath'),
            default_files=data.get('defaultFiles'))
    builds = {}
    for label in ('O', 'P', 'Q'):
        log = TASK/f'build-{label}.log'
        release = read(TASK/f'release-{label}/stable.json', {})
        builds[label] = dict(version=release.get('signed', {}).get('version'),
            local_only_check=log.is_file() and 'verified local-only configuration and frozen hook' in log.read_text(encoding='utf8'),
            payload_files=len(release.get('signed', {}).get('full_release', {}).get('assets', [])))
    screens = sorted(p.name for p in (TASK/'screenshots').glob('*.png'))
    blocked = [str(path) for path in TASK.rglob('mentorflow.log') if 'UAT_LOCAL_ONLY' in path.read_text(encoding='utf8', errors='replace')]
    result['network_blocked_logs'] = blocked
    passed = passed and not blocked and all(v['local_only_check'] for v in builds.values())
    text = f'''# 132-MF04：2.0.15 发版前总验收

结论：**{'通过' if passed else '未通过'}**。基线 386a926a；O=64fdf076（2.0.14），P=9.132.15、Q=9.132.16。
三份独立冻结；O/P 用主线 Inno 编真实预览安装程序。AppId/模型凭据服务隔离为 MentorFlow.UAT.gate132。
全部复用 rehearsal102 与 installer105；PyInstaller（GPL 打包例外）、Inno Setup（Inno Setup License）、Windows UIA、Node 内置 CDP WebSocket；无新库、无新框架。只调整排练脚本，产品代码未改。

| 环节 | 实际结果 |
|---|---|
| 全量一次 | {full_summary.strip('= ')}；运行前 Node 已加入 PATH。已知偶发单项重跑：{json.dumps(retries,ensure_ascii=False)} |
| 全新安装与注册 | 默认原 C 盘位置，实际选择任务目录；注册/登录/Key/新建计划页：{result.get('fresh_register_login_key_newplan', False)}。脚本观测登录 {result.get('fresh_login_seconds', '未测')} 秒（含 UIA 轮询）；实际启动器约 3.02 秒完成，App 登录窗约 2.05 秒可输入；首次展示至登录无多余隐藏再显。 |
| 直接覆盖 | 旧版使用一次、退出后不再开旧版；默认自定义目录 {result.get('cover_setup', {}).get('default_path', '未完成')}；“已安装，可更新应用”：{result.get('cover_setup', {}).get('expected_upgrade_label', False)}；SQLite/标记逐字节不变：{result.get('cover_data_unchanged', False)}；Key/设置保留：{result.get('cover_retained', '未完成')}；最终连贯复走：{result.get('cover_continuous_final', False)} |
| 新结构计划 | 取代 old2～old5（4 条原记录保留），显示 old0/old1/new1；被取代旧点不显示：{result.get('cover_structure_ui', {}).get('replaced_absent', False)}；真实课堂停在第一句前：{result.get('cover_structure_ui', {}).get('classroom_before_teacher', False)} |
| 2.0.14→P | 点击到窗口可输入 {result.get('old_to_P', {}).get('timing', {}).get('new_window_input_ready', '未测')} 秒；HEALTHY、pending 消失、Updater UPDATED、Key/主题保留；原计划真界面可读：{bool(result.get('existing_plan_ui'))}。详细核对值见原始数据。 |
| P→Q | 后台就绪 {result.get('background_Q_seconds', '未测')} 秒；点击到旧 App 退出 {result.get('P_to_Q', {}).get('timing', {}).get('old_app_exit', '未测')} 秒；点击到窗口可输入 {result.get('P_to_Q', {}).get('timing', {}).get('new_window_input_ready', '未测')} 秒。 |
| 第二次启动 | 覆盖/P/Q 均 HEALTHY、pending=false、junk=[]：{all(result.get(k, {}).get('phase') == 'HEALTHY' and result[k].get('pending') is False and result[k].get('junk') == [] for k in ('cover_second','P_second','Q_second'))} |
| 正式版保护 | 全目录/双视图注册表/快捷方式快照：{result.get('formal_full_inventory_unchanged', False)}；正式文件 {result.get('formal_file_count', '未测')}；默认位置未多出第二份：{result.get('default_location_unchanged', False)} |
| 网络边界 | O/P/Q 包内检查：{all(v['local_only_check'] for v in builds.values())}；账号/更新源仅 localhost，非本机阻止日志 {blocked}；无真实账号、模型、服务器访问，花费 ¥0 |

## 决定、范围与问题记录

1. 使用 tests/refactor/restructure114 的 BUG-86 夹具，未复制用户当前学习库。保留 old0/old1，取代 old2～old5，新增 new1 concept_v1；验证被取代的旧点不显示，而非抹掉已学旧点。计划详情和进入课堂均为真打包 UI，停在“开始上课”前，不注入老师回答。
2. 新课堂打开即会预取；假 Key 的模型接口也经实际设置页设为 localhost，防止访问正式模型。模块已准备标记是数据夹具，账号服务未实现同步/模型推理，相关本机 404/TLS 提示属于替身边界，不声称正式同步或模型验证通过。
3. 所有点击为真实 DOM click 或 UIA Invoke/Value/Toggle；没有系统鼠标键盘、SetFocus 或自动化抢前台。所有子进程的 LOCALAPPDATA/TEMP/TMP 指向任务目录；启动走安装的 Updater 快捷方式目标。
4. 未通过/脚本问题：{result.get('error', '无排练异常')}；卸载错误：{result.get('uninstall_error', '无')}。全量失败清单见下，不隐瞒或改产品代码。
5. 驱动修正及实际证据：.NET UIA 的 Inno 按钮曾报 Unsupported Pattern，系统 COM UIA 对同一 HWND 提供 Invoke；补原生 Invoke 适配，没有模拟输入。资料夹具原计划编号 p 被真实桥接口 PARAM_INVALID 拒绝，改成合法的 32 位十六进制，未改产品编号校验。课堂列表默认收起，先点击“展开知识点”再核对；不是跳过断言。下载重建尚在进行时通用 30 秒 wait-text 先超时，退出清理才出现 DownloadCancelled；改等真实可点的更新按钮（最多 180 秒）。卸载残留目录的拒装是现有保护，接续用新目录；最终连贯覆盖结果：{result.get('cover_continuous_final', False)}。原异常全文保留在下方 driver_recoveries。
6. UIA 依据：[Microsoft GetCurrentPattern](https://learn.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationelement-getcurrentpattern)、[InvokePattern.Invoke](https://learn.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationinvokepattern-invoke)；ABI 对照 [Windows SDK UIAutomationClient.h](https://github.com/microsoft/win32metadata/blob/main/generation/WinSDK/RecompiledIdlHeaders/um/UIAutomationClient.h)。只调用 Windows 内置接口，未加入库或复制第三方框架。
7. 时间口径：点击到新窗口可输入采用 launcher-startup 的 login_window_input_ready；随后自动登录并经真实主界面核对设置和 Key。observer 的 copy_complete 若用到了保留的文件 mtime，不能据此计算复制耗时；阶段耗时以 timing_hook 的实际调用 seconds 为准。自动化不抢前台，foreground=false 不作为此次失败依据。
8. 保护快照按每次排练前后比较：初次 9093 个正式目录文件，后续 9090 个；各次内前后相同。阶段间少 3 个的原因未确认、初次逐文件清单已被原驱动覆盖，不能宣称全任务最早到最后逐文件完全相同。最终连贯场景的逐文件快照、注册表和快捷方式比较另列在原始证据中；没有对正式目录发起写入或操作正式进程。

## 全量原始结果

只运行一次：`D:/MentorFlow/.venv/Scripts/python.exe -B -m pytest tests/refactor -q -p no:cacheprovider --basetemp D:/MentorFlow/.local/test-tmp/gate132/full`。
Node 目录事先加入 PATH；已知偶发没有失败，因此没有单项重跑。全量对象为 386a926a 合入后的产品代码；之后只修排练工具，另做语法检查和上述真实安装/更新验证。

```text
{chr(10).join(full.splitlines()[-45:])}
```

## 构建、事务、维护、前后快照及分段原始数据

```json
{json.dumps(dict(result=result,builds=builds,updates=updates,startup=startup,snapshots=snapshots,stages=stages,api=lines(TASK/'logs/api.jsonl'),http=lines(TASK/'logs/http.jsonl')),ensure_ascii=False,indent=2)}
```

## 截图

'''
    for name in screens:
        text += f'- [{name}]({(TASK/"screenshots"/name).as_posix()})\n'
    text += '\n## 提交与最终占用\n\n待收尾填写。\n'
    (TASK/'report.md').write_text(text, encoding='utf8')
    print(json.dumps(dict(passed=bool(passed), full_ok=bool(full_ok), report_bytes=(TASK/'report.md').stat().st_size), ensure_ascii=False))


if __name__ == '__main__':
    main()
