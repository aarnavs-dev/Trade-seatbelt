import { AnimatePresence, m } from 'framer-motion'
import { useEffect, useState } from 'react'
import { int, iso, money, shortHash } from '../lib/format'
import { recomputeHash } from '../lib/hash'
import { badFrom, store, useSnap } from '../lib/store'
import { usePhone } from '../lib/env'
import type { LogEvent, Rules } from '../lib/types'

/** The rule that decided this event, with its configured limit from rules.yaml (as served by /report). */
export function ruleFor(ev: LogEvent, r: Rules | undefined): string {
  if (ev.reason_code === 'OK') return 'all rules passed'
  if (!r) return ev.reason_code
  switch (ev.reason_code) {
    case 'MAX_POSITION': return `max_position_per_symbol = ${r.max_position_per_symbol} shares`
    case 'PRICE_BAND': return `price_band_pct = ${r.price_band_pct}% of reference ${money(r.symbols[ev.symbol]?.reference_price ?? 0)}`
    case 'MAX_DAILY_LOSS': return `max_daily_loss = ${money(r.max_daily_loss)}`
    case 'MAX_ORDERS_PER_MINUTE':
    case 'RATE_LIMIT': return `max_orders_per_minute = ${r.max_orders_per_minute}`
    case 'LOSS_STREAK_COOLDOWN': return `loss_streak_cooldown = ${r.loss_streak_cooldown.losses} losses, ${r.loss_streak_cooldown.cooldown_seconds}s`
    case 'KILL_SWITCH': return 'kill_switch = true'
    case 'BAD_ORDER': return 'symbol must be in rules.yaml; qty and price > 0'
    default: return ev.reason_code
  }
}

function useRecomputed(ev: LogEvent | undefined) {
  const [h, setH] = useState<string | null>(null)
  useEffect(() => {
    setH(null)
    if (!ev) return
    let live = true
    recomputeHash(ev).then((x) => live && setH(x), () => live && setH(null))
    return () => { live = false }
  }, [ev])
  return h
}

const Field = ({ k, children }: { k: string; children: React.ReactNode }) => (
  <div className="grid grid-cols-[76px_1fr] gap-2 py-[2px] text-[12.5px]"><span className="text-ink-3">{k}</span><span className="num min-w-0 break-words text-ink">{children}</span></div>
)

export function BlockPanel() {
  const s = useSnap()
  const phone = usePhone()
  const seq = phone ? s.selected : (s.hover ?? s.selected)
  const pinned = s.selected !== null && seq === s.selected
  const shown = seq !== null ? store.byseq(seq) : undefined
  const t = s.tamper?.result
  const ev = shown && t && t.tampered_seq === shown.seq ? t.tampered_event : shown
  const prevRaw = ev ? store.byseq(ev.seq - 1) : undefined
  const prev = prevRaw && t && t.tampered_seq === prevRaw.seq ? t.tampered_event : prevRaw
  const next = ev ? store.byseq(ev.seq + 1) : undefined
  const mine = useRecomputed(ev)
  const theirs = useRecomputed(prev)
  const bad = badFrom(s)
  const selfOk = mine === null ? null : mine === ev?.hash
  const linkOk = ev && ev.seq === 1 ? ev.prev_hash === '0'.repeat(64) : prev && theirs ? theirs === ev?.prev_hash : null
  const rules = s.report?.rules

  return (
    <AnimatePresence>
      {ev && (
        <m.section key="block" initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: 6 }} transition={{ duration: 0.16, ease: 'easeOut' }}
                   className="glass pointer-events-auto max-h-[58dvh] w-full overflow-y-auto p-4" aria-label={`Block ${ev.seq}`} data-testid="block-panel">
          <div className="mb-2 flex items-center gap-3">
            <span className="num text-xl leading-none">#{ev.seq}</span>
            <span className={`num rounded-md border px-1.5 py-0.5 text-[11px] tracking-wide ${ev.decision === 'ACCEPT' ? 'border-accept/40 text-accept' : 'border-reject/40 text-reject'}`}>{ev.decision}</span>
            {bad !== null && ev.seq >= bad && <span className="num rounded-md border border-reject/40 px-1.5 py-0.5 text-[11px] text-reject">{ev.seq === bad ? 'FIRST BAD SEQ' : 'INVALID'}</span>}
            <span className="ml-auto text-[11px] text-ink-3">{pinned ? 'pinned · Esc to close' : 'click to pin'}</span>
            {pinned && <button className="text-ink-3 hover:text-ink" aria-label="Close block panel" onClick={() => store.select(null)}>✕</button>}
          </div>
          <div className="grid gap-x-8 gap-y-3 md:grid-cols-2">
            <div>
              <Field k="time (UTC)">{iso(ev.ts)}</Field>
              <Field k="order id">{ev.client_order_id}</Field>
              <Field k="order">{ev.side} {int(ev.qty)} {ev.symbol} @ {money(ev.price)}{t && t.tampered_seq === ev.seq && <span className="ml-2 text-reject">(qty edited {t.before} to {t.after})</span>}</Field>
              <Field k="decided by"><span className={ev.decision === 'REJECT' ? 'text-reject' : 'text-ink'}>{ev.reason_code}</span></Field>
              <Field k="rule">{ruleFor(ev, rules)}</Field>
              <Field k="reason">{ev.reason_text}</Field>
            </div>
            <div>
              <div className="py-[3px] text-[13px]">
                <span className="text-ink-3">prev_hash</span>{' '}
                <span className="num break-all text-ink-2">{ev.prev_hash.slice(0, 16)}…</span>
                <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-[12px]">
                  {ev.seq > 1 ? (
                    <button className="num text-accept hover:underline" onClick={() => store.select(ev.seq - 1)} disabled={!prevRaw}>← links to #{ev.seq - 1} {prevRaw ? shortHash(prevRaw.hash) : ''}</button>
                  ) : <span className="text-ink-3">genesis (64 zeros)</span>}
                  {linkOk !== null && <span className={linkOk ? 'text-ink-3' : 'text-reject'}>{linkOk ? 'link recomputed: matches' : 'link BROKEN: #' + (ev.seq - 1) + ' no longer produces this prev_hash'}</span>}
                </div>
              </div>
              <div className="py-[3px] text-[13px]">
                <span className="text-ink-3">hash</span>
                <div className="num break-all text-[12px] leading-snug text-ink-2" data-testid="full-hash">{ev.hash}</div>
                <div className={`mt-1 text-[12px] ${selfOk === false ? 'text-reject' : 'text-ink-3'}`} data-testid="hash-check">
                  {selfOk === null ? 'recomputing SHA-256 in this browser…' : selfOk ? 'SHA-256(prev_hash ‖ canonical JSON) recomputed here: matches' : 'recomputed here: MISMATCH — ' + (mine ?? '').slice(0, 16) + '…'}
                </div>
              </div>
              {next && <button className="num mt-1 text-[12px] text-ink-3 hover:text-ink" onClick={() => store.select(next.seq)}>#{next.seq} links back here →</button>}
            </div>
          </div>
        </m.section>
      )}
    </AnimatePresence>
  )
}
