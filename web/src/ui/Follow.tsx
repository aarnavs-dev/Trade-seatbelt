import { useCallback, useEffect, useMemo, useState } from 'react'
import { ApiError } from '../lib/api'
import { money } from '../lib/format'
import { LOCAL_FEED_WARNING, SOLANA_DOES_NOT, SOLANA_DOES_NOT_TITLE, SOLANA_STEPS, SOLANA_WHAT_IS_PUBLIC } from '../lib/copy'
import {
  byReason, DEFAULT_OPTIONS, FILTERS, follow, nyTime, PACES, rowBadge, rowToEvent, shortKey, STATUS,
  type CopyMode, type Explanation, type Feed, type Leader, type Options, type Row, type SignalSource, type Status, type Summary,
} from '../lib/follow'
import { rules } from '../lib/rules'
import { ChainStore, useSnap } from '../lib/store'
import ChainScene from '../scene/ChainScene'
import { Drawer, Page, Status as StatusText } from './kit'

type Phase = 'idle' | 'starting' | 'running' | 'done' | 'error'
const LIVE = ['starting', 'running']
const FEEDS: { key: Feed; label: string; about: string }[] = [
  { key: 'auto', label: 'Auto', about: 'Chain first; if it cannot be read, a local feed, and the page says so.' },
  { key: 'chain', label: 'Chain (devnet)', about: 'Only signals read from Solana devnet. If that fails, nothing starts.' },
  { key: 'local', label: 'Local feed', about: 'Signals generated on this machine, not on-chain.' },
]
const COPY: { key: CopyMode; label: string }[] = [{ key: 'same', label: 'Same as the leader' }, { key: 'percent', label: 'A percentage' }, { key: 'fixed', label: 'A fixed amount' }]

/** One row's provenance: leader, signal number, where it came from, and a transaction link only when it really has one. */
function SourceBadge({ r, leader }: { r: Row; leader: string }) {
  const b = rowBadge(r, leader)
  return (
    <span className="srcbadge" data-testid="row-source" data-source={r.source}>
      <span className="line"><span style={{ fontWeight: 500 }}>{b.leader}</span><span className="mono muted" title={b.signalId}>{b.signal}</span>
        <span className={`tag tag-${b.tone}`}>{b.source}</span></span>
      <span className="muted" style={{ fontSize: 12.5 }}>
        {b.tx ? <>tx <a className="mono" href={b.tx.url ?? '#'} target="_blank" rel="noopener noreferrer" onClick={(e) => e.stopPropagation()} data-testid="row-tx">{b.tx.short}</a>{r.slot ? ` · slot ${r.slot.toLocaleString('en-US')}` : ''} · {b.note}</> : b.note}
      </span>
    </span>
  )
}

