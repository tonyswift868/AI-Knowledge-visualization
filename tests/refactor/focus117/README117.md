# MF-03 接线说明（117，组件不调用桥接）

```tsx
import { ClassroomMarkdown } from "../classroom/Markdown"
import { RecapCard, type RecapContent } from "../classroom/RecapCard"

// classroomFocus 是课堂级开关，由宿主管理；默认 false。
<ClassroomMarkdown content={message.text} focus={classroomFocus}
  messageKey={message.id} onSource={openExistingSource} />

// message.kind === "recap" 时单独渲染。宿主按约定提供已解析对象。
<RecapCard content={message.content as RecapContent} onSource={openExistingChunk} />
```

- 正文的 `==重点句==` 生成 `<mark class="hl">`；允许同一段多处、重点里嵌套 `**术语**`。不限制条数，由老师决定标哪 1～3 句。
- 代码块、行内代码、数学和转义标记不处理；流式未闭合时保留所有原字符，闭合才高亮。不要在宿主预替换 `==`。
- `ClassroomMarkdownProps`: `content: string`、`onSource?: (id: string) => void`、`focus?: boolean`、`messageKey?: React.Key`。原来的 `content/onSource` 调用无需变更；`focus` 默认 false，旧消息无新增包装 DOM。
- 开重点模式后，CSS `:has(mark.hl)` 判断段落/列表项；没有荧光则折叠为“...（点开）”，可再收起。有荧光时其余文字变淡。最后一个以 `?`/`？` 结束的段落保留，包含紧凑列表内的问题。标题/表格/代码仍按原 Markdown 渲染。
- `messageKey` 在同一条回复流式追加时保持不变；复用组件显示另一条消息时换成新消息 ID，避免展开状态串到另一条消息。切换到全文模式始终显示全文。
- 更低层的 `FocusContent({focus, children, messageKey?})` 与 `FocusBlock({as: "p" | "li", children, ...HTMLAttributes})` 可配对复用；Markdown 已接好，无需宿主逐段切文本。
- `RecapContent`: `{title: string, items: {title: string, text: string, chunk_id?: string | null}[]}`。每个小要点一行结论，窄屏允许自然换行，不截断。
- `RecapCardProps`: `{content: RecapContent, onSource?: (chunkId: string) => void}`。有非空 `chunk_id` 才显示“原文”；回调传原始 chunk ID，不拼接协议、不取资料、不改状态。若未接回调，按钮禁用。
- 样式随组件导入；沿用 `data-theme="sakura"/"aurora"` 与现有主题变量。未改 Classroom.tsx、后端、产品路由或开关持久化。

预览在 `preview117.tsx`，不进产品路由。用 `test_focus117_browser.py` 的 `build(root)` 构建到任务测试目录，再用 Qt 浏览器打开 `root/dist/index.html`。测试拦截所有 HTTP/HTTPS，无模型、无账号。
