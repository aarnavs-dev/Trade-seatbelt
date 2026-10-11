import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { ApiError } from '../lib/api'
import { money } from '../lib/format'
import {
  DEFAULT_LIMITS, FILTERS, follow, LEADERS, limitsFromSummary, nyTime, STATUS, toApiLimits,
  type Explanation, type LeaderName, type Limits, type Row, type Status, type Summary,
} from '../lib/follow'
import { Mark } from './shared'

type Phase = 'idle' | 'starting' | 'running' | 'done' | 'error'
const LIVE = ['starting', 'running']

const FIELDS: { key: keyof Limits; label: string; hint: string; unit: string; mode: 'numeric' | 'decimal' }[] = [
  { key: 'maxPosition', label: 'Biggest position in one stock', hint: 'Orders that would take you past this many shares are blocked.', unit: 'shares', mode: 'numeric' },
  { key: 'maxLossDollars', label: 'Most you can lose in a day', hint: 'After this, orders that add risk are blocked. Closing is always allowed.', unit: '$', mode: 'decimal' },
  { key: 'bandPct', label: 'Price band', hint: 'Blocks prices this far from yesterday’s close (typo protection).', unit: '%', mode: 'numeric' },
  { key: 'cooldownLosses', label: 'Pause after losses in a row', hint: 'After this many losing trades, new risk is paused for 5 minutes.', unit: 'losses', mode: 'numeric' },
]

function Chip({ status }: { status: Status }) {
  const s = STATUS[status]
  return <span className={`num inline-block whitespace-nowrap rounded-md border px-1.5 py-0.5 text-[10.5px] tracking-wide ${s.chip}`} data-testid="chip">{s.label}</span>
}

function Stat({ label, value, testid }: { label: string; value: number | string; testid: string }) {
  return (
    <div className="min-w-0 flex-1 px-4 py-3">
      <div className="label">{label}</div>
      <div className="num mt-1 text-[22px] leading-none" data-testid={testid}>{value}</div>
    </div>
  )
}

function Kv({ k, v }: { k: string; v: ReactNode }) {
  return <div className="grid grid-cols-[120px_1fr] gap-3 py-1.5 text-[13px] max-sm:grid-cols-1 max-sm:gap-0.5"><dt className="text-ink-3">{k}</dt><dd className="min-w-0 break-words text-ink">{v}</dd></div>
}