/** Where this run's signals came from, and what Solana does and does not do. A local or saved source is never made to look like chain data. */
function SignalSourcePanel({ s }: { s: SignalSource }) {
  const tone = s.kind === 'chain' ? 'chain' : s.kind === 'chain_cache' ? 'cache' : 'local'
  const k = (a: string, v: React.ReactNode) => <div className="kv"><dt>{a}</dt><dd>{v}</dd></div>
  return (
    <section className="srcpanel stack" aria-label="Signal source" data-testid="source-panel" data-kind={s.kind}>
      <p className="label">Signal source</p>
      {s.kind !== 'chain' && (
        <p className="note note-warn" role="status" data-testid="source-warning">
          {s.kind === 'local' ? LOCAL_FEED_WARNING : 'A saved copy of an earlier devnet read. It was not read from Solana during this run.'}
          {s.fallback && <> You asked for Solana devnet, so this is a fallback: {s.fallback_why}.</>}
        </p>
      )}
      <dl className="plain">
        {k('Leader', <>{s.leader.name ?? s.leader.id} <span className="muted">· {s.leader.strategy}</span></>)}
        {k('Source used', <><span className={`tag tag-${tone}`} data-testid="source-kind">{s.label}</span>{s.kind === 'chain' && s.rpc_host && <span className="muted"> · read live from {s.rpc_host}</span>}</>)}
        {k('Published by', s.publisher_key
          ? <><span className="mono" title={s.publisher_key} data-testid="publisher-key">{shortKey(s.publisher_key)}</span> · <a href={s.publisher_explorer_url ?? '#'} target="_blank" rel="noopener noreferrer">account on Solana Explorer</a></>
          : <span className="muted">Nobody: local-feed signals are not signed or published.</span>)}
        {k('Transaction', s.first_tx
          ? <><span className="mono" style={{ overflowWrap: 'anywhere' }} data-testid="first-tx">{s.first_tx}</span>{' '}
              <a href={s.first_tx_explorer_url ?? '#'} target="_blank" rel="noopener noreferrer" data-testid="first-tx-link">View on Solana Explorer (devnet)</a>
              <span className="muted"> · {s.with_transaction} of {s.signals} signals have a transaction; the others do not</span></>
          : <span className="muted">None. {s.kind === 'local' ? 'A local feed has no transactions.' : 'No signal in this run carries one.'}</span>)}
        {k('Market data', <>{s.data.label ?? '—'}{s.data.provider && !s.data.synthetic ? <span className="muted"> · {s.data.provider}, session {s.data.session}</span> : null}</>)}
      </dl>
      <div className="stack" style={{ fontSize: 14 }}>
        <p className="label">What Solana does here{s.kind !== 'chain' && ' (when the source is Solana; this run’s is not)'}</p>
        <p className="muted prose">A public, timestamped record of each signal. {SOLANA_STEPS[0].text} {SOLANA_STEPS[1].text} {SOLANA_STEPS[2].text}</p>
        <p className="label">{SOLANA_DOES_NOT_TITLE}</p>
        <p className="muted prose">{SOLANA_DOES_NOT} {SOLANA_WHAT_IS_PUBLIC}</p>
      </div>
    </section>
  )
}

function Kv({ k, v }: { k: string; v: React.ReactNode }) { return <div className="kv"><dt>{k}</dt><dd>{v}</dd></div> }

