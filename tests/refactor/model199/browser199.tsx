import * as React from "react"
import {createRoot} from "react-dom/client"
import {connect} from "../../../refactor/learning/web/src/bridge"
import {App} from "../../../refactor/learning/web/src/App"
import {useApp} from "../../../refactor/learning/web/src/store"
import "../../../refactor/learning/web/src/theme.css"

declare global { interface Window { model199?: { enterClassroom: () => void; goHome: () => void } } }

window.model199 = {
  enterClassroom: () => {
    useApp.getState().set({planId:"plan-199"})
    useApp.getState().go("classroom")
  },
  goHome: () => useApp.getState().go("home"),
}

void connect().then(() => createRoot(document.getElementById("root")!).render(<App/>))
