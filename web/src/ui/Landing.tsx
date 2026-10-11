import { lazy, Suspense, useEffect, useMemo, useState, type ReactNode } from 'react'
import { LazyMotion, MotionConfig, domAnimation, m } from 'framer-motion'
import rulesYaml from '../../../rules.yaml?raw'
import fixtureLog from '../../../fixtures/recon/gate_log.jsonl?raw'
import { api } from '../lib/api'
import { forced2D, hasWebGL, useAsync, usePhone, useReducedMotion } from '../lib/env'
import { int, money } from '../lib/format'
import { store, useSnap } from '../lib/store'
import type { LogEvent } from '../lib/types'
import Chain2D from './Chain2D'
import { pickHeadlines } from './Panels'
import { Mark, SceneBoundary, SimulatedLabel, StageLoading } from './shared'
import { policyText } from './bench'

const Scene = lazy(() => import('../scene/Scene'))

/** The hero replays a real hash-chained log: the bundled fixture, written by the real gate with a scripted clock. */
const FIXTURE: LogEvent[] = fixtureLog.trim().split('\n').map((l) => JSON.parse(l))

function Reveal({ children, className = '', delay = 0 }: { children: ReactNode; className?: string; delay?: number }) {
  return (
    <m.div className={className} initial={{ opacity: 0, y: 10 }} whileInView={{ opacity: 1, y: 0 }} viewport={{ once: true, margin: '-60px' }} transition={{ duration: 0.45, ease: 'easeOut', delay }}>
      {children}
    </m.div>
  )
}

const Section = ({ id, label, title, children }: { id: string; label: string; title: string; children: ReactNode }) => (
  <section id={id} className="mx-auto w-full max-w-[1120px] scroll-mt-20 px-6 py-24 md:px-10 md:py-32">
    <Reveal>
      <div className="label mb-4">{label}</div>
      <h2 className="mb-12 max-w-[760px] text-[28px] font-semibold leading-[1.15] tracking-tight md:text-[40px]">{title}</h2>
    </Reveal>
    {children}
  </section>
)

function Nav() {
  return (
    <nav className="fixed inset-x-0 top-0 z-30 border-b border-line bg-bg/70 backdrop-blur-md" aria-label="Main">
      <div className="mx-auto flex h-14 max-w-[1120px] items-center justify-between px-6 md:px-10">
        <a href="/" className="flex items-center gap-2.5 text-[15px] font-medium tracking-tight"><Mark />Trade Seatbelt</a>
        <div className="flex items-center gap-6 text-[13px] text-ink-2">
          <a className="hidden hover:text-ink md:inline" href="#how">How it works</a>
          <a className="hidden hover:text-ink md:inline" href="#proof">Proof</a>
          <a className="hidden hover:text-ink md:inline" href="/app/follow" data-testid="nav-follow">Follow a leader</a>
          <a className="btn btn-accept" href="/app" data-testid="nav-dashboard">Open dashboard</a>
        </div>
      </div>
    </nav>
  )
}

function HeroStage() {
  const phone = usePhone()
  const reduced = useReducedMotion()
  const [gl] = useState(hasWebGL)
  if (phone || reduced || !gl || forced2D()) {
    return <div className="absolute inset-x-0 bottom-28 h-[150px] px-4 md:bottom-24"><Chain2D compact /></div>
  }
  return (
    <SceneBoundary fallback={() => <div className="absolute inset-x-0 bottom-24 h-[150px] px-4"><Chain2D compact /></div>}>
      <Suspense fallback={<StageLoading text="Loading scene" />}>
        <Scene variant="hero" />
      </Suspense>
    </SceneBoundary>
  )
}

function HeroCaption() {
  const s = useSnap()
  const v = s.verify
  return (
    <div className="num absolute bottom-6 right-6 z-10 max-w-[420px] text-right text-[11px] leading-relaxed text-ink-3 max-md:left-6 max-md:right-6 max-md:text-left" data-testid="hero-caption">
      A real hash-chained log, {FIXTURE.length} events from the bundled fixture, replayed. Each block's hash covers the one before it.
      <div className={v?.outcome === 'ok' ? 'text-accept' : v?.outcome === 'broken' ? 'text-reject' : 'text-ink-3'} aria-live="polite">
        {v?.running ? 'verifying...' : v?.message ?? `block ${Math.max(s.total, 0)} of ${FIXTURE.length}`}
      </div>
    </div>
  )
}

