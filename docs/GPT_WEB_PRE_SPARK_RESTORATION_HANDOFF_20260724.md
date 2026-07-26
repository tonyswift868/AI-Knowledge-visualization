# MentorFlow：Spark 接手前状态恢复交接

生成时间：2026-07-24  
项目路径：`E:\aiPlay\AI-Knowledge visualization`  
当前分支：`recovery/session-replay-20260723`  
基线提交：`becc82b2f28cb08f6faa71c16f2661a4439de0ac`

## 1. 本次任务结论

本轮没有继续开发新功能，而是把当前项目覆盖恢复到 2026-07-24 11:26 已验收的
`MentorFlow_PRE_SPARK_RECOVERED_GPT_WEB_20260724_1126` 证据快照。

覆盖后对快照内 1734 个文件逐一计算 SHA-256：

- 缺失文件：0
- 内容不同文件：0
- `backend/app/koikatsu_teacher_registry.py` 当前与快照 SHA-256：
  `5738C08CE58E13DC36CF6D90DB5860D9401AB3A25AD25FAB703F38CCE963C23A`

这意味着当前项目已经撤销 11:26 之后的单卡头部实验代码和实验缓存，重新回到该恢复快照的状态。

重要边界：这是“最后一个证据充分且可运行的 Spark 前恢复版”，不是逐字节的 2026-07-22
原始全仓副本。历史材料中没有找到完整、无缺口的 7 月 22 全仓快照，因此禁止向用户宣称
百分之百逐字还原。

## 2. 当前可运行方式

后端：

```powershell
cd "E:\aiPlay\AI-Knowledge visualization\backend"
& "E:\aiPlay\AI-Knowledge visualization\.venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

前端：

```powershell
cd "E:\aiPlay\AI-Knowledge visualization\frontend"
npm.cmd run dev -- --host 127.0.0.1 --port 5173
```

当前服务：

- 前端：`http://127.0.0.1:5173/`
- 后端：`http://127.0.0.1:8000/`
- `GET /api/health`：`status=ok`

## 3. 已恢复并实际验证的状态

### 学习远征

- 激活美术包：`mentorflow_expedition_art_v1@1.0.0`
- 状态：`ACTIVE`
- fallback：`false`
- 官方文件：528
- 运行时资产：96
- 审核通过：96
- 浏览器实测前 8 张角色立绘均从
  `/api/expedition/art-assets/.../file` 加载成功，尺寸为 1024×1536。
- `BattleReplay.tsx` 仍保留技能切入图、技能图标与 Phaser tween 调用链。

关键文件：

- `backend/app/expedition_art_pack.py`
- `frontend/src/features/expedition/BattleReplay.tsx`
- `content/expedition_art_packs/mentorflow_expedition_art_v1/1.0.0/manifest.json`
- `data/mentorflow_v2.db`

### AI 教师与恋活角色卡

- 本机角色卡目录可读：1529 张。
- 共享原生白模可用。
- 浏览器实测 AI 教师页面可打开，WebGL 画布存在，通用白模有头、有身体。
- 首张卡 `eb8136372690f12d` 的 HTTP 运行清单返回：
  `status=partial`、`items=13`。
- HTTP 选卡路径不会启动 Koikatsu/Unity，也不会执行全量 1529 张卡预热。
- 页面保留 Spark 前真实证据中的两套并存 UI：
  上方 KKS 通用白模/角色卡编辑器；下方旧 VRM/GLB 本地轮换库。

关键文件：

- `frontend/src/features/teachers/KoikatsuCardEditor.tsx`
- `frontend/src/features/teachers/KoikatsuPmxRig.tsx`
- `frontend/src/features/teachers/TeacherAvatarStudio.tsx`
- `backend/app/koikatsu_teacher_registry.py`
- `backend/app/teacher_avatar_registry.py`
- `data/kks-assets/shared-base/neutral-body/manifest.json`
- `data/kks-assets/shared-base/neutral-head/manifest.json`

## 4. 验证结果

实际执行：

```powershell
cd frontend
npm.cmd run test -- --run src/test/koikatsu_runtime_semantics.test.ts src/test/expedition_assets.test.ts
```

结果：2 个测试文件通过，59/59 tests passed。

```powershell
& ".\.venv\Scripts\python.exe" -m pytest backend\tests\test_expedition_art_pack.py -q
& ".\.venv\Scripts\python.exe" -m pytest backend\tests\test_expedition.py -q
```

结果：13/13 与 7/7 通过，共 20/20；只有 Starlette/httpx 弃用警告。

```powershell
cd frontend
npm.cmd run build
```

结果：构建成功。存在超过 500 kB 的 chunk 警告，不是构建失败。

API/浏览器：

- 前端 `/`：HTTP 200
- 后端 `/api/health`：`ok`
- 活跃远征美术包：ACTIVE，fallback=false
- 本地卡数量：1529
- 首卡 runtime manifest：HTTP 200，partial，13 items
- 学习远征官方 PNG：浏览器加载成功
- AI 教师页：浏览器打开成功，白模头部与身体可见

## 5. 必须如实保留的缺口

1. 没有找到逐字节完整的 2026-07-22 全仓快照。
2. 历史证据显示 Spark 前旧轮换库曾有两个早坂爱 PMX 目录：
   `data/teacher_avatars/kks-hayasaka-ai-kkbp-new-2c6e7b9344` 和
   `data/teacher_avatars/kks-01413f1d1ea31292-5bc73f93d9`；
   当前磁盘和恢复包中都没有这些目录，也没有可安全恢复的源文件。
3. 当前 KKS 头部是共享可见回退头，不是每张卡精确对应的原生头部与 ShapeAnime。
4. 当前 KKS 白模是按需单帧渲染和静态自然姿态，没有真实的待机骨骼动画、眨眼或动作状态机。
5. 下方旧轮换库为空；其预览组件是历史二维参数预览，不代表真实 PMX 已导入。
6. 首张卡运行清单为 `partial`，不能将其描述为所有服装、附件和材质均完整匹配。

## 6. 恢复保护与回退

11:26 之后的实验没有直接删除，已移出项目并保存在：

`E:\aiPlay\开发指南\study RAG\recovery_backups\post-1126-experiment-20260724-113616`

其中包括：

- `koikatsu_teacher_registry.post-1126.py`
- 单卡头部实验缓存 `5dd04c37ccf4b730`

该实验代码不是当前恢复基线，不应未经重新审计就覆盖回来。

数据库恢复前备份仍位于：

`E:\aiPlay\开发指南\study RAG\recovery_backups\mentorflow_v2.before-artpack-restore.20260724-111156.db`

SHA-256：
`BE7EFFA8F3A673D3B928C5B4648141667F4CA746786277D997B8F09F804B2FB9`

## 7. 给下一位 GPT 的要求

先把本包当作规划基线，不要立刻重写 KKS 架构。

规划时必须：

1. 保留官方远征美术包、现有共享白模、1529 张卡索引和按需渲染成果。
2. 区分“已验证恢复”“历史证据”“未知/缺失”，不得把缺失的两个 PMX 目录伪造出来。
3. 把每卡精确头部/ShapeAnime、服装骨骼与材质匹配、真实低成本动作分成可独立验收的阶段。
4. HTTP 选卡路径继续禁止启动游戏、Unity 或请求时转换。
5. 不进行全量 1529 张卡预热，先用代表性样本验证通用管线。
6. 在任何新修改前先保存当前 Git 状态、数据库和关键 KKS 资产清单。

