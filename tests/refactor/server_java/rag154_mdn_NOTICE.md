# RAG154 评估原文的署名与许可

`rag154_mdn_groundtruth154.json` 的 `text` 字段摘自 MDN Web Docs 中文文档，作者为 **Mozilla Contributors**，按 [CC-BY-SA 2.5 或更高版本](https://creativecommons.org/licenses/by-sa/2.5/) 提供；本文件中的这些原文摘录沿用该许可。每项的 `page` 是具体原文链接，`section` 是原页面标题/章节，`source_html_sha256` 与 `paragraph_sha256` 记录本次下载和提取结果。

改动仅是从 HTML 取出段落、去掉 HTML 标记并合并显示空白；问题、序号及评估字段由本项目编写。没有把模型生成的摘要当作资料原文。

来源许可依据：[MDN Attribution and copyright licensing](https://developer.mozilla.org/en-US/docs/MDN/Writing_guidelines/Attrib_copyright_license)。该测试资料不打进服务器发布 JAR，也不作为产品内置教学内容。
