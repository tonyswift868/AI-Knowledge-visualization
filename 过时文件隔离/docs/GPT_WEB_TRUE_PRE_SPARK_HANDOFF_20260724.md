# MentorFlow 真正的 pre-Spark 恢复交接

生成时间：2026-07-24（Asia/Shanghai）

## 先看结论

当前工作区已经覆盖为从 2026-07-15 至 2026-07-16 历史会话证据重建出的 pre-Spark 主线状态。本轮没有新增产品方向，也没有执行 `git reset`、`git clean`、删除历史成果或切换分支。

本次恢复的核心用户可见状态：

- 左侧导航已恢复为简化结构：总览、创建计划、计划日历、AI教师角色库、进度与掌握、学习远征、Provider 设置。
- 不再把“学习主流程 / 今日学习 / 学习剧场 / 选择故事”放在左侧主导航。
- 学习远征恢复为一个所有英雄共享的全局训练等级。
- 阵容恢复为 3×3 九宫格，五名英雄可自由拖放和交换，不是固定十字位。
- 战斗恢复为真实学院战场画布、双方 5 人、后端事件序列、技能动作、受击、反击与战败姿态；不是网格占位演示。
- AI 教师角色库恢复为通用白模 + 1529 张本机角色卡入口、身体/面部参数入口和按需 WebGL 预览。
- 教学流恢复日轮换教师、共享教师记忆、引用小测、独立提问、Persona 与教学脚手架，同时保留较新的幂等、证据校验和奖励补偿合同。

## 当前运行方式

项目根目录：

`E:\aiPlay\AI-Knowledge visualization`

后端：

```powershell
cd "E:\aiPlay\AI-Knowledge visualization\backend"
E:\aiPlay\AI-Knowledge visualization\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

前端：

```powershell
cd "E:\aiPlay\AI-Knowledge visualization\frontend"
npm.cmd run dev -- --host 127.0.0.1 --port 5173
```

浏览器打开：

`http://127.0.0.1:5173/`

当前交接时后端 8000 与前端 5173 已实际启动。

## 已执行验证

后端定向回归：

```powershell
cd "E:\aiPlay\AI-Knowledge visualization\backend"
$env:PYTHONPATH=(Resolve-Path '.').Path
E:\aiPlay\AI-Knowledge visualization\.venv\Scripts\python.exe -m pytest tests\test_expedition.py tests\test_teacher_rotation.py tests\test_learning_run.py -q
```

结果：`19 passed, 1 warning`。警告是 Starlette/httpx 的弃用提示，不是测试失败。

前端生产构建：

```powershell
cd "E:\aiPlay\AI-Knowledge visualization\frontend"
npm.cmd run build
```

结果：成功，`2260 modules transformed`。仅有大 chunk 警告。

真实浏览器验收：

- 健康接口 `/api/health` 返回 `status: ok`。
- 简化侧栏正确显示，且不存在上述已移除的主导航项。
- AI 教师页出现 1 个 WebGL canvas、1529 张角色卡、通用白模与参数入口，无页面错误提示。
- 远征大厅显示“全局训练 / 所有角色共享等级”。
- 九宫格页显示 9 个格子、5 名已上阵英雄和自由拖放元素。
- 挑战成功落盘，回放约 355 个后端事件。
- 补齐 26 张历史 PNG 后，浏览器真实显示学院战场背景与双方 5 人战斗画布。

## 关键恢复证据

远征稳定截止点：

- 来源会话：`rollout-2026-07-15T11-21-45-019f63cb-6ecc-70d2-87ff-f660f3e4c0b4.jsonl`
- 截止：JSONL 第 2786 行，turn `9dff917f-74f6-4071-abfe-e6d5a7b97b32`
- 时间：`2026-07-15T10:23:50.601Z`
- 重建报告（本机外部证据）：`E:\Downloads\MentorFlow_PRESPARK_EXACT_RECOVERY\EXPEDITION_FINAL_STABLE\RECOVERY_REPORT.md`

教师流重建报告（本机外部证据）：

`E:\Downloads\MentorFlow_PRESPARK_EXACT_RECOVERY\TEACHER_FLOW_TARGET\RECOVERY_REPORT.md`

恢复前备份：

`E:\aiPlay\开发指南\study RAG\recovery_backups\before_true_prespark_20260724_121346`

## 后续 GPT 必须遵守的边界

1. 先把当前状态当作恢复基线，不要再从 Gitee 的陈旧提交重新覆盖。
2. 不要把远征退回固定五站位、分别训练或网格占位战斗。
3. 不要针对单张恋活角色卡写兼容分支；问题应修在通用白模、共享骨骼、附件映射或材质管线。
4. 性能优化必须优先减少空闲计算，不得以去掉纹理、蒙皮、材质质量、自然附件位置或参数可调性为代价。
5. 当前 KKS 网页端是 Three.js/WebGL + 离线解析资源，不是把 Unity/恋活原生引擎嵌入浏览器。不得把它描述成原生 Unity 运行时。
6. 本轮只恢复了有证据的 pre-Spark 行为；没有凭空增加“角色自主待机动画”等无法由历史证据确认的能力。
7. 工作区仍是大规模未提交状态。继续工作前必须先读本文件、检查 `git status`，并做可恢复检查点；不要清理或覆盖未提交成果。

## 关键文件

- `frontend/src/App.tsx`
- `frontend/src/features/expedition/ExpeditionView.tsx`
- `frontend/src/features/expedition/BattleReplay.tsx`
- `frontend/src/features/expedition/expedition.css`
- `frontend/src/features/teachers/KoikatsuPmxRig.tsx`
- `backend/app/expedition_engine.py`
- `backend/app/expedition_service.py`
- `backend/app/learning_run.py`
- `backend/app/teacher_rotation.py`
- `backend/app/services.py`

结构化哈希与验证结果见同目录的 `GPT_WEB_TRUE_PRE_SPARK_MANIFEST_20260724.json`。
