import * as React from "react"
import { createRoot } from "react-dom/client"
import { App } from "./App"
import { connect } from "./bridge"
import { useApp } from "./store"
import "./theme.css"

connect().then((connected) => useApp.getState().set({ connected }))
createRoot(document.getElementById("root")!).render(<React.StrictMode><App /></React.StrictMode>)
