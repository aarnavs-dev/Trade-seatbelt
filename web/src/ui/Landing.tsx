import { useEffect, useMemo, useRef, useState } from 'react'
import ChainScene, { useFlat } from '../scene/ChainScene'
import { ChainStore } from '../lib/store'
import { HERO_TRAVEL_MS, startHero } from '../lib/hero'
import { usePhone } from '../lib/env'
import { Page } from './kit'

const REPO = 'https://github.com/aarnavs-dev/Trade-seatbelt'
const STEPS = ['Leader signs\na signal', 'Solana devnet\nrecords it publicly', 'Your copier\nverifies the signer', 'Your rules\napprove or reject', 'Your audit log\nkeeps the proof']
const REASONS = [
  'Stopped: this would take you above your limit of 100 shares in AAPL',
  'Stopped: you don’t hold MSFT, so there is nothing to sell',
  'Stopped: this would bring today’s loss to $62.40, past your limit of $50.00',
  'Stopped: the price is more than 5% from yesterday’s close',
  'Stopped: 3 losing trades in a row, so new trades are paused for 5 minutes',
]
const X0 = 100, DX = 250, RULES_X = X0 + 3 * DX, END_X = X0 + 4 * DX, SPEED = 80   // diagram units; units per second
const GAPS = [3200, 3800, 3500, 3300, 3700]            // a new dot every 3.2 to 3.8 seconds
const STOPS = [false, true, false, false, true, false] // some pass, some stop at "Your rules"

interface Dot { id: number; stop: boolean; stopped: boolean }

function useReduced() {
  const [r, setR] = useState(() => matchMedia('(prefers-reduced-motion: reduce)').matches)
  useEffect(() => {
    const mq = matchMedia('(prefers-reduced-motion: reduce)')
    const on = () => setR(mq.matches)
    mq.addEventListener('change', on)
    return () => mq.removeEventListener('change', on)
  }, [])
  return r
}

function Flow() {
  const reduced = useReduced()
  const [dots, setDots] = useState<Dot[]>([])
  const [reason, setReason] = useState(-1)
  const n = useRef(0)
  const stops = useRef(0)

  useEffect(() => {
    if (reduced) return
    let t: ReturnType<typeof setTimeout>
    const spawn = () => {
      const i = n.current++
      setDots((d) => [...d.slice(-6), { id: i, stop: STOPS[i % STOPS.length], stopped: false }])
      t = setTimeout(spawn, GAPS[i % GAPS.length])
    }
    spawn()
    return () => clearTimeout(t)
  }, [reduced])

  const arrived = (d: Dot) => {
    if (!d.stop) return setDots((x) => x.filter((y) => y.id !== d.id))
    setReason(stops.current++ % REASONS.length)
    setDots((x) => x.map((y) => (y.id === d.id ? { ...y, stopped: true } : y)))
    setTimeout(() => setDots((x) => x.filter((y) => y.id !== d.id)), 2600)
  }
  const caption = reduced ? REASONS[0] : reason >= 0 ? REASONS[reason] : 'Every signal passes through your rules before anything is copied.'

  return (
    <svg className="flow" viewBox="0 0 1200 230" role="img" aria-label="A signal flows from the leader, to Solana devnet, to your copier, through your rules, into your audit log. Some are stopped at your rules.">
      <line className="track" x1={X0} y1={90} x2={END_X} y2={90} />
      {STEPS.map((s, i) => (
        <g key={s}>
          <circle className="node" cx={X0 + i * DX} cy={90} r={7} />
          {s.split('\n').map((line, k) => <text key={k} x={X0 + i * DX} y={128 + k * 18} textAnchor="middle">{line}</text>)}
        </g>
      ))}
      {!reduced && dots.map((d) => (
        <circle key={d.id} className={`sig travel ${d.stop ? 'stop' : ''} ${d.stopped ? 'stopped' : ''}`} cx={0} cy={90} r={5}
                style={{ ['--from' as string]: `${X0}px`, ['--to' as string]: `${d.stop ? RULES_X : END_X}px`, animationDuration: `${((d.stop ? RULES_X : END_X) - X0) / SPEED}s` }}
                onAnimationEnd={() => arrived(d)} />
      ))}
      <text key={caption} className="cap caption-fade" x={RULES_X} y={206} textAnchor="middle" data-testid="flow-caption">{caption}</text>
    </svg>
  )
}

const STEP_TEXT = ['Leader signs a signal', 'Solana devnet records it publicly', 'Your copier verifies the signer', 'Your rules approve or reject', 'Your audit log keeps the proof']

/** Phone width: the five steps as a vertical list (the wide diagram would shrink to unreadable text). Static, so it is also calm. */
function FlowList() {
  return (
    <div className="container" style={{ paddingBottom: 'var(--s6)' }}>
      <ol className="flowlist" aria-label="The five steps">
        {STEP_TEXT.map((t, i) => (
          <li key={t}><i className={`dot ${i === 3 ? 'dot-bad' : 'dot-ok'}`} /><span>{t}</span>
            {i === 3 && <small>Example stop: {REASONS[0].replace('Stopped: ', '')}</small>}</li>
        ))}
      </ol>
    </div>
  )
}