function ExplainDrawer({ run, n, onClose }: { run: string; n: number; onClose: () => void }) {
  const [x, setX] = useState<Explanation | null>(null)
  const [err, setErr] = useState<string | null>(null)
  useEffect(() => {
    let live = true
    setX(null); setErr(null)
    follow.explain(run, n).then((d) => live && setX(d), (e) => live && setErr(e instanceof Error ? e.message : String(e)))
    return () => { live = false }
  }, [run, n])
  useEffect(() => {
    const k = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    addEventListener('keydown', k)
    return () => removeEventListener('keydown', k)
  }, [onClose])
  const sig = x?.leader_signal
  return (
    <aside className="glass fixed bottom-4 right-4 top-[72px] z-30 flex w-[min(500px,calc(100vw-32px))] flex-col max-md:inset-3 max-md:top-3 max-md:w-auto" role="dialog" aria-label="Why this happened" data-testid="drawer">
      <header className="flex items-center justify-between border-b border-line px-4 py-3">
        <h2 className="text-[15px] font-medium">Why this happened</h2>
        <button className="btn h-7 px-2 text-[12px]" onClick={onClose} aria-label="Close">Esc</button>
      </header>
      <div className="scroll-thin min-h-0 flex-1 overflow-y-auto px-4 py-4">
        {err && <p className="text-[13px] text-reject" role="alert">{err}</p>}
        {!x && !err && <div className="space-y-3" aria-busy="true">{[0, 1, 2, 3].map((i) => <div key={i} className="skel h-8 w-full" />)}</div>}
        {x && (
          <>
            <div className="mb-3 flex flex-wrap items-center gap-2"><Chip status={x.status} />{x.order && <span className="num text-[12px] text-ink-2">{x.order}</span>}</div>
            <p className="mb-3 text-[14px] leading-relaxed" data-testid="explain-detail">{x.detail}</p>
            <dl className="divide-y divide-line border-y border-line">
              {x.rule && <Kv k="Rule" v={<>{x.rule}{x.protects_against && <span className="text-ink-2">: protects against {x.protects_against}.</span>}</>} />}
              {x.limit && <Kv k="Your limit" v={x.limit} />}
              {x.value_before && <Kv k="At that moment" v={x.value_before} />}
              {x.value_if_accepted && <Kv k="If it had gone through" v={x.value_if_accepted} />}
              {x.cause_text && <Kv k="Caused by" v={x.cause_text} />}
              {sig && <Kv k="Leader’s signal" v={<span className="num text-[12px] text-ink-2">#{String(sig.leader_seq)} {String(sig.side)} {String(sig.qty)} {String(sig.symbol)} @ {money(Number(sig.ref_price))}, bar ending {nyTime(Number(sig.data_ts))} ET</span>} />}
              {x.explorer_url && <Kv k="On-chain proof" v={<a className="text-accept hover:underline" href={x.explorer_url} target="_blank" rel="noopener noreferrer" data-testid="explorer-link">View the signal on Solana Explorer (devnet)</a>} />}
            </dl>
            <p className="mt-3 text-[11.5px] text-ink-3">Computed from the logs and your rules only. No AI. Paper execution.</p>
          </>
        )}
      </div>
    </aside>
  )
}

export default function Follow() {
  const initialRun = new URLSearchParams(location.search).get('run')
  const [leader, setLeader] = useState<LeaderName>('momentum')
  const [feed, setFeed] = useState<'local' | 'chain'>('local')
  const [limits, setLimits] = useState<Limits>(DEFAULT_LIMITS)
  const [run, setRun] = useState<string | null>(initialRun)
  const [phase, setPhase] = useState<Phase>(initialRun ? 'running' : 'idle')
  const [rows, setRows] = useState<Row[]>([])
  const [summary, setSummary] = useState<Summary | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [limitError, setLimitError] = useState<string | null>(null)
  const [conn, setConn] = useState<'live' | 'reconnecting'>('live')
  const [filter, setFilter] = useState<'ALL' | Status>('ALL')
  const [open, setOpen] = useState<number | null>(null)
  const es = useRef<EventSource | null>(null)

  useEffect(() => { document.body.classList.add('scroll'); return () => document.body.classList.remove('scroll') }, [])

  const refresh = useCallback(async (id: string) => {
    try { setSummary(await follow.summary(id)) } catch (e) { if (e instanceof ApiError && e.status === 404) { setError('That run does not exist.'); setPhase('error') } }
  }, [])

  // stream one run: rows arrive in feed order; 'end' closes it
  useEffect(() => {
    if (!run) return
    const src = new EventSource(follow.streamUrl(run))
    es.current = src
    src.addEventListener('signal', (e) => {
      const r = JSON.parse((e as MessageEvent).data) as Row
      setConn('live'); setPhase('running')
      setRows((prev) => (prev.some((p) => p.n === r.n) ? prev : [...prev, r]))
    })
    src.addEventListener('end', () => { src.close(); setPhase((p) => (p === 'error' ? p : 'done')); void refresh(run) })
    src.onerror = () => src.readyState !== EventSource.CLOSED && setConn('reconnecting')
    const t = setInterval(() => void refresh(run), 1000)
    void refresh(run)
    return () => { src.close(); clearInterval(t) }
  }, [run, refresh])

  useEffect(() => { if (summary && !LIVE.includes(summary.state) && phase === 'running' && rows.length >= summary.seen) setPhase('done') }, [summary, phase, rows.length])
  useEffect(() => { if (initialRun && summary) setLimits(limitsFromSummary(summary.limits)) }, [initialRun, !!summary]) // eslint-disable-line react-hooks/exhaustive-deps

  const start = async () => {
    setError(null); setLimitError(null); setRows([]); setSummary(null); setOpen(null); setPhase('starting')
    try {
      const r = await follow.start(feed, leader, toApiLimits(limits))
      setRun(r.run)
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e)
      if (e instanceof ApiError && e.status === 422) { setLimitError(msg); setPhase('idle') } else { setError(msg); setPhase('error') }
    }
  }
  const stop = async () => { if (run) { try { await follow.stop(run) } catch { /* the stream's end event reports the outcome */ } } }

  const shown = useMemo(() => rows.filter((r) => filter === 'ALL' || r.status === filter).slice().reverse(), [rows, filter])
  const count = (s: Status) => rows.filter((r) => r.status === s).length
  const live = phase === 'starting' || phase === 'running'
  const total = summary?.total_in_feed ?? 0

  return (
    <div className="mx-auto min-h-dvh max-w-[1180px] px-3 pb-10 pt-3 md:px-6" data-testid="follow">
      <header className="glass mb-3 px-4 py-3" data-testid="topbar">
        <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
          <div className="flex items-center gap-3">
            <a href="/" className="flex items-center gap-2.5 text-[15px] font-medium tracking-tight" aria-label="Trade Seatbelt, home"><Mark />Trade Seatbelt</a>
            <span className="num rounded-full border border-accept/40 px-2.5 py-1 text-[11px] tracking-wide text-accept" data-testid="paper-label">PAPER EXECUTION</span>
          </div>
          <div className="flex items-center gap-2">
            <a className="btn hidden md:inline-flex" href="/app">Dashboard</a>
            {live ? <button className="btn btn-reject" onClick={stop} data-testid="btn-stop">Stop</button>
                  : <button className="btn btn-accept" onClick={start} data-testid="btn-start">{phase === 'done' || phase === 'error' ? 'Run again' : 'Start'}</button>}
          </div>
        </div>
        <div className="mt-3 grid gap-3 border-t border-line pt-3 md:grid-cols-[1fr_auto] md:items-center">
          <div data-testid="leader-card" className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <span className="label">Leader</span>
              <select className="btn h-7 bg-bg px-2 text-[12.5px]" value={leader} disabled={live} onChange={(e) => setLeader(e.target.value as LeaderName)} aria-label="Leader strategy" data-testid="leader-select">
                {Object.entries(LEADERS).map(([k, v]) => <option key={k} value={k}>{v.name}</option>)}
              </select>
              <select className="btn h-7 bg-bg px-2 text-[12.5px]" value={feed} disabled={live} onChange={(e) => setFeed(e.target.value as 'local' | 'chain')} aria-label="Signal source" data-testid="feed-select">
                <option value="local">Local feed</option><option value="chain">Chain (devnet)</option>
              </select>
            </div>
            <p className="mt-1.5 text-[13px] text-ink-2">{LEADERS[leader].about} <span className="text-ink-3">Not tuned, not advice.</span></p>
          </div>
          <div className="flex flex-wrap items-center gap-2 md:justify-end" data-testid="badges">
            {summary?.data_label ? <span className="num rounded-md border border-line-2 px-2 py-1 text-[11px] text-ink" data-testid="badge-data">{summary.data_label}</span> : <span className="num rounded-md border border-line px-2 py-1 text-[11px] text-ink-3">data: set when a run starts</span>}
            {summary?.feed?.badge && <span className="num rounded-md border border-line-2 px-2 py-1 text-[11px] text-ink" data-testid="badge-feed">{summary.feed.badge}</span>}
          </div>
        </div>
      </header>

      {summary?.notes.map((n) => <p key={n} className="mb-3 rounded-lg border border-[#fbbf24]/30 px-3 py-2 text-[13px] text-[#fbbf24]" role="status" data-testid="note">{n}</p>)}

      <div className="grid gap-3 md:grid-cols-[300px_minmax(0,1fr)]">
        <section className="glass h-fit px-4 py-4" aria-label="Your rules" data-testid="rules-panel">
          <h2 className="label mb-3">Your rules</h2>
          <div className="space-y-3.5">
            {FIELDS.map((f) => (
              <label key={f.key} className="block">
                <span className="text-[13px] text-ink">{f.label}</span>
                <span className="mt-1 flex items-center gap-2">
                  <input className="num h-8 w-full min-w-0 rounded-lg border border-line bg-white/[0.03] px-2.5 text-[13px] text-ink focus:border-accept/60" inputMode={f.mode} value={limits[f.key]} disabled={live}
                         onChange={(e) => setLimits({ ...limits, [f.key]: e.target.value })} data-testid={`limit-${f.key}`} />
                  <span className="num w-12 shrink-0 text-[11px] text-ink-3">{f.unit}</span>
                </span>
                <span className="mt-1 block text-[11.5px] leading-snug text-ink-3">{f.hint}</span>
              </label>
            ))}
          </div>
          {limitError && <p className="mt-3 rounded-lg border border-reject/30 p-2.5 text-[12.5px] text-reject" role="alert" data-testid="limit-error">{limitError}</p>}
          <p className="mt-3 text-[11.5px] text-ink-3">Applies to the next run. Your repo’s rules.yaml is never changed.</p>
        </section>

        <div className="min-w-0 space-y-3">
          <div className="glass flex divide-x divide-line max-sm:flex-wrap max-sm:divide-x-0" role="group" aria-label="Summary" data-testid="summary">
            <Stat label="Signals seen" value={rows.length + (live && total ? ` / ${total}` : '')} testid="stat-seen" />
            <Stat label="Copied" value={count('COPIED') + count('CLIPPED')} testid="stat-copied" />
            <Stat label="Stopped by copier" value={count('STOPPED_BY_COPIER')} testid="stat-stopped" />
            <Stat label="Rejected by your rules" value={count('REJECTED_BY_RULES')} testid="stat-rejected" />
          </div>

          <section className="glass px-2 py-3 md:px-3" aria-label="Live feed" data-testid="feed">
            <div className="mb-2 flex flex-wrap items-center justify-between gap-2 px-1">
              <div className="flex flex-wrap gap-1.5" role="group" aria-label="Filter by status">
                {FILTERS.map((f) => (
                  <button key={f.key} onClick={() => setFilter(f.key)} aria-pressed={filter === f.key} data-testid={`filter-${f.key}`}
                          className={`num rounded-md border px-2 py-0.5 text-[11.5px] transition-colors hover:bg-white/10 ${filter === f.key ? 'border-line-2 bg-white/10 text-ink' : 'border-line text-ink-2'}`}>{f.label}</button>
                ))}
              </div>
              <span className="text-[11.5px] text-ink-3" aria-live="polite">{conn === 'reconnecting' ? 'Connection lost, retrying…' : live ? 'live' : phase === 'done' ? 'finished' : ''}</span>
            </div>

            {phase === 'error' && (
              <div className="m-2 rounded-lg border border-reject/30 p-3 text-[13px]" role="alert" data-testid="error-box"><p className="text-reject">{error}</p><button className="btn mt-3" onClick={start}>Try again</button></div>
            )}
            {phase === 'idle' && rows.length === 0 && <p className="px-3 py-10 text-center text-[13px] text-ink-3" data-testid="empty">Press Start to follow a leader through one simulated session. Everything here is paper: no real orders, no real money.</p>}
            {phase === 'starting' && <div className="space-y-2 p-2" aria-busy="true" data-testid="loading"><p className="px-1 text-[13px] text-ink-3">Fetching market data and the leader’s signals (this can take up to a minute)…</p>{[0, 1, 2].map((i) => <div key={i} className="skel h-9 w-full" />)}</div>}
            {phase === 'running' && rows.length === 0 && <p className="px-3 py-8 text-center text-[13px] text-ink-3">Waiting for the first signal…</p>}
            {phase === 'done' && rows.length === 0 && <p className="px-3 py-8 text-center text-[13px] text-ink-3" data-testid="no-signals">The leader sent no signals in this session.</p>}
            {rows.length > 0 && shown.length === 0 && <p className="px-3 py-8 text-center text-[13px] text-ink-3">No signals with this status.</p>}

            <ul className="m-0 list-none p-0" data-testid="rows">
              {shown.map((r) => (
                <li key={r.n}>
                  <button onClick={() => setOpen(r.n)} data-testid="row" data-status={r.status} aria-label={`Signal ${r.n}: ${STATUS[r.status].label}. Open the explanation.`}
                          className={`grid w-full grid-cols-[48px_56px_44px_1fr] items-center gap-x-3 gap-y-1 rounded-lg border-b border-line px-2 py-2 text-left text-[13px] last:border-b-0 hover:bg-white/[0.04] md:grid-cols-[48px_56px_44px_88px_88px_172px_1fr] ${open === r.n ? 'bg-white/[0.06]' : ''}`}>
                    <span className="num text-ink-3">{nyTime(r.leader_ts)}</span>
                    <span className="num font-medium">{r.symbol ?? '—'}</span>
                    <span className={`num text-[12px] ${r.side === 'SELL' ? 'text-reject' : 'text-accept'}`}>{r.side ?? '—'}</span>
                    <span className="num text-right md:text-left">{r.qty ?? '—'}{r.status === 'CLIPPED' && r.leader_qty ? <span className="text-ink-3"> of {r.leader_qty}</span> : null}<span className="text-ink-3 md:hidden"> @ {r.price ? money(r.price) : '—'}</span></span>
                    <span className="num hidden md:inline">{r.price ? money(r.price) : '—'}</span>
                    <span className="col-span-4 md:col-span-1"><Chip status={r.status} /></span>
                    <span className="col-span-4 min-w-0 text-ink-2 md:col-span-1 md:truncate">{r.reason_text}</span>
                  </button>
                </li>
              ))}
            </ul>
          </section>
        </div>
      </div>
      {run && open !== null && <ExplainDrawer run={run} n={open} onClose={() => setOpen(null)} />}
    </div>
  )
}
