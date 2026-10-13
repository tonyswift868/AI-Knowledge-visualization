import * as React from "react"
import ReactMarkdown, { defaultUrlTransform } from "react-markdown"
import remarkGfm from "remark-gfm"
import remarkMath from "remark-math"
import rehypeKatex from "rehype-katex"
import "katex/dist/katex.min.css"
import { remarkHighlights } from "./highlight"
import { FocusBlock, FocusContent } from "./FocusContent"

// CommonMark treats a delimiter next to punctuation followed by CJK letters
// as non-closing. A zero-width word joiner inside the span makes that edge
// non-punctuation without adding visible space or changing the source file.
// Leave code, math and escaped markers alone. Only paired ** spans change.
export function cjkStrong(text: string) {
  const literal = /(`{3,}|~{3,})[^\n]*\n[\s\S]*?\1|(`+)[^`]*?\2|\$\$[\s\S]*?\$\$|\$[^$\n]+\$/g
  const punctuation = /[\u3001-\u303f\uff01-\uff65\u2010-\u2027]/u
  const prose = (value: string) => value.replace(/(?<!\\)\*\*([^*\n]+?)(?<!\\)\*\*/g, (_match, body: string) => {
    const start = punctuation.test(body[0]) ? "\u2060" : ""
    const end = punctuation.test(body.at(-1)!) ? "\u2060" : ""
    return `**${start}${body}${end}**`
  })
  let result = "", end = 0
  for (const match of text.matchAll(literal)) {
    result += prose(text.slice(end, match.index)) + match[0]
    end = match.index! + match[0].length
  }
  return result + prose(text.slice(end))
}

export type ClassroomMarkdownProps = {
  content: string
  onSource?: (id: string) => void
  /** Classroom-wide “只看重点”; defaults to the unchanged full message. */
  focus?: boolean
  /** Stable during streaming; change when reusing this component for a new message. */
  messageKey?: React.Key
}

export function ClassroomMarkdown({ content, onSource, focus = false, messageKey }: ClassroomMarkdownProps) {
  return <FocusContent focus={focus} messageKey={messageKey}>
    <ReactMarkdown remarkPlugins={[remarkGfm, remarkMath, [remarkHighlights, { focus }]]} rehypePlugins={[rehypeKatex]}
    urlTransform={url => url.startsWith("mf-source:") ? url : defaultUrlTransform(url)}
    components={{ p: ({ node, children, ...attributes }) => <FocusBlock as="p"
      {...attributes} inList={focus && node?.properties["data-focus-in-list"] === "true"}>{children}</FocusBlock>,
      li: ({ node, children, ...attributes }) => <FocusBlock as="li" {...attributes}>{children}</FocusBlock>,
      strong: ({ children }) => <strong>{React.Children.map(children,
      child => typeof child === "string" ? child.replaceAll("\u2060", "") : child)}</strong>,
      a: ({ href, children }) => href?.startsWith("mf-source:")
      ? <button className="cite" title="看原文" onClick={() => onSource?.(href.slice(10))}>{children}</button>
      : <a href={href} target="_blank" rel="noreferrer">{children}</a> }}>
    {cjkStrong(content)}
  </ReactMarkdown>
  </FocusContent>
}
