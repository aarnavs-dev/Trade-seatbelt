import { lazy, StrictMode, Suspense, type ComponentType } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'

// Five pages, one chunk each. Old routes (/app, /app/follow) redirect to their new homes.
const path = location.pathname.replace(/\/+$/, '') || '/'
const REDIRECTS: Record<string, string> = { '/app/follow': '/follow' }
const target = REDIRECTS[path] ?? (path === '/app' || path.startsWith('/app/') ? '/dashboard' : null)
if (target) location.replace(target + location.search)

const PAGES: Record<string, ComponentType> = {
  '/': lazy(() => import('./ui/Landing')),
  '/rules': lazy(() => import('./ui/Rules')),
  '/follow': lazy(() => import('./ui/Follow')),
  '/dashboard': lazy(() => import('./ui/Dashboard')),
  '/audit': lazy(() => import('./ui/Audit')),
}
const Page = PAGES[path] ?? PAGES['/']

if (!target) {
  createRoot(document.getElementById('root')!).render(
    <StrictMode>
      <Suspense fallback={<p role="status" style={{ padding: 32, color: 'var(--ink-3)' }}>Loading…</p>}>
        <Page />
      </Suspense>
    </StrictMode>,
  )
}
