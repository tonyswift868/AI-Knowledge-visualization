# MentorFlow Guide 导入测试

被测能力是软件现有的 GitHub 公开仓库导入；这里的准备脚本不是产品功能。不需要安装新依赖。

## 准备与发布（架构师操作）

1. 在 GitHub 创建公开仓库 `tonyswift868/mentorflow-guide`，描述为“MentorFlow Guide：MentorFlow 项目学习指南”。本任务不创建仓库、不推送。
2. 合入 MF-05 审改的学习资料后，在主项目根目录运行：

   ```powershell
   D:/MentorFlow/.venv/Scripts/python.exe -B scripts/learn/export_guide.py --strict
   ```

   默认输出 `D:/MentorFlow/.local/guide-export/mentorflow-guide/`。首次输出时 `docs/` 中只有 00～13 的课文；根 `README.md` 供浏览，`_出处.md` 供核对。重导出保留现有仓库中的其他文件，推送前检查并处理不再需要的旧 Markdown。14 篇课文使导入器选择 `docs/`，不会把根 README 当第十五章；出处文件名以下划线开头，导入器跳过它。默认非 strict 模式可准备本地试验并列出敏感位置，**不是公开前检查通过**；公开前使用 `--strict`，有命中就先由资料作者清理。
3. 检查导出目录的差异，再在该目录初始化 Git、提交并推送到上述公开仓库。已有仓库重新导出会保留 `.git`，不要将主项目、测试记录或模型配置推入指南仓库。示例：

   ```powershell
   git -C D:/MentorFlow/.local/guide-export/mentorflow-guide init -b main
   git -C D:/MentorFlow/.local/guide-export/mentorflow-guide add README.md docs _出处.md
   git -C D:/MentorFlow/.local/guide-export/mentorflow-guide commit -m "Update MentorFlow Guide"
   git -C D:/MentorFlow/.local/guide-export/mentorflow-guide remote add origin https://github.com/tonyswift868/mentorflow-guide.git
   git -C D:/MentorFlow/.local/guide-export/mentorflow-guide push -u origin main
   ```

   已有仓库跳过 `init` 和 `remote add`，按正常 Git 流程更新。推送由架构师按用户授权执行。
4. 推送后，在主项目根目录用一个命令检查真实公开链接（只取文、不调用模型）：

   ```powershell
   D:/MentorFlow/.venv/Scripts/python.exe -B -m tests.refactor.guide165.check_guide_import165 --url https://github.com/tonyswift868/mentorflow-guide --import-only --output D:/MentorFlow/.local/test-tmp/guide165/check/public
   ```

   也可将 URL 换为 `https://github.com/tonyswift868/mentorflow-guide/tree/main/docs`。结果列出实际抓取顺序、章节、README、重复、出处和乱码检查。若继续真实切点/讲义/备题，将 `--import-only` 换成 `--live --budget <本轮剩余预算> --account-id <已有管理员账号编号> --preset-root D:/MentorFlowData/account-shell`，另用新的 `--output` 子目录；使用现有模型预设，不复制 Key。脚本每次上限 8 元，累计仍按当轮授权预算执行，不能因换输出目录重置预算。模型调用会收费，先查看本轮回执中的已花金额。

以后更新资料：先审改 `docs/learn/`，重新运行 `export_guide.py --strict`，查看导出仓库 `git diff`，提交/推送，然后再次执行零模型费 URL 检查。需要观察新的切点结果时再单独授权真实模型费用。

## 本地模拟

`export_guide.py --output <本地试验目录>` 准备同样的仓库文件，再将上述检查命令的 `--url ...` 换成 `--local <本地试验目录>`。本地适配只替换抓取，仍调用软件的 `github_repo.import_repo`；`--live` 接着使用当前切点、讲义和备题函数，不模拟学生、不启动用户软件。
