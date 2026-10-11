import { AnimatePresence, m } from 'framer-motion'
import { useRef, useState, type ReactNode } from 'react'
import { api, ApiError, MAX_UPLOAD } from '../lib/api'
import { useAsync } from '../lib/env'
import { int, money, signed } from '../lib/format'
import { store } from '../lib/store'
import type { BenchRun, CfResult, ReconResult } from '../lib/types'
import type { DrawerName } from './Panels'

export function Drawer({ name, title, onClose, children }: { name: DrawerName; title: string; onClose: () => void; children: ReactNode }) {
  return (
    <AnimatePresence>
      {name && (
        <m.aside key={name} initial={{ opacity: 0, x: 24 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: 16 }} transition={{ duration: 0.2, ease: 'easeOut' }}
                 className="glass pointer-events-auto fixed bottom-4 right-4 top-[72px] z-30 flex w-[min(500px,calc(100vw-32px))] flex-col max-md:inset-3 max-md:top-3 max-md:w-auto" role="dialog" aria-label={title} data-testid={`drawer-${name}`}>
          <header className="flex items-center justify-between border-b border-line px-4 py-3">
            <h2 className="text-[15px] font-medium">{title}</h2>
            <button className="btn h-7 px-2 text-[12px]" onClick={onClose} aria-label="Close">Esc</button>
          </header>
          <div className="scroll-thin min-h-0 flex-1 overflow-y-auto px-4 py-4">{children}</div>
        </m.aside>
      )}
    </AnimatePresence>
  )
}

const Skeleton = () => <div className="space-y-3" aria-busy="true">{[0, 1, 2, 3, 4].map((i) => <div key={i} className="skel h-9 w-full" />)}</div>
const ErrorBox = ({ e, retry }: { e: Error; retry: () => void }) => (
  <div className="rounded-lg border border-reject/30 p-3 text-[13px]"><p className="text-reject">{e.message}</p><button className="btn mt-3" onClick={retry}>Retry</button></div>
)
const Chip = ({ children, onClick, active, tone }: { children: ReactNode; onClick?: () => void; active?: boolean; tone?: 'accept' | 'reject' }) => (
  <button onClick={onClick} className={`num rounded-md border px-1.5 py-0.5 text-[11px] transition-colors hover:bg-white/10 ${active ? 'border-line-2 bg-white/10 text-ink' : tone === 'accept' ? 'border-accept/30 text-accept' : tone === 'reject' ? 'border-reject/30 text-reject' : 'border-line text-ink-2'}`}>{children}</button>
)

function CsvUpload<T>({ send, onResult, onReset, active }: { send: (b: Blob) => Promise<T>; onResult: (r: T) => void; onReset: () => void; active: boolean }) {
  const [err, setErr] = useState<string | null>(null)
  const input = useRef<HTMLInputElement>(null)
  const pick = async (f?: File) => {
    if (!f) return
    setErr(null)
    if (f.size > MAX_UPLOAD) return setErr(`File is ${int(f.size)} bytes; the limit is 1 MB (${int(MAX_UPLOAD)} bytes).`)
    try {
      onResult(await send(f))
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : String(e))
    } finally {
      if (input.current) input.current.value = ''
    }
  }
  return (
    <div className="mb-4 rounded-lg border border-dashed border-line-2 p-3">
      <div className="flex items-center gap-2">
        <input ref={input} type="file" accept=".csv,text/csv" className="sr-only" id="csv-up" onChange={(e) => pick(e.target.files?.[0])} />
        <label htmlFor="csv-up" className="btn cursor-pointer">Use my CSV</label>
        {active && <button className="btn" onClick={() => { setErr(null); onReset() }}>Back to fixture</button>}
        <span className="text-[11px] text-ink-3">ts,client_order_id,symbol,side,qty,price,fee · ≤ 1 MB · stays on this machine</span>
      </div>
      {err && <p className="num mt-2 text-[12px] text-reject" role="alert" data-testid="csv-error">{err}</p>}
    </div>
  )
}

