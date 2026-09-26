# MentorFlow — 员工调度（架构师维护）

只记员工、任务、基线和交付状态；需求与项目状态只在 [CURRENT_STATE.md](CURRENT_STATE.md)，这里只写编号引用。规则同 [AGENTS.md](../../AGENTS.md)：先查索引再改，一条事实只写一处，冲突原位替换。

## 协作方式

- 架构师（Claude）写任务 → 用户转发给员工 → 员工在自己工位完成 → 用户把回执贴回 → 架构师审查、合入主目录、本地提交，审查通过后推送 GitHub。员工不改主目录、不推送。
- 核心部分（前端、教师/判定/检验编排）由架构师自己做；员工做边界清晰、耗时的单模块任务。
- **基线**：每次合入后在主目录本地提交，提交编号即“当前基线”。每条任务第一步都把工位切到指定基线，保证所有人从同一份代码开工。
- **工位代码**：每个工位下的 `repo` 目录是主仓库的 git worktree。员工在自己的分支 `office/<工位名>` 上提交，回执写明提交编号；架构师在主仓库直接审查该分支。

## 当前基线

| 项目 | 值 |
| --- | --- |
| 基线提交 | （待填） |
| 说明 | 需求讨论收口后的开工起点 |

## 员工与工位

| 员工 | 工位 | 状态 |
| --- | --- | --- |
| client | D:\MentorFlow-office\client | 空闲 |
| server | D:\MentorFlow-office\server | 空闲 |
| runtime | D:\MentorFlow-office\runtime | 空闲 |
| platform | D:\MentorFlow-office\platform | 空闲 |
| library | D:\MentorFlow-office\library | 空闲 |
| classroom | D:\MentorFlow-office\classroom | 空闲 |
| tools | D:\MentorFlow-office\tools | 空闲 |

工位名只是称呼，不限制能接的任务类型。

## 任务索引

| 任务 | 员工 | 关联需求 | 基线 | 状态 |
| --- | --- | --- | --- | --- |

## 回执格式

~~~text
【员工｜任务号｜完成/部分完成/受阻】
基线：开工时切到的提交编号。
提交：分支 office/<工位名> 上的提交编号。
改动：修改/新增的文件。
验证：实际运行的命令与结果（通过/失败/跳过）；用了替身的写明。
未完成：没做完或拿不准的地方。
~~~
