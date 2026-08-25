import React from 'react'
import { createRoot } from 'react-dom/client'
import { ReviewDemoApp } from './features/review-demo/ReviewDemoApp'
import './features/review-demo/review-demo.css'

createRoot(document.getElementById('root')!).render(
  <React.StrictMode><ReviewDemoApp /></React.StrictMode>,
)
