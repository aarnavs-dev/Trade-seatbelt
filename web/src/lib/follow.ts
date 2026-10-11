import { ApiError } from './api'

const BASE = '/api'

export type Status = 'COPIED' | 'STOPPED_BY_COPIER' | 'REJECTED_BY_RULES' | 'CLIPPED'
export type LeaderName = 'orb' | 'momentum' | 'meanrev'

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
  explorer_url: string | null
}

export interface Summary {
  run_id: string
  state: string
  total_in_feed: number
  seen: number
  copied: number
  clipped: number
  stopped: number
  stopped_by_reason: Record<string, number>
  rejected: number
  rejected_by_reason: Record<string, number>
  notes: string[]
  data_label: string | null
  data: { label: string; session: string; provider: string; fallback_reason: string | null }
  feed: { source: string; badge: string; why?: string }
  limits: { max_position_per_symbol: number; max_daily_loss: number; price_band_pct: number; cooldown_after_losses: number }
}

export interface Explanation {
  n: number
  status: Status
  signal_id: string
  reason_code: string
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

/** The four editable limits, in the units the person sees (dollars, percent, losses). */
export interface Limits {
  maxPosition: string
  maxLossDollars: string
  bandPct: string
  cooldownLosses: string
}

export const DEFAULT_LIMITS: Limits = { maxPosition: '100', maxLossDollars: '50', bandPct: '5', cooldownLosses: '3' }

/** Dollars text -> integer cents without floats ("12.5" -> 1250). Returns null for anything that is not a plain amount. */
export function dollarsToCents(s: string): number | null {
  const m = /^\s*(\d+)(?:\.(\d{1,2}))?\s*$/.exec(s)
  return m ? Number(m[1]) * 100 + Number((m[2] ?? '').padEnd(2, '0') || '0') : null
}

/** Page fields -> the API's `limits` object. Anything that is not a whole number is sent as typed so the API's own error shows. */
export function toApiLimits(l: Limits): Record<string, unknown> {
  const num = (s: string) => (/^\s*-?\d+\s*$/.test(s) ? Number(s) : s)
  const cents = dollarsToCents(l.maxLossDollars)
  return {
    max_position_per_symbol: num(l.maxPosition),
    max_daily_loss: cents ?? l.maxLossDollars,
    price_band_pct: num(l.bandPct),
    loss_streak_cooldown: { losses: num(l.cooldownLosses) },
  }
}

export function limitsFromSummary(s: Summary['limits']): Limits {
  return { maxPosition: String(s.max_position_per_symbol), maxLossDollars: (s.max_daily_loss / 100).toFixed(2), bandPct: String(s.price_band_pct), cooldownLosses: String(s.cooldown_after_losses) }
}

export const STATUS: Record<Status, { label: string; chip: string }> = {
  COPIED: { label: 'COPIED', chip: 'border-[#4ade80]/40 text-[#4ade80]' },
  STOPPED_BY_COPIER: { label: 'STOPPED BY COPIER', chip: 'border-[#fbbf24]/40 text-[#fbbf24]' },
  REJECTED_BY_RULES: { label: 'REJECTED BY YOUR RULES', chip: 'border-reject/40 text-reject' },
  CLIPPED: { label: 'CLIPPED', chip: 'border-line-2 text-ink-2' },
}

export const LEADERS: Record<LeaderName, { name: string; about: string }> = {
  momentum: { name: 'Momentum', about: 'Buys when the price has risen at least 0.30% over the last hour, and sells after a 0.15% fall.' },
  orb: { name: 'Opening range', about: 'Buys when the price breaks above the first 30 minutes of the day, and sells if it drops below that range or at the close.' },
  meanrev: { name: 'Mean reversion', about: 'Buys when the price is 0.40% under its recent average, and sells when it is back at the average.' },
}

/** New York wall-clock time of a signal, e.g. "10:35". */
export function nyTime(ms: number | null): string {
  if (ms === null) return '--:--'
  return new Intl.DateTimeFormat('en-US', { timeZone: 'America/New_York', hour: '2-digit', minute: '2-digit', hour12: false }).format(new Date(ms))
}

export const FILTERS: { key: 'ALL' | Status; label: string }[] = [
  { key: 'ALL', label: 'All' }, { key: 'COPIED', label: 'Copied' }, { key: 'STOPPED_BY_COPIER', label: 'Stopped' },
  { key: 'REJECTED_BY_RULES', label: 'Rejected' }, { key: 'CLIPPED', label: 'Clipped' },
]

export function errorText(status: number, body: unknown): string {
  const b = body as { message?: string; detail?: unknown } | null
  if (b?.message) return b.message
  if (Array.isArray(b?.detail)) return String((b!.detail as { msg?: string }[])[0]?.msg ?? 'invalid request')
  if (status === 0 || (status >= 500 && !b)) return 'API not reachable on 127.0.0.1:8000. Start it with `make api`.'
  return `HTTP ${status}`
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response
  try {
    res = await fetch(BASE + path, init)
  } catch {
    throw new ApiError(0, null, errorText(0, null))
  }
  const text = await res.text()
  let body: unknown = null
  try { body = text ? JSON.parse(text) : null } catch { /* proxy error pages are not JSON */ }
  if (!res.ok) throw new ApiError(res.status, body as never, errorText(res.status, body))
  return body as T
}

export const follow = {
  start: (feed: 'local' | 'chain', leader: LeaderName, limits: Record<string, unknown>) =>
    call<{ run: string }>('/follow/run', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ feed, leader, limits }) }),
  stop: (run: string) => call<{ stopped: boolean }>(`/follow/${run}/stop`, { method: 'POST' }),
  summary: (run: string) => call<Summary>(`/follow/${run}/summary`),
  explain: (run: string, n: number) => call<Explanation>(`/follow/${run}/explain/${n}`),
  streamUrl: (run: string) => `${BASE}/follow/${run}/stream`,
}
