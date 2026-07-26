# 路线 2：按需下载的恋活角色资源包

路线 2 的客户端只接收已经离线组装好的 Web GLB，不启动恋活，也不在 HTTP 请求中扫描本机游戏目录。

## 批量生成

先生成 1500+ 张本地卡的离线队列（只读，不启动恋活）：

```powershell
.\.venv\Scripts\python.exe tools\build_kks_conversion_queue.py
```

队列会区分 `web-model-ready`、`pmx-export-ready` 和 `card-only`。只有前两类进入作者侧转换；`card-only` 不会被网页端偷偷尝试拼装。

```powershell
.\.venv\Scripts\python.exe tools\build_kks_character_packs_batch.py --queue legacy\koikatsu-3d\resources\runtime\kks-batch-conversion-queue.json --force --install
```

默认扫描 `legacy/koikatsu-3d/resources/runtime/web-models/**/model.glb`，并按本地角色卡的目录层级生成：

```text
legacy/koikatsu-3d/resources/runtime/character-packs/
  <分类路径>/
    <角色名>-<card-id>-<指纹前12位>.zip
  catalog.json
```

每个 ZIP 包含 `mentorflow-kks-pack.json` 和 `model.glb`。清单记录角色显示名、分类、原始卡 ID、原生卡指纹、模型 SHA-256 和 GLB 骨骼/材质校验结果。

## 性能和幂等

- `catalog.json` 是下载目录的快速索引，API 优先读取它，不为每次页面加载重新打开全部 ZIP。
- `--force` 只替换同名目标包；不带 `--force` 时已有包保留。
- 资源包使用临时文件写入后原子替换，批量中单个角色失败不会破坏其他包。
- 下载 API 使用 ASCII `pack_id`，中文目录和文件名只用于本地排版与显示。

## 当前边界

该工具负责“已有完整 Web GLB → 客户下载包”。原始 PNG 角色卡、zipmod 和未转换 PMX 不能直接由它变成完整 KKS GLB；这些资源必须先在离线作者环境中完成一次 KKBP/Blender 转换，之后再纳入批量打包。
