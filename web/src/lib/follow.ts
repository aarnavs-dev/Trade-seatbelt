import { API_BASE, call, json } from './api'
import type { LogEvent } from './types'

export type Status = 'COPIED' | 'STOPPED_BY_COPIER' | 'REJECTED_BY_RULES' | 'CLIPPED'
export type Feed = 'auto' | 'chain' | 'local'
export type Pace = 'relaxed' | 'normal' | 'fast'
export type CopyMode = 'same' | 'percent' | 'fixed'

export interface Row {
  n: number
  signal_id: string
  leader: string
  symbol: string | null
  side: 'BUY' | 'SELL' | null
  leader_qty: number | null
  qty: number | null
  price: number | null
  leader_ts: number | null
  status: Status
  reason_code: string
  reason_text: string
  tx: string | null
  slot: number | null
  explorer_url: string | null
  hash: string | null
}

export interface Summary {
  run_id: string
  state: string
  total_in_feed: number
  seen: number
  copied: number
  stopped: number
  rejected: number
  notes: string[]
  data_label: string | null
  feed: { source: string; badge: string }
  session_limit: { code: string; text: string } | null
  pace: { name: string; seed: number | null } | null
}

export interface Explanation {
  n: number
  status: Status
  signal_id: string
  order: string | null
  rule: string | null
  protects_against: string | null
  limit: string | null
  value_before: string | null
  value_if_accepted: string | null
  detail: string
  cause_text: string | null
  leader_signal: Record<string, unknown> | null
  explorer_url: string | null
}

export interface Leader { id: string; name: string; about: string; public_key: string | null; explorer_url: string | null }

export const STATUS: Record<Status, { label: string; dot: string }> = {
  COPIED: { label: 'Copied', dot: 'dot-ok' },
  STOPPED_BY_COPIER: { label: 'Stopped by copier', dot: 'dot-warn' },
  REJECTED_BY_RULES: { label: 'Rejected by your rules', dot: 'dot-bad' },
  CLIPPED: { label: 'Clipped', dot: 'dot-off' },
}

export const FILTERS: { key: 'ALL' | Status; label: string }[] = [
  { key: 'ALL', label: 'All' }, { key: 'COPIED', label: 'Copied' }, { key: 'STOPPED_BY_COPIER', label: 'Stopped' },
  { key: 'REJECTED_BY_RULES', label: 'Rejected' }, { key: 'CLIPPED', label: 'Clipped' },
]

export const PACES: { key: Pace; label: string; about: string }[] = [
  { key: 'relaxed', label: 'Relaxed', about: 'about 4 to 8 seconds between signals' },
  { key: 'normal', label: 'Normal', about: 'about 2 to 4 seconds between signals' },
  { key: 'fast', label: 'Fast', about: 'under a second between signals' },
]

export interface Options {
  feed: Feed
  leader: string
  pace: Pace
  copyMode: CopyMode
  copyValue: string
  maxTrades: string
  maxVolume: string
  maxTotalDollars: string
  maxTradeDollars: string
}

export const DEFAULT_OPTIONS: Options = { feed: 'auto', leader: 'momentum', pace: 'normal', copyMode: 'same', copyValue: '', maxTrades: '', maxVolume: '', maxTotalDollars: '', maxTradeDollars: '' }

/** "12.5" -> 1250 cents without floats; null if it is not a plain amount with at most two decimals. */
export function dollarsToCents(s: string): number | null {
  const m = /^\s*(\d+)(?:\.(\d{1,2}))?\s*$/.exec(s)
  return m ? Number(m[1]) * 100 + Number((m[2] ?? '').padEnd(2, '0') || '0') : null
}

/** A blank field means "no limit"; anything else is sent as typed (numbers when whole) so the API's own message can explain it. */
const whole = (s: string): number | string | undefined => (s.trim() === '' ? undefined : /^\s*-?\d+\s*$/.test(s) ? Number(s) : s)
const cents = (s: string): number | string | undefined => (s.trim() === '' ? undefined : (dollarsToCents(s) ?? s))

export function toRequest(o: Options) {
  const size = o.copyMode === 'same' ? { mode: 'same' } : { mode: o.copyMode, value: whole(o.copyValue) }
  const session = Object.fromEntries(Object.entries({
    max_trades: whole(o.maxTrades), max_volume: whole(o.maxVolume), max_total_amount: cents(o.maxTotalDollars), max_trade_amount: cents(o.maxTradeDollars),
  }).filter(([, v]) => v !== undefined))
  return { feed: o.feed, leader: o.leader, pace: o.pace, size, session }
}

/** New York wall-clock time of a signal, e.g. "10:35". */
export function nyTime(ms: number | null): string {
  if (ms === null) return '--:--'
  return new Intl.DateTimeFormat('en-US', { timeZone: 'America/New_York', hour: '2-digit', minute: '2-digit', hour12: false }).format(new Date(ms))
}

/** A follow row as a chain block: copied/clipped orders are accepted blocks, everything stopped or rejected is a rejected one. */
export function rowToEvent(r: Row): LogEvent {
  return {
    seq: r.n, ts: (r.leader_ts ?? 0) * 1e6, client_order_id: r.signal_id, symbol: r.symbol ?? '', side: r.side ?? 'BUY', qty: r.qty ?? 0, price: r.price ?? 0,
    decision: r.status === 'COPIED' || r.status === 'CLIPPED' ? 'ACCEPT' : 'REJECT', reason_code: r.reason_code, reason_text: r.reason_text, prev_hash: '', hash: r.hash ?? '',
  }
}

export const shortKey = (k: string) => `${k.slice(0, 4)}…${k.slice(-4)}`

export const follow = {
  leaders: () => call<{ leaders: Leader[]; note: string }>('/follow/leaders'),
  start: (o: Options) => call<{ run: string }>('/follow/run', json('POST', toRequest(o))),
  stop: (run: string) => call<{ stopped: boolean }>(`/follow/${run}/stop`, { method: 'POST' }),
  summary: (run: string) => call<Summary>(`/follow/${run}/summary`),
  explain: (run: string, n: number) => call<Explanation>(`/follow/${run}/explain/${n}`),
  streamUrl: (run: string) => `${API_BASE}/follow/${run}/stream`,
}
