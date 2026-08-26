import React from 'react'
import { createRoot } from 'react-dom/client'
import { App } from './App'
import { MENTORFLOW_ROUTES } from './app/routes'
import { TEACHER_LIVE_ROUTE, TeacherLiveClassroom } from './features/teacher-live'
import { verifiedKnowledgeMapFeatureRoute } from './features/verified-knowledge-map/route'
import { BrowserRouter, Route, Routes } from 'react-router-dom'
import './styles/app.css'
import './styles/local-path.css'
import './styles/components.css'
import './styles/grounding-fixes.css'
import './styles/warm-theme.css'
import './styles/standalone-tools.css'
class ErrorBoundary extends React.Component<{children: React.ReactNode}, {error: Error | null}> {
  state={error:null as Error | null}
  static getDerivedStateFromError(error: Error){return {error}}
  render(){return this.state.error ? <main className="error-state"><h1>Something went wrong</h1><p>{this.state.error.message}</p><button onClick={()=>location.reload()}>Retry</button></main> : this.props.children}
}
function RoutedApp(){ return <BrowserRouter><Routes>{MENTORFLOW_ROUTES.map(path=><Route key={path} path={path} element={<App />} />)}<Route path={TEACHER_LIVE_ROUTE} element={<TeacherLiveClassroom />} /><Route path={verifiedKnowledgeMapFeatureRoute.path} element={verifiedKnowledgeMapFeatureRoute.element} /><Route path="*" element={<App />} /></Routes></BrowserRouter> }
createRoot(document.getElementById('root')!).render(<React.StrictMode><ErrorBoundary><RoutedApp /></ErrorBoundary></React.StrictMode>)
