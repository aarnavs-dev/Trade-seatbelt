import { useEffect } from 'react'
import { store, useSnap } from '../lib/store'
import ChainScene from '../scene/ChainScene'
import { Page } from './kit'

export default function Audit() {
  const s = useSnap()
  useEffect(() => { store.connect('demo'); return () => { store.clear() } }, [])
  const busy = !!s.busy
  const message = s.tamper ? (s.verify?.message ?? s.tamper.result.message) : s.verify?.message ?? null
  return (
    <Page title="Audit">
      <div className="container" style={{ padding: 'var(--s8) var(--gutter) var(--s4)' }}>
        <h1 style={{ fontSize: 'clamp(36px, 4.4vw, 56px)' }}>Audit</h1>
        <p className="muted prose" style={{ marginTop: 'var(--s2)' }}>The full audit page (log, rejections, Solana, export) is built in the next step. This is the integrity check on a demo log.</p>
        <div className="tabs" role="tablist" style={{ marginTop: 'var(--s4)' }}><button className="tab" role="tab" aria-selected="true">Integrity</button></div>
      </div>
      <ChainScene store={store} variant="medium" label="The log chain: verify lights each link in turn; the tamper demo edits a copy and every later block turns invalid" />
      <div className="container" style={{ padding: 'var(--s3) var(--gutter) var(--s12)' }}>
        <div style={{ display: 'flex', gap: 'var(--s2)', flexWrap: 'wrap', alignItems: 'center' }}>
          <button className="btn btn-primary" disabled={busy} onClick={() => store.runDemo()} data-testid="btn-demo">{s.busy === 'demo' ? 'Running…' : 'Run demo'}</button>
          <button className="btn" disabled={busy || s.total === 0} onClick={() => store.verify()} data-testid="btn-verify">Verify log</button>
          <button className="btn" disabled={busy || s.total === 0 || !!s.tamper} onClick={() => store.tamper()} data-testid="btn-tamper">Tamper with a copy</button>
          <button className="btn" disabled={!s.tamper} onClick={() => store.reset()} data-testid="btn-reset">Reset</button>
        </div>
        <p style={{ marginTop: 'var(--s3)', minHeight: 28 }} data-testid="verifier-message" aria-live="polite">{message ?? (s.total ? `${s.total} blocks in this log.` : 'No log yet. Run the demo.')}</p>
        {s.notice && <p className="error" role="alert">{s.notice}</p>}
        <p className="muted prose" style={{ fontSize: 13.5 }}>Tampering edits a copy of the log, never the real one. The chain proves that no line was edited, removed from the middle or reordered; it cannot show that the newest lines were deleted.</p>
      </div>
    </Page>
  )
}
