import { AnimatePresence, m } from 'framer-motion'
import type { ReactNode } from 'react'
import { api } from '../lib/api'
import { int, money, signed } from '../lib/format'
import { useAsync } from '../lib/env'
import { badFrom, store, useSnap } from '../lib/store'
import type { BenchRun } from '../lib/types'
import { policyText } from './bench'

export type DrawerName = 'recon' | 'cf' | 'bench' | null

export function Panel({ title, right, children, className = '', onClick, label }: { title: string; right?: ReactNode; children: ReactNode; className?: string; onClick?: () => void; label?: string }) {
  const Tag = onClick ? 'button' : 'section'
  return (
    <Tag onClick={onClick} aria-label={label} className={`glass block w-full p-4 text-left ${onClick ? 'cursor-pointer transition-colors hover:border-line-2' : ''} ${className}`}>
      <div className="mb-3 flex items-center justify-between">
        <h2 className="label">{title}</h2>
        {right}
      </div>
      {children}
    </Tag>
  )
}

const Row = ({ k, v, dim }: { k: string; v: ReactNode; dim?: boolean }) => (
  <div className="flex items-baseline justify-between gap-3 py-[3px]">
    <span className={`text-[13px] ${dim ? 'text-ink-3' : 'text-ink-2'}`}>{k}</span>
    <span className="num text-[13px] text-ink">{v}</span>
  </div>
)

export function PnlPanel() {
  const s = useSnap()
  const r = s.report
  const empty = !r || r.counts.events === 0
  return (
    <Panel title="P&L and positions" right={<span className="flex items-center gap-1.5 text-[11px] text-ink-3"><i className={`inline-block h-1.5 w-1.5 rounded-full ${s.conn === 'live' ? 'live-dot bg-accept' : 'bg-ink-3'}`} />{s.conn === 'live' ? 'live' : s.conn}</span>}>
      {s.reportError && !r ? (
        <p className="text-[13px] text-reject">{s.reportError}</p>
      ) : !r ? (
        <div className="space-y-2" aria-busy="true"><div className="skel h-8 w-2/3" /><div className="skel h-4 w-full" /><div className="skel h-4 w-5/6" /></div>
      ) : empty ? (
        <p className="text-[13px] text-ink-3">No orders yet. Press <kbd className="kbd">D</kbd> to run the demo.</p>
      ) : (
        <>
          <div className="num mb-1 text-[28px] leading-none tracking-tight" data-testid="net-pnl">{money(r.pnl.realized_net)}</div>
          <div className="mb-3 text-[11px] text-ink-3">realized net, FIFO, after fees</div>
          <Row k="Gross" v={money(r.pnl.realized_gross)} />
          <Row k="Fees" v={money(-r.pnl.fees)} />
          <Row k="Cash" v={money(r.cash)} dim />
          <div className="my-3 h-px bg-line" />
          <div className="label mb-1.5">Positions</div>
          {Object.keys(r.positions).length === 0 ? (
            <div className="text-[13px] text-ink-3">flat</div>
          ) : (
            Object.entries(r.positions).map(([sym, q]) => <Row key={sym} k={sym} v={`${signed(q)} sh`} />)
          )}
          <div className="my-3 h-px bg-line" />
          <div className="flex items-center justify-between text-[12px]">
            <span className="text-ink-2"><span className="num text-accept">{int(r.counts.accepted)}</span> accepted</span>
            <span className="text-ink-2"><span className="num text-reject">{int(r.counts.rejected)}</span> rejected</span>
          </div>
          {Object.entries(r.counts.rejects_by_reason).length > 0 && (
            <div className="mt-2 flex flex-wrap gap-1.5">
              {Object.entries(r.counts.rejects_by_reason).map(([k, n]) => (
                <span key={k} className="num rounded-md border border-reject/25 px-1.5 py-0.5 text-[10.5px] text-reject">{k} {n}</span>
              ))}
            </div>
          )}
        </>
      )}
    </Panel>
  )
}

export function ReconSummary({ open }: { open: (d: DrawerName) => void }) {
  const q = useAsync(() => api.recon(), [])
  const c = q.data?.reconciliation.counts
  const total = c ? Object.values(c).reduce((a, b) => a + b, 0) : 0
  const bad = c ? total - (c.MATCHED ?? 0) : 0
  return (
    <Panel title="Reconciliation" onClick={() => open('recon')} label="Open reconciliation" right={<kbd className="kbd">1</kbd>}>
      {q.loading ? <div className="skel h-10 w-full" /> : q.error || !c ? <p className="text-[13px] text-reject">{q.error?.message ?? 'unavailable'}</p> : (
        <>
          <div className="mb-2 flex h-1.5 overflow-hidden rounded-full bg-white/5" aria-hidden>
            {Object.entries(c).map(([k, n]) => <span key={k} style={{ width: `${(n / total) * 100}%` }} className={k === 'MATCHED' ? 'bg-accept/80' : k === 'AMBIGUOUS' ? 'bg-ink-2' : 'bg-reject/85'} />)}
          </div>
          <div className="text-[13px] text-ink-2"><span className="num text-ink">{c.MATCHED ?? 0}</span> matched, <span className="num text-reject">{bad}</span> need a look</div>
          <div className="mt-0.5 text-[11px] text-ink-3">bundled fixture, not your broker</div>
        </>
      )}
    </Panel>
  )
}