// ------------------------------------------------------------------------------------ reconciliation
export function ReconDrawer() {
  const q = useAsync(() => api.recon(), [])
  const [res, setRes] = useState<ReconResult | null>(null)
  const [row, setRow] = useState<number | null>(null)
  const d = res ?? q.data
  const rc = d?.reconciliation
  const total = rc ? Object.values(rc.counts).reduce((a, b) => a + b, 0) : 0
  return (
    <>
      <CsvUpload send={api.reconUpload} onResult={setRes} onReset={() => setRes(null)} active={!!res} />
      {q.loading && !d ? <Skeleton /> : q.error && !d ? <ErrorBox e={q.error} retry={q.reload} /> : rc && (
        <div>
          <p className="mb-3 text-[12px] text-ink-3">{d!.input === 'upload' ? 'Your CSV' : 'Bundled fixture'} compared with the bundled fixture log ({d!.log_events} events, {rc.log_fills} ACCEPTED fills), {rc.csv_rows} broker rows, tolerance {rc.policy.tolerance_seconds}s for rows without an id.</p>
          <div className="mb-4 flex flex-wrap gap-1.5">
            {Object.entries(rc.counts).map(([k, n]) => <span key={k} className={`num rounded-md border px-2 py-1 text-[11px] ${k === 'MATCHED' ? 'border-accept/30 text-accept' : 'border-reject/30 text-reject'}`}>{k} {n}</span>)}
          </div>
          <ul className="divide-y divide-line">
            {rc.findings.filter((f) => f.category !== 'MATCHED').map((f, i) => {
              const hit = row !== null && f.csv_rows.includes(row)
              return (
                <li key={i} className={`py-3 transition-colors ${hit ? 'bg-white/[0.05] -mx-2 px-2 rounded-lg' : ''}`} data-testid="finding">
                  <div className="flex items-center justify-between gap-2">
                    <span className="num text-[12px] text-reject">{f.category}</span>
                    <span className="flex flex-wrap justify-end gap-1">
                      {f.log_seqs.map((s) => <Chip key={'s' + s} tone="accept" onClick={() => store.focusSeq('recon', s)}>seq {s}</Chip>)}
                      {f.csv_rows.map((r) => <Chip key={'r' + r} active={row === r} onClick={() => setRow(row === r ? null : r)}>row {r}</Chip>)}
                    </span>
                  </div>
                  <p className="mt-1 text-[13px] text-ink-2">{f.detail}{f.group ? ` [${f.group.log_fills} log fill(s), ${f.group.broker_rows} broker row(s)]` : ''}</p>
                </li>
              )
            })}
          </ul>
          <p className="mt-2 text-[12px] text-ink-3">{total - (rc.counts.MATCHED ?? 0)} of {total} findings are not clean matches. Click <em>seq</em> to focus that block; click <em>row</em> to highlight every finding citing that CSV row.</p>
          <div className="mt-4 rounded-lg border border-line p-3">
            <div className="label mb-1.5">Implied difference (broker minus log)</div>
            <div className="num text-[13px]">{Object.entries(rc.implied_difference.position).map(([s, q]) => `${s} ${signed(q)}`).join(', ') || 'none'}; cash {money(rc.implied_difference.cash_excl_fees)} (notional, fees excluded)</div>
            <div className="mt-1 text-[12px] text-ink-3">Left out: {rc.implied_difference.excluded_ambiguous.findings} AMBIGUOUS finding(s). Ambiguous evidence is never guessed.</div>
          </div>
          {rc.rejected_rows.length > 0 && <p className="mt-3 text-[12px] text-reject">{rc.rejected_rows.length} unreadable row(s) skipped.</p>}
        </div>
      )}
    </>
  )
}