function Hero() {
  useEffect(() => {
    store.startReplay(FIXTURE, 1000)
    return () => store.stopReplay()
  }, [])
  return (
    <header className="relative min-h-[640px] overflow-hidden" style={{ height: 'min(100dvh, 900px)' }} data-testid="hero">
      <HeroStage />
      <div className="pointer-events-none absolute inset-0 bg-[linear-gradient(90deg,rgb(7_8_10/0.92)_0%,rgb(7_8_10/0.7)_34%,rgb(7_8_10/0)_62%)] max-md:bg-[linear-gradient(180deg,rgb(7_8_10/0.9)_0%,rgb(7_8_10/0)_62%)]" />
      <div className="relative z-10 mx-auto flex h-full max-w-[1120px] flex-col justify-center px-6 pb-24 pt-20 md:px-10 max-md:justify-start max-md:pt-28">
        <Reveal><SimulatedLabel /></Reveal>
        <Reveal delay={0.05}>
          <h1 className="mt-7 text-[44px] font-semibold leading-[1.04] tracking-[-0.03em] md:text-[60px] lg:text-[72px]">
            <span className="block md:whitespace-nowrap">Rules before the trade.</span><span className="block text-ink-2 md:whitespace-nowrap">Receipts after.</span>
          </h1>
        </Reveal>
        <Reveal delay={0.12}>
          <p className="mt-6 max-w-[500px] text-[16px] leading-relaxed text-ink-2 md:text-[18px]">
            Trade Seatbelt checks every simulated order against your own risk rules, then writes each decision to a tamper-evident log that anyone can verify afterwards.
          </p>
        </Reveal>
        <Reveal delay={0.18} className="mt-9 flex flex-wrap items-center gap-3">
          <a className="btn btn-accept h-11 px-5 text-[15px]" href="/app" data-testid="cta-hero">Open the dashboard</a>
          <a className="btn h-11 px-5 text-[15px]" href="#how">How it works</a>
        </Reveal>
      </div>
      <HeroCaption />
    </header>
  )
}

const Col = ({ title, children }: { title: string; children: ReactNode }) => (
  <div className="border-t border-line pt-5"><h3 className="mb-2 text-[17px] font-medium tracking-tight">{title}</h3><p className="text-[15px] leading-relaxed text-ink-2">{children}</p></div>
)

function Problem() {
  return (
    <Section id="problem" label="The problem" title="Intraday trading is fast. Manual records are not.">
      <div className="grid grid-cols-1 gap-8 md:grid-cols-3">
        <Reveal><Col title="Heavy volume">Many orders in a short time is normal. Nobody checks each one against their own limits while the market moves.</Col></Reveal>
        <Reveal delay={0.06}><Col title="Manual logs fall behind">A spreadsheet or a notebook is filled in after the fact, if at all. A gap or an edit is invisible until it matters.</Col></Reveal>
        <Reveal delay={0.12}><Col title="One broken rule can wipe out a day">A position over your limit, a fat-finger price, revenge trading after a loss: each one is a single order away.</Col></Reveal>
      </div>
    </Section>
  )
}

function Step({ n, title, children, extra }: { n: number; title: string; children: ReactNode; extra: ReactNode }) {
  return (
    <div className="flex min-w-0 flex-col">
      <div className="num mb-4 text-[12px] text-ink-3">0{n}</div>
      <h3 className="mb-2 text-[20px] font-medium tracking-tight">{title}</h3>
      <p className="mb-5 text-[15px] leading-relaxed text-ink-2">{children}</p>
      <div className="glass mt-auto overflow-hidden p-4">{extra}</div>
    </div>
  )
}

