import { lazy, StrictMode, Suspense } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'

// "/" is the landing page, "/app" the live dashboard. Each is its own chunk.
const Dashboard = lazy(() => import('./ui/Dashboard'))
const Landing = lazy(() => import('./ui/Landing'))
const Page = location.pathname.replace(/\/+$/, '').endsWith('/app') ? Dashboard : Landing

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <Suspense fallback={<div className="grid h-dvh place-items-center text-[13px] text-ink-3" role="status">Loading…</div>}>
      <Page />
    </Suspense>
  </StrictMode>,
)
