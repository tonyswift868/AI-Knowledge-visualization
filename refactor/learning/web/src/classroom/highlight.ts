/** Small remark transform; only prose text delimiters participate, never code/math. */
type Node = {
  type: string
  value?: string
  children?: Node[]
  position?: { start: { offset?: number }; end: { offset?: number } }
  data?: { hName?: string; hProperties?: Record<string, unknown> }
}
const delimiter = /(?<!=)==(?!=)/g

function parts(node: Node, source: string): Node[] {
  if (node.type !== "text") return [node]
  const value = node.value || ""
  const matches = [...value.matchAll(delimiter)]
  const raw = source.slice(node.position?.start.offset, node.position?.end.offset)
  const original = [...raw.matchAll(delimiter)]
  // Character references must not invent syntax after Markdown decodes them.
  if (matches.length !== original.length) return [node]
  const result: Node[] = []
  let cursor = 0
  matches.forEach((match, i) => {
    const at = original[i].index!
    let escapes = 0
    for (let j = at - 1; j >= 0 && raw[j] === "\\"; j--) escapes++
    if (escapes % 2) return
    if (match.index! > cursor) result.push({ type: "text", value: value.slice(cursor, match.index) })
    result.push({ type: "focusDelimiter", value: "==" })
    cursor = match.index! + 2
  })
  if (cursor < value.length) result.push({ type: "text", value: value.slice(cursor) })
  return result
}

function highlights(children: Node[], source: string): Node[] {
  const output: Node[] = []
  let pending: Node[] | null = null
  for (const node of children.flatMap(child => parts(child, source))) {
    if (node.type === "focusDelimiter") {
      if (pending === null) pending = []
      else {
        output.push({ type: "highlight", children: pending, data: { hName: "mark", hProperties: { className: ["hl"] } } })
        pending = null
      }
    } else (pending ?? output).push(node)
  }
  // Streaming: an opening delimiter stays visible until its closing pair arrives.
  if (pending !== null) output.push({ type: "text", value: "==" }, ...pending)
  return output
}

function text(node: Node): string {
  return node.value ?? (node.children || []).map(text).join("")
}

export function remarkHighlights(options?: { focus: boolean }) {
  return (tree: Node, file: { value: unknown }) => {
    const paragraphs: Node[] = []
    const visit = (node: Node, inList = false) => {
      if (!node.children || ["code", "inlineCode", "math", "inlineMath", "html"].includes(node.type)) return
      node.children.forEach(child => visit(child, inList || node.type === "listItem"))
      if (["paragraph", "heading", "strong", "emphasis", "delete", "link", "tableCell"].includes(node.type)) {
        node.children = highlights(node.children, String(file.value))
      }
      if (node.type === "paragraph") {
        paragraphs.push(node)
        if (inList && options?.focus) node.data = { ...node.data, hProperties: { ...node.data?.hProperties, "data-focus-in-list": "true" } }
      }
    }
    visit(tree)
    const question = options?.focus && paragraphs.findLast(p => /[?？]$/.test(text(p).trim()))
    if (question) {
      const tag = (node: Node): boolean => {
        const contains = node === question || (node.children || []).map(tag).some(Boolean)
        if (node === question || (node.type === "listItem" && contains)) {
          node.data = { ...node.data, hProperties: { ...node.data?.hProperties, "data-focus-question": "true" } }
        }
        return contains
      }
      tag(tree)
    }
  }
}
