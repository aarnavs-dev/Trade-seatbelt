import { lazy, Suspense, useCallback, useEffect, useMemo, useState } from 'react'
import { LazyMotion, MotionConfig, domAnimation } from 'framer-motion'
import { forced2D, hasWebGL, usePhone, useReducedMotion } from '../lib/env'
import { store, useSnap } from '../lib/store'
import { BlockPanel } from './BlockPanel'
import Chain2D from './Chain2D'
import { BenchDrawer, CfDrawer, Drawer, ReconDrawer } from './Drawers'
import { Palette, type Command } from './Palette'
import { BenchSummary, CfSummary, PnlPanel, ReconSummary, Verdict, type DrawerName } from './Panels'
import { Mark, SceneBoundary, SimulatedLabel, StageLoading } from './shared'
import { Tip } from './Tip'

const Scene = lazy(() => import('../scene/Scene'))

function Stage({ phone }: { phone: boolean }) {
  const reduced = useReducedMotion()
  const [gl] = useState(hasWebGL)
  const why = forced2D() ? '2D view' : !gl ? 'WebGL is unavailable, showing the 2D chain' : reduced ? 'Reduced motion is on, showing the 2D chain' : null
  if (phone || why) {
    return (
      <div className={phone ? 'px-3 pt-1' : 'absolute left-[324px] right-6 top-[32%] h-[260px]'}>
        <Chain2D compact={phone} reason={phone ? undefined : why ?? undefined} />
      </div>
    )
  }
  return (
    <SceneBoundary fallback={(e) => <div className="absolute left-[324px] right-6 top-[32%] h-[260px]"><Chain2D reason={`3D scene failed (${e.message}), showing the 2D chain`} /></div>}>
      <Suspense fallback={<StageLoading text="Loading 3D scene" />}>
        <Scene />
      </Suspense>
    </SceneBoundary>
  )
}