function Explain({ run, n, onClose }: { run: string; n: number; onClose: () => void }) {
  const [x, setX] = useState<Explanation | null>(null)
  const [err, setErr] = useState<string | null>(null)
  useEffect(() => {
    let live = true
    setX(null); setErr(null)
    follow.explain(run, n).then((d) => live && setX(d), (e) => live && setErr(e instanceof Error ? e.message : String(e)))
    return () => { live = false }
  }, [run, n])
  const s = x?.leader_signal
  return (
    <Drawer title="Why this happened" onClose={onClose}>
      {err && <p className="error" role="alert">{err}</p>}
      {!x && !err && <p className="muted" role="status">Loading the explanation…</p>}
      {x && (
        <div className="stack">
          <p><StatusText dot={STATUS[x.status].dot}>{STATUS[x.status].label}</StatusText>{x.order && <span className="mono muted"> · {x.order}</span>}</p>
          <p style={{ fontFamily: 'var(--font-serif)', fontSize: 22, lineHeight: 1.3 }} data-testid="explain-detail">{x.detail}</p>
          <dl className="plain">
            {x.rule && <Kv k="Rule" v={<>{x.rule}{x.protects_against && <span className="muted">. It protects against {x.protects_against}.</span>}</>} />}
            {x.limit && <Kv k="Your limit" v={x.limit} />}
            {x.value_before && <Kv k="At that moment" v={x.value_before} />}
            {x.value_if_accepted && <Kv k="If it had gone through" v={x.value_if_accepted} />}
            {x.cause_text && <Kv k="Caused by" v={x.cause_text} />}
            {s && <Kv k="Leader’s signal" v={<span className="mono" style={{ fontSize: 13 }}>#{String(s.leader_seq)} {String(s.side)} {String(s.qty)} {String(s.symbol)} at {money(Number(s.ref_price))}, bar ending {nyTime(Number(s.data_ts))} ET</span>} />}
            <Kv k="Solana" v={x.explorer_url ? <a href={x.explorer_url} target="_blank" rel="noopener noreferrer" data-testid="explorer-link">View this signal on Solana Explorer (devnet)</a> : <span className="muted">Local feed, not on-chain</span>} />
          </dl>
          <p className="muted" style={{ fontSize: 13 }}>Computed from the logs and your rules only. Paper trading.</p>
        </div>
      )}
    </Drawer>
  )
}

export default function Follow() {
  const initialRun = new URLSearchParams(location.search).get('run')
  const [leaders, setLeaders] = useState<Leader[]>([])
  const [leaderNote, setLeaderNote] = useState('')
  const [saved, setSaved] = useState<boolean | null>(null)
  const [o, setO] = useState<Options>(DEFAULT_OPTIONS)
  const [run, setRun] = useState<string | null>(initialRun)
  const [phase, setPhase] = useState<Phase>(initialRun ? 'running' : 'idle')
  const [rows, setRows] = useState<Row[]>([])
  const [summary, setSummary] = useState<Summary | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [optError, setOptError] = useState<string | null>(null)
  const [conn, setConn] = useState<'live' | 'reconnecting'>('live')
  const [filter, setFilter] = useState<'ALL' | Status>('ALL')
  const [open, setOpen] = useState<number | null>(null)
  const [setup, setSetup] = useState(!initialRun)
  // the strip above the feed: the last decisions as blocks, in step with the rows
  const chain = useMemo(() => new ChainStore({ travelMs: 0, spawnGapMs: 150 }), [])
  const snap = useSnap(chain)
  useEffect(() => { if (snap.selected !== null) { setOpen(snap.selected); chain.follow() } }, [snap.selected, chain])

  useEffect(() => {
    follow.leaders().then((l) => { setLeaders(l.leaders); setLeaderNote(l.note) }, () => undefined)
    rules.get().then((s) => setSaved(s.saved), () => setSaved(null))
  }, [])

  const refresh = useCallback(async (id: string) => {
    try { setSummary(await follow.summary(id)) } catch (e) { if (e instanceof ApiError && e.status === 404) { setError('That run does not exist.'); setPhase('error') } }
  }, [])

  useEffect(() => {
    if (!run) return
    const src = new EventSource(follow.streamUrl(run))
    src.addEventListener('signal', (e) => {
      const r = JSON.parse((e as MessageEvent).data) as Row
      setConn('live'); setPhase('running')
      chain.push(rowToEvent(r), { animate: true, onchain: !!r.tx })
      setRows((prev) => (prev.some((p) => p.n === r.n) ? prev : [...prev, r]))
    })
    src.addEventListener('end', () => { src.close(); setPhase((p) => (p === 'error' ? p : 'done')); void refresh(run) })
    src.onerror = () => src.readyState !== EventSource.CLOSED && setConn('reconnecting')
    const t = setInterval(() => void refresh(run), 1500)
    void refresh(run)
    return () => { src.close(); clearInterval(t) }
  }, [run, refresh, chain])

  const start = async () => {
    setError(null); setOptError(null); setRows([]); chain.clear(); setSummary(null); setOpen(null); setPhase('starting'); setSetup(false)
    try { setRun((await follow.start(o)).run) }
    catch (e) {
      const msg = e instanceof Error ? e.message : String(e)
      if (e instanceof ApiError && (e.status === 422 || e.status === 409)) { setOptError(msg); setPhase('idle'); setSetup(true); if (e.body?.error === 'rules_not_saved') setSaved(false) }
      else { setError(msg); setPhase('error') }
    }
  }
  const stop = async () => { if (run) { try { await follow.stop(run) } catch { /* the stream's end event reports the outcome */ } } }
  const set = (patch: Partial<Options>) => { setO((x) => ({ ...x, ...patch })); setOptError(null) }

  const shown = useMemo(() => rows.filter((r) => filter === 'ALL' || r.status === filter).slice().reverse(), [rows, filter])
  const count = (...s: Status[]) => rows.filter((r) => s.includes(r.status)).length
  const live = LIVE.includes(phase)
  const leaderName = summary?.signal_source.leader.name ?? leaders.find((l) => l.id === o.leader)?.name ?? o.leader
  const finishedBySession = phase === 'done' && summary?.session_limit

  return (
    <Page title="Follow">
      <div className="container" style={{ padding: 'var(--s8) var(--gutter) var(--s12)' }}>
        <div className="stack-lg">
          <div className="stack">
            <h1 style={{ fontSize: 'clamp(36px, 4.4vw, 56px)', lineHeight: 1.08 }}>Follow a leader</h1>
            <p className="muted prose">Pick whose signals to copy and how much. Each one passes your rules before anything is copied. All fills are paper.</p>
          </div>

          {saved === false && <p className="note note-warn" data-testid="rules-banner">You haven’t saved your rules yet. <a href="/rules">Set your rules</a> first; a run only starts under limits you chose.</p>}

          {setup && !live && (
            <div className="grid12" data-testid="setup">
              <div className="c-1-7 stack-lg">
                <div>
                  <p className="label" style={{ marginBottom: 'var(--s1)' }}>Leader</p>
                  <ul className="rule-list" style={{ borderTop: '1px solid var(--line)' }}>
                    {(leaders.length ? leaders : [{ id: 'momentum', name: 'Momentum', about: '', public_key: null, explorer_url: null } as Leader]).map((l) => (
                      <li key={l.id} style={{ gridTemplateColumns: '28px minmax(0,1fr)' }}>
                        <input type="radio" name="leader" checked={o.leader === l.id} onChange={() => set({ leader: l.id })} aria-label={l.name} data-testid={`leader-${l.id}`} style={{ accentColor: 'var(--accent)', marginTop: 4 }} />
                        <div><span style={{ fontWeight: 500 }}>{l.name}</span> <span className="muted">{l.about}</span>
                          <div className="mono muted" style={{ fontSize: 12.5, marginTop: 4 }}>{l.public_key ? <>Signed by {shortKey(l.public_key)} · <a href={l.explorer_url ?? '#'} target="_blank" rel="noopener noreferrer">account on Solana Explorer</a></> : 'No leader key yet: run make leaders-setup'}</div></div>
                      </li>
                    ))}
                  </ul>
                  {leaderNote && <p className="muted" style={{ fontSize: 13, marginTop: 8 }}>{leaderNote}</p>}
                </div>
                <div className="stack">
                  <p className="label">Source</p>
                  <div className="choices" role="group" aria-label="Source">{FEEDS.map((f) => <button key={f.key} className="choice" aria-pressed={o.feed === f.key} onClick={() => set({ feed: f.key })} data-testid={`feed-${f.key}`}>{f.label}</button>)}</div>
                  <p className="muted" style={{ fontSize: 13.5 }}>{FEEDS.find((f) => f.key === o.feed)!.about}</p>
                </div>
                <div className="stack">
                  <p className="label">Pace</p>
                  <div className="choices" role="group" aria-label="Pace">{PACES.map((p) => <button key={p.key} className="choice" aria-pressed={o.pace === p.key} onClick={() => set({ pace: p.key })} data-testid={`pace-${p.key}`}>{p.label}</button>)}</div>
                  <p className="muted" style={{ fontSize: 13.5 }}>{PACES.find((p) => p.key === o.pace)!.about}. Pace changes timing only, never a decision.</p>
                </div>
              </div>
              <div className="c-8-5 stack-lg">
                <div className="stack">
                  <p className="label">How much to copy</p>
                  <div className="choices" role="group" aria-label="Copy size">{COPY.map((c) => <button key={c.key} className="choice" aria-pressed={o.copyMode === c.key} onClick={() => set({ copyMode: c.key })} data-testid={`copy-${c.key}`}>{c.label}</button>)}</div>
                  {o.copyMode !== 'same' && (
                    <div><input className="input" style={{ maxWidth: 160 }} inputMode="numeric" value={o.copyValue} onChange={(e) => set({ copyValue: e.target.value })} aria-label={o.copyMode === 'percent' ? 'Percent of the leader’s size' : 'Shares per trade'} data-testid="copy-value" />
                      <p className="unit">{o.copyMode === 'percent' ? 'percent of the leader’s size, rounded down' : 'shares per trade'}</p></div>
                  )}
                </div>
                <div>
                  <p className="label" style={{ marginBottom: 'var(--s1)' }}>Session limits <span style={{ textTransform: 'none', letterSpacing: 0 }}>(optional)</span></p>
                  {([['maxTrades', 'Most trades', 'trades'], ['maxVolume', 'Most shares in total', 'shares'], ['maxTotalDollars', 'Most money in total', 'dollars'], ['maxTradeDollars', 'Most money in one trade', 'dollars']] as const).map(([k, name, unit]) => (
                    <div className="field" key={k} style={{ gridTemplateColumns: 'minmax(0,1fr) 120px', padding: '12px 0' }}>
                      <label htmlFor={`s-${k}`}>{name}</label>
                      <div><input id={`s-${k}`} className="input" inputMode="decimal" placeholder="no limit" value={o[k]} onChange={(e) => set({ [k]: e.target.value })} data-testid={`session-${k}`} /><p className="unit">{unit}</p></div>
                    </div>
                  ))}
                  <p className="muted" style={{ fontSize: 13, marginTop: 8 }}>These stop new trades only; closing a position is never blocked. Reaching one ends the session.</p>
                </div>
              </div>
            </div>
          )}

          {optError && <p className="error" role="alert" data-testid="option-error">{optError}</p>}

          <div style={{ display: 'flex', gap: 'var(--s3)', alignItems: 'center', flexWrap: 'wrap', borderTop: '1px solid var(--line)', paddingTop: 'var(--s3)' }}>
            {live ? <button className="btn btn-primary" onClick={stop} data-testid="btn-stop">Stop</button>
                  : <button className="btn btn-primary" onClick={start} disabled={saved === false} data-testid="btn-start">{phase === 'done' || phase === 'error' ? 'Run again' : 'Start'}</button>}
            {!live && !setup && <button className="btn" onClick={() => setSetup(true)}>Change options</button>}
            {summary?.data_label && (
              <p className="muted" style={{ fontSize: 13.5 }} data-testid="badges">
                Data: <span className="mono" style={{ color: 'var(--ink)' }} data-testid="badge-data">{summary.data_label}</span>
                {' · '}Signals: <span className="mono" style={{ color: 'var(--ink)' }} data-testid="badge-feed">{summary.feed.badge}</span>
                {' · '}<span className="mono" style={{ color: 'var(--ink)' }}>PAPER EXECUTION</span>
              </p>
            )}
          </div>

          {summary?.notes.map((n) => <p key={n} className="note note-warn" role="status" data-testid="note">{n}</p>)}
          {finishedBySession && <p className="note note-warn" role="status" data-testid="session-limit">{summary!.session_limit!.text} The session finished here.</p>}
          {summary?.signal_source && <SignalSourcePanel s={summary.signal_source} />}

          {(rows.length > 0 || phase !== 'idle') && (
            <section aria-label="Live feed" data-testid="feed">
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: 'var(--s3)', flexWrap: 'wrap', alignItems: 'baseline', marginBottom: 'var(--s3)' }}>
                <div className="tabs" role="tablist" aria-label="Filter by status">
                  {FILTERS.map((f) => <button key={f.key} className="tab" role="tab" aria-selected={filter === f.key} onClick={() => setFilter(f.key)} data-testid={`filter-${f.key}`}>{f.label}</button>)}
                </div>
                <p className="muted num" style={{ fontSize: 13 }} aria-live="polite" data-testid="strip">
                  {rows.length}{summary && live ? ` of ${summary.total_in_feed}` : ''} seen · {count('COPIED', 'CLIPPED')} copied · {count('STOPPED_BY_COPIER')} skipped before the gate · {count('REJECTED_BY_RULES')} blocked by your rules
                  {conn === 'reconnecting' ? ' · connection lost, retrying' : live ? ' · live' : phase === 'done' ? ' · finished' : ''}
                </p>
              </div>
              <p className="muted" style={{ fontSize: 13, margin: '-8px 0 var(--s3)' }} data-testid="count-help">
                <b style={{ fontWeight: 500 }}>Skipped before the gate:</b> the copier never sent the order (nothing to sell, a repeat or stale signal, a session limit)
                {summary && byReason(summary.stopped_by_reason) ? <> — <span className="mono">{byReason(summary.stopped_by_reason)}</span></> : null}.{' '}
                <b style={{ fontWeight: 500 }}>Blocked by your rules:</b> the gate received it and refused it under your limits
                {summary && byReason(summary.rejected_by_reason) ? <> — <span className="mono">{byReason(summary.rejected_by_reason)}</span></> : null}.
              </p>

              {phase !== 'idle' && <div style={{ marginBottom: 'var(--s3)' }}><ChainScene store={chain} variant="strip" label="The last decisions as blocks, newest next to the rules ring" /></div>}
              {phase === 'error' && <div role="alert" data-testid="error-box"><p className="error">{error}</p><button className="btn" style={{ marginTop: 'var(--s2)' }} onClick={start}>Try again</button></div>}
              {phase === 'starting' && <p className="muted" role="status" data-testid="loading">Fetching market data and the leader’s signals. This can take up to a minute.</p>}
              {phase === 'running' && rows.length === 0 && <p className="muted" role="status">Waiting for the first signal…</p>}
              {phase === 'done' && rows.length === 0 && <p className="muted" data-testid="no-signals">The leader sent no signals in this session.</p>}
              {rows.length > 0 && shown.length === 0 && <p className="muted">No signals with this status.</p>}

              {shown.length > 0 && (
                <table className="tbl feed">
                  <thead><tr><th>Time</th><th>Symbol</th><th>Side</th><th className="r">Qty</th><th className="r">Price</th><th>Status</th><th>Reason</th><th>Signal source</th></tr></thead>
                  <tbody data-testid="rows">
                    {shown.map((r) => (
                      <tr key={r.n} className="rowbtn row-in" tabIndex={0} role="button" data-testid="row" data-status={r.status} data-open={open === r.n ? 1 : 0} data-hover={snap.hover === r.n ? 1 : 0}
                          onMouseEnter={() => chain.hover(r.n)} onMouseLeave={() => chain.hover(null)}
                          aria-label={`Signal ${r.n}: ${STATUS[r.status].label}. Open the explanation.`}
                          onClick={() => setOpen(r.n)} onKeyDown={(e) => (e.key === 'Enter' || e.key === ' ') && (e.preventDefault(), setOpen(r.n))}>
                        <td className="num muted">{nyTime(r.leader_ts)}</td>
                        <td className="num" style={{ fontWeight: 500 }}>{r.symbol ?? '—'}</td>
                        <td className="num">{r.side ?? '—'}</td>
                        <td className="num r">{r.qty ?? '—'}{r.status === 'CLIPPED' && r.leader_qty ? <span className="muted"> of {r.leader_qty}</span> : null}</td>
                        <td className="num r c-price">{r.price ? money(r.price) : '—'}</td>
                        <td className="c-status"><StatusText dot={STATUS[r.status].dot}>{STATUS[r.status].label}</StatusText></td>
                        <td className="c-reason" style={{ color: 'var(--ink-2)', maxWidth: '38ch' }}>{r.reason_text}</td>
                        <td className="c-sol" style={{ fontSize: 13 }}><SourceBadge r={r} leader={leaderName} /></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </section>
          )}

          {phase === 'idle' && rows.length === 0 && <p className="muted prose" data-testid="empty">Press Start to follow a leader through one simulated session. Nothing here is real: no real orders, no real money.</p>}

          <details style={{ borderTop: '1px solid var(--line)', paddingTop: 'var(--s3)' }} data-testid="how">
            <summary>How this works</summary>
            <ol className="steps prose" style={{ marginTop: 'var(--s2)' }}>
              {SOLANA_STEPS.map((x) => <li key={x.title}><div><b>{x.title}.</b> <span className="muted">{x.text}</span></div></li>)}
            </ol>
            <p className="muted prose" style={{ marginTop: 'var(--s2)' }}>{SOLANA_DOES_NOT}</p>
          </details>
        </div>
      </div>
      {run && open !== null && <Explain run={run} n={open} onClose={() => { setOpen(null); chain.select(null); chain.follow() }} />}
    </Page>
  )
}