// ------------------------------------------------------------------------------------ counterfactual
export function CfDrawer() {
  const q = useAsync(() => api.counterfactual(), [])
  const [res, setRes] = useState<CfResult | null>(null)
  const [row, setRow] = useState<number | null>(null)
  const d = res ?? q.data
  return (
    <>
      <CsvUpload send={api.counterfactualUpload} onResult={setRes} onReset={() => setRes(null)} active={!!res} />
      {q.loading && !d ? <Skeleton /> : q.error && !d ? <ErrorBox e={q.error} retry={q.reload} /> : d && (
        <div>
          <p className="mb-3 text-[12px] text-ink-3">{d.input === 'upload' ? 'Your trades' : 'Bundled 17-trade fixture'} replayed through the gate's own rules, in time order. A replay of what the rules would have decided, not a prediction of what you would have done.</p>
          <div className="mb-4 grid grid-cols-3 gap-2">
            {[['trades', d.orders], ['accepted', d.accepted], ['blocked', d.blocked]].map(([k, n]) => (
              <div key={k as string} className="rounded-lg border border-line p-2.5"><div className="label">{k}</div><div className={`num text-xl ${k === 'blocked' ? 'text-reject' : ''}`}>{n}</div></div>
            ))}
          </div>
          <table className="num mb-4 w-full text-[12.5px]">
            <thead><tr className="text-ink-3"><th className="py-1 text-left font-normal"></th><th className="text-right font-normal">gross</th><th className="text-right font-normal">fees</th><th className="text-right font-normal">net</th></tr></thead>
            <tbody>
              <tr><td className="py-1 text-ink-2">original</td><td className="text-right">{money(d.pnl.original.gross)}</td><td className="text-right">{money(d.pnl.original.fees)}</td><td className="text-right">{money(d.pnl.original.net)}</td></tr>
              <tr><td className="py-1 text-ink-2">with rules</td><td className="text-right">{money(d.pnl.with_rules.gross)}</td><td className="text-right">{money(d.pnl.with_rules.fees)}</td><td className="text-right">{money(d.pnl.with_rules.net)}</td></tr>
              <tr className="border-t border-line"><td className="py-1.5 text-ink-2">change</td><td className="text-right">{money(d.pnl.impact.gross, true)}</td><td className="text-right">{money(d.pnl.impact.fees, true)}</td><td className="text-right text-ink">{money(d.pnl.impact.net, true)}</td></tr>
            </tbody>
          </table>
          <div className="label mb-1">Blocked trades</div>
          <ul className="divide-y divide-line">
            {d.blocks.map((b) => (
              <li key={b.row} className={`cursor-pointer py-2.5 transition-colors ${row === b.row ? 'bg-white/[0.05] -mx-2 rounded-lg px-2' : ''}`} onClick={() => setRow(row === b.row ? null : b.row)} data-testid="cf-block">
                <div className="flex items-center justify-between gap-2">
                  <span className="num text-[12px] text-reject">{b.rule}</span>
                  <Chip active={row === b.row}>row {b.row}</Chip>
                </div>
                <div className="num mt-0.5 text-[12.5px] text-ink-2">{b.side} {b.qty} {b.symbol} @ {money(b.price)}</div>
                <div className="num text-[12px] text-ink-3">limit {String(b.limit)} · value {String(b.value)}</div>
                {row === b.row && <p className="mt-1 text-[12px] text-ink-2">{b.gate_text}. Trade time {b.ts}, id {b.client_order_id}.</p>}
              </li>
            ))}
          </ul>
          <details className="mt-4 text-[12px] text-ink-3"><summary className="cursor-pointer">Assumptions</summary><ul className="mt-2 list-disc space-y-1 pl-4">{d.assumptions.map((a) => <li key={a}>{a}</li>)}</ul></details>
          <p className="mt-3 text-[12px] text-ink-3">The fixture's change comes from 17 hand-built trades made to trigger every rule. It says nothing about what rules would do for a real trader.</p>
        </div>
      )}
    </>
  )
}

// ------------------------------------------------------------------------------------ benchmarks
const modeText = (mode: string, c: number) => `${mode} × ${c}`