export default function Dashboard() {
  const s = useSnap()
  const phone = usePhone()
  const [drawer, setDrawer] = useState<DrawerName>(() => {
    const o = new URLSearchParams(location.search).get('open')
    return o === 'recon' || o === 'cf' || o === 'bench' ? o : null
  })
  const [palette, setPalette] = useState(false)

  useEffect(() => { store.connect('demo') }, [])

  const open = useCallback((d: DrawerName) => setDrawer((cur) => (cur === d ? null : d)), [])
  const commands = useMemo<Command[]>(() => [
    { id: 'demo', label: 'Run demo', hint: 'fixture orders through a real gate', keys: 'D', run: () => store.runDemo() },
    { id: 'verify', label: 'Verify chain', hint: 'sweep every link', keys: 'V', run: () => store.verify() },
    { id: 'tamper', label: 'Tamper with a copy of the log', hint: 'edit one line, see the first bad seq', keys: 'T', run: () => store.tamper() },
    { id: 'reset', label: 'Reset tamper', keys: 'R', run: () => store.reset() },
    { id: 'recon', label: 'Open reconciliation', keys: '1', run: () => setDrawer('recon') },
    { id: 'cf', label: 'Open counterfactual', keys: '2', run: () => setDrawer('cf') },
    { id: 'bench', label: 'Open benchmarks', keys: '3', run: () => setDrawer('bench') },
    { id: 'live', label: 'Jump to the latest block', keys: 'L', run: () => store.follow() },
  ], [])

  useEffect(() => {
    const on = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); setPalette((p) => !p); return }
      const t = e.target as HTMLElement
      if (palette || e.metaKey || e.ctrlKey || e.altKey || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName)) return
      const k = e.key.toLowerCase()
      if (e.key === 'Escape') {
        if (drawer) setDrawer(null)
        else if (store.getSnap().selected !== null) store.select(null)
        return
      }
      const c = commands.find((x) => x.keys?.toLowerCase() === k)
      if (c) { e.preventDefault(); c.run() }
    }
    window.addEventListener('keydown', on)
    return () => window.removeEventListener('keydown', on)
  }, [commands, drawer, palette])

  const busy = s.busy
  const tools = (
    <>
      <Tip label="Run the demo" keys={['D']}><button className="btn btn-accept" disabled={!!busy} onClick={() => store.runDemo()} data-testid="btn-demo">{busy === 'demo' ? 'Running…' : 'Run demo'}</button></Tip>
      <Tip label="Verify the chain" keys={['V']}><button className="btn" disabled={!!busy || s.total === 0} onClick={() => store.verify()} data-testid="btn-verify">Verify</button></Tip>
      <Tip label="Edit a copy of the log" keys={['T']}><button className="btn btn-reject" disabled={!!busy || s.total === 0 || !!s.tamper} onClick={() => store.tamper()} data-testid="btn-tamper">Tamper</button></Tip>
      <Tip label="Restore the chain" keys={['R']}><button className="btn" disabled={!s.tamper} onClick={() => store.reset()} data-testid="btn-reset">Reset</button></Tip>
      <a className="btn" href="/app/follow" data-testid="nav-follow">Follow</a>
      <Tip label="Command palette" keys={['⌘', 'K']}><button className="btn" onClick={() => setPalette(true)} aria-label="Open command palette" data-testid="btn-palette">⌘K</button></Tip>
    </>
  )

  const drawers = (
    <Drawer name={drawer} title={drawer === 'recon' ? 'Reconciliation' : drawer === 'cf' ? 'Counterfactual' : 'Benchmarks'} onClose={() => setDrawer(null)}>
      {drawer === 'recon' && <ReconDrawer />}
      {drawer === 'cf' && <CfDrawer />}
      {drawer === 'bench' && <BenchDrawer />}
    </Drawer>
  )

  const status = (
    <div className="num flex items-center gap-3 text-[11px] text-ink-3" data-testid="status">
      <span className="flex items-center gap-1.5"><i className={`inline-block h-1.5 w-1.5 rounded-full ${s.conn === 'live' ? 'bg-accept' : s.conn === 'connecting' ? 'live-dot bg-ink-3' : 'bg-reject'}`} />{s.conn}</span>
      <span>{s.source === 'recon' ? 'reconciliation fixture' : 'demo log'}</span>
      <span>{s.total ? `seq ${s.total}` : 'no events'}</span>
      {s.report && <span>head {s.report.head_hash.slice(0, 7)}</span>}
      {!s.followLive && <button className="text-accept hover:underline" onClick={() => store.follow()}>back to latest</button>}
      {s.source === 'recon' && <button className="text-accept hover:underline" onClick={() => store.connect('demo')}>back to demo log</button>}
    </div>
  )

  const connBanner = s.conn === 'error' || s.conn === 'reconnecting' ? (
    <div className="glass pointer-events-auto flex items-center gap-3 px-4 py-2.5 text-[13px] text-reject" role="alert">
      {s.connError}
      <button className="btn h-7" onClick={() => store.connect()}>Retry</button>
    </div>
  ) : null

  const empty = s.total === 0 && s.conn === 'live' && s.report && s.report.counts.events === 0
  const emptyHint = empty ? (
    <div className="pointer-events-none absolute inset-x-0 top-[44%] text-center text-[13px] text-ink-3">No log yet. Press <kbd className="kbd">D</kbd> to run the demo.</div>
  ) : null

  return (
    <LazyMotion features={domAnimation} strict={false}>
      <MotionConfig reducedMotion="user">
        {phone ? (
          <div className="h-dvh overflow-y-auto pb-24" data-testid="dashboard-phone">
            <header className="sticky top-0 z-20 border-b border-line bg-bg/85 px-4 pb-3 pt-3 backdrop-blur">
              <div className="mb-2 flex items-center justify-between"><a href="/" className="flex items-center gap-2 text-[15px] font-medium" aria-label="Trade Seatbelt, home"><Mark />Trade Seatbelt</a></div>
              <SimulatedLabel />
              <div className="no-scrollbar -mx-4 mt-3 flex gap-2 overflow-x-auto px-4">{tools}</div>
            </header>
            <div className="mt-3 space-y-3 px-3">
              <Stage phone />
              {connBanner}
              <Verdict />
              {emptyHint && <p className="px-1 text-[13px] text-ink-3">No log yet. Tap Run demo.</p>}
              <PnlPanel />
              <ReconSummary open={open} />
              <CfSummary open={open} />
              <BenchSummary open={open} />
              <div className="px-1">{status}</div>
            </div>
            <div className="fixed inset-x-3 bottom-3 z-20"><BlockPanel /></div>
            {drawers}
          </div>
        ) : (
          <div className="fixed inset-0 overflow-hidden bg-bg" data-testid="dashboard">
            <Stage phone={false} />
            <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(ellipse_at_center,transparent_55%,rgb(7_8_10/0.55)_100%)]" />
            {emptyHint}
            <header className="pointer-events-none absolute inset-x-0 top-0 z-20 flex items-center justify-between px-5 py-4">
              <div className="pointer-events-auto flex items-center gap-4"><a href="/" className="flex items-center gap-2.5 text-[15px] font-medium tracking-tight" aria-label="Trade Seatbelt, home"><Mark />Trade Seatbelt</a><SimulatedLabel /></div>
              <div className="pointer-events-auto flex items-center gap-2">{tools}</div>
            </header>
            <aside className="scroll-thin pointer-events-none absolute bottom-12 left-5 top-[72px] z-10 flex w-[292px] flex-col gap-3 overflow-y-auto pr-1">
              <div className="pointer-events-auto"><PnlPanel /></div>
              <div className="pointer-events-auto"><ReconSummary open={open} /></div>
              <div className="pointer-events-auto"><CfSummary open={open} /></div>
              <div className="pointer-events-auto"><BenchSummary open={open} /></div>
            </aside>
            <div className="pointer-events-none absolute inset-x-0 top-[72px] z-10 flex flex-col items-center gap-3">{connBanner}<Verdict /></div>
            <div className="pointer-events-none absolute bottom-12 left-1/2 z-10 w-[min(660px,calc(100vw-660px))] min-w-[520px] -translate-x-1/2"><BlockPanel /></div>
            <footer className="absolute bottom-4 left-5 right-5 z-10 flex items-center justify-between">
              {status}
              <span className="text-[11px] text-ink-3">scroll to travel the chain · click a block to pin · <kbd className="kbd">⌘K</kbd> commands</span>
            </footer>
            {drawers}
          </div>
        )}
        <Palette open={palette} onClose={() => setPalette(false)} commands={commands} />
      </MotionConfig>
    </LazyMotion>
  )
}
