import * as React from "react"
import "./focus.css"

const Focus = React.createContext(false)

export type FocusContentProps = {
  /** Controlled by the classroom-wide switch; no storage or bridge calls here. */
  focus: boolean
  children: React.ReactNode
  /** Change on a different message; keep stable while that message streams. */
  messageKey?: React.Key
}

/** Paired with FocusBlock (ClassroomMarkdown already wires both). */
export function FocusContent({ focus, children, messageKey }: FocusContentProps) {
  return <Focus.Provider value={focus}><React.Fragment key={messageKey}>
    {focus ? <div className="focus-content" data-focus="true">{children}</div> : children}
  </React.Fragment></Focus.Provider>
}

export type FocusBlockProps = React.HTMLAttributes<HTMLElement> & {
  as: "p" | "li"
  inList?: boolean
  "data-focus-question"?: string
}

/** CSS :has(mark.hl) determines which blocks fold, including nested emphasis. */
export function FocusBlock({ as, inList, children, ...attributes }: FocusBlockProps) {
  const focus = React.useContext(Focus)
  const [open, setOpen] = React.useState(false)
  const id = React.useId()
  const content = React.createElement(as, attributes, children)
  if (!focus || inList) return content
  const folded = <div className="focus-block" data-open={open ? "true" : "false"}>
    <button type="button" className="focus-reveal" aria-expanded={open} aria-controls={id}
      onClick={() => setOpen(value => !value)}>{open ? "收起补充" : "…（点开）"}</button>
    <div className="focus-body" id={id} data-focus-question={attributes["data-focus-question"]}>{as === "li" ? children : content}</div>
  </div>
  return as === "li" ? <li {...attributes}>{folded}</li> : folded
}