export function BenchDrawer() {
  const q = useAsync(() => api.bench(), [])
  const [file, setFile] = useState<string | null>(null)
  const runs = q.data?.runs ?? []
  const run: BenchRun | undefined = runs.find((r) => r.file === file) ?? [...runs].reverse().find((r) => r.label === 'final') ?? runs[runs.length - 1]
  if (q.loading) return <Skeleton />
  if (q.error) return <ErrorBox e={q.error} retry={q.reload} />
  if (!run) return <p className="text-[13px] text-ink-3">No result files in bench/results/. Run <span className="num">make bench</span>.</p>
  const e = run.environment
  const micro = q.data?.fsync_micro
  return (
    <div>
      <label className="label mb-1.5 block" htmlFor="bench-run">Result file</label>
      <select id="bench-run" value={run.file} onChange={(ev) => setFile(ev.target.value)} className="mb-3 w-full rounded-lg border border-line bg-[#0d0f12] px-2.5 py-2 text-[13px] text-ink">
        {runs.map((r) => <option key={r.file} value={r.file}>{r.label} · {r.backend === 'docker' ? 'Docker' : 'native'} · {int(r.orders)} orders · {r.file}</option>)}
      </select>
      <div className="mb-3 flex flex-wrap items-center gap-2 text-[12px]">
        <span className={`num rounded-md border px-1.5 py-0.5 ${run.backend === 'docker' ? 'border-line-2 text-ink' : 'border-accept/30 text-accept'}`}>{run.backend === 'docker' ? 'DOCKER' : 'NATIVE'}</span>
        <span className="text-ink-3">{String(e.limits ?? '')}</span>
      </div>
      <p className="mb-2 text-[12px] text-ink-3">sync call: {e.fsync_call ? String(e.fsync_call) : 'not recorded in this result file (it predates that field)'}; policy per row below.</p>
      <p className="mb-3 text-[12px] text-ink-3">{String(e.cpu_model ?? '')}, {String(e.os ?? '')}. {int(run.orders)} orders, {run.reps} reps; each cell is the median of the valid reps with [min–max]. Closed loop = one order in flight per connection (latency); pipelined = throughput only.</p>
      <div className="overflow-x-auto">
        <table className="num w-full min-w-[420px] text-[12px]" data-testid="bench-table">
          <thead><tr className="text-left text-ink-3"><th className="py-1 font-normal">fsync policy</th><th className="font-normal">mode × conns</th><th className="text-right font-normal">orders/s</th><th className="whitespace-nowrap pl-3 text-right font-normal">p50 µs</th><th className="whitespace-nowrap pl-3 text-right font-normal">p99 µs</th></tr></thead>
          <tbody>
            {run.rows.map((r) => (
              <tr key={r.fsync + r.mode + r.conns} className="border-t border-line align-top">
                <td className="py-1.5 pr-2 text-ink-2">{r.fsync === 'none' ? 'none (NON-DURABLE)' : r.fsync}</td>
                <td className="whitespace-nowrap pr-2 text-ink-3">{modeText(r.mode, r.conns)}</td>
                <td className="whitespace-nowrap text-right text-ink">{int(Math.round(r.orders_per_sec.median))}<div className="text-[10px] text-ink-3">{int(Math.round(r.orders_per_sec.min))}–{int(Math.round(r.orders_per_sec.max))}</div></td>
                <td className="pl-3 text-right">{r.mode === 'closed' ? Math.round(r.lat_p50.median) : <span className="text-ink-3">n/a</span>}</td>
                <td className="pl-3 text-right">{r.mode === 'closed' ? Math.round(r.lat_p99.median) : <span className="text-ink-3">n/a</span>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="mt-2 text-[11px] text-ink-3">Latency is only measured in closed-loop mode. Policy <span className="num">none</span> never calls fsync and is not durable.</p>
      {run.recovery.length > 0 && (
        <div className="mt-5">
          <div className="label mb-1.5">Gate recovery from a log of N events ({run.backend})</div>
          <table className="num w-full text-[12px]"><tbody>
            {run.recovery.map((r) => (
              <tr key={r.events} className="border-t border-line"><td className="py-1.5 text-ink-2">{int(r.events)} events</td>
                {r.outcome && r.outcome !== 'OK' ? <td className="text-right text-reject" colSpan={2}>{r.outcome.replace('_', ' ')}: {r.note}</td> : <><td className="text-right">{r.recovery_s_median?.toFixed(2)} s</td><td className="text-right text-ink-3">{r.peak_rss_mb?.toFixed(0)} MB peak</td></>}
              </tr>
            ))}
          </tbody></table>
        </div>
      )}
      {micro && (
        <div className="mt-5">
          <div className="label mb-1.5">fsync microbenchmark ({micro.calls} calls, native)</div>
          <div className="num text-[12px] text-ink-2">os.fsync median {micro['os.fsync'].median_us.toFixed(0)} µs · F_FULLFSYNC median {micro.F_FULLFSYNC.median_us.toFixed(0)} µs</div>
        </div>
      )}
      <p className="mt-5 text-[11px] text-ink-3">Source: bench/results/{run.file}. Nothing on this panel is computed or estimated in the browser.</p>
    </div>
  )
}