function HowItWorks() {
  const reject = useMemo(() => FIXTURE.find((e) => e.decision === 'REJECT'), [])
  const rulesShown = useMemo(() => rulesYaml.split('\n').filter((l) => l.trim() && !l.trim().startsWith('#') || l.includes('# ')).slice(0, 12).join('\n'), [])
  return (
    <Section id="how" label="How it works" title="Three steps, each one checkable.">
      <div className="grid grid-cols-1 gap-10 md:grid-cols-3">
        <Reveal>
          <Step n={1} title="Set your rules" extra={<pre className="num overflow-x-auto text-[11px] leading-relaxed text-ink-2" aria-label="rules.yaml">{rulesShown}</pre>}>
            Position size, price band, order rate, loss streak, daily loss and a kill switch live in one small file. This is the file the gate and the auditor both read.
          </Step>
        </Reveal>
        <Reveal delay={0.07}>
          <Step n={2} title="The gate checks every order" extra={reject && (
            <div className="num text-[12px] leading-relaxed">
              <div><span className="text-ink-3">#{reject.seq}</span> {reject.side} {reject.qty} {reject.symbol} @ {money(reject.price)}</div>
              <div className="text-reject">REJECT {reject.reason_code}</div>
              <div className="text-ink-2">{reject.reason_text}</div>
              <div className="mt-2 text-ink-3">from the bundled fixture log</div>
            </div>
          )}>
            Each order is accepted or rejected with a reason code, in the order received. The decision is written to an append-only, hash-chained log before the reply is sent.
          </Step>
        </Reveal>
        <Reveal delay={0.14}>
          <Step n={3} title="The auditor proves what happened" extra={<div className="num text-[12px] leading-relaxed text-ink-2">edit one line of a copy of the log<br /><span className="text-reject">chain broken at seq N: hash mismatch</span><br /><span className="text-ink-3">it names the first bad seq; the real log is untouched</span></div>}>
            A separate process replays the log with the same rules, verifies every link, computes FIFO P&amp;L with fees, and compares against a broker CSV without guessing.
          </Step>
        </Reveal>
      </div>
    </Section>
  )
}

function Who() {
  return (
    <Section id="who" label="Who it is for" title="People who need the record to hold up.">
      <div className="grid grid-cols-1 gap-8 md:grid-cols-3">
        <Reveal><Col title="Intraday traders">With high order volume, who cannot manually log and audit every trade.</Col></Reveal>
        <Reveal delay={0.06}><Col title="Traders with large losses">Losses that come from breaking their own rules: oversized positions, revenge trading, fat-finger prices.</Col></Reveal>
        <Reveal delay={0.12}><Col title="Anyone reconciling with a broker">When the broker statement and your own records disagree and you need to show what actually happened.</Col></Reveal>
      </div>
    </Section>
  )
}

function Stat({ big, unit, lines, badge }: { big: string; unit: string; lines: string[]; badge: string }) {
  return (
    <div className="border-t border-line pt-5">
      <div className="flex items-baseline gap-2"><span className="num text-[34px] leading-none tracking-tight md:text-[40px]">{big}</span><span className="text-[14px] text-ink-2">{unit}</span></div>
      <div className="mt-3 flex flex-wrap items-center gap-2"><span className={`num rounded-md border px-1.5 py-0.5 text-[10.5px] ${badge === 'DOCKER' ? 'border-line-2 text-ink' : 'border-accept/30 text-accept'}`}>{badge}</span></div>
      <ul className="mt-2 space-y-0.5 text-[12.5px] text-ink-3">{lines.map((l) => <li key={l}>{l}</li>)}</ul>
    </div>
  )
}

const pill = {
  passing: 'border-accept/35 text-accept',
  partial: 'border-line-2 text-ink-2',
  failing: 'border-reject/45 text-reject',
  unknown: 'border-line-2 text-ink-3',
  not_pytest: 'border-line-2 text-ink-3',
} as const
const pillText = (r: { status: keyof typeof pill; passed: number; failed: number }) =>
  r.status === 'passing' ? `passing · ${r.passed} tests` : r.status === 'failing' ? `${r.failed} failing` : r.status === 'partial' ? `partial · ${r.passed} passed` : r.status === 'not_pytest' ? 'checked by the benchmark harness' : 'no result'

