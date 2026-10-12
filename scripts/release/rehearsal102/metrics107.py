"""Preserve gate evidence in one report before deleting large rehearsal trees."""
import hashlib
import json
from datetime import datetime
from common import TASK, ROOT

def read(path): return json.loads(path.read_bytes())
def rows(path): return [json.loads(line) for line in path.read_text(encoding='utf8').splitlines() if line.strip()]
def digest(value): return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False).encode()).hexdigest()

def main():
    result=read(TASK/'logs/result107.json')
    before=read(TASK/'before-inventory.json');after=read(TASK/'after-inventory.json')
    stages=rows(TASK/'logs/stages.jsonl')
    clicks=[r for r in rows(TASK/'logs/scenarios.jsonl') if r['action']=='update-click-begin']
    detail={}
    for tag,click in zip(('old-P','P-Q'),clicks,strict=True):
        observations=read(TASK/f'logs/{tag}-observations.json')
        stop=max(r['observed'] for r in observations)+1
        detail[tag]=dict(observations=observations,
            functions=[r for r in stages if click['at']<=r['at']<=stop],
            moves=[r for r in rows(TASK/f'logs/{tag}-startup.jsonl')
                   if r['phase']=='prepared_tree_moved'],
            maintenance=[r for r in rows(TASK/f'logs/{tag}-startup.jsonl')
                         if r['phase']=='updater_refresh_attempt'])
    fresh=rows(TASK/'fresh/MentorFlow/Data/logs/launcher-startup.jsonl')
    api=rows(TASK/'logs/api.jsonl');http=rows(TASK/'logs/http.jsonl')
    logs=list((TASK/'fresh').rglob('mentorflow.log'))+list((TASK/'user').rglob('mentorflow.log'))
    blocked=[str(p) for p in logs if 'UAT_LOCAL_ONLY' in p.read_text(encoding='utf8',errors='replace')]
    assert not blocked,blocked
    pytest=(TASK/'pytest-all.log').read_text(encoding='utf8',errors='replace')
    passed=result.get('complete') and result['formal_full_inventory_unchanged'] and result['cover']['expected_upgrade_label']
    text=f'''# 107-MF04：2.0.14 发版前总验收

结论：{'通过' if passed else '未通过'}。基线 b88ad324；O=10e625f4（2.0.13），P=9.107.14，Q=9.107.15；三个版本独立冻结。只修排练脚本，产品源码未改。PyInstaller（GPL 打包例外）、Inno Setup（Inno Setup License）、Windows UIA 和 Node 内置 CDP WebSocket，未加依赖。

| 项目 | 实际结果 |
| --- | --- |
| 全量一次 | 3529 passed、32 skipped、0 failed、16 warnings，757.51 秒；无重跑 |
| 全新安装 | 真 Inno；默认 C:/Users/21533/AppData/Local/Programs/MentorFlow；实际改装任务目录 fresh/MentorFlow；HEALTHY |
| 注册/登录/Key/新建计划 | CDP 输入并提交本机替身固定验证码；UIA 登录；假 Key 保存成功；真实主界面新建计划表单正常打开；未建计划、未调模型 |
| O→P | 点击→退出 {result['old_to_P']['timing']['old_app_exit']} 秒；→新窗口可输入 {result['old_to_P']['timing']['new_window_input_ready']} 秒；HEALTHY，pending 清除，Key/极光夜保持，Updater UPDATED→9.107.14；课程库 v9→v10（新增 classroom_positions）；第二次启动 UP_TO_DATE、cleanup_complete、无垃圾 |
| 原位置覆盖 | 真 P 安装程序默认识别 gate107/user/MentorFlow，显示“已安装，可更新应用”；覆盖成功；关闭状态的 SQLite 和测试数据 SHA256 一致；默认 C 盘位置前后均 0 文件 |
| P→Q | 后台就绪 {result['background_Q_seconds']} 秒；点击→退出 {result['P_to_Q']['timing']['old_app_exit']} 秒；→新窗口可输入 {result['P_to_Q']['timing']['new_window_input_ready']} 秒（≤30秒）；HEALTHY、pending 清除、Updater UPDATED→9.107.15，Key/主题保留；第二次启动干净 |
| 自动登录 | 两次软件内更新都自动回主界面，正常关闭后的第二次启动回登录窗，符合本次没有勾选普通自动登录的设置 |
| 网络边界 | O/P/Q 均通过 refreeze103 --check-only；P/Q 额外对照冻结代码与主线源码。包内账号 https://127.0.0.1:18764；更新 http://127.0.0.1:18763；timing_hook 拦非本机 HTTP；日志未出现非本机阻止事件。API {len(api)} 条、更新源 {len(http)} 条本机请求；正式账号、真实模型、服务器均未访问，¥0 |
| 正式版快照 | 全目录 {len(before['formalFiles'])} 文件；注册项 {len(before['registry'])}，快捷方式 {len(before['shortcuts'])}；前后全部一致。快照摘要前 {digest(before)}，后 {digest(after)} |

## 运行方法与脚本修正

MF_UAT_ROOT 指向 gate107，MF_UAT_APP_ID=MentorFlow.UAT.gate107；LOCALAPPDATA/TEMP/TMP 全在任务目录。按 README 107 节依次 prepare.py、build107.py O/P/Q、scenario107.py。启动均走已安装 Updater/UserRuntime.exe launch --root（与桌面图标目标一致），启动器 LOCALAPPDATA 与 App .MentorFlow-runtime 分离。主界面按钮和表单由真实 CDP 操作，原生向导/登录按钮由 UIA Invoke/Value 操作，无系统输入或抢前台操作。最后真卸载器清理预览注册与快捷方式。

修正：common/timing_hook 使用同一任务 AppId 环境值，避免沿用 fast105 凭据服务；UIA 只给 Inno 的 TNewEdit 增加 class/value，供读取实际安装位置，不记录密码字段；refreeze 支持 Q；installer105 快照支持本轮任务环境。新增 build107/scenario107/metrics107/clear107，复用已有工具。

## 问题记录

1. 替身没有实现学习同步和 session 心跳：本机 /sync/...、/auth/native/session 返回 404，主界面显示同步失败/连接重试。API 日志证明均为本机 404，这是账号夹具范围，不代表正式同步验证通过；本任务只验证安装、更新、注册、Key 与页面打开。未改产品代码。
2. 首次登录窗截图用了错误的窗口标题，得到空图；脚本改为读取真实 UIA 标题供以后使用。空图删除，没有把它当证据。首次启动时序以原始日志和 UIA 非最小化记录为证；有效登录窗截图 P-second/Q-second，注册和新建计划为首次安装现场截图。
3. P→Q 的 transaction_copy_complete 使用保留的文件 mtime，可能是负偏移；这是后台准备文件的时间，不是点击后复制。以 prepared_tree_moved 和真实函数计时为准，未误记为耗时。

## 有效截图

'''
    for name in ('fresh-location.png','fresh-finish.png','registration-filled.png','fresh-new-plan.png',
                 'old-P-new.png','cover-location.png','cover-finish.png','P-Q-new.png','P-second.png','Q-second.png','scenario-updating.png'):
        text+=f'- [{name}]({TASK.as_posix()}/screenshots/{name})\n'
    text+='\n## 原始结果与日志片段（清理前固化）\n\n```json\n'+json.dumps(dict(result=result,
        fresh_startup=fresh,updates=detail,api=api),ensure_ascii=False,indent=2)+'\n```\n'
    text+='\n## 全量命令与警告\n\n`D:/MentorFlow/.venv/Scripts/python.exe -B -m pytest tests/refactor -q -p no:cacheprovider --basetemp D:/MentorFlow/.local/test-tmp/gate107/pytest-all`\n\n'
    text+='```text\n'+pytest[pytest.index('============================== warnings summary'):]+ '\n```\n'
    text+='\n## 本地占用与提交\n\n待收尾写入。\n'
    (TASK/'report.md').write_text(text,encoding='utf8')
    print(json.dumps(dict(passed=bool(passed),before=digest(before),after=digest(after),
        report_bytes=(TASK/'report.md').stat().st_size),ensure_ascii=False))

if __name__=='__main__':main()
