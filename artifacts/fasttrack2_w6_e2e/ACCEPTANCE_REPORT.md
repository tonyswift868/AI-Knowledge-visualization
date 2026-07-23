# FAST-TRACK 2 W6 HTTP/E2E 验收报告

日期：2026-07-23

## 本轮新增的可复现覆盖

主矩阵位于 `backend/tests/test_w6_acceptance_matrix.py`，使用测试隔离的 legacy/ORM
数据库和内存生成的固定资料，不读取或修改用户资料。

- `algorithm`：Markdown、Python、JSON。
- `mixed_textbook`：两页 PDF、DOCX、最小有效 PPTX/XLSX；格式若尚未接入 archive
  导入，preview 必须明确返回 `ignored + reason`，不得伪装成功。
- `code_repository`：README、Python 源码、测试、TOML。
- 三套资料均覆盖 ZIP preview/receipt/import、知识地图与 source refs、目标、可行性、
  计划、今日任务、Learning Run、Evaluator、服务端完成、复习队列与奖励幂等。
- 异常矩阵覆盖半完成、提前开始、缺席、目标/容量变化、replan、undo、连续失败的
  有界终止、无效 Provider 教师动作的确定性 fallback、自主提前学习的显式批准门。
- KKS 覆盖 `unavailable`、`partial`、缺失 artifact 明细和
  `teacher_without_avatar` 降级；未启动游戏、未扫描 BetterRepack、未做原生视觉验收。

## 实测结果

### HTTP/TestClient 主矩阵

```text
.\.venv\Scripts\python.exe -m pytest backend/tests/test_w6_acceptance_matrix.py -q
6 passed, 1 warning in 4.52s
exit 0
```

警告仅为 Starlette TestClient 的 `httpx` 迁移提示。

### 默认 W6 硬门

```text
powershell -ExecutionPolicy Bypass -File .\scripts\w6_fasttrack2_acceptance.ps1
14 passed, 1 warning in 9.03s
exit 0
```

这 14 项由 6 项主矩阵，以及 Provider 失败、奖励幂等/补偿、archive 安全/取消和
KKS runtime 聚焦回归组成。原始输出见 `backend_w6_http_matrix.txt`。

### 既有真实浏览器基线抽样

```text
cd frontend
npm.cmd exec -- playwright test e2e/demo.spec.ts --grep "home exposes|real provider failure|normal ZIP preview|malicious ZIP traversal"
4 failed
exit 1
```

这组结果不计为通过。失败证据表明既有 `demo.spec.ts` 已与当前导航/演示行为失配：

- “一键打开 Demo”后不再出现旧 `.plan-body`；
- Provider 设置仍用旧导航序号，实际落到进度页，找不到 `Provider` 表单；
- ZIP 两项依赖同一个过期 `openDemo()` 前置流程。

失败不是后端 HTTP 矩阵的假阳性；同轮三套资料和异常 API 路径 6/6 通过。浏览器
脚本需要先按当前可访问名称/测试 id 重录，之后用
`scripts/w6_fasttrack2_acceptance.ps1 -IncludeLegacyBrowser` 纳入硬门。

## 可重复命令

默认硬门（不包含已知过期的浏览器用例）：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\w6_fasttrack2_acceptance.ps1
```

显式复跑浏览器现状并保留失败日志：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\w6_fasttrack2_acceptance.ps1 -IncludeLegacyBrowser
```

## 尚未证明

- 旧 Playwright UI 用例目前不是绿色，不能声称浏览器 E2E 通过。
- KKS 原生 PMX/GLB、真实材质、20+ 切卡、显存/内存与视觉回归未执行。
- 真实外部 Provider 的公网可用性未测试；只验证失败不切 Fake，以及教师动作验证失败
  使用确定性本地决策。
- PPTX/XLSX 的转换 adapter 已有独立 W2 测试，但 archive 接口当前允许以
  `ignored + reason` 诚实降级；若要宣称完整混合资料导入，仍需接通 archive registry。