/** The five steps as a slow 3D scene (or the 2D diagram on phones, with reduced motion, or without WebGL). */
function Hero() {
  const { flat } = useFlat()
  const phone = usePhone()
  const store = useMemo(() => new ChainStore({ travelMs: HERO_TRAVEL_MS, spawnGapMs: 0 }), [])
  const [caption, setCaption] = useState('Every signal passes through your rules before anything is copied.')
  useEffect(() => (flat ? undefined : startHero(store, setCaption)), [flat, store])
  return (
    <section aria-label="How a signal flows" data-testid="hero-scene">
      <ChainScene store={store} variant="hero" fallback={phone ? <FlowList /> : <div className="container" style={{ paddingBottom: 'var(--s6)' }}><Flow /></div>} label={`The five steps: ${STEP_TEXT.join(', ')}.`} />
      {!flat && (
        <div className="container" style={{ paddingTop: 'var(--s2)' }}>
          <p className="caption-line" key={caption} data-testid="hero-caption"><span className="caption-fade">{caption}</span></p>
          <p className="ill">Illustration with sample signals, not live data.</p>
          <ol className="sr-only">{STEP_TEXT.map((t) => <li key={t}>{t}</li>)}</ol>
        </div>
      )}
    </section>
  )
}

const Rule = ({ t, d }: { t: string; d: string }) => <li><span style={{ fontWeight: 500 }}>{t}</span><span className="muted">{d}</span></li>

export default function Landing() {
  const how = useRef<HTMLDivElement>(null)
  return (
    <Page>
      <section className="hero" style={{ padding: 'var(--s12) 0 var(--s8)' }}>
        <div className="container stack-lg">
          <h1>Follow any trader, but never beyond your own limits.</h1>
          <p className="sub">Every copied trade has to pass your rules first, and every rejection is explained.</p>
          <p className="actions">
            <a className="btn btn-primary" href="/rules" data-testid="cta-rules">Set your rules</a>
            <a className="link-quiet" href="#how" onClick={(e) => { e.preventDefault(); how.current?.scrollIntoView({ behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' }) }}>How it works</a>
          </p>
        </div>
      </section>

      <Hero />

      <section className="section" id="how" ref={how}>
        <div className="container">
          <h2 className="sec-title">How Solana is used</h2>
          <div className="center-col stack-lg">
            <ol className="steps">
              <li><div><h3 style={{ fontSize: 22 }}>Publish</h3><p className="muted prose">The leader’s strategy posts each trade as a signed memo transaction on Solana devnet.</p></div></li>
              <li><div><h3 style={{ fontSize: 22 }}>Verify</h3><p className="muted prose">Anyone can read it. Your copier accepts only signals signed by the leader’s key, and every signal links to Solana Explorer.</p></div></li>
              <li><div><h3 style={{ fontSize: 22 }}>Decide</h3><p className="muted prose">Nothing happens because a signal is on-chain. Your own rules decide.</p></div></li>
            </ol>
            <div className="note"><h3 style={{ fontSize: 20, marginBottom: 8 }}>What Solana does not do here</h3>
              <p className="prose">It does not run your trades. It does not judge whether the leader is any good. Devnet is a test network, and fills are paper.</p></div>
          </div>
        </div>
      </section>

      <section className="section">
        <div className="container">
          <h2 className="sec-title">How your rules protect you</h2>
          <div className="center-col stack-lg">
            <ul className="rule-list">
              <Rule t="Biggest position in one stock" d="No order may take you above the number of shares you chose." />
              <Rule t="Most you can lose in a day" d="Once you have lost this much, new risk is blocked. Closing a position is always allowed." />
              <Rule t="Price band" d="Blocks an order priced too far from yesterday’s close, which catches typos." />
              <Rule t="Pause after losing trades" d="After several losses in a row, new trades wait a few minutes." />
              <Rule t="Session limits" d="A cap on trades, shares or money for one session. When it is reached, the session ends." />
            </ul>
            <div className="note" data-testid="example-rejection">
              <p className="label" style={{ marginBottom: 8 }}>Example</p>
              <p style={{ fontWeight: 500 }}><i className="dot dot-bad" />Rejected by your rules: BUY 10 AAPL at $336.31</p>
              <dl className="plain" style={{ marginTop: 'var(--s2)' }}>
                <div className="kv"><dt>Rule</dt><dd>Biggest position in one stock. It protects against a position larger than you are willing to hold.</dd></div>
                <div className="kv"><dt>Your limit</dt><dd className="num">100 shares</dd></div>
                <div className="kv"><dt>At that moment</dt><dd className="num">95 shares</dd></div>
                <div className="kv"><dt>If it had gone through</dt><dd className="num">105 shares</dd></div>
              </dl>
            </div>
          </div>
        </div>
      </section>

      <section className="section">
        <div className="container">
          <h2 className="sec-title">What you can check</h2>
          <ol className="steps center-col">
            <li><div><h3 style={{ fontSize: 22 }}>A hash-chained log</h3><p className="muted prose">Every decision is written to an append-only log. Each line carries the hash of the line before it, so an edit, a deletion or a reordering is detectable.</p></div></li>
            <li><div><h3 style={{ fontSize: 22 }}>A verify button</h3><p className="muted prose">The audit page re-checks the whole chain and names the first line that does not hold.</p></div></li>
            <li><div><h3 style={{ fontSize: 22 }}>Tests and docs</h3><p className="muted prose">The invariants, the benchmark method and the known limits are written down next to the code. <a href={REPO} target="_blank" rel="noopener noreferrer">Read the repository</a>.</p></div></li>
          </ol>
        </div>
      </section>

      <footer className="section" style={{ paddingBottom: 'var(--s8)' }}>
        <div className="container">
          <p className="label" style={{ textAlign: 'center', marginBottom: 'var(--s3)' }}>Honest limits</p>
          <ul className="center-col muted" style={{ margin: '0 auto', paddingLeft: 18 }}>
            <li>Fills are simulated. No real orders, no real money, no broker connection.</li>
            <li>Signals are published to Solana devnet, a test network that can be reset.</li>
            <li>Sessions replay historical data unless a live source is enabled.</li>
            <li>This is not financial advice and says nothing about future results.</li>
          </ul>
        </div>
      </footer>
    </Page>
  )
}
