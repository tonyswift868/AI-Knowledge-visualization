# MentorFlow 恋活建模版恢复交接（2026-07-24）

## 本轮目标

恢复到 2026-07-22 Spark 大范围修改前、以“通用恋活白模 + 原生角色卡 + 44/52 参数 + 服装/附件运行时资源”为主线的版本；不继续开发新功能。

## 当前恢复结果

- 工作区：`E:\aiPlay\AI-Knowledge visualization`
- 分支：`recovery/session-replay-20260723`
- 基准提交：`becc82b2f28cb08f6faa71c16f2661a4439de0ac`
- 前端 KKS 主渲染器已按历史事件闭合恢复：
  - `frontend/src/features/teachers/KoikatsuPmxRig.tsx`
  - SHA-256：`0343CF622E9FAB614B74F1C59570C7CE631A6E1F429011AFE583CCBCE469B4B6`
- 恋活角色卡读取、检查、原生参数改写链路已恢复：
  - `backend/app/koikatsu_card.py`
  - 具备 44 项身体参数、52 项面部参数、ABMX、服装/头部/面部材质引用解析。
- Unity 运行时皮肤导出器已恢复：
  - `backend/tools/kks_runtime_skin_export.py`
  - SHA-256：`F56842C5BA9D39CF236E795D41F086B89702CF24B98F6DDB983B924689C2139F`
- 本地卡片索引已生成：1529 张可枚举角色卡。
- zipmod 索引已恢复到历史同等边界：3272 个归档、14464 行，9 个损坏/不兼容压缩包被记录为 partial。
- 通用原生白模已生成：
  - `data/kks-assets/shared-base/neutral-body`
  - `data/kks-assets/shared-base/neutral-head`
  - API 返回 19 个身体/头部渲染项。
- 页面已恢复“AI教师角色库”，不存在“今日学习/选择故事”替代该入口的情况。
- 角色选择 HTTP 链路已恢复为缓存优先：浏览器请求不会启动重型 Unity 批量转换，避免把 CPU 打满。

## 已验证工作流

1. 打开 `http://127.0.0.1:5173/`。
2. 左侧进入“AI教师角色库”。
3. 页面显示“通用恋活角色卡预览”和 1529 张本机角色卡。
4. 选择 `Takamaki Ann`。
5. 当前角色切换为对应 PNG。
6. 页面显示 44 项身体滑杆，数值来自卡片原生数据；“面部参数”入口与下载修改后卡片按钮存在。
7. 后端角色卡原图、资源解析、运行时清单均返回 HTTP 200。

## 启动方式

后端：

```powershell
cd "E:\aiPlay\AI-Knowledge visualization\backend"
..\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

前端：

```powershell
cd "E:\aiPlay\AI-Knowledge visualization\frontend"
npm.cmd run dev -- --host 127.0.0.1 --port 5173
```

当前交接时两个服务均已启动：前端 5173、后端 8000。

## 验证记录

- `npm.cmd run build`：通过。
- 前端 KKS 定向测试：
  - 67 项通过；
  - `kks_body_shape_driver.test.ts` 单独以 forks 重跑，10 项通过。
- 后端材质、面部合成、眼部合成、缓存覆盖测试：21 项通过。
- `backend/tests/test_kks_runtime.py` 未进入执行：它仍导入旧接口 `build_card_index`，与恢复后的离线 SQLite 索引 API 不一致，属于测试债，不得写成“通过”。
- API 实测：
  - `/api/koikatsu-cards/local?limit=1`：200，count=1529。
  - `/api/koikatsu-cards/shared-base/neutral-template`：200，status=ready，items=19。
  - 首卡 `/resources`：200。
  - 首卡 `/runtime-manifest`：200，约 0.1 秒返回，不触发在线 Unity 转换。

## 必须保留的成果

- `KoikatsuPmxRig.tsx` 的共享骨架、按需渲染和可见性恢复逻辑。
- `kksBodyShapeDriver.ts`、`kksHeadShapeDriver.ts`、`kksAbmx.ts`。
- 四个 `frontend/public/assets/kks/*.json` 驱动文件。
- `koikatsu_card.py` 的边界解析、44/52 参数和原生卡改写。
- SQLite 卡片索引与 zipmod 索引，不要退回每次请求扫描 1500 张 PNG/3000 个 zipmod。
- `shared-base/neutral-body` 与 `shared-base/neutral-head`。
- 浏览器请求“只读缓存”的性能约束；重型转换只能由 `scripts/prewarm_kks_runtime.py` 离线执行。

## 已知边界与下一位 GPT 必须先处理的事项

1. 当前页面可见白模身体、卡片可选、44 项身体参数可读，但头部渲染状态仍报告：
   - `native head geometry/ShapeAnime atomic commit failed`
   - 因此当前截图中头部没有成功提交。
   - 这是下一阶段最先规划的单一缺陷；不要转向重做模型体系。
2. `backend/app/main.py` 与 `backend/app/koikatsu_teacher_registry.py` 没有历史整文件 SHA，可确认的是“基于 7 月 20–22 日事件和源码切片恢复并通过当前 API 验证”，不能声称逐字节精确。
3. `backend/app/koikatsu_teacher_registry.py` 的少量旧中文错误字符串存在乱码；核心 API 可运行，但后续应做只读盘点后再决定是否单独修复。
4. 当前运行时缓存只覆盖恢复/验收过程中接触的资源，不应在用户浏览时预热全部 1529 张卡。
5. 角色卡/游戏本体/Mod 原始资源没有放进 ZIP；它们仍位于：
   - `E:\BaiduNetdiskDownload\lahdxydj\KoikatuSunshine`
   - 角色卡总量约 1531（SQLite 当前收录可用项 1529）。
6. 不要清理当前 dirty worktree，不要执行 `git reset --hard`、`git clean`，不要删除恢复成果。应先创建只包含当前恢复成果的安全提交或备份，再计划后续修改。

## 下一轮建议起点

只规划，不立即大改：

1. 复现并定位头部 `ShapeAnime atomic commit failed` 的输入契约差异。
2. 对比 neutral-head manifest、head shape driver（0/200/201）和 `KoikatsuPmxRig.tsx` 的 52 通道要求。
3. 用白模 + 3 张不同头型卡做视觉验收；修复必须作用于通用模板，禁止角色专属分支。
4. 然后才评估服装/附件错位和材质质量。
