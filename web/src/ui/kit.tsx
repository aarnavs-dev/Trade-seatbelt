import { useEffect, type ReactNode } from 'react'

const LINKS = [['/follow', 'Follow'], ['/rules', 'Rules'], ['/dashboard', 'Dashboard'], ['/audit', 'Audit']] as const

export function TopBar() {
  const here = location.pathname.replace(/\/+$/, '')
  return (
    <header className="topbar">
      <div className="container">
        <a className="wordmark" href="/">Trade Seatbelt</a>
        <nav className="nav" aria-label="Main">
          {LINKS.map(([href, label]) => <a key={href} href={href} aria-current={here === href ? 'page' : undefined}>{label}</a>)}
        </nav>
        <span className="net" data-testid="net-indicator"><i className="dot dot-ok" style={{ margin: 0 }} />Devnet · Paper trading</span>
      </div>
    </header>
  )
}

export function Page({ children, title }: { children: ReactNode; title?: string }) {
  useEffect(() => { document.title = title ? `${title} · Trade Seatbelt` : 'Trade Seatbelt' }, [title])
  return (<><TopBar /><main>{children}</main></>)
}

export function Status({ dot, children }: { dot: string; children: ReactNode }) {
  return <span className="status"><i className={`dot ${dot}`} />{children}</span>
}

export function Drawer({ title, onClose, children }: { title: string; onClose: () => void; children: ReactNode }) {
  useEffect(() => {
    const k = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    addEventListener('keydown', k)
    return () => removeEventListener('keydown', k)
  }, [onClose])
  return (
    <aside className="drawer" role="dialog" aria-label={title} data-testid="drawer">
      <header><h2 style={{ fontSize: 22 }}>{title}</h2><button className="btn" onClick={onClose} aria-label="Close">Close</button></header>
      <div className="body">{children}</div>
    </aside>
  )
}

export function Stub({ name }: { name: string }) {
  return (
    <Page title={name}>
      <div className="container" style={{ padding: 'var(--s12) var(--gutter)' }}>
        <h1 style={{ fontSize: 48 }}>{name}</h1>
        <p className="muted prose" style={{ marginTop: 'var(--s2)' }}>This page is built in the next step. Follow and Rules are ready.</p>
      </div>
    </Page>
  )
}