export function CfSummary({ open }: { open: (d: DrawerName) => void }) {
  const q = useAsync(() => api.counterfactual(), [])
  const d = q.data
  return (
    <Panel title="Counterfactual" onClick={() => open('cf')} label="Open counterfactual" right={<kbd className="kbd">2</kbd>}>
      {q.loading ? <div className="skel h-10 w-full" /> : q.error || !d ? <p className="text-[13px] text-reject">{q.error?.message ?? 'unavailable'}</p> : (
        <>
          <div className="text-[13px] text-ink-2"><span className="num text-reject">{d.blocked}</span> of <span className="num text-ink">{d.orders}</span> past trades would have been blocked</div>
          <div className="num mt-1 text-[12px] text-ink-2">replay P&amp;L change {money(d.pnl.impact.net, true)}</div>
          <div className="mt-0.5 text-[11px] text-ink-3">illustrative fixture, not a forecast</div>
        </>
      )}
    </Panel>
  )
}

/** Headline numbers read from /bench; every number carries its backend, fsync policy and call. */
export function pickHeadlines(runs: BenchRun[]) {
  const out: { run: BenchRun; row: BenchRun['rows'][number] }[] = []
  const want = (backend: 'native' | 'docker', fsync: string, mode: string, conns: number) => {
    const run = [...runs].reverse().find((r) => r.backend === backend && r.orders >= 100000 && r.rows.some((x) => x.fsync === fsync && x.mode === mode && x.conns === conns))
    const row = run?.rows.find((x) => x.fsync === fsync && x.mode === mode && x.conns === conns)
    if (run && row) out.push({ run, row })
  }
  want('native', 'batched', 'pipelined', 8)
  want('native', 'per_order', 'closed', 1)
  want('docker', 'batched', 'pipelined', 8)
  return out
}

export function BenchSummary({ open }: { open: (d: DrawerName) => void }) {
  const q = useAsync(() => api.bench(), [])
  const hs = q.data ? pickHeadlines(q.data.runs) : []
  return (
    <Panel title="Benchmarks" onClick={() => open('bench')} label="Open benchmarks" right={<kbd className="kbd">3</kbd>}>
      {q.loading ? <div className="skel h-14 w-full" /> : q.error ? <p className="text-[13px] text-reject">{q.error.message}</p> : hs.length === 0 ? <p className="text-[13px] text-ink-3">No result files in bench/results.</p> : (
        <ul className="space-y-2">
          {hs.map(({ run, row }) => (
            <li key={run.file + row.fsync + row.mode + row.conns} className="text-[12px] leading-snug">
              <span className="num text-[14px] text-ink">{int(Math.round(row.orders_per_sec.median))}</span> <span className="text-ink-2">orders/s</span>
              <div className="text-[11px] text-ink-3">
                <span className="text-ink-2">{run.backend === 'docker' ? 'Docker 1 CPU / 512 MB' : 'native'}</span> · {policyText(run, row.fsync)} · {row.mode}, {row.conns} conn{row.conns > 1 ? 's' : ''}
              </div>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  )
}

/** Verdict banner: the verifier's own message, naming the exact bad seq. */
export function Verdict() {
  const s = useSnap()
  const bad = badFrom(s)
  const v = s.verify
  const t = s.tamper
  let body: ReactNode = null
  let key = ''
  if (s.notice) {
    key = 'notice'
    body = (
      <div className="flex items-start gap-3">
        <p className="text-[13px] text-reject">{s.notice}</p>
        <button className="text-[12px] text-ink-3 hover:text-ink" onClick={() => store.clearNotice()}>dismiss</button>
      </div>
    )
  } else if (t && !(v?.running)) {
    key = 'tamper'
    const r = t.result
    body = (
      <>
        <div className="label mb-1 text-reject">Tamper detected</div>
        <p className="num text-[13px] text-ink" data-testid="verifier-message">{r.message}</p>
        <p className="mt-1 text-[12px] text-ink-3">
          Edited a copy: block {r.tampered_seq} {r.field} {r.before} to {r.after}. Blocks {r.first_bad_seq}
          {s.total > (r.first_bad_seq ?? 0) ? `–${s.total}` : ''} no longer verify. The real log was not touched.
        </p>
        <button className="btn mt-3 h-8" onClick={() => store.reset()}>Reset <kbd className="kbd">R</kbd></button>
      </>
    )
  } else if (v && !v.running && v.outcome) {
    key = 'verify' + v.startedAt
    body = v.outcome === 'ok' ? (
      <>
        <div className="label mb-1 text-accept">Verified</div>
        <p className="num text-[13px] text-ink" data-testid="verifier-message">{v.message}</p>
      </>
    ) : (
      <>
        <div className="label mb-1 text-reject">Chain broken</div>
        <p className="num text-[13px] text-ink" data-testid="verifier-message">{v.message}</p>
      </>
    )
  } else if (v?.running) {
    key = 'running'
    body = <div className="label text-accept">Verifying links...</div>
  } else if (bad !== null) {
    key = 'badlog'
    body = <p className="num text-[13px] text-reject">chain broken at seq {bad}</p>
  }
  return (
    <AnimatePresence mode="wait">
      {body && (
        <m.div key={key} initial={{ opacity: 0, y: -6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }} transition={{ duration: 0.18, ease: 'easeOut' }}
               className={`glass pointer-events-auto max-w-[560px] px-4 py-3 ${t || bad !== null ? 'border-reject/35' : v?.outcome === 'ok' ? 'border-accept/30' : ''}`} role="status" aria-live="polite">
          {body}
        </m.div>
      )}
    </AnimatePresence>
  )
}