function Proof() {
  const bench = useAsync(() => api.bench(), [])
  const inv = useAsync(() => api.invariants(), [])
  const hs = bench.data ? pickHeadlines(bench.data.runs) : []
  const nat = bench.data?.runs.find((r) => r.backend === 'native' && r.orders >= 100000)
  const closed = nat?.rows.find((r) => r.fsync === 'per_order' && r.mode === 'closed' && r.conns === 1)
  const stats = [
    ...hs.filter((h) => h.row.mode === 'pipelined').map(({ run, row }) => ({
      key: run.file + row.fsync, big: int(Math.round(row.orders_per_sec.median)), unit: 'orders/s, throughput',
      badge: run.backend === 'docker' ? 'DOCKER' : 'NATIVE',
      lines: [`${policyText(run, row.fsync)}, ${row.mode}, ${row.conns} connections`, run.backend === 'docker' ? String(run.environment.limits ?? '') : 'no CPU or memory limit', `${int(run.orders)} orders, median of ${row.valid_reps} runs [${int(Math.round(row.orders_per_sec.min))}–${int(Math.round(row.orders_per_sec.max))}]`, `${run.file}`],
    })),
    ...(nat && closed ? [{
      key: 'lat', big: `${Math.round(closed.lat_p50.median)}`, unit: 'µs median decision latency',
      badge: 'NATIVE', lines: [`${policyText(nat, closed.fsync)}, ${closed.mode}, ${closed.conns} connection, p99 ${Math.round(closed.lat_p99.median)} µs`, 'no CPU or memory limit', `${int(nat.orders)} orders, median of ${closed.valid_reps} runs`, nat.file],
    }] : []),
  ]
  const list = inv.data?.invariants ?? []
  const passing = list.filter((r) => r.status === 'passing').length
  return (
    <Section id="proof" label="Proof" title="Numbers read from result files. Tests read from a test report.">
      <div className="grid grid-cols-1 gap-14 md:grid-cols-2">
        <Reveal>
          <div className="label mb-5">Benchmarks</div>
          {bench.loading ? <div className="space-y-4" aria-busy="true">{[0, 1, 2].map((i) => <div key={i} className="skel h-20 w-full" />)}</div>
            : bench.error ? <p className="text-[14px] text-ink-2">Benchmark numbers are read from <span className="num">bench/results/*.json</span> through the local API, which is not running. Start it with <span className="num">make api</span>. No number is shown without its result file.</p>
            : stats.length === 0 ? <p className="text-[14px] text-ink-2">No result files in bench/results. Run <span className="num">make bench</span>.</p>
            : <div className="grid grid-cols-1 gap-8 sm:grid-cols-2 md:grid-cols-1 lg:grid-cols-2">{stats.map(({ key, ...s }) => <Stat key={key} {...s} />)}</div>}
          <p className="mt-6 max-w-[520px] text-[12.5px] leading-relaxed text-ink-3">Simulated order flow generated on one laptop, gate and load generator on the same machine. A measurement of this code, not of a trading venue and not a promise about yours. All tables: <a className="text-accept hover:underline" href="/app?open=bench">benchmarks in the dashboard</a>.</p>
        </Reveal>
        <Reveal delay={0.08}>
          <div className="label mb-5">Invariants and their tests</div>
          {inv.loading ? <div className="space-y-3" aria-busy="true">{[0, 1, 2, 3, 4].map((i) => <div key={i} className="skel h-9 w-full" />)}</div>
            : inv.error ? <p className="text-[14px] text-ink-2">The invariant list is read from <span className="num">docs/INVARIANTS.md</span> through the local API, which is not running.</p>
            : (
              <>
                <p className="mb-4 text-[13px] leading-relaxed text-ink-2" data-testid="inv-summary">
                  {inv.data!.result_file_found
                    ? <>{passing} of {list.length} invariants have only passing tests in <span className="num">{inv.data!.source}</span> ({inv.data!.suite?.tests} tests, {inv.data!.suite?.failures} failed, {inv.data!.suite?.skipped} skipped), written by <span className="num">make test</span> at {inv.data!.generated}.</>
                    : <>{inv.data!.note} Until then no invariant is shown as passing.</>}
                </p>
                <ul className="divide-y divide-line border-y border-line">
                  {list.map((r) => (
                    <li key={r.id} className="flex items-start gap-3 py-3 text-[13px]" data-testid="invariant">
                      <span className="num w-7 shrink-0 pt-0.5 text-ink-3">{r.id}</span>
                      <span className="flex-1 leading-snug text-ink-2">{r.claim}</span>
                      <span className={`num shrink-0 whitespace-nowrap rounded-md border px-1.5 py-0.5 text-[10.5px] ${pill[r.status]}`}>{pillText(r)}</span>
                    </li>
                  ))}
                </ul>
              </>
            )}
        </Reveal>
      </div>
    </Section>
  )
}

function Cta() {
  return (
    <section className="mx-auto w-full max-w-[1120px] px-6 py-24 md:px-10 md:py-32">
      <Reveal>
        <div className="glass flex flex-col items-start gap-6 p-8 md:flex-row md:items-center md:justify-between md:p-12">
          <div>
            <h2 className="text-[26px] font-semibold tracking-tight md:text-[34px]">See a log get checked.</h2>
            <p className="mt-2 max-w-[520px] text-[15px] text-ink-2">Run the demo, edit a copy of the log, and watch the verifier name the first bad line.</p>
          </div>
          <a className="btn btn-accept h-11 shrink-0 px-6 text-[15px]" href="/app" data-testid="cta-bottom">Open the dashboard</a>
        </div>
      </Reveal>
    </section>
  )
}

function Footer() {
  const bench = useAsync(() => api.bench(), [])
  const oom = bench.data?.runs.find((r) => r.backend === 'docker')?.recovery.find((r) => r.outcome === 'OOM_KILLED')
  return (
    <footer className="border-t border-line" data-testid="footer">
      <div className="mx-auto grid max-w-[1120px] gap-10 px-6 py-14 text-[13px] leading-relaxed text-ink-2 md:grid-cols-3 md:px-10">
        <div>
          <SimulatedLabel />
          <p className="mt-4">Everything here is simulated. No real orders, no real money, no broker connection and no market data. An ACCEPT is an immediate full fill at the order's own price. All orders and logs in the demo are generated.</p>
        </div>
        <div>
          <h3 className="label mb-3">Not covered</h3>
          <ul className="space-y-2">
            <li>No power-loss testing. Only process crashes (<span className="num">kill -9</span>) are tested.</li>
            <li>Recovering a 1,000,000-event log runs out of memory under 512 MB{oom ? <> (recorded as OOM_KILLED in the Docker result file)</> : null}.</li>
            <li>Tail truncation is not detectable: deleting the newest lines leaves a valid, shorter chain.</li>
            <li>The broker CSV layout is our assumption; no real broker export has been parsed.</li>
          </ul>
        </div>
        <div>
          <h3 className="label mb-3">What this does not claim</h3>
          <p>It does not claim to make or save anyone money. The counterfactual figures come from a hand-built fixture and describe what the rules would have decided, not what a trader would have done. Full list: <span className="num">docs/LIMITATIONS.md</span>. MIT licensed.</p>
        </div>
      </div>
    </footer>
  )
}

export default function Landing() {
  useEffect(() => {
    document.body.classList.add('scroll')
    return () => document.body.classList.remove('scroll')
  }, [])
  return (
    <LazyMotion features={domAnimation} strict={false}>
      <MotionConfig reducedMotion="user">
        <div className="min-h-dvh bg-bg" data-testid="landing">
          <Nav />
          <Hero />
          <Problem />
          <HowItWorks />
          <Who />
          <Proof />
          <Cta />
          <Footer />
        </div>
      </MotionConfig>
    </LazyMotion>
  )
}
