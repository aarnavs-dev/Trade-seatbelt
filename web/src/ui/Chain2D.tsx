import { useEffect, useRef, useState } from 'react'
import { badFrom, store, SWEEP_MS, useSnap } from '../lib/store'
import { shortHash } from '../lib/format'
import { useReducedMotion } from '../lib/env'

const W = 64, H = 72, GAP = 22

/** Static 2D version of the chain: used on phones, with prefers-reduced-motion, and when WebGL is unavailable. */
export default function Chain2D({ compact = false, reason }: { compact?: boolean; reason?: string }) {
  const s = useSnap()
  const reduced = useReducedMotion()
  const ref = useRef<HTMLDivElement>(null)
  const [, tick] = useState(0)
  const count = compact ? 14 : 40
  const blocks = store.blocks.slice(-count).reverse() // newest first, next to the gate
  const bad = badFrom(s)
  const sweeping = s.verify?.running
  // sweep: drive a progress value with rAF only while it runs
  useEffect(() => {
    if (!sweeping) return
    let id = 0
    const f = () => { tick((x) => x + 1); id = requestAnimationFrame(f) }
    id = requestAnimationFrame(f)
    return () => cancelAnimationFrame(id)
  }, [sweeping])
  useEffect(() => { ref.current?.scrollTo({ left: 0 }) }, [s.source])
  const tamperedSeq = s.tamper?.result.tampered_seq
  useEffect(() => {
    if (tamperedSeq === undefined || !ref.current) return
    const i = store.blocks.slice(-count).reverse().findIndex((b) => b.ev.seq === tamperedSeq)
    if (i >= 0) ref.current.scrollTo({ left: Math.max(0, 66 + i * (W + GAP) - 80), behavior: reduced ? 'auto' : 'smooth' })
  }, [tamperedSeq]) // eslint-disable-line react-hooks/exhaustive-deps
  const v = s.verify
  const lo = blocks.length ? blocks[blocks.length - 1].ev.seq : 1
  const hi = blocks.length ? blocks[0].ev.seq : 1
  const end = v?.badSeq ?? hi
  const p = v ? (v.running ? Math.min(1, (performance.now() - v.startedAt) / SWEEP_MS) : 1) : 0
  const sweepSeq = lo + (end - lo) * p
  const earlier = blocks.length ? blocks[blocks.length - 1].ev.seq - 1 : 0
  const width = 56 + blocks.length * (W + GAP) + (earlier > 0 ? 130 : 0)

  return (
    <div ref={ref} className="no-scrollbar relative h-full w-full overflow-x-auto overflow-y-hidden" aria-label="Hash chain, newest block first" role="list">
      {reason && <div className="absolute left-0 top-0 z-10 text-[11px] text-ink-3">{reason}</div>}
      <svg width={Math.max(width, 320)} height={compact ? 150 : 220} className="block" role="presentation">
        {/* gate */}
        <g transform={`translate(26, ${compact ? 75 : 110})`}>
          <circle r={22} fill="none" stroke="var(--color-ink-3)" strokeWidth={1} opacity={0.7} />
          <circle r={15} fill="none" stroke="var(--color-line-2)" strokeWidth={1} />
          <text y={-32} textAnchor="middle" className="fill-ink-3" fontSize={9} fontFamily="var(--font-mono)" letterSpacing={1}>GATE</text>
        </g>
        {blocks.map((b, i) => {
          const ev = b.ev
          const x = 66 + i * (W + GAP)
          const y = compact ? 75 - H / 2 : 110 - H / 2
          const acc = ev.decision === 'ACCEPT'
          const invalid = bad !== null && ev.seq >= bad
          const swept = v && ev.seq <= sweepSeq && ev.seq <= end
          const stroke = invalid ? 'var(--color-ink-3)' : acc ? 'var(--color-accept)' : 'var(--color-reject)'
          const selected = s.selected === ev.seq
          const tampered = s.tamper?.result.tampered_seq === ev.seq
          return (
            <g key={ev.seq} role="listitem" tabIndex={0} aria-label={`block ${ev.seq} ${ev.decision} ${ev.reason_code}`}
               onClick={() => store.select(selected ? null : ev.seq)} onKeyDown={(e) => (e.key === 'Enter' || e.key === ' ') && store.select(ev.seq)}
               onMouseEnter={() => store.hover(ev.seq)} onMouseLeave={() => store.hover(null)} style={{ cursor: 'pointer', animation: reduced ? undefined : 'fadein .35s ease both' }}>
              {i < blocks.length - 1 && (
                <line x1={x + W} y1={y + H / 2} x2={x + W + GAP} y2={y + H / 2} stroke={invalid ? 'var(--color-reject)' : swept ? 'var(--color-accept)' : 'var(--color-line-2)'} strokeWidth={1} strokeDasharray={invalid ? '3 3' : undefined} />
              )}
              <rect x={x} y={y} width={W} height={H} rx={6} fill={selected ? 'rgb(255 255 255 / 0.07)' : 'rgb(255 255 255 / 0.025)'} stroke={stroke} strokeWidth={selected || s.hover === ev.seq ? 1.6 : 1}
                    opacity={invalid ? 0.7 : 1} style={{ filter: swept && !invalid ? 'drop-shadow(0 0 6px rgb(92 200 255 / .55))' : undefined, transition: 'stroke .2s' }} />
              <text x={x + W / 2} y={y + 26} textAnchor="middle" fontSize={15} fontWeight={600} className="num fill-ink">{ev.seq}</text>
              <text x={x + W / 2} y={y + 44} textAnchor="middle" fontSize={10} className="num fill-ink-3">{shortHash(ev.hash)}</text>
              <text x={x + W / 2} y={y + 60} textAnchor="middle" fontSize={8.5} letterSpacing={0.8} fill={invalid ? 'var(--color-ink-3)' : acc ? 'var(--color-accept)' : 'var(--color-reject)'} className="num">
                {acc ? 'ACCEPT' : 'REJECT'}
              </text>
              {tampered && <path d={`M${x + 8},${y + 4} l14,16 l-8,8 l16,14 l-6,10 M${x + 40},${y + 6} l-6,12 l12,10`} stroke="var(--color-reject)" strokeWidth={1.6} fill="none" strokeLinejoin="round" />}
            </g>
          )
        })}
        {earlier > 0 && (
          <text x={66 + blocks.length * (W + GAP) + 6} y={(compact ? 75 : 110) + 4} fontSize={11} className="fill-ink-2">{earlier.toLocaleString('en-US')} earlier block{earlier === 1 ? '' : 's'}</text>
        )}
      </svg>
    </div>
  )
}
