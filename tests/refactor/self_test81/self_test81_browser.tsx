import * as React from "react"
import { createRoot } from "react-dom/client"
import { Tools } from "../../../refactor/learning/web/src/pages/Tools"
import { connect } from "../../../refactor/learning/web/src/bridge"
import "../../../refactor/learning/web/src/theme.css"

connect().then(() => {
  createRoot(document.getElementById("root")!).render(<Tools />)
  ;(window as any).selfTest81 = { theme: (theme: string) => { document.documentElement.dataset.theme = theme } }
})
